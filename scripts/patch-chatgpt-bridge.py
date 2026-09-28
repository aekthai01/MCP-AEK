#!/usr/bin/env python3
from pathlib import Path
import re

path = Path("bridge/app/src/main/java/com/cgfree/net/WebViewChatEngine.kt")
text = path.read_text(encoding="utf-8")


def sub_once(pattern: str, replacement: str, label: str) -> None:
    global text
    text, count = re.subn(pattern, lambda _m: replacement, text, count=1, flags=re.S)
    if count != 1:
        raise SystemExit(f"{label} patch target count={count}, expected 1")


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label} patch target count={count}, expected 1")
    text = text.replace(old, new, 1)


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
    "findInput",
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
    "findSendBtn",
)

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
            function assistantFallbackNodes() {
              var selectors = [
                'main .markdown.prose',
                'main [class*="markdown"][class*="prose"]',
                'main [data-testid^="conversation-turn-"] .markdown'
              ];
              for (var fi = 0; fi < selectors.length; fi++) {
                var found = document.querySelectorAll(selectors[fi]);
                if (found && found.length) return found;
              }
              return [];
            }
            function assistantText() {
              var nodes = roleNodes('assistant');
              var last = null;
              if (nodes && nodes.length) {
                last = nodes[nodes.length - 1];
                var content = last.querySelector('.markdown') ||
                  last.querySelector('.prose') ||
                  last.querySelector('[class*="markdown"]') || last;
                var t = content.innerText || content.textContent || '';
                return t.split(String.fromCharCode(8203)).join('');
              }
              var fallback = assistantFallbackNodes();
              if (!fallback || !fallback.length) return '';
              last = fallback[fallback.length - 1];
              var ft = last.innerText || last.textContent || '';
              return ft.split(String.fromCharCode(8203)).join('');
            }
            function assistantCount() {
              var nodes = roleNodes('assistant');
              if (nodes && nodes.length) return nodes.length;
              var fallback = assistantFallbackNodes();
              return fallback ? fallback.length : 0;
            }
            function userCount() {
              var nodes = roleNodes('user');
              return nodes ? nodes.length : 0;
            }
''',
    "assistant DOM",
)

replace_once(
    "            var pre = assistantText();\n            var preCount = assistantCount();\n            log('pre-text-len=' + pre.length + ' pre-assistant-count=' + preCount);",
    "            var pre = assistantText();\n            var preCount = assistantCount();\n            var preUserCount = userCount();\n            var prePath = location.pathname;\n            log('pre-text-len=' + pre.length + ' pre-assistant-count=' + preCount + ' pre-user-count=' + preUserCount + ' pre-path=' + prePath);",
    "pre-send baseline",
)

replace_once(
    "            var accepted = false;\n            for (var wa = 0; wa < 24; wa++) {\n              var inputNow = findInput();\n              var inputNowText = inputNow ? (((inputNow.isContentEditable === true) || inputNow.tagName === 'DIV') ? (inputNow.innerText || '') : (inputNow.value || '')) : '';\n              if (findStopBtn() || assistantCount() > preCount || inputNowText.length === 0) { accepted = true; break; }\n              var acceptError = pageError();",
    "            var accepted = false;\n            var acceptReason = '';\n            for (var wa = 0; wa < 24; wa++) {\n              var inputNow = findInput();\n              var inputNowText = inputNow ? (((inputNow.isContentEditable === true) || inputNow.tagName === 'DIV') ? (inputNow.innerText || '') : (inputNow.value || '')) : '';\n              if (findStopBtn()) { accepted = true; acceptReason = 'stop-button'; break; }\n              if (assistantCount() > preCount) { accepted = true; acceptReason = 'assistant-turn'; break; }\n              if (userCount() > preUserCount) { accepted = true; acceptReason = 'user-turn'; break; }\n              if (/^\\/c\\//.test(location.pathname) && location.pathname !== prePath) { accepted = true; acceptReason = 'conversation-route'; break; }\n              if (inputNowText.length === 0) { accepted = true; acceptReason = 'composer-cleared'; break; }\n              var acceptError = pageError();",
    "send acceptance",
)

replace_once(
    "            if (!accepted) {\n              AndroidBridge.onError(0, '点击发送后 12 秒页面仍无响应，请打开账号页检查验证状态');\n              return;\n            }\n            var lastText = pre;",
    "            if (!accepted) {\n              AndroidBridge.onError(0, '点击发送后 12 秒页面仍无响应，请打开账号页检查验证状态');\n              return;\n            }\n            log('send-accepted-by=' + acceptReason + ' path=' + location.pathname + ' users=' + userCount() + ' assistants=' + assistantCount());\n            var lastText = pre;",
    "acceptance logging",
)

sub_once(
    r'''  var inp = document\.querySelector\('textarea#prompt-textarea'\).*?;\n  r\.input =''',
    '''  var inp = document.querySelector('form[data-chatgpt-composer] #prompt-textarea') ||
    document.querySelector('form[data-chatgpt-composer] .ProseMirror[contenteditable="true"]') ||
    document.querySelector('form[data-chatgpt-composer] [contenteditable="true"][role="textbox"]') ||
    document.querySelector('#prompt-textarea');
  r.input =''',
    "diag input",
)

sub_once(
    r'''  var nodes = document\.querySelectorAll\('\[data-message-author-role="assistant"\]'\);\n  r\.assistantCount = nodes\.length;\n  r\.lastAssistant = .*?;\n''',
    '''  var nodes = document.querySelectorAll('section[data-turn="assistant"]');
  if (!nodes.length) nodes = document.querySelectorAll('article[data-turn="assistant"]');
  if (!nodes.length) nodes = document.querySelectorAll('[data-testid^="conversation-turn-"][data-turn="assistant"]');
  if (!nodes.length) nodes = document.querySelectorAll('[data-message-author-role="assistant"]');
  var markdownNodes = document.querySelectorAll('main .markdown.prose, main [class*="markdown"][class*="prose"]');
  r.assistantCount = nodes.length || markdownNodes.length;
  var lastAssistant = nodes.length ? nodes[nodes.length - 1] : (markdownNodes.length ? markdownNodes[markdownNodes.length - 1] : null);
  r.lastAssistant = lastAssistant ? String(lastAssistant.innerText || lastAssistant.textContent || '').slice(0, 300) : '';
  var userNodes = document.querySelectorAll('section[data-turn="user"]');
  if (!userNodes.length) userNodes = document.querySelectorAll('article[data-turn="user"]');
  if (!userNodes.length) userNodes = document.querySelectorAll('[data-testid^="conversation-turn-"][data-turn="user"]');
  if (!userNodes.length) userNodes = document.querySelectorAll('[data-message-author-role="user"]');
  r.userCount = userNodes.length;
  r.conversationTurnCount = document.querySelectorAll('[data-testid^="conversation-turn-"]').length;
''',
    "diag turn selectors",
)

sub_once(
    r'''  var sb = document\.querySelector\('button\[data-testid="send-button"\]'\);''',
    '''  var sb = document.querySelector('button[data-testid="send-button"]') ||
    document.querySelector('#composer-submit-button') ||
    document.querySelector('form[data-chatgpt-composer] button[type="submit"]') ||
    document.querySelector('button[aria-label="Send prompt"]') ||
    document.querySelector('button[aria-label="Send"]') ||
    document.querySelector('button.composer-submit-btn');''',
    "diag send button",
)

path.write_text(text, encoding="utf-8")
print("patched", path)
