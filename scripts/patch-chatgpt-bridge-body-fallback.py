#!/usr/bin/env python3
from pathlib import Path

p = Path('bridge/app/src/main/java/com/cgfree/net/WebViewChatEngine.kt')
s = p.read_text(encoding='utf-8')

def rep(a,b,label):
    global s
    n=s.count(a)
    if n != 1:
        raise SystemExit(f'{label}: count={n}')
    s=s.replace(a,b,1)

helper = '''            function bodyAssistantText() {
              var body = document.body ? String(document.body.innerText || '') : '';
              if (!body) return '';
              var labels = ['ChatGPT พูดว่า:', 'ChatGPT said:', 'ChatGPT 说：', 'ChatGPT 说:'];
              var start = -1, markerLen = 0;
              for (var i = 0; i < labels.length; i++) {
                var pos = body.lastIndexOf(labels[i]);
                if (pos > start) { start = pos; markerLen = labels[i].length; }
              }
              if (start < 0) return '';
              var tail = body.slice(start + markerLen).trim();
              var footers = ['ChatGPT อาจทำผิดพลาดได้ ตรวจสอบข้อมูลสำคัญ', 'ChatGPT can make mistakes. Check important info.', 'ChatGPT 可能会犯错。请核查重要信息。'];
              var cut = tail.length;
              for (var j = 0; j < footers.length; j++) {
                var fp = tail.indexOf(footers[j]);
                if (fp >= 0 && fp < cut) cut = fp;
              }
              return tail.slice(0, cut).trim();
            }
            function bodyAssistantComplete() {
              if (!bodyAssistantText()) return false;
              var body = document.body ? String(document.body.innerText || '') : '';
              return body.indexOf('ตอบกลับเสร็จสมบูรณ์แล้ว') >= 0 || body.indexOf('Response complete') >= 0 || body.indexOf('回复已完成') >= 0;
            }
'''

rep("            function assistantText() {\n", helper + "            function assistantText() {\n", 'helpers')
rep("              var fallback = assistantFallbackNodes();\n              if (!fallback || !fallback.length) return '';\n              return nodeText(fallback[fallback.length - 1]);", "              var fallback = assistantFallbackNodes();\n              if (fallback && fallback.length) return nodeText(fallback[fallback.length - 1]);\n              return bodyAssistantText();", 'text fallback')
rep("              var fallback = assistantFallbackNodes();\n              return fallback ? fallback.length : 0;", "              var fallback = assistantFallbackNodes();\n              if (fallback && fallback.length) return fallback.length;\n              return bodyAssistantText() ? 1 : 0;", 'count fallback')
rep("              if (!nodes || !nodes.length) return false;", "              if (!nodes || !nodes.length) return bodyAssistantComplete();", 'complete fallback')
rep("              return !!(root.querySelector('button[data-testid=\"copy-turn-action-button\"]') ||\n                root.querySelector('button[aria-label*=\"Copy response\" i]'));", "              return !!(root.querySelector('button[data-testid=\"copy-turn-action-button\"]') ||\n                root.querySelector('button[aria-label*=\"Copy response\" i]')) || bodyAssistantComplete();", 'complete footer')

for marker in ['function bodyAssistantText()', 'function bodyAssistantComplete()', 'return bodyAssistantText();']:
    if marker not in s:
        raise SystemExit('missing ' + marker)

p.write_text(s, encoding='utf-8')
print('patched visible transcript fallback', p)
