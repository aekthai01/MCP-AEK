#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo '[1/5] Installing Termux packages...'
pkg install -y git python clang cmake make ninja binutils file ripgrep jq zip unzip tar

# Useful reverse-engineering packages are not available in every Termux repository.
# Install when available, but do not make the whole setup fail if a package is absent.
echo '[2/5] Trying optional reverse-engineering packages...'
pkg install -y rizin 2>/dev/null || true
pkg install -y radare2 2>/dev/null || true
pkg install -y lua54 2>/dev/null || pkg install -y lua 2>/dev/null || true
pkg install -y luajit 2>/dev/null || true

echo '[3/5] Creating Python virtual environment...'
python -m venv .venv
.venv/bin/python -m pip install --upgrade pip setuptools wheel
.venv/bin/pip install -e .

echo '[4/5] Preparing configuration and workspace...'
if [[ ! -f .env ]]; then
  cp .env.example .env
fi
mkdir -p "$HOME/mcp-aek/workspaces/default"
chmod +x "$ROOT/aek" "$ROOT/scripts/install-termux.sh"

echo '[5/5] Local validation...'
.venv/bin/python -m compileall -q mcp_aek
./aek workspace info

echo
echo 'MCP-AEK installed.'
echo 'Next:'
echo '  1) Start chatgpt-free-api-android and enable its local API.'
echo '  2) Edit .env if your API key/model differs.'
echo '  3) Run: ./aek doctor'
echo '  4) Run: ./aek chat'
