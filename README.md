# MCP-AEK

Mobile-first AI workspace bridge for Android + Termux.

The project is designed to pair a ChatGPT-compatible local endpoint running on the phone (for example `chatgpt-free-api-android`) with a Termux agent that can discover MCP tools, call them, feed tool results back to the model, and keep every operation scoped to an explicit workspace.

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
- Workspace isolation by default.
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
```

The model name should be changed to one returned by your local `/v1/models` endpoint.

## Safety model

File operations are restricted to the active workspace after path resolution. `run_command` runs an executable directly with an argument array and workspace cwd, avoiding a shell by default. `shell_exec` is intentionally opt-in because a shell cannot be reliably sandboxed by string filtering.

This project is intended for code, build, interoperability, debugging, and analysis of files/systems you own or are authorized to inspect.

## Status

Initial mobile MVP is being built directly in this repository.
