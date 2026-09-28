#!/usr/bin/env python3
from pathlib import Path
import re

path = Path('bridge/app/src/main/java/com/cgfree/net/WebViewChatEngine.kt')
text = path.read_text(encoding='utf-8')


def sub_once(pattern: str, replacement: str, label: str) -> None:
    global text
    text, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise SystemExit(f'{label} patch target not found exactly once')


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label} patch target count={count}, expected 1')
    text = text.replace(old, new, 1)


# ChatGPT's composer has moved between textarea, ProseMirror and form-scoped DOMs.
# Keep selectors narrow enough that we never fall back to an unrelated contenteditable.
sub_once(
    r'''            function findInput\(\) \{\n.*?            \}\n            function findSendBtn\(\) \{''',
    '''            function findInput() {
              var selectors = [
                'form[data-chatgpt-composer] #prompt-textarea',
                'form[data-chatgpt-composer] .ProseMirror[contenteditable="true"]',
                'form[data-chatgpt-composer] [contenteditable="true"][role="textbox"]',
                '#prompt-textarea[contenteditable="true"]',
                '#prompt-textarea',
                'textarea[name="prompt-textarea"]'
              ];
              for (var i = 0; i < selectors.length; i++) {
                var q = document.querySelector(selectors[i]);
                if (q) return q;
              }
              return null;
            }
            function findSendBtn() {''',
    'findInput',
)

sub_once(
    r'''            function findSendBtn\(\) \{\n.*?            \}\n            function findStopBtn\(\) \{''',
    '''            function findSendBtn() {
              var selectors = [
                'button[data-testid="send-button"]',
                '#composer-submit-button',
                'form[data-chatgpt-composer] button[type="submit"]',
                'button[aria-label="Send prompt"]',
                'button[aria-label="Send"]',
                'button.composer-submit-btn'
              ];
              for (var i = 0; i < selectors.length; i++) {
                var b = document.querySelector(selectors[i]);
                if (b) return b;
              }
              return null;
            }
            function findStopBtn() {''',
    'findSendBtn',
)

# ChatGPT 2026 renders turns primarily as section/article nodes with data-turn,
# while some deployments still expose data-message-author-role. Use priority
# selectors instead of a broad union so nested compatibility wrappers do not
# double-count a single turn.
sub_once(
    r'''            function assistantText\(\) \{\n.*?            \}\n            function assistantCount\(\) \{\n.*?            \}\n''',
    '''            function roleNodes(role) {
              var selectors = [
                'section[data-turn="' + role + '"]',
                'article[data-turn="' + role + '"]',
                '[data-testid^="conversation-turn-"][data-turn="' + role + '"]',
                '[data-testid^="conversation-turn-"][data-message-author-role="' + role + '"]',
                '[data-message-author-role="' + role + '"]',
                '[data-role="' + role + '"]',
                '[data-message-author="' + role + '"]'
              ];
              for (var si = 0; si < selectors.length; si++) {
                var found = document.querySelectorAll(selectors[si]);
                if (found && found.length) return found;
              }
              return [];
            }
            function assistantText() {
              var nodes = roleNodes('assistant');
              if (!nodes || !nodes.length) return '';
              var last = nodes[nodes.length - 1];
              if (!last) return '';
              var content = last.querySelector('.markdown') ||
                last.querySelector('.prose') ||
                last.querySelector('[class*="markdown"]') || last;
              var t = content.innerText || content.textContent || '';
              return t.replace(/\\u200b/g, '');
            }
            function assistantCount() {
              var nodes = roleNodes('assistant');
              return nodes ? nodes.length : 0;
            }
            function userCount() {
              var nodes = roleNodes('user');
              return nodes ? nodes.length : 0;
            }
''',
    'assistant DOM',
)

replace_once(
    "            var pre = assistantText();\n            var preCount = assistantCount();\n            log('pre-text-len=' + pre.length + ' pre-assistant-count=' + preCount);",
    "            var pre = assistantText();\n            var preCount = assistantCount();\n            var preUserCount = userCount();\n            var prePath = location.pathname;\n            log('pre-text-len=' + pre.length + ' pre-assistant-count=' + preCount + ' pre-user-count=' + preUserCount + ' pre-path=' + prePath);",
    'pre-send baseline',
)

