#!/usr/bin/env python3
from pathlib import Path
import re

path = Path('bridge/app/src/main/java/com/cgfree/net/WebViewChatEngine.kt')
text = path.read_text(encoding='utf-8')

input_re = re.compile(r'''            function findInput\(\) \{\n.*?            \}\n            function findSendBtn\(\) \{''', re.S)
input_repl = '''            function findInput() {
              var selectors = [
                'form[data-chatgpt-composer] #prompt-textarea',
                'form[data-chatgpt-composer] .ProseMirror[contenteditable="true"]',
                'form[data-chatgpt-composer] [contenteditable="true"][role="textbox"]',
                '#prompt-textarea',
                'textarea[name="prompt-textarea"]'
              ];
              for (var i = 0; i < selectors.length; i++) {
                var q = document.querySelector(selectors[i]);
                if (q) return q;
              }
              return null;
            }
            function findSendBtn() {'''
text, n1 = input_re.subn(input_repl, text, count=1)
if n1 != 1:
    raise SystemExit('findInput patch target not found exactly once')

send_re = re.compile(r'''            function findSendBtn\(\) \{\n.*?            \}\n            function findStopBtn\(\) \{''', re.S)
send_repl = '''            function findSendBtn() {
              var selectors = [
                'button[data-testid="send-button"]',
                '#composer-submit-button',
                'form[data-chatgpt-composer] button[type="submit"]',
                'button[aria-label="Send prompt"]',
                'button.composer-submit-btn'
              ];
              for (var i = 0; i < selectors.length; i++) {
                var b = document.querySelector(selectors[i]);
                if (b) return b;
              }
              return null;
            }
            function findStopBtn() {'''
text, n2 = send_re.subn(send_repl, text, count=1)
if n2 != 1:
    raise SystemExit('findSendBtn patch target not found exactly once')

# Broaden diagnostics so current/future ChatGPT composer changes are visible immediately.
text = text.replace(
    "var inp = document.querySelector('textarea#prompt-textarea') || document.querySelector('div#prompt-textarea') || document.querySelector('[contenteditable=\\\"true\\\"]');",
    "var inp = document.querySelector('form[data-chatgpt-composer] #prompt-textarea') || document.querySelector('form[data-chatgpt-composer] .ProseMirror[contenteditable=\\\"true\\\"]') || document.querySelector('form[data-chatgpt-composer] [contenteditable=\\\"true\\\"][role=\\\"textbox\\\"]') || document.querySelector('#prompt-textarea');"
)
text = text.replace(
    "var sb = document.querySelector('button[data-testid=\\\"send-button\\\"]');",
    "var sb = document.querySelector('button[data-testid=\\\"send-button\\\"]') || document.querySelector('#composer-submit-button') || document.querySelector('form[data-chatgpt-composer] button[type=\\\"submit\\\"]') || document.querySelector('button[aria-label=\\\"Send prompt\\\"]') || document.querySelector('button.composer-submit-btn');"
)

path.write_text(text, encoding='utf-8')
print('patched', path)
