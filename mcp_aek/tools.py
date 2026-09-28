from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .config import Settings


TEXT_EXTENSIONS = {
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx",
    ".lua", ".luau", ".py", ".pyi", ".js", ".mjs", ".cjs", ".ts", ".tsx",
    ".java", ".kt", ".kts", ".rs", ".go", ".sh", ".bash", ".zsh",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".md", ".txt",
    ".gradle", ".properties", ".xml", ".html", ".css", ".sql",
}


def _settings() -> Settings:
    return Settings.load()


def _workspace() -> Path:
    return _settings().workspace


def _resolve(path: str | None = None) -> Path:
    root = _workspace()
    raw = Path(path or ".")
    candidate = (root / raw).resolve() if not raw.is_absolute() else raw.resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"path escapes active workspace: {path}")
    return candidate


def _rel(path: Path) -> str:
    return str(path.relative_to(_workspace())) if path != _workspace() else "."


def _clip(text: str, limit: int = 12000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated {len(text) - limit} chars]"


def workspace_info() -> dict[str, Any]:
    """Return active workspace identity, path, and basic contents."""
    s = _settings()
    entries = sorted(p.name + ("/" if p.is_dir() else "") for p in s.workspace.iterdir())[:200]
    return {
        "active_workspace": s.active_workspace,
        "workspace": str(s.workspace),
        "workspace_root": str(s.workspace_root),
        "shell_enabled": s.enable_shell,
        "entries": entries,
    }


def list_files(path: str = ".", recursive: bool = False, limit: int = 500) -> dict[str, Any]:
    """List files/directories inside the active workspace."""
    base = _resolve(path)
    if not base.exists():
        raise FileNotFoundError(path)
    if base.is_file():
        return {"items": [_rel(base)], "truncated": False}
    iterator = base.rglob("*") if recursive else base.iterdir()
    items: list[str] = []
    for p in iterator:
        items.append(_rel(p) + ("/" if p.is_dir() else ""))
        if len(items) >= max(1, min(limit, 5000)):
            return {"items": sorted(items), "truncated": True}
    return {"items": sorted(items), "truncated": False}


def read_text(path: str, start_line: int = 1, end_line: int = 0) -> dict[str, Any]:
    """Read UTF-8 text from a workspace file. end_line=0 means through EOF."""
    p = _resolve(path)
    if not p.is_file():
        raise FileNotFoundError(path)
    max_bytes = _settings().max_file_bytes
    size = p.stat().st_size
    if size > max_bytes:
        raise ValueError(f"file is {size} bytes; limit is {max_bytes}. Use command-line tools for large files")
    text = p.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    start = max(1, start_line)
    stop = len(lines) if end_line <= 0 else min(len(lines), max(start, end_line))
    selected = lines[start - 1 : stop]
    return {
        "path": _rel(p),
        "start_line": start,
        "end_line": stop,
        "total_lines": len(lines),
        "content": "\n".join(selected),
    }


