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
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import version
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .agent import AEKAgent
from .config import Settings, list_workspaces, set_active_workspace
from .sessions import SessionStore, sanitize

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
            if self.busy:
                raise ValueError('a task is already running')
            if session_id != self.session:
                raise ValueError('active session changed; reload before sending')
            with self.store.execution_lock():
                self.store.append(self.session, {"role": "user", "content": prompt})
            self.busy = True
            self.request_id = uuid.uuid4().hex
            threading.Thread(target=self.worker, args=(prompt,), daemon=True).start()
            return {'request_id': self.request_id}

    def worker(self, prompt):
        async def run():
            agent = self.agent_factory(self.settings)
            try:
                with self.store.execution_lock():
                    history = await asyncio.to_thread(self.store.history, self.session, self.settings.context_bytes)
                    await agent.run(prompt, history, store=self.store, session_id=self.session, emit=self.emit, recorded=True)
            finally:
                await agent.close()
        try:
            asyncio.run(run())
        except Exception as exc:
            self.emit({'type': 'error', 'message': f'{type(exc).__name__}: {exc}'})
        finally:
            with self.lock:
                self.busy = False
                self.emit({'type': 'request_finished'})

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
                if path == '/api/workspaces':
                    return self.respond({'workspaces': list_workspaces(), 'active': app.settings.active_workspace})
                if path == '/api/sessions':
                    return self.respond({'sessions': app.store.list(), 'active': app.session})
                if path.startswith('/api/sessions/') and path.endswith('/messages'):
                    sid = path.split('/')[3]
                    return self.respond({'messages': app.store.messages(sid, int(query.get('before', ['0'])[0]), int(query.get('limit', ['50'])[0]))})
            assets = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css', '/manifest.webmanifest': 'manifest.webmanifest', '/icon.svg': 'icon.svg', '/icon-192.png': 'icon-192.png', '/icon-512.png': 'icon-512.png'}
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
            with app.store.execution_lock():
                if path == '/api/sessions':
                    app.session = app.store.create(data.get('title', 'New session'))['id']
                elif path == '/api/sessions/use':
                    app.session = app.store.use(data.get('session_id'))['id']
                elif path == '/api/sessions/rename':
                    app.store.rename(app.session, data.get('title'))
                elif path == '/api/sessions/summary':
                    app.store.set_summary(app.session, data.get('summary'))
                elif path == '/api/sessions/delete':
                    if data.get('confirm') is not True:
                        raise ValueError('explicit confirmation required')
                    app.store.delete(data.get('session_id'))
                    app.session = app.store.current()['id']
                elif path == '/api/workspaces':
                    name = data.get('name')
                    if not isinstance(name, str) or len(name) > 100:
                        raise ValueError('invalid workspace name')
                    set_active_workspace(name)
                    app.reload()
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
