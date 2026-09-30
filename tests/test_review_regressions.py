import asyncio
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.request import urlopen

from mcp_aek import config
from mcp_aek.config import set_active_workspace
from mcp_aek.sessions import SessionStore
from mcp_aek.web import App, Server


class RecordingAgent:
    calls = []

    def __init__(self, settings):
        self.settings = settings

    async def run(self, prompt, history=None, *, store=None, session_id=None, emit=None,
                  recorded=False, task_id=None):
        type(self).calls.append({'prompt': prompt, 'history': list(history or []), 'task_id': task_id})
        if emit:
            emit({'type': 'request_started'})
        answer = 'done'
        if store:
            store.append(session_id, {'role': 'assistant', 'content': answer}, task_id)
        if emit:
            emit({'type': 'final_answer', 'content': answer})
        return answer, list(history or []) + [{'role': 'assistant', 'content': answer}]

    async def close(self):
        pass


class Bridge(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def reply(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.reply({'data': [{'id': 'test-model'}]})

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        messages = data['messages']
        last = messages[-1]
        if last.get('content') == 'verify queue order':
            relevant = [m.get('content') for m in messages
                        if m.get('content') in {'ui A', 'assistant A', 'ui B', 'assistant B'}]
            content = 'ORDER_OK' if relevant == ['ui A', 'assistant A', 'ui B', 'assistant B'] else 'ORDER_BAD:' + repr(relevant)
        else:
            content = 'OK'
        self.reply({'choices': [{'message': {'role': 'assistant', 'content': content}}]})


class ReviewRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {
            'AEK_WORKSPACE_ROOT': str(self.root / 'workspaces'),
            'AEK_STATE_DIR': str(self.root / 'state'),
            'AEK_ACTIVE_WORKSPACE': 'default',
            'AEK_UPSTREAM_BASE_URL': 'http://127.0.0.1:1/v1',
        })
        self.env.start()
        self.p1 = patch.object(config, 'STATE_DIR', self.root / 'state')
        self.p2 = patch.object(config, 'STATE_FILE', self.root / 'state/state.json')
        self.p1.start(); self.p2.start()
        RecordingAgent.calls = []

    def tearDown(self):
        self.p2.stop(); self.p1.stop(); self.env.stop(); self.tmp.cleanup()

    def wait(self, app):
        deadline = time.time() + 5
        while app.busy and time.time() < deadline:
            time.sleep(.02)
        self.assertFalse(app.busy)

    def test_queue_logical_order_for_history_messages_and_api_pagination(self):
        app = App(RecordingAgent)
        sid = app.session
        first = app.store.enqueue(sid, 'user A')
        second = app.store.enqueue(sid, 'user B')
        self.assertEqual(app.store.next_task()['id'], first)
        app.store.append(sid, {'role': 'assistant', 'content': 'assistant A'}, task_id=first)
        self.assertEqual(app.store.next_task()['id'], second)
        app.store.append(sid, {'role': 'assistant', 'content': 'assistant B'}, task_id=second)
        expected = ['user A', 'assistant A', 'user B', 'assistant B']
        self.assertEqual([m.get('content') for m in app.store.history(sid, 60000)], expected)
        self.assertEqual([r['message'].get('content') for r in app.store.messages(sid)], expected)

        server = Server(('127.0.0.1', 0), app)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            with urlopen(f'http://127.0.0.1:{port}/api/sessions/{sid}/messages?limit=2') as response:
                newest = json.load(response)
            self.assertEqual([r['message'].get('content') for r in newest['messages']], ['user B', 'assistant B'])
            self.assertTrue(newest['has_more'])
            cursor = newest['messages'][0]['cursor']
            with urlopen(f'http://127.0.0.1:{port}/api/sessions/{sid}/messages?limit=2&before={cursor}') as response:
                older = json.load(response)
            self.assertEqual([r['message'].get('content') for r in older['messages']], ['user A', 'assistant A'])
            self.assertFalse(older['has_more'])
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_startup_recovery_skipped_by_lock_cannot_replay_stale_task(self):
        workspace = self.root / 'workspaces/default'
        store = SessionStore(workspace)
        sid = store.current()['id']
        stale = store.enqueue(sid, 'stale-before-restart')
        lock = store.execution_lock(); lock.__enter__()
        try:
            app = App(RecordingAgent)
            self.assertEqual(next(t for t in app.store.tasks() if t['id'] == stale)['status'], 'queued')
        finally:
            lock.__exit__(None, None, None)
        fresh = app.start('fresh-after-restart', app.session)['request_id']
        self.wait(app)
        tasks = {t['id']: t for t in app.store.tasks()}
        self.assertEqual(tasks[stale]['status'], 'failed')
        self.assertEqual(tasks[fresh]['status'], 'completed')
        self.assertFalse(any(call['prompt'] == 'stale-before-restart' for call in RecordingAgent.calls))

    def test_switching_workspace_recovers_stale_tasks_before_new_work(self):
        set_active_workspace('second')
        second = SessionStore(self.root / 'workspaces/second')
        sid = second.current()['id']
        running = second.enqueue(sid, 'stale-running')
        self.assertEqual(second.next_task()['id'], running)
        queued = second.enqueue(sid, 'stale-queued')
        set_active_workspace('default')
        app = App(RecordingAgent)
        set_active_workspace('second')
        app.reload()
        tasks = {t['id']: t for t in app.store.tasks()}
        self.assertEqual(tasks[running]['status'], 'failed')
        self.assertEqual(tasks[queued]['status'], 'failed')
        fresh = app.start('fresh-second', app.session)['request_id']
        self.wait(app)
        self.assertEqual(next(t for t in app.store.tasks() if t['id'] == fresh)['status'], 'completed')
        self.assertFalse(any(call['prompt'] in {'stale-running', 'stale-queued'} for call in RecordingAgent.calls))

    def test_live_raw_prompt_reaches_agent_but_storage_and_events_are_redacted(self):
        app = App(RecordingAgent)
        prompt = 'inspect api_key=TEST123'
        app.start(prompt, app.session)
        self.wait(app)
        self.assertEqual(RecordingAgent.calls[-1]['prompt'], prompt)
        self.assertTrue(any(m.get('role') == 'user' and m.get('content') == prompt
                            for m in RecordingAgent.calls[-1]['history']))
        durable = json.dumps({
            'messages': app.store.messages(app.session),
            'tasks': app.store.tasks(),
            'events': list(app.events),
        }, ensure_ascii=False)
        self.assertNotIn('TEST123', durable)
        self.assertIn('[REDACTED]', durable)

    def test_malicious_git_pointer_cannot_write_external_exclude(self):
        workspace = self.root / 'malicious-worktree'; workspace.mkdir()
        external = self.root / 'external-git'; (external / 'info').mkdir(parents=True)
        sentinel = external / 'info/keep'; sentinel.write_text('unchanged')
        (workspace / '.git').write_text('gitdir: ' + str(external))
        SessionStore(workspace).current()
        self.assertEqual(sentinel.read_text(), 'unchanged')
        self.assertFalse((external / 'info/exclude').exists())

    def test_final_message_and_task_completion_are_reconciled(self):
        store = SessionStore(self.root / 'standalone')
        sid = store.current()['id']
        task = store.enqueue(sid, 'finish')
        self.assertEqual(store.next_task()['id'], task)
        store.append(sid, {'role': 'assistant', 'content': 'durable final'}, task_id=task)
        self.assertEqual(next(t for t in store.tasks() if t['id'] == task)['status'], 'completed')
        store.finish_task(task, 'late error')
        self.assertEqual(next(t for t in store.tasks() if t['id'] == task)['status'], 'completed')
        with store.connect() as db:
            db.execute("UPDATE tasks SET status='running',finished_at=NULL WHERE id=?", (task,))
        store.recover_tasks()
        self.assertEqual(next(t for t in store.tasks() if t['id'] == task)['status'], 'completed')


class CLIQueueContinuationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bridge = ThreadingHTTPServer(('127.0.0.1', 0), Bridge)
        self.thread = threading.Thread(target=self.bridge.serve_forever, daemon=True)
        self.thread.start()
        self.env = {
            **os.environ,
            'AEK_STATE_DIR': self.tmp.name + '/state',
            'AEK_WORKSPACE_ROOT': self.tmp.name + '/ws',
            'AEK_MODEL': 'test-model',
            'AEK_UPSTREAM_BASE_URL': f'http://127.0.0.1:{self.bridge.server_address[1]}/v1',
        }

    def tearDown(self):
        self.bridge.shutdown(); self.bridge.server_close(); self.thread.join(); self.tmp.cleanup()

    def test_cli_continuation_after_ui_queue_receives_logical_order(self):
        workspace = Path(self.env['AEK_WORKSPACE_ROOT']) / 'default'
        store = SessionStore(workspace)
        sid = store.current()['id']
        first = store.enqueue(sid, 'ui A'); second = store.enqueue(sid, 'ui B')
        self.assertEqual(store.next_task()['id'], first)
        store.append(sid, {'role': 'assistant', 'content': 'assistant A'}, task_id=first)
        self.assertEqual(store.next_task()['id'], second)
        store.append(sid, {'role': 'assistant', 'content': 'assistant B'}, task_id=second)
        result = subprocess.run(
            [sys.executable, '-m', 'mcp_aek.cli', 'chat'], env=self.env,
            input='verify queue order\n/exit\n', text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('ORDER_OK', result.stdout)
        self.assertNotIn('ORDER_BAD', result.stdout)


if __name__ == '__main__':
    unittest.main()
