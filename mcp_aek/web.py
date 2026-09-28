"""Loopback-only HTTP UI. No extra dependencies; one serialized agent worker."""
from __future__ import annotations
import asyncio
import json
import mimetypes
import os
import platform
import secrets
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import version
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .agent import AEKAgent
from .config import Settings, set_active_workspace, clone_workspace, workspace_details, set_model, remove_workspace, rename_workspace
from .sessions import SessionStore, sanitize
from .workspace import list_files, inspect_file, save_text, git, artifacts, safe_path, file_action

STATIC = Path(__file__).with_name('static')


class App:
    def __init__(self, agent_factory=AEKAgent):
        self.agent_factory = agent_factory
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.condition = threading.Condition()
        self.events = deque(maxlen=500)
        self.sequence = 0
        self.busy = False
        self.request_id = None
        self.reload()
        try:
            with self.store.execution_lock():
                self.store.recover_tasks()
        except ValueError:
            pass  # Another process still owns the workspace.

    def reload(self):
        self.settings = Settings.load()
        self.store = SessionStore(self.settings.workspace, (self.settings.upstream_api_key,))
        self.session = self.store.current()['id']

    def emit(self, event):
        event = sanitize(event, (self.settings.upstream_api_key,))
        # Keep event replay memory bounded even for very large upstream answers/arguments.
        for key in ('content', 'result', 'arguments'):
            if key in event:
                value = event[key]
                if not isinstance(value, str):
                    value = json.dumps(value, ensure_ascii=False)
                event[key] = value[:12000]
        with self.condition:
            self.sequence += 1
            self.events.append({'id': self.sequence, 'request_id': self.request_id, 'session_id': self.session, 'time': time.time(), **event})
            self.condition.notify_all()

    def status(self):
        with self.lock:
            return {'workspace': self.settings.active_workspace, 'path': str(self.settings.workspace), 'model': self.settings.model,
                    'session': self.store.info(self.session), 'busy': self.busy, 'request_id': self.request_id,
                    'backend': 'online', 'context_bytes': self.settings.context_bytes, 'shell_enabled': self.settings.enable_shell}

    def start(self, prompt, session_id):
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode()) > 48000:
            raise ValueError('prompt must contain 1–48000 UTF-8 bytes')
        with self.lock:
            if session_id != self.session:
                raise ValueError('active session changed; reload before sending')
            if self.busy:
                task_id = self.store.enqueue(self.session, prompt)
                self.emit({'type': 'task_queued', 'task_id': task_id})
                return {'request_id': task_id, 'queued': True}
            reservation = self.store.execution_lock()
            reservation.__enter__()
            try:
                task_id = self.store.enqueue(self.session, prompt)
                self.busy = True
                self.request_id = task_id
                threading.Thread(target=self.worker, args=(reservation,), daemon=True).start()
            except BaseException:
                self.busy = False
                if 'task_id' in locals():
                    self.store.finish_task(task_id, 'Worker could not start')
                reservation.__exit__(None, None, None)
                raise
            return {'request_id': self.request_id}

    def worker(self, reservation):
        released = False
        try:
            while True:
                with self.lock:
                    task = self.store.next_task()
                    if not task:
                        reservation.__exit__(None, None, None)
                        self.busy = False
                        released = True
                        return
                    self.request_id = task['id']

                async def run():
                    agent = self.agent_factory(self.settings)
                    try:
                        history = await asyncio.to_thread(
                            self.store.history_for_task, task['session'], task['id'], self.settings.context_bytes)
                        await agent.run(task['prompt'], history, store=self.store, session_id=task['session'],
                                        emit=self.emit, recorded=True, task_id=task['id'])
                    finally:
                        await agent.close()

                error = None
                try:
                    asyncio.run(run())
                except Exception as exc:
                    error = f'{type(exc).__name__}: {exc}'
                    self.emit({'type': 'error', 'message': error})
                self.store.finish_task(task['id'], error)
                with self.lock:
                    queue_pending = self.store.has_queued_tasks()
                    if not queue_pending:
                        reservation.__exit__(None, None, None)
                        self.busy = False
                        released = True
                    self.emit({'type': 'request_finished', 'queue_pending': queue_pending})
                if not queue_pending:
                    return
        finally:
            if not released:
                with self.lock:
                    reservation.__exit__(None, None, None)
                    self.busy = False

    async def diagnostics(self, tools_only=False):
        from mcp import Client
        from .tools import tool_status
        agent = self.agent_factory(self.settings)
        try:
            if tools_only:
                async with Client(agent._stdio_server()) as client:
                    listed = await client.list_tools()
                    return {'tools': [{'name': t.name, 'description': t.description, 'available': t.name != 'shell_exec' or self.settings.enable_shell} for t in listed.tools], 'binaries': tool_status(), 'shell_enabled': self.settings.enable_shell}
            info = await agent.doctor()
            info.update({'python': platform.python_version(), 'termux': '/com.termux/' in os.environ.get('PREFIX', '') or 'com.termux' in os.environ.get('PREFIX', ''), 'dependencies': {n: version(n) for n in ('mcp', 'httpx', 'python-dotenv')}, 'history_path': str(self.store.path), 'active_session': self.session, 'ui_backend': 'online'})
            info['tool_count'] = len(info.get('mcp_tools', []))
            return sanitize(info, (self.settings.upstream_api_key,))
        finally:
            await agent.close()

    async def models(self):
        agent = self.agent_factory(self.settings)
        try:
            return await agent._upstream_models()
        finally:
            await agent.close()


