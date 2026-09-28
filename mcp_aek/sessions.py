"""Workspace-local transactional history. SQLite commits every message before execution continues."""
from __future__ import annotations
import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def sanitize(value, secrets=()):
    if isinstance(value, dict):
        return {k: '[REDACTED]' if re.search(r'(?i)(token|cookie|authorization|api.?key|password|secret)', k) else sanitize(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize(v, secrets) for v in value]
    if isinstance(value, str):
        if value.lstrip().startswith(('{', '[')):
            try:
                parsed = json.loads(value)
            except (ValueError, TypeError):
                pass
            else:
                return json.dumps(sanitize(parsed, secrets), ensure_ascii=False)
        for secret in secrets:
            if secret:
                value = value.replace(secret, '[REDACTED]')
        value = re.sub(r'(?im)^(\s*(?:cookie|set-cookie|authorization)\s*:)\s*.*$', r'\1 [REDACTED]', value)
        value = re.sub(r'(https?://)[^/\s@]+:[^/\s@]+@', r'\1[REDACTED]@', value)
        value = re.sub(r'(?i)(Bearer\s+)[^\s"\'<>]+', r'\1[REDACTED]', value)
        value = re.sub(r'(?i)((?:[\w-]*(?:token|cookie|password|secret)|api[_-]?key|authorization)["\']?\s*[:=]\s*["\']?)[^\s,;"\'}]+', r'\1[REDACTED]', value)
        return value
    return value



def _ensure_git_local_exclude(workspace: Path) -> None:
    """Ignore .aek through Git metadata without touching the tracked .gitignore."""
    marker = workspace / '.git'
    if marker.is_symlink() or not marker.exists():
        return
    git_dir: Path | None = None
    if marker.is_dir():
        git_dir = marker.resolve()
    elif marker.is_file():
        try:
            raw = marker.read_text(encoding='utf-8', errors='strict')
        except OSError:
            return
        if len(raw) > 4096 or not raw.startswith('gitdir:'):
            return
        value = raw.split(':', 1)[1].strip()
        candidate = Path(value)
        git_dir = (workspace / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
        if not git_dir.is_dir():
            return
    if git_dir is None:
        return
    common = git_dir
    commondir = git_dir / 'commondir'
    if commondir.is_file() and not commondir.is_symlink():
        try:
            value = commondir.read_text(encoding='utf-8', errors='strict').strip()
            candidate = Path(value)
            common = (git_dir / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
        except OSError:
            common = git_dir
    exclude = common / 'info' / 'exclude'
    if exclude.is_symlink():
        raise ValueError('Git local exclude cannot be a symlink')
    exclude.parent.mkdir(parents=True, exist_ok=True)
    pattern = '/.aek/'
    try:
        existing = exclude.read_text(encoding='utf-8', errors='replace') if exclude.exists() else ''
    except OSError:
        existing = ''
    if pattern in {line.strip() for line in existing.splitlines()}:
        return
    prefix = '' if not existing or existing.endswith('\n') else '\n'
    fd = os.open(exclude, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, (prefix + pattern + '\n').encode('utf-8'))
    finally:
        os.close(fd)


class SessionStore:
    def __init__(self, workspace: Path, secrets=()):
        self.workspace = workspace.resolve()
        _ensure_git_local_exclude(self.workspace)
        self.root = self.workspace / '.aek'
        if self.root.is_symlink():
            raise ValueError('session directory cannot be a symlink')
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = self.root / 'sessions.sqlite3'
        for p in self.root.iterdir():
            if p.is_symlink():
                raise ValueError('session storage cannot contain symlinks')
        os.chmod(self.root, 0o700)
        self.secrets = secrets
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS messages(seq INTEGER PRIMARY KEY AUTOINCREMENT, session TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE, data TEXT NOT NULL, task_id TEXT);
                CREATE INDEX IF NOT EXISTS messages_session ON messages(session,seq);
                CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, session TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE, workspace TEXT NOT NULL, prompt TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, error TEXT, message_seq INTEGER);
                CREATE INDEX IF NOT EXISTS tasks_status ON tasks(status,created_at);
            ''')
            message_columns = {row[1] for row in db.execute('PRAGMA table_info(messages)')}
            if 'task_id' not in message_columns:
                db.execute('ALTER TABLE messages ADD COLUMN task_id TEXT')
            task_columns = {row[1] for row in db.execute('PRAGMA table_info(tasks)')}
            if 'message_seq' not in task_columns:
                db.execute('ALTER TABLE tasks ADD COLUMN message_seq INTEGER')
            db.execute('CREATE INDEX IF NOT EXISTS messages_task ON messages(task_id,seq)')
        os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self):
        if self.path.is_symlink():
            raise ValueError('session database cannot be a symlink')
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA synchronous=FULL')
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def execution_lock(self):
        import fcntl
        fd = os.open(self.root / 'execution.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError('workspace is busy in another process') from exc
            yield
        finally:
            os.close(fd)

    def create(self, title='New session'):
        title = self.title(title)
        sid, now = uuid.uuid4().hex, datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute('INSERT INTO sessions(id,title,created_at,updated_at) VALUES(?,?,?,?)', (sid, title, now, now))
            db.execute("INSERT OR REPLACE INTO state VALUES('current',?)", (sid,))
        return self.info(sid)

    def title(self, title):
        if not isinstance(title, str) or not title.strip() or len(title) > 120:
            raise ValueError('title must contain 1–120 characters')
        return sanitize(title.strip(), self.secrets)

    def list(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT s.*, (SELECT count(*) FROM messages WHERE session=s.id) message_count FROM sessions s ORDER BY updated_at DESC')]

    def resolve(self, ref):
        if not isinstance(ref, str) or not ref or '/' in ref or '\\' in ref or ref in {'.', '..'}:
            raise ValueError('invalid session reference')
        rows = [s for s in self.list() if s['id'] == ref or s['title'] == ref]
        if len(rows) != 1:
            raise ValueError('session not found or name is ambiguous; use the full ID')
        return rows[0]['id']

    def info(self, ref):
        sid = self.resolve(ref)
        return next(s for s in self.list() if s['id'] == sid)

    def current(self):
        with self.connect() as db:
            row = db.execute("SELECT value FROM state WHERE key='current'").fetchone()
        return self.info(row[0]) if row else self.create()

    def current_id(self):
        with self.connect() as db:
            row = db.execute("SELECT value FROM state WHERE key='current'").fetchone()
        return row[0] if row else None

    def use(self, ref):
        sid = self.resolve(ref)
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO state VALUES('current',?)", (sid,))
        return self.info(sid)

    def rename(self, ref, title):
        sid = self.resolve(ref)
        with self.connect() as db:
            db.execute('UPDATE sessions SET title=? WHERE id=?', (self.title(title), sid))
        return self.info(sid)

    def set_summary(self, ref, text):
        if not isinstance(text, str) or len(text.encode()) > 8000:
            raise ValueError("summary must be at most 8000 UTF-8 bytes")
        sid = self.resolve(ref)
        with self.connect() as db:
            db.execute("UPDATE sessions SET summary=? WHERE id=?", (sanitize(text, self.secrets), sid))

    def delete(self, ref):
        sid = self.resolve(ref)
        with self.connect() as db:
            db.execute('DELETE FROM sessions WHERE id=?', (sid,))
            active = db.execute("SELECT value FROM state WHERE key='current'").fetchone()
            if active and active[0] == sid:
                next_row = db.execute('SELECT id FROM sessions ORDER BY updated_at DESC LIMIT 1').fetchone()
                next_id = next_row[0] if next_row else uuid.uuid4().hex
                if not next_row:
                    now = datetime.now(timezone.utc).isoformat()
                    db.execute('INSERT INTO sessions(id,title,created_at,updated_at) VALUES(?,?,?,?)', (next_id, 'New session', now, now))
                db.execute("INSERT OR REPLACE INTO state VALUES('current',?)", (next_id,))
        return self.current()

    def clear_all(self):
        """Replace this workspace's history in one SQLite transaction."""
        sid, now = uuid.uuid4().hex, datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            counts = db.execute('SELECT (SELECT count(*) FROM sessions), (SELECT count(*) FROM messages)').fetchone()
            db.execute('DELETE FROM messages')
            db.execute('DELETE FROM sessions')
            db.execute('INSERT INTO sessions(id,title,created_at,updated_at) VALUES(?,?,?,?)', (sid, 'New session', now, now))
            db.execute("INSERT OR REPLACE INTO state VALUES('current',?)", (sid,))
        return {'deleted_sessions': counts[0], 'deleted_messages': counts[1], 'session': self.info(sid)}

    def enqueue(self, sid, prompt):
        """Persist the user message and queued task in the same committed transaction."""
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError('prompt must not be empty')
        sid = self.resolve(sid)
        task_id, now = uuid.uuid4().hex, datetime.now(timezone.utc).isoformat()
        clean_prompt = sanitize(prompt, self.secrets)
        data = json.dumps({'role': 'user', 'content': clean_prompt}, ensure_ascii=False)
        with self.connect() as db:
            cursor = db.execute('INSERT INTO messages(session,data,task_id) VALUES(?,?,?)',
                                (sid, data, task_id))
            message_seq = cursor.lastrowid
            db.execute('UPDATE sessions SET updated_at=? WHERE id=?', (now, sid))
            db.execute('INSERT INTO tasks(id,session,workspace,prompt,status,created_at,message_seq) VALUES(?,?,?,?,?,?,?)',
                       (task_id, sid, str(self.workspace), clean_prompt, 'queued', now, message_seq))
        return task_id

    def next_task(self):
        with self.connect() as db:
            row = db.execute("SELECT * FROM tasks WHERE status='queued' ORDER BY created_at,rowid LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE tasks SET status='running',started_at=? WHERE id=?",
                           (datetime.now(timezone.utc).isoformat(), row['id']))
        return dict(row) if row else None

    def finish_task(self, task_id, error=None):
        with self.connect() as db:
            db.execute('UPDATE tasks SET status=?,finished_at=?,error=? WHERE id=?',
                       ('failed' if error else 'completed', datetime.now(timezone.utc).isoformat(),
                        sanitize(error, self.secrets) if error else None, task_id))

    def has_queued_tasks(self):
        with self.connect() as db:
            return db.execute("SELECT 1 FROM tasks WHERE status='queued' LIMIT 1").fetchone() is not None

    def tasks(self):
        now = datetime.now(timezone.utc)
        with self.connect() as db:
            rows = [dict(row) for row in db.execute('''
                SELECT t.id,t.session,t.workspace,t.status,t.created_at,t.started_at,t.finished_at,t.error,
                       t.message_seq,substr(t.prompt,1,160) prompt,s.title session_title
                FROM tasks t LEFT JOIN sessions s ON s.id=t.session
                ORDER BY t.created_at DESC,t.rowid DESC LIMIT 50
            ''')]
        for row in rows:
            row['duration_ms'] = None
            if row['started_at']:
                try:
                    started = datetime.fromisoformat(row['started_at'])
                    finished = datetime.fromisoformat(row['finished_at']) if row['finished_at'] else now
                    row['duration_ms'] = max(0, round((finished - started).total_seconds() * 1000))
                except ValueError:
                    pass
        return rows

    def recover_tasks(self):
        with self.connect() as db:
            db.execute("UPDATE tasks SET status='failed',error='Backend stopped before completion',finished_at=? WHERE status IN ('running','queued')",
                       (datetime.now(timezone.utc).isoformat(),))

    def append(self, sid, message, task_id=None):
        sid = self.resolve(sid)
        data = json.dumps(sanitize(message, self.secrets), ensure_ascii=False)
        with self.connect() as db:
            if task_id is not None and not db.execute(
                    'SELECT 1 FROM tasks WHERE id=? AND session=?', (task_id, sid)).fetchone():
                raise ValueError('task is not associated with this session')
            db.execute('INSERT INTO messages(session,data,task_id) VALUES(?,?,?)', (sid, data, task_id))
            db.execute('UPDATE sessions SET updated_at=? WHERE id=?', (datetime.now(timezone.utc).isoformat(), sid))

    def messages(self, sid, before=0, limit=100):
        sid = self.resolve(sid)
        with self.connect() as db:
            rows = db.execute('SELECT seq,data FROM messages WHERE session=? AND (?=0 OR seq<?) ORDER BY seq DESC LIMIT ?', (sid, before, before, min(max(limit, 1), 500))).fetchall()
        return [{'seq': r[0], 'message': json.loads(r[1])} for r in reversed(rows)]

    def history_for_task(self, sid, task_id, budget):
        '''Build logical task turns while excluding prompts that are still in the future queue.'''
        sid = self.resolve(sid)
        with self.connect() as db:
            current = db.execute('SELECT rowid FROM tasks WHERE id=? AND session=?', (task_id, sid)).fetchone()
            if not current:
                raise ValueError('task not found for session')
            anchors = db.execute('''
                SELECT 'task' kind,t.id ref,t.message_seq anchor
                FROM tasks t WHERE t.session=? AND t.rowid<=? AND t.message_seq IS NOT NULL
                UNION ALL
                SELECT 'message' kind,CAST(m.seq AS TEXT) ref,m.seq anchor
                FROM messages m WHERE m.session=? AND m.task_id IS NULL
                ORDER BY anchor DESC LIMIT 2000
            ''', (sid, current[0], sid)).fetchall()
            units = []
            size = 0
            for anchor in anchors:
                if anchor['kind'] == 'task':
                    raw = [r[0] for r in db.execute(
                        'SELECT data FROM messages WHERE session=? AND task_id=? ORDER BY seq',
                        (sid, anchor['ref']))]
                else:
                    row = db.execute('SELECT data FROM messages WHERE session=? AND seq=?',
                                     (sid, int(anchor['ref']))).fetchone()
                    raw = [row[0]] if row else []
                if not raw:
                    continue
                cost = sum(len(value.encode('utf-8')) for value in raw)
                if units and size + cost > budget * 2:
                    break
                units.append((anchor['anchor'], [json.loads(value) for value in raw]))
                size += cost
            units.sort(key=lambda item: item[0])
            return [message for _, group in units for message in group]

    def history(self, sid, budget):
        # Read only a bounded suffix, without loading a lifetime of history into RAM.
        result, size = [], 0
        with self.connect() as db:
            for row in db.execute('SELECT data FROM messages WHERE session=? ORDER BY seq DESC', (self.resolve(sid),)):
                size += len(row[0].encode('utf-8'))
                if size > budget * 2:
                    break
                result.append(json.loads(row[0]))
        result.reverse()
        return result


def context_messages(history, system, budget=60000, summary=''):
    """Trim whole turns; never send orphan tool results or fabricate interrupted execution."""
    groups = []
    for msg in history:
        if msg.get('role') == 'user':
            groups.append([])
        if groups and msg.get('role') != 'system':
            groups[-1].append(msg)
    clean = []
    for group in groups:
        pending = set()
        valid = []
        for msg in group:
            if msg.get('role') == 'assistant' and msg.get('tool_calls'):
                pending = {c['id'] for c in msg['tool_calls']}
                valid.append(msg)
            elif msg.get('role') == 'tool':
                if msg.get('tool_call_id') in pending:
                    pending.remove(msg['tool_call_id'])
                    valid.append(msg)
            else:
                valid.append(msg)
        if pending:
            # Preserve the user prompt but omit the incomplete call/result group.
            start = next(i for i, m in enumerate(valid) if m.get('tool_calls') and any(c['id'] in pending for c in m['tool_calls']))
            valid = valid[:start]
        clean.append(valid)
    prefix = [{'role': 'system', 'content': system}]
    if summary:
        prefix.append({'role': 'system', 'content': 'User-maintained session notes (not verified tool evidence):\n' + summary})
    size = len(json.dumps(prefix, ensure_ascii=False).encode())
    selected = []
    for group in reversed(clean):
        cost = len(json.dumps(group, ensure_ascii=False).encode())
        if size + cost > budget:
            if not selected:
                raise ValueError('latest turn exceeds AEK_CONTEXT_BYTES; start a new session or increase the budget; raw history is retained')
            break
        selected.insert(0, group)
        size += cost
    return prefix + [m for g in selected for m in g]
