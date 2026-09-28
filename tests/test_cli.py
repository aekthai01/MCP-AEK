import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

class Bridge(BaseHTTPRequestHandler):
    requests=[]
    def log_message(self,*_):pass
    def reply(self,data):
        body=json.dumps(data).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def do_GET(self):self.reply({'data':[{'id':'test-model'}]})
    def do_POST(self):
        data=json.loads(self.rfile.read(int(self.headers['Content-Length'])));type(self).requests.append(data)
        messages=data['messages'];last=messages[-1]
        if last['role']=='tool':response={'content':'default'}
        elif 'workspace_info' in last.get('content',''):response={'tool_calls':[{'type':'function','id':'call_http','function':{'name':'workspace_info','arguments':'{}'}}]}
        elif 'รหัสเมื่อกี้' in last.get('content',''):response={'content':'ABC-123' if any('ABC-123' in m.get('content','') for m in messages[:-1]) else 'missing'}
        else:response={'content':'OK'}
        self.reply({'choices':[{'message':{'role':'assistant',**response}}]})

class CLITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.bridge=ThreadingHTTPServer(('127.0.0.1',0),Bridge)
        self.thread=threading.Thread(target=self.bridge.serve_forever,daemon=True);self.thread.start()
        self.env={**os.environ,'AEK_STATE_DIR':self.tmp.name+'/state','AEK_WORKSPACE_ROOT':self.tmp.name+'/ws','AEK_MODEL':'test-model','AEK_UPSTREAM_BASE_URL':f'http://127.0.0.1:{self.bridge.server_address[1]}/v1'}
    def tearDown(self):self.bridge.shutdown();self.bridge.server_close();self.thread.join();self.tmp.cleanup()
    def cli(self,*args,input=None):
        result=subprocess.run([sys.executable,'-m','mcp_aek.cli',*args],env=self.env,input=input,text=True,capture_output=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr);return result.stdout
    def test_cli_one_shot_doctor_and_real_http_tool_loop(self):
        self.assertEqual(self.cli('run','ตอบ OK เท่านั้น').strip(),'OK')
        self.assertEqual(self.cli('run','workspace_info').strip(),'default')
        doctor=json.loads(self.cli('doctor'));self.assertTrue(doctor['upstream_ok']);self.assertTrue(doctor['mcp_ok']);self.assertTrue(doctor['model_available'])
        self.assertEqual(len(doctor['mcp_tools']),13)
    def test_chat_process_restart_commands_and_clear(self):
        self.cli('chat','new','luas')
        self.assertIn('OK',self.cli('chat',input='จำรหัสทดสอบนี้ไว้ ABC-123\n/exit\n'))
        self.assertIn('ABC-123',self.cli('chat',input='รหัสเมื่อกี้คืออะไร\n/exit\n'))
        info=json.loads(self.cli('chat','info'));self.assertEqual(info['message_count'],4)
        self.cli('chat','summary','User note: inspect src/main.lua; verify with read_text')
        self.assertIn('User note',json.loads(self.cli('chat','info'))['summary'])
        self.cli('chat',input='/clear\n/exit\n');self.assertIn('luas',self.cli('chat','list'))
        self.cli('chat','use','luas');self.cli('chat','rename','renamed')
        self.assertEqual(json.loads(self.cli('chat','info'))['id'],info['id'])
        self.cli('chat','delete','renamed','--yes')
        self.assertNotIn('renamed',self.cli('chat','list'))
    def test_missing_arguments_and_workspace_clear(self):
        for command in ('use','rename','summary','delete'):
            result=subprocess.run([sys.executable,'-m','mcp_aek.cli','chat',command],env=self.env,text=True,capture_output=True)
            self.assertEqual(result.returncode,2)
            self.assertNotIn('Traceback',result.stderr)
        self.cli('chat','new','before-clear')
        data=json.loads(self.cli('chat','clear-all','--yes'))
        self.assertGreaterEqual(data['deleted_sessions'],1)
        self.assertEqual(len(self.cli('chat','list').splitlines()),1)
