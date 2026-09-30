# MCP-AEK

Mobile-first AI workspace bridge for Android + Termux.

The project is designed to pair a ChatGPT-compatible local endpoint running on the phone (for example `chatgpt-free-api-android`) with a Termux agent that can discover MCP tools, call them, feed tool results back to the model, and bind file operations and command working directories to an explicit workspace. Arbitrary executables are not OS-sandboxed.

## Architecture

```text
ChatGPT account
      |
      v
chatgpt-free-api-android
http://127.0.0.1:5656/v1
      |
      v
MCP-AEK Agent (Termux)
      |
      v
MCP-AEK Server
      |
      +-- workspace/file tools
      +-- shell/build tools
      +-- git tools
      +-- binary/reverse-engineering helpers
      +-- artifact/hash helpers
      |
      v
~/mcp-aek/workspaces/<project>
```

The bridge does **not** require Codex/Work as its upstream transport. It speaks OpenAI-style `/v1/chat/completions` to a configurable local endpoint and uses the MCP tool loop on the Termux side.

## MVP goals

- Android/Termux first.
- OpenAI-compatible upstream configurable by URL, key, and model.
- Real MCP v2 server using the official Python SDK.
- Agent loop that translates MCP tool schemas to OpenAI `tools` and executes returned `tool_calls`.
- Workspace-bound file tools and persistent sessions.
- Useful tools for source, build, Git, archives, binaries, Lua/Python/C/C++ workflows.
- Optional raw shell access, disabled unless explicitly enabled.
- No hard dependency on a desktop computer.

## Quick install on Termux

```bash
pkg update -y
pkg install -y git

git clone https://github.com/aekthai01/MCP-AEK.git
cd MCP-AEK
bash scripts/install-termux.sh
```

The installer deliberately installs the Termux Rust toolchain (`rust`, `cargo`, `pkg-config`, OpenSSL/libffi headers) in addition to Python/C build tools. MCP v2 currently depends on Python packages such as `rpds-py` and `pydantic-core`; PyPI does not provide Android wheels for every Termux/Python combination, so these modules may need to compile locally. Without system Rust, `maturin` may try to use rustup with `aarch64-unknown-linux-android` and fail.

If an older installation already failed while building `rpds-py`, do **not** delete the repository or virtual environment. Update and rerun the installer:

```bash
git pull
bash scripts/install-termux.sh
```

The installer reuses `.venv` and continues/repairs the interrupted Python dependency installation.

Then make sure your ChatGPT bridge app is running on the phone and its local API is reachable:

```bash
curl -H 'Authorization: Bearer sk-cgfree-local' \
  http://127.0.0.1:5656/v1/models
```

Copy the environment template:

```bash
cp .env.example .env
```

Start a chat:

```bash
./aek chat
```

Or send one task directly:

```bash
./aek run 'inspect the active workspace, identify the build system, and build the project'
```

## Workspace model

Default root:

```text
~/mcp-aek/workspaces
```

Create a workspace:

```bash
./aek workspace create luas-recovery
./aek workspace use luas-recovery
```

Import files from shared storage after granting Termux storage access:

```bash
termux-setup-storage
cp /sdcard/Download/sample.so ~/mcp-aek/workspaces/luas-recovery/
```

The MCP file tools reject paths outside the active workspace. Raw shell access is separate because a shell can escape any path jail; enable it only when you want that power:

```bash
export AEK_ENABLE_SHELL=1
```

## Included tool families

### Workspace and files

- `workspace_info`
- `list_files`
- `read_text`
- `write_text`
- `replace_text`
- `search_text`
- `file_hash`

### Process/build/reverse helpers

- `tool_status`
- `run_command` (argv form, workspace cwd)
- `shell_exec` (opt-in)
- `binary_inspect`
- `git_status`
- `git_diff`

`run_command` is enough to drive tools installed in Termux such as `python`, `lua`, `luajit`, `clang`, `clang++`, `cmake`, `make`, `ninja`, `readelf`, `objdump`, `nm`, `strings`, `rizin`, `radare2`, `jadx`, or `apktool` when present.

## Configuration

Environment variables are loaded from `.env` when present.

