import asyncio
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from mcp_aek import config
from mcp_aek.agent import AEKAgent
from mcp_aek.sessions import SessionStore
from mcp_aek.web import App, Server

class FakeUpstreamAgent(AEKAgent):
    requests=[]
    async def _completion(self,messages,tools):
        type(self).requests.append(messages)
        if messages[-1]['role']=='tool':
            return {'role':'assistant','content':'default ภาษาไทย'}
        if messages[-1]['content']=='fail':raise RuntimeError('test upstream failure')
        if messages[-1]['content']=='tool-error':
            return {'role':'assistant','tool_calls':[{'type':'function','function':{'name':'read_text','arguments':'{"path":"../../outside"}'}}]}
        return {'role':'assistant','content':'','tool_calls':[{'id':'real-call','type':'function','function':{'name':'workspace_info','arguments':'{}'}}]}

class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.env=patch.dict(os.environ,{'AEK_WORKSPACE_ROOT':str(self.root/'workspaces'),'AEK_STATE_DIR':str(self.root/'state'),'AEK_ACTIVE_WORKSPACE':'default','AEK_UPSTREAM_BASE_URL':'http://127.0.0.1:1/v1'})
        self.env.start();self.p1=patch.object(config,'STATE_DIR',self.root/'state');self.p2=patch.object(config,'STATE_FILE',self.root/'state/state.json');self.p1.start();self.p2.start()
        self.app=App(FakeUpstreamAgent);self.server=Server(('127.0.0.1',0),self.app);self.port=self.server.server_address[1]
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):
        deadline=time.time()+10
        while self.app.busy and time.time()<deadline:time.sleep(.05)
        self.server.shutdown();self.server.server_close();self.thread.join();self.p2.stop();self.p1.stop();self.env.stop();self.tmp.cleanup()
    def req(self,path,data=None,headers=None,raw=None):
        h={'Content-Type':'application/json','X-AEK-Token':self.app.token};h.update(headers or {})
        body=raw if raw is not None else (json.dumps(data).encode() if data is not None else None)
        return urlopen(Request(f'http://127.0.0.1:{self.port}'+path,data=body,headers=h),timeout=15)
    def get(self,path,data=None,**kw):
        with self.req(path,data,**kw) as r:return json.load(r)
    def wait(self):
        deadline=time.time()+15
        while self.app.busy and time.time()<deadline:time.sleep(.05)
        self.assertFalse(self.app.busy)
    def test_http_mcp_events_and_persistence(self):
        status=self.get('/api/status');sid=status['session']['id']
        self.get('/api/chat',{'prompt':'workspace','session_id':sid});self.wait()
        events=list(self.app.events);types=[e['type'] for e in events]
        self.assertEqual(types,['request_started','model_started','model_completed','tool_started','tool_completed','model_started','model_completed','final_answer','request_finished'])
        result=json.loads(next(e['result'] for e in events if e['type']=='tool_completed'))
        self.assertEqual(result['active_workspace'],'default')
        self.assertIn(str(self.root/'workspaces/default'), result['workspace'])
        rows=self.get(f'/api/sessions/{sid}/messages')['messages'];self.assertEqual(len(rows),4)
        self.assertEqual(rows[-1]['message']['content'],'default ภาษาไทย')
        self.assertEqual(App(FakeUpstreamAgent).session,sid)
        with self.req('/api/events',headers={'Last-Event-ID':str(events[-2]['id'])}) as r:
            self.assertIn(str(events[-1]['id']).encode(),r.readline())
            self.assertIn(b'request_finished',r.readline())
        self.get('/api/chat',{'prompt':'again','session_id':sid});self.wait()
        self.assertTrue(any(m.get('content')=='default ภาษาไทย' for m in FakeUpstreamAgent.requests[-2]))
    def test_sessions_workspace_and_validation(self):
        a=self.get('/api/sessions',{'title':'ทดสอบ'})['session']['id']
        self.get('/api/sessions/rename',{'title':'renamed'})
        self.get('/api/workspaces',{'name':'second'})
        with self.assertRaises(HTTPError):self.get(f'/api/sessions/{a}/messages')
        self.get('/api/workspaces',{'name':'default'})
        self.assertEqual(self.get('/api/status')['session']['id'],a)
        self.app.busy=True
        try:
            for path,data in [('/api/workspaces',{'name':'bad'}),('/api/sessions',{'title':'bad'})]:
                with self.assertRaises(HTTPError):self.get(path,data)
            self.assertTrue(self.get('/api/chat',{'prompt':'x','session_id':a})['queued'])
        finally:self.app.busy=False
        for raw in [b'{bad',b'[]']:
            with self.assertRaises(HTTPError):self.get('/api/chat',raw=raw)
        with self.assertRaises(HTTPError):self.get('/api/sessions/delete',{'session_id':a})
        self.get('/api/sessions/delete',{'session_id':a,'confirm':True})
    def test_clear_all_confirmation_isolation_and_busy_lock(self):
        sid=self.app.session
        self.app.store.append(sid,{'role':'user','content':'secret history'})
        self.get('/api/workspaces',{'name':'second'})
        second=self.app.session
        self.get('/api/workspaces',{'name':'default'})
        with self.assertRaises(HTTPError):self.get('/api/sessions/clear-all',{'confirm_workspace':'second'})
        self.app.busy=True
        try:
            with self.assertRaises(HTTPError):self.get('/api/sessions/clear-all',{'confirm_workspace':'default'})
        finally:self.app.busy=False
        result=self.get('/api/sessions/clear-all',{'confirm_workspace':'default'})
        self.assertEqual(result['deleted_messages'],1)
        self.assertNotEqual(result['session']['id'],sid)
        self.get('/api/workspaces',{'name':'second'})
        self.assertEqual(self.app.session,second)
    def test_workspace_archive_delete_and_clone_url_validation(self):
        from mcp_aek.config import clone_workspace
        self.get('/api/workspaces',{'name':'second'})
        self.get('/api/workspaces',{'name':'default'})
        for url in ('https://user:pass@github.com/owner/repo', 'https://example.com/owner/repo', 'file:///tmp/repo'):
            with self.assertRaises(ValueError):clone_workspace(url,'unsafe')
        with self.assertRaises(HTTPError):self.get('/api/workspaces/remove',{'name':'second','mode':'delete'})
        self.get('/api/workspaces/remove',{'name':'second','mode':'archive','confirm_workspace':'second'})
        self.assertNotIn('second',[w['name'] for w in self.get('/api/workspaces')['workspaces']])
        self.assertTrue((self.root/'workspaces/.archived/second').is_dir())
        self.get('/api/workspaces',{'name':'third'})
        self.get('/api/workspaces',{'name':'default'})
        self.get('/api/workspaces/rename',{'name':'third','new_name':'renamed-third'})
        self.assertFalse((self.root/'workspaces/third').exists())
        self.assertTrue((self.root/'workspaces/renamed-third').exists())
        self.get('/api/workspaces',{'name':'renamed-third'})
        self.get('/api/workspaces',{'name':'default'})
        self.get('/api/workspaces/remove',{'name':'renamed-third','mode':'delete','confirm_workspace':'renamed-third'})
        self.assertFalse((self.root/'workspaces/renamed-third').exists())
    def test_model_selection_persists_and_respects_env_override(self):
        from mcp_aek.config import set_model
        with patch.dict(os.environ,{'AEK_MODEL':''}):
            set_model('test-model')
            self.assertEqual(config.Settings.load().model,'test-model')
        with patch.dict(os.environ,{'AEK_MODEL':'pinned-model'}):
            with self.assertRaises(ValueError):set_model('other')
            self.assertEqual(config.Settings.load().model,'pinned-model')
    def test_file_git_api(self):
        file=self.app.settings.workspace/'example.txt'
        file.write_text('original\n')
        page=self.get('/api/files')
        self.assertTrue(any(e['name']=='example.txt' for e in page['entries']))
        info=self.get('/api/file?path=example.txt')
        self.assertEqual(info['text'],'original\n')
        with self.assertRaises(HTTPError):self.get('/api/file?path=../state/state.json')
        preview=self.get('/api/file/preview',{'path':'example.txt','content':'replaced\n','sha256':info['sha256']})
        self.assertIn('+replaced',preview['diff'])
        self.assertEqual(file.read_text(),'original\n')
        self.get('/api/file/save',{'path':'example.txt','content':'replaced\n','sha256':info['sha256']})
        with self.assertRaises(HTTPError):self.get('/api/file/save',{'path':'example.txt','content':'stale','sha256':info['sha256']})
        with self.assertRaises(HTTPError):self.get('/api/git')
    def test_origin_host_csrf_and_traversal(self):
        for headers in [{'Origin':'https://evil.example'},{'Host':'evil.example'},{'Sec-Fetch-Site':'cross-site'}]:
            with self.assertRaises(HTTPError) as cm:self.get('/api/status',headers=headers)
            self.assertEqual(cm.exception.code,403)
        with self.assertRaises(HTTPError):self.get('/api/sessions',{'title':'bad'},headers={'X-AEK-Token':'bad'})
        for path in ['/../.env','/api/sessions/../messages']:
            with self.assertRaises(HTTPError):self.get(path)
        with self.req('/') as r:
            self.assertIn("script-src 'self'",r.headers['Content-Security-Policy'])
        with self.req('/app.js') as r:self.assertNotIn(b'innerHTML',r.read())
    def test_upstream_failure_preserves_user_turn(self):
        self.get('/api/chat',{'prompt':'fail','session_id':self.app.session});self.wait()
        self.assertIn('error',[e['type'] for e in self.app.events])
        self.assertEqual(self.app.store.messages(self.app.session)[0]['message']['content'],'fail')
    def test_ui_reserves_execution_lock_before_worker_runs(self):
        gate=threading.Event()
        original=FakeUpstreamAgent._completion
        async def delayed(agent,messages,tools):
            await asyncio.to_thread(gate.wait, 3)
            return await original(agent,messages,tools)
        with patch.object(FakeUpstreamAgent,'_completion',delayed):
            self.app.start('reserve',self.app.session)
            with self.assertRaises(ValueError):
                with SessionStore(self.app.settings.workspace).execution_lock():pass
            gate.set()
            self.wait()
    def test_queue_order_and_failure_does_not_block_next(self):
        gate=threading.Event()
        original=FakeUpstreamAgent._completion
        async def delayed(agent,messages,tools):
            if messages[-1].get('content')=='first':
                await asyncio.to_thread(gate.wait, 3)
            return await original(agent,messages,tools)
        with patch.object(FakeUpstreamAgent,'_completion',delayed):
            first=self.app.start('first',self.app.session)['request_id']
            failed=self.app.start('fail',self.app.session)
            last=self.app.start('last',self.app.session)
            self.assertTrue(failed['queued'])
            self.assertTrue(last['queued'])
            self.assertGreaterEqual([t['status'] for t in self.app.store.tasks()].count('queued'),2)
            gate.set()
            self.wait()
        tasks={t['id']:t for t in self.app.store.tasks()}
        self.assertEqual(tasks[first]['status'],'completed')
        self.assertEqual(tasks[failed['request_id']]['status'],'failed')
        self.assertEqual(tasks[last['request_id']]['status'],'completed')
        prompts=[m['message']['content'] for m in self.app.store.messages(self.app.session) if m['message']['role']=='user']
        self.assertEqual(prompts,['first','fail','last'])
    def test_queue_ack_is_durable_associated_and_not_duplicated(self):
        gate=threading.Event()
        original=FakeUpstreamAgent._completion
        async def delayed(agent,messages,tools):
            if messages[-1].get('content')=='first-ack':
                await asyncio.to_thread(gate.wait,3)
            return await original(agent,messages,tools)
        with patch.object(FakeUpstreamAgent,'_completion',delayed):
            first=self.app.start('first-ack',self.app.session)
            queued=self.app.start('queued-ack',self.app.session)
            self.assertTrue(queued['queued'])
            immediate=[m['message']['content'] for m in self.app.store.messages(self.app.session) if m['message']['role']=='user']
            self.assertEqual(immediate,['first-ack','queued-ack'])
            queued_task=next(t for t in self.app.store.tasks() if t['id']==queued['request_id'])
            self.assertIsNotNone(queued_task['message_seq'])
            gate.set();self.wait()
        prompts=[m['message']['content'] for m in self.app.store.messages(self.app.session) if m['message']['role']=='user']
        self.assertEqual(prompts.count('first-ack'),1)
        self.assertEqual(prompts.count('queued-ack'),1)
        tasks={t['id']:t for t in self.get('/api/tasks')['tasks']}
        for task_id in (first['request_id'],queued['request_id']):
            task=tasks[task_id]
            self.assertEqual(task['status'],'completed')
            self.assertEqual(task['workspace'],str(self.app.settings.workspace))
            self.assertEqual(task['session'],self.app.session)
            self.assertIsNotNone(task['started_at']);self.assertIsNotNone(task['finished_at'])
            self.assertIsNotNone(task['duration_ms'])

    def test_non_active_session_rename_does_not_change_active(self):
        first=self.app.session
        second=self.get('/api/sessions',{'title':'Second'})['session']['id']
        self.assertEqual(self.app.session,second)
        self.get('/api/sessions/rename',{'session_id':first,'title':'First renamed'})
        self.assertEqual(self.app.session,second)
        data=self.get('/api/sessions')
        self.assertEqual(data['active'],second)
        self.assertEqual(next(s['title'] for s in data['sessions'] if s['id']==first),'First renamed')

    def test_workspace_details_does_not_create_session_metadata(self):
        fresh=self.root/'workspaces/fresh-git';fresh.mkdir()
        subprocess.run(['git','init','-q',str(fresh)],check=True)
        self.assertFalse((fresh/'.aek').exists())
        rows=self.get('/api/workspaces')['workspaces']
        row=next(item for item in rows if item['name']=='fresh-git')
        self.assertTrue(row['git']);self.assertEqual(row['sessions'],0)
        self.assertFalse((fresh/'.aek').exists())
        self.assertEqual(subprocess.check_output(['git','status','--porcelain'],cwd=fresh,text=True),'')

    def test_clone_cleanup_on_timeout_and_exception(self):
        from mcp_aek.config import clone_workspace
        def timeout_run(argv,**_kwargs):
            Path(argv[-1]).mkdir(parents=True)
            (Path(argv[-1])/'partial').write_text('x')
            raise subprocess.TimeoutExpired(argv,180)
        with patch('mcp_aek.config.subprocess.run',side_effect=timeout_run):
            with self.assertRaisesRegex(ValueError,'timed out'):
                clone_workspace('https://github.com/owner/repo.git','timeout-clone')
        self.assertFalse((self.root/'workspaces/timeout-clone').exists())
        def error_run(argv,**_kwargs):
            Path(argv[-1]).mkdir(parents=True)
            raise OSError('git unavailable')
        with patch('mcp_aek.config.subprocess.run',side_effect=error_run):
            with self.assertRaisesRegex(ValueError,'OSError'):
                clone_workspace('https://github.com/owner/repo.git','error-clone')
        self.assertFalse((self.root/'workspaces/error-clone').exists())

    def test_doctor_real_mcp_and_failed_upstream(self):
        data=self.get('/api/diagnostics')
        self.assertTrue(data['mcp_ok']);self.assertFalse(data['upstream_ok']);self.assertEqual(data['tool_count'],13)
        tools=self.get('/api/tools');self.assertFalse(next(t['available'] for t in tools['tools'] if t['name']=='shell_exec'))
    def test_tool_failure_missing_id_and_durable_result(self):
        self.get('/api/chat',{'prompt':'tool-error','session_id':self.app.session});self.wait()
        event=next(e for e in self.app.events if e['type']=='tool_failed')
        self.assertIn('Error executing tool read_text',event['result'])
        rows=self.app.store.messages(self.app.session)
        self.assertEqual(rows[1]['message']['tool_calls'][0]['id'], rows[2]['message']['tool_call_id'])
        self.assertTrue(event['call_id'])

    def test_one_shot_still_works(self):
        async def run():
            agent=FakeUpstreamAgent()
            try:return await agent.run('workspace')
            finally:await agent.close()
        answer,history=asyncio.run(run());self.assertEqual(answer,'default ภาษาไทย');self.assertEqual(len(history),4)
