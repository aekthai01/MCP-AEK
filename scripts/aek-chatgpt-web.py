#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

CDP_URL = os.environ.get("AEK_CHATGPT_CDP_URL", "http://127.0.0.1:9222").rstrip("/")
ROOT = Path(os.environ.get("AEK_CHATGPT_BRIDGE_HOME", "~/.local/share/aek-chatgpt-web-bridge")).expanduser()
UPSTREAM = ROOT / "upstream"
VENV = ROOT / ".venv"
BRIDGE = UPSTREAM / "skills" / "chatgpt-bridge" / "scripts" / "bridge.py"
PYTHON = VENV / "bin" / "python"
CHATGPT_HOME = "https://chatgpt.com/"

TEMP_BUTTON_RE = re.compile(r"(?:^|\b)(temporary(?:\s+chat)?|ชั่วคราว|แชตชั่วคราว)(?:$|\b)", re.I)


def die(message: str, code: int = 2) -> "NoReturn":
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(code)


def http_json(url: str, timeout: float = 3.0) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "AEK-ChatGPT-Android/2"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def cdp_ready() -> bool:
    try:
        value = http_json(f"{CDP_URL}/json/version")
        return isinstance(value, dict) and bool(value.get("webSocketDebuggerUrl"))
    except Exception:
        return False