```dotenv
AEK_UPSTREAM_BASE_URL=http://127.0.0.1:5656/v1
AEK_UPSTREAM_API_KEY=sk-cgfree-local
AEK_MODEL=gpt-5-6
AEK_WORKSPACE_ROOT=~/mcp-aek/workspaces
AEK_ACTIVE_WORKSPACE=default
AEK_ENABLE_SHELL=0
AEK_TOOL_TIMEOUT=120
AEK_MAX_TOOL_ROUNDS=24
AEK_CONTEXT_BYTES=60000
```

The model name should be changed to one returned by your local `/v1/models` endpoint.

## Safety model

File operations are restricted to the active workspace after path resolution. `run_command` runs an executable directly with an argument array and workspace cwd, avoiding a shell by default. `shell_exec` is intentionally opt-in because a shell cannot be reliably sandboxed by string filtering.

Session metadata lives under `<workspace>/.aek`. For a normal Git repository whose `.git` is a real directory inside the workspace, MCP-AEK adds `/.aek/` to `.git/info/exclude` without changing the tracked `.gitignore`. It deliberately does not follow `.git` pointer files to external Git metadata, so an untrusted pointer cannot make MCP-AEK write outside the workspace.

This project is intended for code, build, interoperability, debugging, and analysis of files/systems you own or are authorized to inspect.

## Status

MCP-AEK `0.3.0` is the persistent mobile engineering-workspace release candidate. Actual Android/bridge acceptance checks remain separate from Linux CI.

## Persistent mobile chat (0.3)

```bash
./aek ui                     # http://127.0.0.1:8766
./aek ui --port 8766 --open  # optional browser launch
./aek chat                   # resume the current workspace session
./aek chat new "luas"
./aek chat new "web-ui"
./aek chat list
./aek chat use luas          # a full stable ID also works
./aek chat rename "analysis"
./aek chat info
./aek chat summary "User notes: inspect src/main.lua; verify facts with tools"
./aek chat delete web-ui --yes
```

The mobile UI has session and workspace switching, real MCP activity, expandable outputs, copyable code, tool availability, Git/file panels, task status, themes, and sanitized diagnostics. Quick actions only fill the composer. `/clear` starts a new session and keeps the previous one. `run` remains one-shot; it does not append to the current chat.

History is stored in `<workspace>/.aek/sessions.sqlite3`. For browser tasks the sanitized user message and task row are committed atomically before `/api/chat` returns HTTP 202. The original unsanitized prompt is retained only in the live process long enough to execute that accepted task; it is not persisted in SQLite or emitted in UI events. If the process dies before a queued task starts, that task is marked failed after recovery and is never silently replayed from its sanitized stored copy.

Queue-associated messages are presented in logical task order, so a durable physical sequence such as `user A, user B, assistant A, assistant B` is exposed to model history, CLI continuation and UI pagination as `user A, assistant A, user B, assistant B`. Older sessions without task associations keep their legacy sequence ordering. UI history pagination uses a logical cursor while physical SQLite sequence numbers remain stable evidence identifiers.

A final assistant message and task completion are committed together. Recovery also reconciles an interrupted running task that already has a durable final assistant response as completed, rather than incorrectly downgrading it to failed.

`AEK_CONTEXT_BYTES=60000` bounds serialized message context in UTF-8 bytes (not tokens; model/tool-schema overhead is separate). Only recent whole turns are sent. An oversized newest turn fails clearly, preserving disk history. Incomplete tool-call sets after a crash are omitted from the next request, not fabricated or rerun. `chat summary` supplies optional user-maintained notes, explicitly labeled unverified; automatic model summarization is not implemented.

Open the UI in Android Chrome, then use **Add to Home screen** (or **Install**, when offered). The shortcut does not start Termux or the bridge; both must remain running. There is no offline chat cache. The UI reports reconnecting when the backend is unavailable.

Known deferred work for 0.3: real cancellation, streaming Git-clone progress, artifact origin/task attribution, and the proposed workflow engine/shortcuts. Push, tag, release and automatic merge are not exposed in the UI.

See [mobile upgrade notes](docs/MOBILE_UPGRADE.md) for security details, validation boundaries, and exact Termux smoke tests.
