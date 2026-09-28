# Mobile chat upgrade

## Implementation and compatibility

The existing `cli → AEKAgent → HTTP completion → MCP stdio server → tools` path remains.
The bridge patches and APK build workflow are unchanged. The model comes from existing
configuration. No new runtime dependency was introduced: Python's HTTP server, SQLite,
threading and asyncio supply the local API and persistence; the frontend is vanilla JS.
A single worker serializes requests inside a UI backend. Each MCP child receives a
pinned workspace, preventing another CLI process changing its working workspace.
A nonblocking workspace file lock prevents concurrent persistent chat execution and
session mutations across processes. Tools still run with the Termux user's privileges.

The API only binds `127.0.0.1`. Static paths are an explicit allowlist. JSON POSTs are
size-limited, require a fresh per-process request token and enforce same-origin/Host
checks. No CORS allowance is emitted. Browser output uses text nodes and a deliberately
small Markdown subset (headings, bold, inline code, fenced code); raw HTML, remote
images, tables and link parsing are not supported. CSP excludes inline scripts and
framing. Session directories use mode 0700, databases 0600, and session storage rejects
symlinks. Workspace symlinks and file traversal are rejected; recursive source search
skips symlink files.

Configured upstream keys, structured token/cookie/password fields and recognizable
credential strings are redacted from stored messages, displayed events and diagnostics.
This is not a general-purpose secret detector: do not paste unlabelled credentials or
ask tools to dump credentials. Redaction takes precedence over byte-exact history.
Do not publish the session database. Arbitrary executable tools (`python`, compilers,
`git`, etc.) can access anything permitted to the Termux UID; workspace cwd/path checks
are **not an OS sandbox**. Disabling `shell_exec` disables that tool only, not equivalent
capabilities of other executables. Run only trusted tasks on files you are authorized
to inspect. A local same-user malicious process is outside this security boundary.

## API and live events

- `GET /api/status`: workspace, session, busy state and local request token.
- `GET /api/workspaces`; `POST /api/workspaces` with `name`: create/select workspace.
- `GET /api/sessions`; `POST /api/sessions` with optional `title`: create session.
- `POST /api/sessions/use` with `session_id`.
- `POST /api/sessions/rename` with `title` (current session).
- `POST /api/sessions/summary` with `summary` (current session, max 8000 bytes).
- `POST /api/sessions/delete` with `session_id` and `confirm: true`.
- `GET /api/sessions/:id/messages?before=<seq>&limit=50`: chronological page.
- `POST /api/chat` with `prompt` and current `session_id`: 202, request ID.
- `GET /api/events`: SSE with IDs, Last-Event-ID replay and heartbeat.
- `GET /api/tools`, `GET /api/diagnostics`: actual runtime checks, unavailable during tasks.

Every POST requires `Content-Type: application/json` and `X-AEK-Token` from status.
Errors have shape `{"error":{"message":"..."}}`.
Events: `request_started`, `model_started`, `model_completed`, `tool_started`,
`tool_completed`, `tool_failed`, `final_answer`, `error`, `request_finished`.
Tool duration uses a monotonic clock. Events come from execution, not a simulated timer.
Replay is bounded to 500 events; reconnect also reloads status and durable messages.
History pages default to 50, maximum 500; frontend keeps at most 150 message elements.
The model response is not token-streamed. Stop/cancel is intentionally absent: the
current process tools cannot guarantee cancellation of child commands. Tool timeout
and upstream HTTP timeout still apply. After a crash, inspect tools/files before retrying;
external side effects are not transactionally rolled back or automatically replayed.

## Upgrade

Install this branch/revision using the existing virtual environment:

```bash
cd ~/MCP-AEK
git fetch origin
git switch feat/persistent-mobile-chat
.venv/bin/pip install -e .
./aek doctor
./aek ui
```

Keep your existing `.env`, workspaces, bridge app and patched APK. Do not overwrite
`.env` with the example. Set `AEK_MODEL` only to a name returned by your bridge.
No migration is required; the first persistent chat creates a new local session.

## Termux acceptance tests still required

1. Start the existing bridge and run `./aek doctor`; verify model and MCP health.
2. Run `./aek run "ตอบ OK เท่านั้น"` and
   `./aek run "เรียก workspace_info แล้วบอกชื่อ active workspace เท่านั้น"`.
3. Run `./aek chat`, send `จำรหัสทดสอบนี้ไว้ ABC-123`, then `/exit`.
   Restart `./aek chat` and ask `รหัสเมื่อกี้คืออะไร`.
4. Create `./aek chat new luas` and `./aek chat new web-ui`; list and switch using
   `./aek chat use luas`. Verify history isolation and `/clear` retains the old session.
5. Run `./aek ui`; open `http://127.0.0.1:8766` in Chrome. Ask for `workspace_info`.
   Observe actual tool start/completion and final output. Check Tools and Diagnostics.
6. While running a task, verify session/workspace changes are blocked. Reload the page;
   verify current activity/history recover. Stop/restart the backend and verify history.
7. On the actual phone check keyboard resize, scrolling, copy buttons, long code blocks,
   portrait/landscape, Add to Home screen and standalone launch. Background Termux
   survival depends on Android battery restrictions and cannot be promised by this app.

## Validation boundaries

Automated tests use a real MCP subprocess and real local HTTP requests. The upstream
model is deterministic test code or a local HTTP fixture; they do not prove live
ChatGPT/WebView behavior. Process-exit tests prove SQLite commit/rollback and history
restoration. There is no Android device or real bridge in the Linux validation environment.
Chromium was unavailable and its download failed, so rendered screenshot/mobile keyboard
validation was not performed. Python 3.14 on Termux and PWA install behavior need the
manual checks above. Existing CI runs Python 3.12.

## Executed validation (Linux, Python 3.12)

- `python -m unittest discover -s tests -v`: **23 tests passed**, 21.903 seconds.
- `python -m compileall -q mcp_aek tests`: passed.
- `node --check mcp_aek/static/app.js`: passed.
- MCP server/package imports and CLI parser checks: passed.
- Real `./aek ui --port 8877` subprocess + `curl --noproxy '*' --fail` against
  `/api/status`: HTTP success, correct workspace/session/model/busy state.
- Wheel build: succeeded; inspected archive contains all seven static assets.
- `git diff --check`: passed. Existing bridge patch/workflow diff: empty.
- The negative tool traversal test intentionally emits an MCP exception on stderr;
  it verifies `tool_failed`, durable error output, and normalized call IDs.
