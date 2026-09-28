"""Bounded, workspace-local file and Git operations for the loopback UI."""
from __future__ import annotations

import difflib
import hashlib
import os
import subprocess
import tempfile
from pathlib import Path


def safe_path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or relative.startswith(('/', '\\')) or '\\' in relative or '\x00' in relative:
        raise ValueError('invalid workspace path')
    parts = Path(relative).parts
    if any(part in {'.', '..', '.aek', '.git'} for part in parts):
        raise ValueError('path is not accessible')
    base = root.resolve()
    path = base.joinpath(*parts)
    current = base
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise ValueError('symlinks are not accessible')
    if path.resolve() != base and base not in path.resolve().parents:
        raise ValueError('path escapes workspace')
    return path


def list_files(root: Path, relative: str = '') -> list[dict]:
    path = safe_path(root, relative)
    if not path.is_dir():
        raise ValueError('not a directory')
    entries = []
    for entry in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.casefold())):
        if entry.name in {'.git', '.aek'} or entry.is_symlink():
            continue
        entries.append({'name': entry.name, 'path': entry.relative_to(root).as_posix(),
                        'directory': entry.is_dir(), 'size': entry.stat().st_size if entry.is_file() else None})
        if len(entries) >= 200:
            break
    return entries


def inspect_file(root: Path, relative: str) -> dict:
    path = safe_path(root, relative)
    if not path.is_file():
        raise ValueError('not a file')
    size = path.stat().st_size
    if size > 2_000_000:
        return {'path': relative, 'size': size, 'large': True}
    data = path.read_bytes()
    result = {'path': relative, 'size': size, 'sha256': hashlib.sha256(data).hexdigest()}
    if b'\x00' in data[:4096]:
        result['binary'] = True
    else:
        try:
            result['text'] = data.decode('utf-8')[:64000]
            result['truncated'] = len(data) > 64000
        except UnicodeDecodeError:
            result['binary'] = True
    return result


def save_text(root: Path, relative: str, content: str, expected_hash: str, preview=False) -> dict:
    path = safe_path(root, relative)
    if not path.is_file() or not isinstance(content, str) or len(content.encode('utf-8')) > 1_000_000:
        raise ValueError('file must exist and text must be at most 1 MB')
    original = inspect_file(root, relative)
    if 'text' not in original or original.get('truncated'):
        raise ValueError('only complete UTF-8 text files may be edited')
    if expected_hash != original['sha256']:
        raise ValueError('file changed since preview; reload it')
    diff = ''.join(difflib.unified_diff(original['text'].splitlines(True), content.splitlines(True),
                                        fromfile=relative, tofile=relative, n=3))
    if not preview:
        fd, temp = tempfile.mkstemp(prefix='.aek-edit-', dir=path.parent)
        try:
            os.fchmod(fd, path.stat().st_mode & 0o777)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(content.encode('utf-8'))
                stream.flush()
                os.fsync(stream.fileno())
            if inspect_file(root, relative)['sha256'] != expected_hash:
                raise ValueError('file changed during save; reload it')
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
    return {'diff': diff[:64000], 'sha256': hashlib.sha256(content.encode()).hexdigest()}


def git(root: Path, action: str, value: str = '') -> dict:
    if not (root / '.git').is_dir():
        raise ValueError('workspace is not a Git repository')
    reads = {'status': ['status', '--short', '--branch'], 'diff': ['diff', '--', '.'],
             'staged': ['diff', '--cached', '--', '.'], 'log': ['log', '-10', '--oneline']}
    if action in reads:
        args = reads[action]
    elif action in {'stage', 'unstage'}:
        path = safe_path(root, value)
        if not path.exists():
            raise ValueError('file does not exist')
        if action == 'stage':
            args = ['add', '--', value]
        elif subprocess.run(['git', 'rev-parse', '--verify', 'HEAD'], cwd=root,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
            args = ['rm', '--cached', '--', value]
        else:
            args = ['restore', '--staged', '--', value]
    elif action == 'commit':
        if not isinstance(value, str) or not value.strip() or len(value) > 200:
            raise ValueError('commit message must contain 1–200 characters')
        args = ['commit', '-m', value]
    elif action in {'branch', 'switch'}:
        if not isinstance(value, str) or not value or value.startswith('-') or subprocess.run(
                ['git', 'check-ref-format', '--branch', value], cwd=root,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
            raise ValueError('invalid branch name')
        args = ['switch', '-c', value] if action == 'branch' else ['switch', value]
    else:
        raise ValueError('unsupported Git action')
    result = subprocess.run(['git', *args], cwd=root, capture_output=True, text=True, timeout=30,
                            env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'})
    return {'output': (result.stdout + result.stderr)[:64000], 'ok': result.returncode == 0}


def artifacts(root: Path) -> list[dict]:
    extensions = {'.apk', '.aab', '.so', '.c', '.log', '.pdf', '.zip', '.jar'}
    found = []
    visited = 0
    for base, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if d not in {'.git', '.aek'} and not (Path(base) / d).is_symlink()]
        for name in files:
            visited += 1
            if visited > 5000:
                return sorted(found, key=lambda r: r['updated'], reverse=True)[:100]
            path = Path(base) / name
            if path.is_symlink() or path.suffix.lower() not in extensions:
                continue
            stat = path.stat()
            found.append({'name': name, 'path': path.relative_to(root).as_posix(),
                          'size': stat.st_size, 'updated': stat.st_mtime})
    return sorted(found, key=lambda r: r['updated'], reverse=True)[:100]


def file_action(root: Path, action: str, relative: str, name: str = '') -> None:
    path = safe_path(root, relative)
    if action in {'new-file', 'new-folder', 'rename'}:
        if not isinstance(name, str) or not name or name in {'.', '..'} or '/' in name or '\\' in name or '\x00' in name:
            raise ValueError('invalid file or folder name')
        target = safe_path(root, str(Path(relative) / name) if action != 'rename' else str(Path(relative).parent / name))
        if target.exists() or target.is_symlink():
            raise ValueError('target already exists')
        if action == 'new-file':
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(fd)
        elif action == 'new-folder':
            target.mkdir()
        elif action == 'rename':
            if not path.exists():
                raise ValueError('source does not exist')
            path.rename(target)
    elif action == 'delete':
        if not path.exists() or path == root.resolve():
            raise ValueError('source does not exist')
        if path.is_dir():
            try:
                path.rmdir()  # Only empty folders can be deleted from the UI.
            except OSError as exc:
                raise ValueError('folder must be empty before deletion') from exc
        else:
            path.unlink()
    else:
        raise ValueError('unsupported file action')
