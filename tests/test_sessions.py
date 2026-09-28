import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from mcp_aek.sessions import SessionStore, context_messages, sanitize

class SessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = SessionStore(self.root, ('super-secret-key',))
        self.sid = self.store.current()['id']
    def tearDown(self):
        self.tmp.cleanup()
    def test_unicode_calls_and_restart(self):
        messages=[{'role':'user','content':'ภาษาไทย 中文 ABC-123'}, {'role':'assistant','content':'', 'tool_calls':[{'id':'a','function':{'name':'workspace_info','arguments':'{}'}}]}, {'role':'tool','tool_call_id':'a','content':'default'}]
        for m in messages:self.store.append(self.sid,m)
        other=SessionStore(self.root)
        self.assertEqual(other.current()['id'], self.sid)
        self.assertEqual([m['message'] for m in other.messages(self.sid)],messages)
        self.assertEqual(other.current()['message_count'],3)
    def test_process_restart_and_uncommitted_crash(self):
        code="from pathlib import Path; from mcp_aek.sessions import SessionStore; import sys,os; s=SessionStore(Path(sys.argv[1])); s.append(s.current()['id'],{'role':'user','content':'บันทึกแล้ว'}); os._exit(0)"
        subprocess.run([sys.executable,'-c',code,str(self.root)],check=True)
        code="import sqlite3,sys,os; c=sqlite3.connect(sys.argv[1]); c.execute(\"INSERT INTO messages(session,data) VALUES(?,?)\",(sys.argv[2],'uncommitted')); os._exit(0)"
        subprocess.run([sys.executable,'-c',code,str(self.store.path),self.sid],check=True)
        self.assertEqual(SessionStore(self.root).messages(self.sid)[0]['message']['content'],'บันทึกแล้ว')
        self.assertEqual(self.store.current()['message_count'],1)
    def test_management_isolation_and_pagination(self):
        self.store.rename(self.sid,'luas'); other=self.store.create('web-ui')['id']
        self.store.use('luas');self.assertEqual(self.store.current()['id'],self.sid)
        for n in range(5):self.store.append(self.sid,{'role':'user','content':str(n)})
        page=self.store.messages(self.sid,limit=2)
        self.assertEqual([r['message']['content'] for r in page],['3','4'])
        self.assertEqual(self.store.messages(self.sid,before=page[0]['seq'],limit=2)[0]['message']['content'],'1')
        separate=SessionStore(self.root/'other')
        with self.assertRaises(ValueError):separate.info(self.sid)
        self.store.delete(self.sid);self.assertNotEqual(self.store.current()['id'],self.sid)
        self.assertEqual(self.store.info(other)['title'],'web-ui')
    def test_corruption_does_not_silently_reset(self):
        self.store.path.write_bytes(b'not sqlite')
        with self.assertRaises(sqlite3.DatabaseError):SessionStore(self.root)
        self.assertEqual(self.store.path.read_bytes(),b'not sqlite')
    def test_traversal_symlink_permissions_lock(self):
        for ref in ['../x','/x','..','invalid']:
            with self.assertRaises(ValueError):self.store.info(ref)
        self.assertEqual(self.store.path.stat().st_mode&0o777,0o600)
        with self.store.execution_lock():
            with self.assertRaises(ValueError):
                with SessionStore(self.root).execution_lock():pass
        other=self.root/'evil';other.mkdir();(other/'.aek').symlink_to(self.store.root,target_is_directory=True)
        with self.assertRaises(ValueError):SessionStore(other)
    def test_serialized_credentials_and_cookie_headers(self):
        value = sanitize({"arguments": json.dumps({"api_key": "abc", "path": "src/main.lua"})})
        self.assertEqual(json.loads(value["arguments"])["api_key"], "[REDACTED]")
        self.assertEqual(json.loads(value["arguments"])["path"], "src/main.lua")
        self.assertNotIn("sid=", sanitize("Cookie: sid=aaa; other=bbb"))

    def test_secrets(self):
        self.store.append(self.sid,{'role':'user','content':'key super-secret-key Authorization: Bearer xyz123 api_key=hidden cookie=abc'})
        data=json.dumps(self.store.messages(self.sid))
        for secret in ['super-secret-key','xyz123','hidden','cookie=abc']:self.assertNotIn(secret,data)
        self.assertEqual(sanitize({'accessToken':'abc'})['accessToken'],'[REDACTED]')

class ContextTests(unittest.TestCase):
    def test_whole_turns_and_no_duplicate_system(self):
        h=[{'role':'system','content':'old'},{'role':'user','content':'x'*1000},{'role':'assistant','content':'old'},{'role':'user','content':'new'}]
        result=context_messages(h,'system',300)
        self.assertEqual(result,[{'role':'system','content':'system'},{'role':'user','content':'new'}])
    def test_interrupted_tool_group(self):
        h=[{'role':'user','content':'go'},{'role':'assistant','tool_calls':[{'id':'a'},{'id':'b'}]},{'role':'tool','tool_call_id':'a','content':'real'}]
        self.assertEqual(len(context_messages(h,'system')),2)
        h.append({'role':'tool','tool_call_id':'b','content':'real2'})
        self.assertEqual(context_messages(h,'system')[1:],h)
    def test_oversize_refuses_without_altering_evidence(self):
        h=[{'role':'user','content':'x'*5000}]
        with self.assertRaises(ValueError):context_messages(h,'system',200)
        self.assertEqual(len(h[0]['content']),5000)
