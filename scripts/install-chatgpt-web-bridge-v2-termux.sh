#!/data/data/com.termux/files/usr/bin/bash
set -Eeuo pipefail

ROOT="$HOME/.local/share/aek-chatgpt-web-bridge"
UPSTREAM="$ROOT/upstream"
VENV="$ROOT/.venv"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PIN="v0.9.0"

log(){ printf '\n==> %s\n' "$*"; }
ok(){ printf '[OK] %s\n' "$*"; }

log "ติดตั้ง dependency สำหรับ Android/Termux"
pkg update -y
pkg install -y git python nodejs android-tools curl jq

log "ติดตั้ง chatgpt-web-bridge แบบ pinned"
mkdir -p "$ROOT"
if [ ! -d "$UPSTREAM/.git" ]; then
  git clone https://github.com/QiuweiLiu/chatgpt-web-bridge.git "$UPSTREAM"
fi
git -C "$UPSTREAM" fetch --tags --force
git -C "$UPSTREAM" checkout --detach "$PIN"

log "สร้าง Python environment แยก ไม่ปนกับ OpenCode"
python -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip wheel
"$VENV/bin/python" -m pip install -r "$UPSTREAM/requirements.txt"

log "ติดตั้ง Android wrapper"
install -m 755 "$REPO_ROOT/scripts/aek-chatgpt-web.py" "$ROOT/aek-chatgpt-web.py"
cat > "$PREFIX/bin/aek-gpt" <<EOF
#!/data/data/com.termux/files/usr/bin/bash
exec "$VENV/bin/python" "$ROOT/aek-chatgpt-web.py" "\$@"
EOF
chmod +x "$PREFIX/bin/aek-gpt"

log "ติดตั้ง OpenCode skill"
SKILL_DST="$HOME/.config/opencode/skills/chatgpt-web-android"
mkdir -p "$SKILL_DST"
install -m 644 "$REPO_ROOT/opencode-skill/chatgpt-web-android/SKILL.md" "$SKILL_DST/SKILL.md"

cat > "$ROOT/README.local.txt" <<EOF
AEK ChatGPT Web Bridge v2
Pinned upstream: QiuweiLiu/chatgpt-web-bridge $PIN
CDP: http://127.0.0.1:9222
Command: aek-gpt

First test:
  aek-gpt doctor
EOF

ok "ติดตั้งเสร็จ"
echo
echo "ขั้นแรกเปิด Chrome -> https://chatgpt.com/ และล็อกอินไว้"
echo "จากนั้นให้ adb ของ Termux เชื่อมกับ Android เครื่องนี้ผ่าน Wireless debugging"
echo "แล้วรัน:"
echo "  aek-gpt doctor"
echo
echo "ทดสอบ Temporary Chat:"
echo "  aek-gpt temp"
