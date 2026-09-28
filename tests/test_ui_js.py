import subprocess
import unittest
from pathlib import Path


class UIJavaScriptTests(unittest.TestCase):
    def setUp(self):
        self.source = Path('mcp_aek/static/app.js').read_text(encoding='utf-8')

    def node(self, script):
        result = subprocess.run(['node','-e',script],text=True,capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr or result.stdout)

    def test_busy_ui_keeps_composer_send_enabled_and_submits_queue(self):
        script = r"""
const fs=require('fs');const src=fs.readFileSync('mcp_aek/static/app.js','utf8');
const elements={send:{disabled:null},prompt:{disabled:null,value:'queued from browser'},new:{},rename:{},workspace:{},context:{},activity:{replaceChildren(){}}};
global.state={busy:false,session:{id:'session-1'}};global.$=id=>elements[id];
const busy=src.split('\n').find(line=>line.startsWith('function setBusy('));eval(busy);setBusy(true);
if(elements.send.disabled!==false||elements.prompt.disabled!==false||state.busy!==true)throw new Error('busy disabled queue composer');
let called=null;global.action=fn=>fn;global.api=async(path,data)=>{called={path,data};return {queued:true,request_id:'1234567890'};};
global.grow=()=>{};global.activity={clear(){}};global.notice=()=>{};global.history=async()=>{};
const send=src.split('\n').find(line=>line.startsWith("$('send').onclick="));eval(send);
(async()=>{await elements.send.onclick();if(!called||called.path!='/api/chat'||called.data.prompt!=='queued from browser')throw new Error('busy send did not enqueue');})().catch(e=>{console.error(e);process.exit(1);});
"""
        self.node(script)

    def test_theme_storage_falls_back_when_localstorage_is_unavailable(self):
        script = r"""
const fs=require('fs');const src=fs.readFileSync('mcp_aek/static/app.js','utf8');
const get=src.split('\n').find(line=>line.startsWith('function storageGet('));
const set=src.split('\n').find(line=>line.startsWith('function storageSet('));
Object.defineProperty(globalThis,'localStorage',{get(){throw new Error('blocked');}});eval(get);eval(set);
if(storageGet('aek-theme','system')!=='system')throw new Error('theme fallback failed');storageSet('aek-theme','dark');
"""
        self.node(script)
        self.assertNotIn("theme(localStorage.getItem", self.source)

    def test_request_finished_keeps_busy_when_queue_remains(self):
        self.assertIn("if(e.type==='request_finished'){setBusy(Boolean(e.queue_pending));action(()=>refresh())();}",self.source)
        self.assertNotIn("if(e.type==='request_finished'){setBusy(false)",self.source)


if __name__ == '__main__':
    unittest.main()
