from __future__ import annotations

import json
import os
import tempfile
import fcntl
import re
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _expand_path(raw: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(raw))).resolve()


STATE_DIR = _expand_path(os.getenv("AEK_STATE_DIR", "~/.config/mcp-aek"))
STATE_FILE = STATE_DIR / "state.json"


def _read_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _write_state(data: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="state-", suffix=".tmp", dir=STATE_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, STATE_FILE)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def _state_lock():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(STATE_DIR / "state.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


@dataclass(frozen=True)
class Settings:
    upstream_base_url: str
    upstream_api_key: str
    model: str
    workspace_root: Path
    active_workspace: str
    enable_shell: bool
    tool_timeout: int
    max_tool_rounds: int
    max_file_bytes: int
    context_bytes: int = 60000

    @classmethod
    def load(cls) -> "Settings":
        state = _read_state()
        root = _expand_path(os.getenv("AEK_WORKSPACE_ROOT", "~/mcp-aek/workspaces"))
        active = str(os.getenv("AEK_PINNED_WORKSPACE") or state.get("active_workspace") or os.getenv("AEK_ACTIVE_WORKSPACE", "default")).strip()
        if not active or active.startswith('.') or "/" in active or "\\" in active:
            active = "default"
        root.mkdir(parents=True, exist_ok=True)
        if (root / active).is_symlink():
            raise ValueError("workspace cannot be a symlink")
        (root / active).mkdir(parents=True, exist_ok=True)
        return cls(
            upstream_base_url=os.getenv("AEK_UPSTREAM_BASE_URL", "http://127.0.0.1:5656/v1").rstrip("/"),
            upstream_api_key=os.getenv("AEK_UPSTREAM_API_KEY", "sk-cgfree-local"),
            model=os.getenv("AEK_MODEL") or state.get("model") or "gpt-5-6",
            workspace_root=root,
            active_workspace=active,
            enable_shell=_env_bool("AEK_ENABLE_SHELL", False),
            tool_timeout=max(1, int(os.getenv("AEK_TOOL_TIMEOUT", "120"))),
            max_tool_rounds=max(1, int(os.getenv("AEK_MAX_TOOL_ROUNDS", "24"))),
            context_bytes=max(4096, int(os.getenv("AEK_CONTEXT_BYTES", "60000"))),
            max_file_bytes=max(1024, int(os.getenv("AEK_MAX_FILE_BYTES", "1048576"))),
        )

    @property
    def workspace(self) -> Path:
        path = (self.workspace_root / self.active_workspace).resolve()
        if path.parent != self.workspace_root.resolve():
            raise ValueError("workspace escapes root")
        path.mkdir(parents=True, exist_ok=True)
        return path


def set_active_workspace(name: str) -> Path:
    if not isinstance(name, str):
        raise ValueError("workspace name must be one simple directory name")
    name = name.strip()
    if not name or name.startswith('.') or "/" in name or "\\" in name or any(ord(c) < 32 for c in name):
        raise ValueError("workspace name must be one simple directory name")
    settings = Settings.load()
    if (settings.workspace_root / name).is_symlink():
        raise ValueError("workspace cannot be a symlink")
    path = (settings.workspace_root / name).resolve()
    if settings.workspace_root not in path.parents and path != settings.workspace_root:
        raise ValueError("workspace escapes root")
    path.mkdir(parents=True, exist_ok=True)
    with _state_lock():
        state = _read_state()
        # Keep a running task bound to its original workspace across processes.
        from .sessions import SessionStore
        current_name = str(state.get("active_workspace") or os.getenv("AEK_ACTIVE_WORKSPACE", "default"))
        if current_name != name:
            with SessionStore(settings.workspace_root / current_name).execution_lock():
                state["active_workspace"] = name
                _write_state(state)
            return path
        state["active_workspace"] = name
        _write_state(state)
    return path


def list_workspaces() -> list[str]:
    settings = Settings.load()
    return sorted(p.name for p in settings.workspace_root.iterdir() if p.is_dir() and not p.is_symlink() and not p.name.startswith('.'))


def clone_workspace(url: str, name: str) -> Path:
    if not isinstance(url, str) or not re.fullmatch(r'https://[^\s]+', url):
        raise ValueError('repository URL must use HTTPS')
    from urllib.parse import urlsplit
    parsed = urlsplit(url)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('credentials and URL parameters are not supported')
    if parsed.hostname not in {'github.com', 'gitlab.com', 'bitbucket.org'}:
        raise ValueError('repository host is not supported')
    if not isinstance(name, str) or not name or name != name.strip() or name.startswith('.') or '/' in name or '\\' in name or any(ord(c) < 32 for c in name):
        raise ValueError('workspace name must be one simple directory name')
    settings = Settings.load()
    target = settings.workspace_root / name
    if target.exists() or target.is_symlink():
        raise ValueError('workspace already exists')
    # Destination must stay absent until git creates it; never pass input through a shell.
    result = subprocess.run(['git', '-c', 'credential.helper=', 'clone', '--progress', '--', url, str(target)],
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=180,
                            env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'})
    if result.returncode:
        import shutil
        if target.exists():
            shutil.rmtree(target)
        raise ValueError('clone failed; verify repository URL and connectivity')
    return target


def workspace_details() -> list[dict]:
    from .sessions import SessionStore
    settings = Settings.load()
    result = []
    for name in list_workspaces():
        path = settings.workspace_root / name
        size = 0
        visited = 0
        for base, dirs, files in os.walk(path, followlinks=False):
            dirs[:] = [d for d in dirs if d not in {'.git', '.aek'} and not (Path(base) / d).is_symlink()]
            for file in files:
                visited += 1
                if visited > 5000:
                    dirs.clear()
                    break
                p = Path(base) / file
                if not p.is_symlink():
                    try: size += p.stat().st_size
                    except OSError: pass
        result.append({'name': name, 'path': str(path), 'size': size, 'last_activity': path.stat().st_mtime,
                       'git': (path / '.git').is_dir(), 'sessions': len(SessionStore(path).list())})
    return result


def set_model(model: str) -> None:
    if not isinstance(model, str) or not model or len(model) > 120:
        raise ValueError('invalid model ID')
    if os.getenv('AEK_MODEL'):
        raise ValueError('AEK_MODEL is set in the environment; update it and restart to change models')
    with _state_lock():
        state = _read_state()
        state['model'] = model
        _write_state(state)


def remove_workspace(name: str, archive=False) -> None:
    import shutil
    from .sessions import SessionStore
    with _state_lock():
        if name not in list_workspaces():
            raise ValueError('workspace not found')
        settings = Settings.load()
        if name == settings.active_workspace:
            raise ValueError('switch to another workspace first')
        path = settings.workspace_root / name
        with SessionStore(path).execution_lock():
            if archive:
                archived = settings.workspace_root / '.archived'
                archived.mkdir(exist_ok=True)
                target = archived / name
                if target.exists():
                    raise ValueError('an archived workspace with this name already exists')
                path.rename(target)
            else:
                shutil.rmtree(path)


def rename_workspace(name: str, new_name: str) -> Path:
    from .sessions import SessionStore
    if not isinstance(new_name, str) or not new_name or new_name != new_name.strip() or new_name.startswith('.') or '/' in new_name or '\\' in new_name or any(ord(c) < 32 for c in new_name):
        raise ValueError('invalid new workspace name')
    with _state_lock():
        if name not in list_workspaces():
            raise ValueError('workspace not found')
        settings = Settings.load()
        if name == settings.active_workspace:
            raise ValueError('switch to another workspace before renaming')
        source = settings.workspace_root / name
        target = settings.workspace_root / new_name
        if target.exists() or target.is_symlink():
            raise ValueError('target workspace already exists')
        with SessionStore(source).execution_lock():
            source.rename(target)
    return target
