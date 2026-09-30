# Mobile engineering workspace upgrade — v0.3 release candidate

## Implementation and compatibility

The existing `cli → AEKAgent → HTTP completion → MCP stdio server → tools` path remains. The bridge patches and APK build workflow are unchanged. The model still comes from the existing configuration/bridge. Python's HTTP server, SQLite, threading and asyncio provide the local API and persistence; the frontend remains vanilla JavaScript.

A single UI worker serializes agent execution inside one workspace. Each MCP child receives a pinned workspace. A nonblocking workspace execution lock prevents persistent chat execution and session mutations from racing across processes. Tools still run with the Termux user's privileges; workspace path checks are **not** an OS sandbox.

## Persistence and queue semantics

Session history is stored in `<workspace>/.aek/sessions.sqlite3` with `synchronous=FULL`.

For browser submissions:

- the task row and a **sanitized** user message are committed in one SQLite transaction before `/api/chat` returns HTTP 202;
- the original raw prompt exists only in the live backend's in-memory pending queue and is supplied to the model for that accepted task;
- raw secrets are not intentionally persisted in task/message rows or emitted in task/UI events;
- if the backend dies before a queued task starts, recovery marks that stale task failed and does not replay it from SQLite;
- after obtaining the workspace execution lock, a new worker always performs stale-task recovery **before** enqueuing/draining new work, covering startup recovery that was previously skipped because another process temporarily held the lock;
- switching into a workspace also attempts recovery immediately, with the same guaranteed recovery before later execution.

Task-associated history uses logical task ordering. SQLite may physically contain:

```text
user A
user B
assistant A
assistant B
```

when A and B were acknowledged before A completed. Model history, CLI continuation and browser history expose the logical conversation as:

```text
user A
assistant A
user B
assistant B
```

Legacy messages without task associations retain their original physical sequence ordering. `/api/sessions/:id/messages` returns a logical pagination cursor in each row; the UI uses that cursor rather than assuming physical `seq` alone describes conversation order.

A task's final assistant message and its `completed` state are persisted in the same SQLite transaction. Recovery also reconciles a stale `running` task that already has a durable final assistant response as completed. A late worker error cannot downgrade a transactionally completed task to failed.

## Git metadata safety

MCP-AEK keeps `.aek` out of `git status` without modifying tracked `.gitignore` files. Automatic local exclusion is performed only when `<workspace>/.git` is a real directory inside the workspace. `.git` pointer files are deliberately not followed, because an untrusted absolute or relative `gitdir:` target could otherwise cause writes outside the workspace boundary.

The file/Git UI continues to reject `.git`, `.aek`, traversal paths and symlink escapes. Git stage/unstage supports tracked deleted files.

## API and live events

- `GET /api/status`: workspace, session, model, busy state and local request token.
- `GET /api/workspaces`; workspace create/select/rename/archive/delete and synchronous public HTTPS clone.
- `GET /api/sessions`; create/use/rename/delete/clear workspace history.
- `GET /api/sessions/:id/messages?before=<logical-cursor>&limit=50`: logical chronological page.
- `POST /api/chat`: HTTP 202 with running/queued task ID; queueing remains available while the backend worker is busy.
- `GET /api/tasks`: newest persisted task records including status, workspace/session, timestamps and duration.
- `GET /api/events`: SSE with bounded replay.
- file explorer/edit/preview, Git cockpit, artifacts, tools, diagnostics and model selection routes remain available as implemented.

Every POST requires JSON plus the per-process `X-AEK-Token`, and same-origin/Host checks remain enforced. Static assets are served from an allowlist. Browser message rendering uses text nodes and a deliberately small Markdown subset rather than raw HTML.

The frontend keeps the composer available while backend work is running so another task can be queued. Separately, an in-flight `/api/chat` submit is guarded: double tap/Enter cannot enqueue the same prompt twice before that POST is acknowledged.

## Security notes

Configured upstream keys, structured token/cookie/password fields and recognizable credential strings are redacted from stored messages, task metadata, displayed events and diagnostics. This remains a bounded redaction layer, not a general-purpose secret detector. Do not publish `.aek/sessions.sqlite3` or intentionally ask tools to dump secrets.

`run_command`, compilers, Python, Git and other executables can access anything allowed to the Termux UID. `AEK_ENABLE_SHELL=0` disables `shell_exec` only; it is not a process sandbox.

## v0.3 package version

The release-candidate package metadata and `mcp_aek.__version__` are `0.3.0`.

## Known limitations / deferred work

The following remain explicitly deferred and must not be represented as implemented:

- **Real cancel:** no guaranteed process-group cancellation/rollback of running tools.
- **Streaming clone progress:** Git clone is synchronous and surfaces final success/failure; partial targets are cleaned on timeout/exception/non-zero failure.
- **Artifact origin:** artifacts are discovered by bounded extension scan; originating task/session attribution is not recorded.
- **Workflow engine:** proposed engineering workflow shortcuts/orchestration are not implemented.
- Model responses are not token-streamed.
- Push, tag, release and automatic merge are not exposed by this UI.

## Validation boundaries

Linux automated tests use deterministic local upstreams and real local HTTP/SQLite behavior. Tests that exercise the normal agent path use the installed MCP dependency. They do not prove Android WebView/bridge behavior, PWA installation, mobile keyboard layout, Android background survival, or Termux Python 3.14 behavior.

The earlier **23-test** result belonged to the v0.2-era tree and is historical. It is **not** current v0.3 HEAD evidence. The v0.3 release candidate must be validated from its actual HEAD with:

```bash
python -m unittest discover -s tests -v
python -m compileall -q mcp_aek tests
node --check mcp_aek/static/app.js
git diff --check
```

The exact current GitHub Actions run ID, test count and result should be taken from PR #1 after the release-candidate commit triggers a fresh run; do not infer success from the earlier `action_required` run that produced zero jobs.

## Termux acceptance tests still required

After CI passes, update the feature branch on the phone and reinstall into the existing virtual environment:

```bash
cd ~/MCP-AEK
git fetch origin
git switch feat/persistent-mobile-chat
git pull --ff-only origin feat/persistent-mobile-chat
.venv/bin/pip install -e .
./aek doctor
./aek run "ตอบ OK เท่านั้น"
./aek run "เรียก workspace_info แล้วบอกชื่อ active workspace เท่านั้น"
./aek chat
./aek ui
```

On the real Android/Termux device verify persistent session restart, multiple-session isolation, browser queueing, backend restart without stale task replay, workspace switching, Git cleanliness, deleted-file stage/unstage, themes, file editing, tool activity, diagnostics redaction, keyboard resize, portrait/landscape and Home-screen/PWA behavior. Existing bridge patches should be exercised unchanged against the live bridge.