def write_text(path: str, content: str, overwrite: bool = True) -> dict[str, Any]:
    """Write a UTF-8 text file inside the active workspace."""
    p = _resolve(path)
    if p.exists() and p.is_dir():
        raise IsADirectoryError(path)
    if p.exists() and not overwrite:
        raise FileExistsError(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return {"path": _rel(p), "bytes": p.stat().st_size}


def replace_text(path: str, old: str, new: str, count: int = 0) -> dict[str, Any]:
    """Replace exact text in a UTF-8 file. count=0 replaces all matches."""
    p = _resolve(path)
    if not p.is_file():
        raise FileNotFoundError(path)
    text = p.read_text(encoding="utf-8", errors="strict")
    matches = text.count(old)
    if matches == 0:
        raise ValueError("old text not found")
    replaced = text.replace(old, new, count if count > 0 else -1)
    p.write_text(replaced, encoding="utf-8")
    return {"path": _rel(p), "matches_found": matches, "replacements": min(matches, count) if count > 0 else matches}


def search_text(query: str, path: str = ".", regex: bool = False, max_results: int = 200) -> dict[str, Any]:
    """Search text files under the workspace and return matching lines."""
    base = _resolve(path)
    pattern = re.compile(query) if regex else None
    results: list[dict[str, Any]] = []
    files = [base] if base.is_file() else base.rglob("*")
    for p in files:
        if p.is_symlink() or not p.is_file():
            continue
        if p.suffix.lower() not in TEXT_EXTENSIONS and p.name not in {"Makefile", "Dockerfile", "CMakeLists.txt"}:
            continue
        try:
            if p.stat().st_size > _settings().max_file_bytes:
                continue
            for idx, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                matched = bool(pattern.search(line)) if pattern else query in line
                if matched:
                    results.append({"path": _rel(p), "line": idx, "text": _clip(line, 800)})
                    if len(results) >= max(1, min(max_results, 2000)):
                        return {"results": results, "truncated": True}
        except OSError:
            continue
    return {"results": results, "truncated": False}


def file_hash(path: str, algorithm: str = "sha256") -> dict[str, Any]:
    """Hash a workspace file with sha256/sha1/md5/blake2b."""
    p = _resolve(path)
    if not p.is_file():
        raise FileNotFoundError(path)
    allowed = {"sha256", "sha1", "md5", "blake2b"}
    if algorithm not in allowed:
        raise ValueError(f"algorithm must be one of {sorted(allowed)}")
    h = hashlib.new(algorithm)
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return {"path": _rel(p), "algorithm": algorithm, "digest": h.hexdigest(), "bytes": p.stat().st_size}


def tool_status(names: list[str] | None = None) -> dict[str, Any]:
    """Report whether useful Termux/build/reverse-engineering executables are installed."""
    defaults = [
        "python", "pip", "git", "clang", "clang++", "cmake", "make", "ninja",
        "lua", "luajit", "file", "readelf", "objdump", "nm", "strings",
        "rizin", "rz-bin", "radare2", "r2", "jadx", "apktool", "zip", "unzip", "tar", "rg", "jq",
    ]
    selected = names or defaults
    return {name: shutil.which(name) for name in selected}


def _run(argv: list[str], cwd: Path, timeout: int | None = None) -> dict[str, Any]:
    if not argv:
        raise ValueError("argv cannot be empty")
    exe = shutil.which(argv[0])
    if exe is None:
        raise FileNotFoundError(f"executable not found: {argv[0]}")
    effective_timeout = timeout or _settings().tool_timeout
    proc = subprocess.run(
        [exe, *argv[1:]],
        cwd=str(cwd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=effective_timeout,
        env=os.environ.copy(),
        check=False,
    )
    return {
        "argv": argv,
        "cwd": _rel(cwd),
        "exit_code": proc.returncode,
        "stdout": _clip(proc.stdout),
        "stderr": _clip(proc.stderr),
    }


def run_command(argv: list[str], cwd: str = ".", timeout: int = 0) -> dict[str, Any]:
    """Run one executable directly (no shell) inside the workspace. Pass arguments as an array."""
    if len(argv) > 256:
        raise ValueError("too many arguments")
    workdir = _resolve(cwd)
    if not workdir.is_dir():
        raise NotADirectoryError(cwd)
    return _run(argv, workdir, timeout if timeout > 0 else None)


def shell_exec(command: str, cwd: str = ".", timeout: int = 0) -> dict[str, Any]:
    """Run a shell command inside the workspace. Disabled unless AEK_ENABLE_SHELL=1."""
    s = _settings()
    if not s.enable_shell:
        raise PermissionError("shell_exec is disabled; set AEK_ENABLE_SHELL=1 to enable it")
    workdir = _resolve(cwd)
    if not workdir.is_dir():
        raise NotADirectoryError(cwd)
    shell = shutil.which("bash") or shutil.which("sh")
    if shell is None:
        raise FileNotFoundError("bash/sh not found")
    proc = subprocess.run(
        [shell, "-lc", command],
        cwd=str(workdir),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout if timeout > 0 else s.tool_timeout,
        env=os.environ.copy(),
        check=False,
    )
    return {
        "command": command,
        "cwd": _rel(workdir),
        "exit_code": proc.returncode,
        "stdout": _clip(proc.stdout),
        "stderr": _clip(proc.stderr),
    }


def binary_inspect(path: str) -> dict[str, Any]:
    """Run available metadata/symbol helpers against a binary such as ELF .so."""
    p = _resolve(path)
    if not p.is_file():
        raise FileNotFoundError(path)
    rel = _rel(p)
    commands = [
        ["file", rel],
        ["readelf", "-h", rel],
        ["readelf", "-S", rel],
        ["nm", "-D", "--defined-only", rel],
        ["strings", "-n", "6", rel],
    ]
    reports: list[dict[str, Any]] = []
    for argv in commands:
        if shutil.which(argv[0]):
            try:
                reports.append(_run(argv, _workspace(), min(_settings().tool_timeout, 60)))
            except subprocess.TimeoutExpired:
                reports.append({"argv": argv, "error": "timeout"})
    return {"path": rel, "reports": reports}


def git_status() -> dict[str, Any]:
    """Return git status for the active workspace."""
    return _run(["git", "status", "--short", "--branch"], _workspace())


def git_diff(cached: bool = False, path: str = ".") -> dict[str, Any]:
    """Return git diff for a path in the active workspace."""
    target = _resolve(path)
    argv = ["git", "diff"]
    if cached:
        argv.append("--cached")
    argv.extend(["--", _rel(target)])
    return _run(argv, _workspace())
