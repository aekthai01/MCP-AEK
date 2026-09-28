from __future__ import annotations

import json
import os
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
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


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

    @classmethod
    def load(cls) -> "Settings":
        state = _read_state()
        root = _expand_path(os.getenv("AEK_WORKSPACE_ROOT", "~/mcp-aek/workspaces"))
        active = str(state.get("active_workspace") or os.getenv("AEK_ACTIVE_WORKSPACE", "default")).strip()
        if not active or active in {".", ".."} or "/" in active or "\\" in active:
            active = "default"
        root.mkdir(parents=True, exist_ok=True)
        (root / active).mkdir(parents=True, exist_ok=True)
        return cls(
            upstream_base_url=os.getenv("AEK_UPSTREAM_BASE_URL", "http://127.0.0.1:5656/v1").rstrip("/"),
            upstream_api_key=os.getenv("AEK_UPSTREAM_API_KEY", "sk-cgfree-local"),
            model=os.getenv("AEK_MODEL", "gpt-5-6"),
            workspace_root=root,
            active_workspace=active,
            enable_shell=_env_bool("AEK_ENABLE_SHELL", False),
            tool_timeout=max(1, int(os.getenv("AEK_TOOL_TIMEOUT", "120"))),
            max_tool_rounds=max(1, int(os.getenv("AEK_MAX_TOOL_ROUNDS", "24"))),
            max_file_bytes=max(1024, int(os.getenv("AEK_MAX_FILE_BYTES", "1048576"))),
        )

    @property
    def workspace(self) -> Path:
        path = (self.workspace_root / self.active_workspace).resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path


def set_active_workspace(name: str) -> Path:
    name = name.strip()
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise ValueError("workspace name must be one simple directory name")
    settings = Settings.load()
    path = (settings.workspace_root / name).resolve()
    if settings.workspace_root not in path.parents and path != settings.workspace_root:
        raise ValueError("workspace escapes root")
    path.mkdir(parents=True, exist_ok=True)
    state = _read_state()
    state["active_workspace"] = name
    _write_state(state)
    return path


def list_workspaces() -> list[str]:
    settings = Settings.load()
    return sorted(p.name for p in settings.workspace_root.iterdir() if p.is_dir())
