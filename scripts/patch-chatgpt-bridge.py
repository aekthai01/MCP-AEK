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
                '[data-testid="prompt-textarea"]',
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
                'button[aria-label="Send message"]',
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
    r'''            function findStopBtn\(\) \{\n.*?            \}\n            function pageError\(\) \{''',
    '''            function findStopBtn() {
              return document.querySelector('button[data-testid="stop-button"]') ||
                document.querySelector('button[aria-label*="Stop generating" i]') ||
                document.querySelector('button[aria-label="Stop"]') ||
                document.querySelector('button[aria-label*="停止"]');
            }
            function pageError() {''',
    "findStopBtn",
)

# Prefer explicit role metadata, then current ChatGPT .agent-turn containers,
# then roleless conversation-turn containers. Each API request starts from a
# fresh chat, so the second/newest roleless turn is the assistant fallback.
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
              if (role === 'assistant') {
                var agents = document.querySelectorAll('.agent-turn');
                if (agents && agents.length) return agents;
              }
              return [];
            }
            function turnNodes() {
              var selectors = [
                'section[data-testid^="conversation-turn-"]',
                'article[data-testid^="conversation-turn-"]',
                '[data-testid^="conversation-turn-"]',
                'section[data-turn-id]',
                'article[data-turn-id]'
              ];
              for (var ti = 0; ti < selectors.length; ti++) {
                var found = document.querySelectorAll(selectors[ti]);
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
            function nodeText(node) {
              if (!node) return '';
              var content = node.querySelector('.markdown') ||
                node.querySelector('.prose') ||
                node.querySelector('[class*="markdown"]') || node;
              var clone = content.cloneNode(true);
              try {
                clone.querySelectorAll('button, script, style, [aria-hidden="true"], [data-testid*="copy" i]').forEach(function(n){ n.remove(); });
              } catch(e) {}
              var t = clone.textContent || content.innerText || content.textContent || '';
              return t.split(String.fromCharCode(8203)).join('').trim();
            }
            function assistantText() {
              var nodes = roleNodes('assistant');
              if (nodes && nodes.length) return nodeText(nodes[nodes.length - 1]);
              var turns = turnNodes();
              if (turns && turns.length >= 2) return nodeText(turns[turns.length - 1]);
              var fallback = assistantFallbackNodes();
              if (!fallback || !fallback.length) return '';
              return nodeText(fallback[fallback.length - 1]);
            }
            function assistantCount() {
              var nodes = roleNodes('assistant');
              if (nodes && nodes.length) return nodes.length;
              var turns = turnNodes();
              if (turns && turns.length >= 2) return Math.floor(turns.length / 2);
              var fallback = assistantFallbackNodes();
              return fallback ? fallback.length : 0;
            }
            function userCount() {
              var nodes = roleNodes('user');
              if (nodes && nodes.length) return nodes.length;
              var turns = turnNodes();
              return turns && turns.length ? Math.ceil(turns.length / 2) : 0;
            }
            function assistantComplete() {
              var nodes = roleNodes('assistant');
              if (!nodes || !nodes.length) return false;
              var last = nodes[nodes.length - 1];
              var root = last.closest('[data-turn="assistant"]') ||
                last.closest('[data-testid^="conversation-turn-"]') ||
                last.closest('[data-turn-id]') || last;
              return !!(root.querySelector('button[data-testid="copy-turn-action-button"]') ||
                root.querySelector('button[aria-label*="Copy response" i]'));
            }
''',
    "assistant DOM",
)

replace_once(
    "            var pre = assistantText();\n            var preCount = assistantCount();\n            log('pre-text-len=' + pre.length + ' pre-assistant-count=' + preCount);",
    "            var pre = assistantText();\n            var preCount = assistantCount();\n            var preUserCount = userCount();\n            var preTurnCount = turnNodes().length;\n            var prePath = location.pathname;\n            log('pre-text-len=' + pre.length + ' pre-assistant-count=' + preCount + ' pre-user-count=' + preUserCount + ' pre-turn-count=' + preTurnCount + ' pre-path=' + prePath);",
    "pre-send baseline",
)

replace_once(
    "            var accepted = false;\n            for (var wa = 0; wa < 24; wa++) {\n              var inputNow = findInput();\n              var inputNowText = inputNow ? (((inputNow.isContentEditable === true) || inputNow.tagName === 'DIV') ? (inputNow.innerText || '') : (inputNow.value || '')) : '';\n              if (findStopBtn() || assistantCount() > preCount || inputNowText.length === 0) { accepted = true; break; }\n              var acceptError = pageError();",
    "            var accepted = false;\n            var acceptReason = '';\n            for (var wa = 0; wa < 24; wa++) {\n              var inputNow = findInput();\n              var inputNowText = inputNow ? (((inputNow.isContentEditable === true) || inputNow.tagName === 'DIV') ? (inputNow.innerText || '') : (inputNow.value || '')) : '';\n              if (findStopBtn()) { accepted = true; acceptReason = 'stop-button'; break; }\n              if (assistantCount() > preCount) { accepted = true; acceptReason = 'assistant-turn'; break; }\n              if (userCount() > preUserCount) { accepted = true; acceptReason = 'user-turn'; break; }\n              if (turnNodes().length > preTurnCount) { accepted = true; acceptReason = 'conversation-turn'; break; }\n              if (/^\\/c\\//.test(location.pathname) && location.pathname !== prePath) { accepted = true; acceptReason = 'conversation-route'; break; }\n              if (inputNowText.length === 0) { accepted = true; acceptReason = 'composer-cleared'; break; }\n              var acceptError = pageError();",
    "send acceptance",
)

replace_once(
    "            if (!accepted) {\n              AndroidBridge.onError(0, '点击发送后 12 秒页面仍无响应，请打开账号页检查验证状态');\n              return;\n            }\n            var lastText = pre;",
    "            if (!accepted) {\n              AndroidBridge.onError(0, '点击发送后 12 秒页面仍无响应，请打开账号页检查验证状态');\n              return;\n            }\n            log('send-accepted-by=' + acceptReason + ' path=' + location.pathname + ' users=' + userCount() + ' assistants=' + assistantCount() + ' turns=' + turnNodes().length);\n            var lastText = pre;",
    "acceptance logging",
)

replace_once(
    "                log('loop#' + loopCnt + ' textLen=' + cur.length + ' nodes=' + nodeCount + '/' + preCount + ' stop=' + (stopNow ? 'Y' : 'N') + ' sendBtn=' + (dbgBtn ? (dbgBtn.disabled ? 'disabled' : 'ok') : 'missing') + ' stable=' + stableCnt + ' new=' + (newReply ? 'Y' : 'N'));",
    "                log('loop#' + loopCnt + ' textLen=' + cur.length + ' nodes=' + nodeCount + '/' + preCount + ' turns=' + turnNodes().length + '/' + preTurnCount + ' stop=' + (stopNow ? 'Y' : 'N') + ' complete=' + (assistantComplete() ? 'Y' : 'N') + ' sendBtn=' + (dbgBtn ? (dbgBtn.disabled ? 'disabled' : 'ok') : 'missing') + ' stable=' + stableCnt + ' new=' + (newReply ? 'Y' : 'N'));",
    "poll diagnostics",
)

# A current ChatGPT assistant turn exposes its Copy-response action when complete.
# Use that as the strongest completion signal; keep the existing stability fallback.
replace_once(
    "              var sendBtn2 = findSendBtn();\n              var done = !stopNow && newReply && sendBtn2 && !sendBtn2.disabled;\n              if (done) { finish(cur); return; }",
    "              var sendBtn2 = findSendBtn();\n              var done = !stopNow && newReply && assistantComplete();\n              if (done) { finish(cur); return; }",
    "completion signal",
)

sub_once(
    r'''  var inp = document\.querySelector\('textarea#prompt-textarea'\).*?;\n  r\.input =''',
    '''  var inp = document.querySelector('form[data-chatgpt-composer] #prompt-textarea') ||
    document.querySelector('form[data-chatgpt-composer] .ProseMirror[contenteditable="true"]') ||
    document.querySelector('form[data-chatgpt-composer] [contenteditable="true"][role="textbox"]') ||
    document.querySelector('#prompt-textarea') || document.querySelector('[data-testid="prompt-textarea"]');
  r.input =''',
    "diag input",
)

sub_once(
    r'''  var nodes = document\.querySelectorAll\('\[data-message-author-role="assistant"\]'\);\n  r\.assistantCount = nodes\.length;\n  r\.lastAssistant = .*?;\n''',
    '''  var nodes = document.querySelectorAll('section[data-turn="assistant"]');
  if (!nodes.length) nodes = document.querySelectorAll('article[data-turn="assistant"]');
  if (!nodes.length) nodes = document.querySelectorAll('[data-testid^="conversation-turn-"][data-turn="assistant"]');
  if (!nodes.length) nodes = document.querySelectorAll('[data-message-author-role="assistant"]');
  if (!nodes.length) nodes = document.querySelectorAll('.agent-turn');
  var turns = document.querySelectorAll('section[data-testid^="conversation-turn-"]');
  if (!turns.length) turns = document.querySelectorAll('article[data-testid^="conversation-turn-"]');
  if (!turns.length) turns = document.querySelectorAll('[data-testid^="conversation-turn-"]');
  if (!turns.length) turns = document.querySelectorAll('section[data-turn-id], article[data-turn-id]');
  var markdownNodes = document.querySelectorAll('main .markdown.prose, main [class*="markdown"][class*="prose"]');
  var rolelessAssistant = (!nodes.length && turns.length >= 2) ? turns[turns.length - 1] : null;
  r.assistantCount = nodes.length || (turns.length >= 2 ? Math.floor(turns.length / 2) : markdownNodes.length);
  var lastAssistant = nodes.length ? nodes[nodes.length - 1] : (rolelessAssistant || (markdownNodes.length ? markdownNodes[markdownNodes.length - 1] : null));
  r.lastAssistant = lastAssistant ? String(lastAssistant.innerText || lastAssistant.textContent || '').slice(0, 300) : '';
  var userNodes = document.querySelectorAll('section[data-turn="user"]');
  if (!userNodes.length) userNodes = document.querySelectorAll('article[data-turn="user"]');
  if (!userNodes.length) userNodes = document.querySelectorAll('[data-testid^="conversation-turn-"][data-turn="user"]');
  if (!userNodes.length) userNodes = document.querySelectorAll('[data-message-author-role="user"]');
  r.userCount = userNodes.length || (turns.length ? Math.ceil(turns.length / 2) : 0);
  r.conversationTurnCount = turns.length;
  r.agentTurnCount = document.querySelectorAll('.agent-turn').length;
  r.streamingStatusCount = document.querySelectorAll('[data-streaming-response-status]').length;
  r.copyActionCount = document.querySelectorAll('button[data-testid="copy-turn-action-button"], button[aria-label*="Copy response" i]').length;
''',
    "diag turn selectors",
)

sub_once(
    r'''  var sb = document\.querySelector\('button\[data-testid="send-button"\]'\);''',
    '''  var sb = document.querySelector('button[data-testid="send-button"]') ||
    document.querySelector('#composer-submit-button') ||
    document.querySelector('form[data-chatgpt-composer] button[type="submit"]') ||
    document.querySelector('button[aria-label="Send prompt"]') ||
    document.querySelector('button[aria-label="Send message"]') ||
    document.querySelector('button[aria-label="Send"]') ||
    document.querySelector('button.composer-submit-btn');''',
    "diag send button",
)

replace_once(
    "try { r.bodyHead = document.body ? document.body.innerText.slice(0, 400) : ''; } catch(e) {}",
    "try { var bt = document.body ? document.body.innerText : ''; r.bodyHead = bt.slice(0, 400); r.bodyTail = bt.slice(-600); } catch(e) {}",
    "diag body tail",
)

required = [
    'function turnNodes()',
    '.agent-turn',
    'copy-turn-action-button',
    'button[aria-label="Send message"]',
    'section[data-testid^="conversation-turn-"]',
    'send-accepted-by=',
    'pre-turn-count=',
    'agentTurnCount',
    'copyActionCount',
    'r.bodyTail',
]
for marker in required:
    if marker not in text:
        raise SystemExit(f"post-patch verification missing marker: {marker}")
if text.count('function findSendBtn()') != 1:
    raise SystemExit('post-patch verification expected exactly one findSendBtn()')
if text.count('function findStopBtn()') != 1:
    raise SystemExit('post-patch verification expected exactly one findStopBtn()')
if text.count('function assistantComplete()') != 1:
    raise SystemExit('post-patch verification expected exactly one assistantComplete()')

path.write_text(text, encoding="utf-8")
print("patched", path)