def adb_devices() -> list[str]:
    adb = shutil.which("adb")
    if not adb:
        return []
    result = subprocess.run([adb, "devices"], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    devices: list[str] = []
    for line in result.stdout.splitlines()[1:]:
        parts = line.strip().split()
        if len(parts) >= 2 and parts[1] == "device":
            devices.append(parts[0])
    return devices


def ensure_cdp_forward() -> dict[str, Any]:
    if cdp_ready():
        return {"ok": True, "already_ready": True, "cdp_url": CDP_URL}

    adb = shutil.which("adb")
    if not adb:
        die("ไม่พบ adb; ติดตั้งด้วย `pkg install android-tools`")

    devices = adb_devices()
    if not devices:
        die(
            "ADB ยังไม่เชื่อมกับเครื่องนี้ เปิด Developer options > Wireless debugging แล้ว pair/connect adb ก่อน "
            "จากนั้นรัน `aek-gpt doctor` อีกครั้ง"
        )
    if len(devices) > 1:
        die(f"พบ ADB มากกว่า 1 device {devices}; ตั้ง ANDROID_SERIAL ให้ชัดเจนก่อน")

    env = os.environ.copy()
    env.setdefault("ANDROID_SERIAL", devices[0])
    result = subprocess.run(
        [adb, "forward", "tcp:9222", "localabstract:chrome_devtools_remote"],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.returncode != 0:
        die(f"adb forward ล้มเหลว: {result.stdout.strip()}")

    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if cdp_ready():
            return {"ok": True, "already_ready": False, "cdp_url": CDP_URL, "device": devices[0]}
        time.sleep(0.25)

    die(
        "forward สำเร็จแต่ Chrome DevTools ไม่ตอบที่ 127.0.0.1:9222; "
        "เปิด Chrome และตรวจว่า Chrome remote debugging ใช้งานได้"
    )


def page_targets() -> list[dict[str, Any]]:
    value = http_json(f"{CDP_URL}/json")
    if not isinstance(value, list):
        return []
    return [
        item
        for item in value
        if isinstance(item, dict) and item.get("type") == "page" and isinstance(item.get("url"), str)
    ]


def chatgpt_targets() -> list[dict[str, Any]]:
    return [
        item
        for item in page_targets()
        if item.get("url", "").startswith(("https://chatgpt.com/", "https://chat.openai.com/"))
    ]


def choose_chatgpt_target() -> dict[str, Any]:
    targets = chatgpt_targets()
    if not targets:
        die("ไม่พบแท็บ ChatGPT ใน Chrome; เปิด https://chatgpt.com/ และล็อกอินบัญชีของคุณก่อน")
    if len(targets) == 1:
        return targets[0]

    landing = [
        t
        for t in targets
        if t.get("url", "").rstrip("/") in {"https://chatgpt.com", "https://chat.openai.com"}
    ]
    if len(landing) == 1:
        return landing[0]

    urls = [t.get("url") for t in targets]
    die(
        "พบแท็บ ChatGPT หลายแท็บ จึงไม่เดาว่าจะควบคุมอันไหน; ปิดให้เหลือแท็บเดียวก่อน: "
        + json.dumps(urls, ensure_ascii=False)
    )


async def ws_command(
    ws_url: str,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    timeout: float = 10.0,
) -> dict[str, Any]:
    try:
        import websockets
    except ImportError as exc:
        die("Python environment ไม่มี websockets; รัน installer ของ bridge ใหม่")
        raise exc

    message_id = 1
    async with websockets.connect(ws_url, open_timeout=timeout, close_timeout=timeout) as ws:
        await ws.send(json.dumps({"id": message_id, "method": method, "params": params or {}}))
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remain = deadline - asyncio.get_running_loop().time()
            if remain <= 0:
                raise TimeoutError(f"CDP {method} timeout")
            raw = await asyncio.wait_for(ws.recv(), remain)
            event = json.loads(raw)
            if event.get("id") != message_id:
                continue
            if event.get("error"):
                raise RuntimeError(f"CDP {method}: {event['error']}")
            result = event.get("result")
            return result if isinstance(result, dict) else {}


async def runtime_eval(target: dict[str, Any], expression: str) -> Any:
    ws_url = target.get("webSocketDebuggerUrl")
    if not isinstance(ws_url, str) or not ws_url:
        die("ChatGPT target ไม่มี webSocketDebuggerUrl")
    result = await ws_command(
        ws_url,
        "Runtime.evaluate",
        {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True,
            "userGesture": True,
        },
        timeout=12,
    )
    payload = result.get("result") or {}
    if payload.get("subtype") == "error":
        raise RuntimeError(str(payload.get("description") or "Runtime.evaluate error"))
    return payload.get("value")


async def navigate_home(target: dict[str, Any]) -> dict[str, Any]:
    ws_url = target.get("webSocketDebuggerUrl")
    if not isinstance(ws_url, str) or not ws_url:
        die("ChatGPT target ไม่มี webSocketDebuggerUrl")
    await ws_command(ws_url, "Page.navigate", {"url": CHATGPT_HOME}, timeout=12)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        await asyncio.sleep(0.5)
        try:
            state = await runtime_eval(target, "(() => ({href: location.href, ready: document.readyState}))()")
        except Exception:
            continue
        if (
            isinstance(state, dict)
            and str(state.get("href", "")).startswith("https://chatgpt.com/")
            and state.get("ready") in {"interactive", "complete"}
        ):
            return state
    raise TimeoutError("ChatGPT landing page โหลดไม่เสร็จภายใน 30 วินาที")


TEMP_DISCOVER_JS = r'''(() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const label = (el) => `${el.getAttribute('aria-label') || ''} ${(el.innerText || el.textContent || '').trim()}`.trim();
  const controls = Array.from(document.querySelectorAll('button,[role="button"],[role="menuitem"],[role="option"],[role="radio"]'))
    .filter(visible)
    .map((el, i) => ({i, label: label(el), pressed: el.getAttribute('aria-pressed'), checked: el.getAttribute('aria-checked'), selected: el.getAttribute('aria-selected'), state: el.getAttribute('data-state')}));
  return {href: location.href, controls};
})()'''

TEMP_CLICK_JS = r'''(() => {
  const normalize = (v) => String(v || '').trim().toLowerCase().replace(/\s+/g, ' ');
  const visible = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden'; };
  const label = (el) => normalize(`${el.getAttribute('aria-label') || ''} ${(el.innerText || el.textContent || '').trim()}`);
  const all = Array.from(document.querySelectorAll('button,[role="button"]')).filter(visible);
  const candidates = all.filter((el) => {
    const x = label(el);
    return x === 'temporary' || x === 'temporary chat' || x === 'ชั่วคราว' || x.includes('แชตชั่วคราว');
  });
  if (candidates.length !== 1) return {ok:false, stage:'temporary_button', count:candidates.length, labels:candidates.map(label)};
  const el = candidates[0];
  const selected = [el.getAttribute('aria-pressed'), el.getAttribute('aria-checked'), el.getAttribute('aria-selected'), el.getAttribute('data-state')]
    .some((v) => ['true','1','selected','checked','on','active'].includes(String(v || '').toLowerCase()));
  if (!selected) el.click();
  return {ok:true, clicked:!selected, already_selected:selected, label:label(el)};
})()'''

UNPERSONALIZED_CLICK_JS = r'''(() => {
  const normalize = (v) => String(v || '').trim().toLowerCase().replace(/\s+/g, ' ');
  const visible = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden'; };
  const label = (el) => normalize(`${el.getAttribute('aria-label') || ''} ${(el.innerText || el.textContent || '').trim()}`);
  const all = Array.from(document.querySelectorAll('button,[role="button"],[role="menuitem"],[role="option"],[role="radio"]')).filter(visible);
  const exact = all.filter((el) => {
    const x = label(el);
    return x === 'unpersonalized' || x.includes('without personalization') || x.includes('ไม่ปรับเฉพาะบุคคล') || x.includes('ไม่ใช้การปรับเฉพาะบุคคล');
  });
  if (exact.length === 1) { exact[0].click(); return {ok:true, clicked:true, label:label(exact[0])}; }
  return {ok:false, count:exact.length, visible:all.map(label).filter(Boolean).slice(0,80)};
})()'''

HIGH_VERIFY_JS = r'''(() => {
  const visible = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden'; };
  const rows = Array.from(document.querySelectorAll('button,[role="button"],[role="radio"],[role="menuitemradio"],[role="option"]')).filter(visible).map((el) => ({
    text: `${el.getAttribute('aria-label') || ''} ${(el.innerText || el.textContent || '').trim()}`.trim(),
    pressed: el.getAttribute('aria-pressed'), checked: el.getAttribute('aria-checked'), selected: el.getAttribute('aria-selected'), state: el.getAttribute('data-state')
  }));
  const high = rows.filter((r) => /(^|\b)(high|สูง)(\b|$)|reasoning\s*high|คิด(?:ระดับ)?สูง/i.test(r.text));
  const selected = high.filter((r) => [r.pressed,r.checked,r.selected,r.state].some((v) => ['true','1','selected','checked','on','active'].includes(String(v || '').toLowerCase())));
  return {ok: selected.length > 0 || (high.length === 1 && ![high[0].pressed,high[0].checked,high[0].selected].some((v)=>v!==null)), high, selected};
})()'''


async def enable_temporary_unpersonalized() -> dict[str, Any]:
    target = choose_chatgpt_target()
    await navigate_home(target)
    await asyncio.sleep(1.0)

    clicked = await runtime_eval(target, TEMP_CLICK_JS)
    if not isinstance(clicked, dict) or not clicked.get("ok"):
        discover = await runtime_eval(target, TEMP_DISCOVER_JS)
        raise RuntimeError(
            "temporary_chat_unavailable: หา control Temporary แบบยืนยันไม่ได้: "
            + json.dumps({"click": clicked, "discover": discover}, ensure_ascii=False)[:4000]
        )

    await asyncio.sleep(0.8)
    choice = await runtime_eval(target, UNPERSONALIZED_CLICK_JS)
    if isinstance(choice, dict) and choice.get("ok"):
        await asyncio.sleep(0.8)
        return {"temporary": True, "personalization": "unpersonalized", "target_url": target.get("url")}

    state = await runtime_eval(target, TEMP_DISCOVER_JS)
    controls = state.get("controls", []) if isinstance(state, dict) else []
    verified = False
    for row in controls:
        if not isinstance(row, dict):
            continue
        label = str(row.get("label", ""))
        if not TEMP_BUTTON_RE.search(label):
            continue
        values = [row.get("pressed"), row.get("checked"), row.get("selected"), row.get("state")]
        if any(str(v or "").lower() in {"true", "1", "selected", "checked", "on", "active"} for v in values):
            verified = True
            break
    if not verified:
        raise RuntimeError(
            "temporary_chat_unverified: กด Temporary แล้วแต่ UI ไม่ให้หลักฐานว่าเปิดสำเร็จ; "
            "หยุดก่อนส่งเพื่อไม่สร้างแชทปกติให้รก"
        )
    return {"temporary": True, "personalization": "legacy_or_existing", "target_url": target.get("url")}


async def verify_high() -> dict[str, Any]:
    target = choose_chatgpt_target()
    value = await runtime_eval(target, HIGH_VERIFY_JS)
    if not isinstance(value, dict) or not value.get("ok"):
        raise RuntimeError(
            "high_reasoning_unverified: ตั้ง ChatGPT เป็น GPT-5.6 Sol + High ในแท็บ Chrome ก่อน แล้วลองใหม่; "
            + json.dumps(value, ensure_ascii=False)[:2000]
        )
    return value


def bridge_cmd(*args: str) -> list[str]:
    if not PYTHON.is_file():
        die(f"ไม่พบ bridge Python: {PYTHON}; รัน installer ก่อน")
    if not BRIDGE.is_file():
        die(f"ไม่พบ upstream bridge: {BRIDGE}; รัน installer ก่อน")
    return [str(PYTHON), str(BRIDGE), *args, "--cdp-url", CDP_URL]


def run_bridge(args: list[str]) -> int:
    env = os.environ.copy()
    env["CDP_URL"] = CDP_URL
    env.setdefault("CHROME_DEVTOOLS_MCP_PACKAGE", "chrome-devtools-mcp@1.8.0")
    env["CHATGPT_MCP_PYTHON"] = str(PYTHON)
    proc = subprocess.run(args, env=env)
    return proc.returncode


def command_doctor(_: argparse.Namespace) -> int:
    result: dict[str, Any] = {
        "adb": shutil.which("adb"),
        "node": shutil.which("node"),
        "npx": shutil.which("npx"),
        "bridge": str(BRIDGE),
        "bridge_exists": BRIDGE.is_file(),
        "python": str(PYTHON),
        "python_exists": PYTHON.is_file(),
    }
    result["cdp"] = ensure_cdp_forward()
    result["chatgpt_targets"] = [
        {"title": t.get("title"), "url": t.get("url"), "id": t.get("id")}
        for t in chatgpt_targets()
    ]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if BRIDGE.is_file() and PYTHON.is_file() and result.get("cdp"):
        return run_bridge(bridge_cmd("doctor"))
    return 1


def command_temp(_: argparse.Namespace) -> int:
    ensure_cdp_forward()
    result = asyncio.run(enable_temporary_unpersonalized())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def command_ask(args: argparse.Namespace) -> int:
    ensure_cdp_forward()
    if not args.message_file.is_file():
        die(f"ไม่พบไฟล์ brief: {args.message_file}")

    temporary = asyncio.run(enable_temporary_unpersonalized())
    high = asyncio.run(verify_high())
    print(json.dumps({"preflight": "ok", "temporary": temporary, "high": high}, ensure_ascii=False), file=sys.stderr)

    cmd = bridge_cmd(
        "send",
        "--conversation-url",
        CHATGPT_HOME,
        "--new-conversation",
        "--message-file",
        str(args.message_file.resolve()),
        "--timeout",
        str(args.timeout),
        "--verified-high",
        "--confirm-send",
    )
    return run_bridge(cmd)


def command_inspect(_: argparse.Namespace) -> int:
    ensure_cdp_forward()
    return run_bridge(bridge_cmd("inspect"))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Android/Termux ChatGPT Web bridge for OpenCode")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="ตรวจ adb/CDP/ChatGPT/upstream bridge")
    sub.add_parser("temp", help="เปิด Temporary Chat แบบ Unpersonalized และยืนยันก่อนส่ง")
    sub.add_parser("inspect", help="เรียก upstream read-only inspect")
    ask = sub.add_parser("ask", help="ส่ง brief ผ่าน Temporary Chat; ไม่สร้าง history ปกติ")
    ask.add_argument("--message-file", type=Path, required=True)
    ask.add_argument("--timeout", type=int, default=600)
    return p


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "doctor":
        return command_doctor(args)
    if args.command == "temp":
        return command_temp(args)
    if args.command == "inspect":
        return command_inspect(args)
    if args.command == "ask":
        return command_ask(args)
    die(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