# A new /c/<id> route and/or a newly-rendered user turn proves the click was
# accepted even if the assistant/stop controls have not rendered yet. This is
# exactly what current ChatGPT does on slow model startup.
replace_once(
    "            var accepted = false;\n            for (var wa = 0; wa < 24; wa++) {\n              var inputNow = findInput();\n              var inputNowText = inputNow ? (((inputNow.isContentEditable === true) || inputNow.tagName === 'DIV') ? (inputNow.innerText || '') : (inputNow.value || '')) : '';\n              if (findStopBtn() || assistantCount() > preCount || inputNowText.length === 0) { accepted = true; break; }\n              var acceptError = pageError();",
    "            var accepted = false;\n            var acceptReason = '';\n            for (var wa = 0; wa < 24; wa++) {\n              var inputNow = findInput();\n              var inputNowText = inputNow ? (((inputNow.isContentEditable === true) || inputNow.tagName === 'DIV') ? (inputNow.innerText || '') : (inputNow.value || '')) : '';\n              if (findStopBtn()) { accepted = true; acceptReason = 'stop-button'; break; }\n              if (assistantCount() > preCount) { accepted = true; acceptReason = 'assistant-turn'; break; }\n              if (userCount() > preUserCount) { accepted = true; acceptReason = 'user-turn'; break; }\n              if (/^\\/c\\//.test(location.pathname) && location.pathname !== prePath) { accepted = true; acceptReason = 'conversation-route'; break; }\n              if (inputNowText.length === 0) { accepted = true; acceptReason = 'composer-cleared'; break; }\n              var acceptError = pageError();",
    'send acceptance',
)

replace_once(
    "            if (!accepted) {\n              AndroidBridge.onError(0, '点击发送后 12 秒页面仍无响应，请打开账号页检查验证状态');\n              return;\n            }\n            var lastText = pre;",
    "            if (!accepted) {\n              AndroidBridge.onError(0, '点击发送后 12 秒页面仍无响应，请打开账号页检查验证状态');\n              return;\n            }\n            log('send-accepted-by=' + acceptReason + ' path=' + location.pathname + ' users=' + userCount() + ' assistants=' + assistantCount());\n            var lastText = pre;",
    'acceptance logging',
)

# Broaden diagnostics so a future ChatGPT DOM change is visible immediately.
replace_once(
    "  var inp = document.querySelector('textarea#prompt-textarea') || document.querySelector('div#prompt-textarea') || document.querySelector('[contenteditable=\\\"true\\\"]');",
    "  var inp = document.querySelector('form[data-chatgpt-composer] #prompt-textarea') || document.querySelector('form[data-chatgpt-composer] .ProseMirror[contenteditable=\\\"true\\\"]') || document.querySelector('form[data-chatgpt-composer] [contenteditable=\\\"true\\\"][role=\\\"textbox\\\"]') || document.querySelector('#prompt-textarea');",
    'diag input',
)
replace_once(
    "  var sb = document.querySelector('button[data-testid=\\\"send-button\\\"]');",
    "  var sb = document.querySelector('button[data-testid=\\\"send-button\\\"]') || document.querySelector('#composer-submit-button') || document.querySelector('form[data-chatgpt-composer] button[type=\\\"submit\\\"]') || document.querySelector('button[aria-label=\\\"Send prompt\\\"]') || document.querySelector('button[aria-label=\\\"Send\\\"]') || document.querySelector('button.composer-submit-btn');",
    'diag send button',
)

# The original diag only knows the legacy assistant-role attribute. Teach it
# the same turn selectors used by the runtime so the next report is trustworthy.
replace_once(
    "  var nodes = document.querySelectorAll('[data-message-author-role=\\\"assistant\\\"]');\n  r.assistantCount = nodes.length;\n  r.lastAssistant = nodes.length ? String(nodes[nodes.length - 1].innerText || '').slice(0, 300) : '';",
    "  var nodes = document.querySelectorAll('section[data-turn=\\\"assistant\\\"]');\n  if (!nodes.length) nodes = document.querySelectorAll('article[data-turn=\\\"assistant\\\"]');\n  if (!nodes.length) nodes = document.querySelectorAll('[data-testid^=\\\"conversation-turn-\\\"][data-turn=\\\"assistant\\\"]');\n  if (!nodes.length) nodes = document.querySelectorAll('[data-message-author-role=\\\"assistant\\\"]');\n  r.assistantCount = nodes.length;\n  r.lastAssistant = nodes.length ? String(nodes[nodes.length - 1].innerText || '').slice(0, 300) : '';\n  var userNodes = document.querySelectorAll('section[data-turn=\\\"user\\\"]');\n  if (!userNodes.length) userNodes = document.querySelectorAll('article[data-turn=\\\"user\\\"]');\n  if (!userNodes.length) userNodes = document.querySelectorAll('[data-testid^=\\\"conversation-turn-\\\"][data-turn=\\\"user\\\"]');\n  if (!userNodes.length) userNodes = document.querySelectorAll('[data-message-author-role=\\\"user\\\"]');\n  r.userCount = userNodes.length;",
    'diag turn selectors',
)

path.write_text(text, encoding='utf-8')
print('patched', path)
