# MCP-AEK implementation plan

## Phase 1 — Mobile MVP (current)

Goal: one Android phone can use a ChatGPT web-session bridge as the model transport while Termux provides real workspace tools.

- [x] Python package and Termux installer
- [x] Persistent active workspace
- [x] Official MCP Python SDK v2 server
- [x] File/search/hash tools
- [x] Direct process runner
- [x] Opt-in raw shell
- [x] Git status/diff tools
- [x] ELF/.so inspection helper
- [x] Chat-completions ↔ MCP tool-call loop
- [x] Interactive CLI and one-shot tasks
- [x] Basic CI/tests
- [ ] Verify against a real `chatgpt-free-api-android` device
- [ ] Add streaming display to CLI

## Phase 2 — Reverse/build toolbox

Keep orchestration generic and add capability only where a dedicated tool is materially better than `run_command`.

- Lua/LUAS inventory and batch source-materialization helpers
- ELF symbol/section/call-reference reports
- Rizin/radare2 structured wrappers when installed
- APK/Dex helpers (jadx/apktool when installed)
- Build presets for C/C++/CMake/Ninja/Make/Python/Lua
- Artifact manifest with SHA-256 and provenance
- Large-file chunk/range readers

## Phase 3 — Long-running workspace agent

- Durable task journal per workspace
- Resume interrupted build/analysis tasks
- Approval policy for destructive operations
- Multiple MCP servers and dynamic tool discovery
- Optional GitHub MCP/connector bridge
- Per-workspace tool policy

## Design rules

1. The model is not trusted as evidence. Tool output is evidence.
2. File tools stay inside the active workspace after path resolution.
3. Raw shell is explicit because no string filter can turn a shell into a real sandbox.
4. Reverse-engineering helpers should preserve raw artifacts and label inference separately from recovered facts.
5. Do not bind the architecture to one model or one ChatGPT bridge. The upstream base URL is configuration.
6. Keep the mobile setup usable without a desktop computer.