class Server(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address, app):
        if address[0] != '127.0.0.1':
            raise ValueError('UI must bind to 127.0.0.1')
        self.app = app
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass  # Never log request bodies or auth tokens.

    def setup(self):
        super().setup()
        self.connection.settimeout(20)

    def respond(self, data, status=200, content_type='application/json; charset=utf-8'):
        body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.send_header('Referrer-Policy', 'no-referrer')
        self.end_headers()
        self.wfile.write(body)

    def guard(self):
        port = self.server.server_address[1]
        allowed = {f'127.0.0.1:{port}', f'localhost:{port}'}
        host = self.headers.get('Host', '')
        if host not in allowed:
            raise PermissionError('invalid host')
        origin = self.headers.get('Origin')
        if origin and origin != 'http://' + host:
            raise PermissionError('cross-origin request rejected')
        if self.headers.get('Sec-Fetch-Site') not in (None, 'same-origin', 'none'):
            raise PermissionError('cross-site request rejected')
        if self.command == 'POST' and not secrets.compare_digest(self.headers.get('X-AEK-Token', ''), self.server.app.token):
            raise PermissionError('invalid local request token; reload the page')

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch()

    def dispatch(self):
        try:
            self.guard()
            self.route()
        except PermissionError as exc:
            self.respond({'error': {'message': str(exc)}}, 403)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            self.respond({'error': {'message': str(exc)}}, 400)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except Exception:
            self.respond({'error': {'message': 'Backend failure; check session storage and run aek doctor'}}, 500)

    def route(self):
        app = self.server.app
        url = urlsplit(self.path)
        path, query = url.path, parse_qs(url.query)
        if self.command == 'GET':
            if path == '/api/status':
                return self.respond({**app.status(), 'csrf_token': app.token})
            if path == '/api/events':
                after = int(self.headers.get('Last-Event-ID') or query.get('after', ['0'])[0])
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                # Reopen periodically; Last-Event-ID gives bounded replay on reconnect.
                until = time.monotonic() + 15
                while time.monotonic() < until:
                    with app.condition:
                        rows = [e for e in app.events if e['id'] > after]
                        if not rows:
                            app.condition.wait(timeout=2)
                    if rows:
                        for row in rows:
                            self.wfile.write(f"id: {row['id']}\ndata: {json.dumps(row, ensure_ascii=False)}\n\n".encode())
                            after = row['id']
                    else:
                        self.wfile.write(b': heartbeat\n\n')
                    self.wfile.flush()
                return
            if path in ('/api/diagnostics', '/api/tools'):
                with app.lock:
                    if app.busy:
                        raise ValueError('diagnostics are unavailable while a task is running')
                    return self.respond(asyncio.run(app.diagnostics(path == '/api/tools')))
            with app.lock:
                if path == '/api/models':
                    if app.busy:
                        raise ValueError('models unavailable while a task is running')
                    return self.respond({'models': asyncio.run(app.models()), 'selected': app.settings.model})
                if path == '/api/tasks':
                    return self.respond({'tasks': app.store.tasks()})
                if path == '/api/workspaces':
                    return self.respond({'workspaces': workspace_details(), 'active': app.settings.active_workspace})
                if path == '/api/files':
                    return self.respond({'entries': list_files(app.settings.workspace, query.get('path', [''])[0])})
                if path == '/api/file':
                    return self.respond(inspect_file(app.settings.workspace, query.get('path', [''])[0]))
                if path == '/api/git':
                    return self.respond(git(app.settings.workspace, query.get('action', ['status'])[0]))
                if path == '/api/artifacts':
                    return self.respond({'artifacts': artifacts(app.settings.workspace)})
                if path == '/api/artifact':
                    relative = query.get('path', [''])[0]
                    target = safe_path(app.settings.workspace, relative)
                    if target.suffix.lower() not in {'.apk', '.aab', '.so', '.c', '.log', '.pdf', '.zip', '.jar'} or not target.is_file() or target.stat().st_size > 20_000_000:
                        raise ValueError('artifact is unavailable or exceeds 20 MB')
                    return self.respond(target.read_bytes(), content_type='application/octet-stream')
                if path == '/api/sessions':
                    return self.respond({'sessions': app.store.list(), 'active': app.session})
                if path.startswith('/api/sessions/') and path.endswith('/messages'):
                    sid = path.split('/')[3]
                    return self.respond({'messages': app.store.messages(sid, int(query.get('before', ['0'])[0]), int(query.get('limit', ['50'])[0]))})
            assets = {'/': 'index.html', '/app.js': 'app.js', '/theme-init.js': 'theme-init.js', '/style.css': 'style.css', '/manifest.webmanifest': 'manifest.webmanifest', '/icon.svg': 'icon.svg', '/icon-192.png': 'icon-192.png', '/icon-512.png': 'icon-512.png'}
            if path in assets:
                target = STATIC / assets[path]
                return self.respond(target.read_bytes(), content_type=mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
            return self.respond({'error': {'message': 'Not found'}}, 404)
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            raise ValueError('Content-Type must be application/json')
        length = int(self.headers.get('Content-Length', '0'))
        if length < 1 or length > 65536:
            raise ValueError('request body must contain 1–65536 bytes')
        data = json.loads(self.rfile.read(length))
        if not isinstance(data, dict):
            raise ValueError('JSON body must be an object')
        with app.lock:
            if path == '/api/chat':
                return self.respond(app.start(data.get('prompt'), data.get('session_id')), 202)
            if app.busy:
                raise ValueError('cannot change workspace/session while a task is running')
            if path == '/api/workspaces':
                name = data.get('name')
                if not isinstance(name, str) or len(name) > 100:
                    raise ValueError('invalid workspace name')
                set_active_workspace(name)
                app.reload()
                return self.respond(app.status())
            if path == '/api/workspaces/clone':
                target = clone_workspace(data.get('url'), data.get('name'))
                return self.respond({'path': str(target)}, 201)
            if path == '/api/workspaces/remove':
                name = data.get('name')
                if data.get('confirm_workspace') != name or data.get('mode') not in {'archive', 'delete'}:
                    raise ValueError('type the workspace name to confirm archive or deletion')
                remove_workspace(name, archive=data['mode'] == 'archive')
                return self.respond({'removed': name, 'mode': data['mode']})
            if path == '/api/workspaces/rename':
                return self.respond({'path': str(rename_workspace(data.get('name'), data.get('new_name')))})
            if path == '/api/models':
                model = data.get('model')
                if model not in asyncio.run(app.models()):
                    raise ValueError('model not offered by the current bridge')
                set_model(model)
                app.reload()
                return self.respond(app.status())
            if path == '/api/file/preview' or path == '/api/file/save':
                with app.store.execution_lock():
                    return self.respond(save_text(app.settings.workspace, data.get('path'), data.get('content'),
                                                  data.get('sha256'), preview=path.endswith('/preview')))
            if path == '/api/file/action':
                if data.get('action') == 'delete' and data.get('confirm') is not True:
                    raise ValueError('explicit deletion confirmation required')
                with app.store.execution_lock():
                    file_action(app.settings.workspace, data.get('action'), data.get('path'), data.get('name', ''))
                return self.respond({'ok': True})
            if path == '/api/git':
                if data.get('action') == 'commit' and data.get('confirm') is not True:
                    raise ValueError('explicit commit confirmation required')
                with app.store.execution_lock():
                    return self.respond(git(app.settings.workspace, data.get('action'), data.get('value', '')))
            with app.store.execution_lock():
                if path == '/api/sessions':
                    app.session = app.store.create(data.get('title', 'New session'))['id']
                elif path == '/api/sessions/use':
                    app.session = app.store.use(data.get('session_id'))['id']
                elif path == '/api/sessions/rename':
                    app.store.rename(data.get('session_id') or app.session, data.get('title'))
                elif path == '/api/sessions/summary':
                    app.store.set_summary(app.session, data.get('summary'))
                elif path == '/api/sessions/delete':
                    if data.get('confirm') is not True:
                        raise ValueError('explicit confirmation required')
                    app.session = app.store.delete(data.get('session_id'))['id']
                elif path == '/api/sessions/clear-all':
                    if data.get('confirm_workspace') != app.settings.active_workspace:
                        raise ValueError('type the current workspace name to confirm')
                    result = app.store.clear_all()
                    app.session = result['session']['id']
                    return self.respond({**app.status(), **result})
                else:
                    return self.respond({'error': {'message': 'Not found'}}, 404)
            return self.respond(app.status())


def serve(port=8766, open_browser=False):
    app = App()
    server = Server(('127.0.0.1', port), app)
    url = f'http://127.0.0.1:{server.server_address[1]}'
    print(f'AEK UI running at {url}\nworkspace: {app.settings.active_workspace}\nmodel: {app.settings.model}', flush=True)
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
