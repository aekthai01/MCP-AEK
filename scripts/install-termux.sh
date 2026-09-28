#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo '[1/6] Installing Termux packages...'
# MCP v2 pulls Python packages such as rpds-py / pydantic-core (and, through
# crypto extras, cryptography). PyPI does not currently ship Android wheels
# for every one of these packages, so Termux must be able to build native
# Rust/C extensions locally. Installing rust up-front also prevents maturin
# from trying rustup with the unsupported aarch64-unknown-linux-android target.
pkg install -y \
  git python clang cmake make ninja binutils file ripgrep jq zip unzip tar \
  rust pkg-config openssl libffi

if ! command -v rustc >/dev/null 2>&1 || ! command -v cargo >/dev/null 2>&1; then
  echo 'ERROR: Rust toolchain is required on Termux but rustc/cargo was not found.' >&2
  echo 'Run: pkg install -y rust' >&2
  exit 1
fi

echo "      $(rustc --version)"
echo "      $(cargo --version)"

# Android/Termux can intermittently return ETXTBSY ("Text file busy") when
# Cargo compiles and immediately executes multiple build scripts in parallel.
# A single Cargo job is slower but reliable for maturin packages such as
# pydantic-core, rpds-py and cryptography. Respect an explicit user override.
export CARGO_BUILD_JOBS="${CARGO_BUILD_JOBS:-1}"
export CARGO_INCREMENTAL="${CARGO_INCREMENTAL:-0}"
echo "      Cargo build jobs: $CARGO_BUILD_JOBS"

# Useful reverse-engineering packages are not available in every Termux repository.
# Install when available, but do not make the whole setup fail if a package is absent.
echo '[2/6] Trying optional reverse-engineering packages...'
pkg install -y rizin 2>/dev/null || true
pkg install -y radare2 2>/dev/null || true
pkg install -y lua54 2>/dev/null || pkg install -y lua 2>/dev/null || true
pkg install -y luajit 2>/dev/null || true

echo '[3/6] Creating/reusing Python virtual environment...'
if [[ ! -x .venv/bin/python ]]; then
  python -m venv .venv
fi
.venv/bin/python -m pip install --upgrade pip setuptools wheel

# A previous failed install can leave an otherwise healthy venv behind. Re-running
# this command is intentional and repairs/continues the dependency installation.
echo '[4/6] Installing MCP-AEK and Python dependencies...'
.venv/bin/pip install -e .

echo '[5/6] Preparing configuration and workspace...'
if [[ ! -f .env ]]; then
  cp .env.example .env
fi
mkdir -p "$HOME/mcp-aek/workspaces/default"
chmod +x "$ROOT/aek" "$ROOT/scripts/install-termux.sh"

echo '[6/6] Local validation...'
.venv/bin/python -m compileall -q mcp_aek
./aek workspace info

echo
echo 'MCP-AEK installed.'
echo 'Next:'
echo '  1) Start chatgpt-free-api-android and enable its local API.'
echo '  2) Edit .env if your API key/model differs.'
echo '  3) Run: ./aek doctor'
echo '  4) Run: ./aek chat'
