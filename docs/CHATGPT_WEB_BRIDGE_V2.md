# ChatGPT Web Bridge V2 for MCP-AEK

This replaces the old patched `chatgpt-free-api-android` WebView bridge path.

## Why replace the old bridge

The old design patched `WebViewChatEngine.kt` with ChatGPT DOM selectors and translated ChatGPT Web into an OpenAI-style `/v1/chat/completions` endpoint. That made every ChatGPT UI DOM change a potential API outage and forced repeated selector patches.

V2 does not patch an APK and does not pretend ChatGPT Web is an OpenAI API.

Instead:

```text
OpenCode (Termux)
  -> OpenCode skill / aek-gpt CLI
  -> Chrome DevTools Protocol forwarded by adb
  -> normal signed-in Chrome ChatGPT Web tab
  -> Temporary Chat
  -> response returned to OpenCode
```

OpenCode keeps the durable work history. ChatGPT delegation is intentionally disposable.

## Chat history policy

V2 defaults to **Temporary Chat + Unpersonalized**. If the UI cannot be positively verified as temporary, it refuses to send. This prevents delegated agent prompts from filling the normal ChatGPT sidebar.

OpenAI currently documents that unsaved Temporary Chats do not appear in history and do not create/update memories. A safety copy may still be retained for up to 30 days.

## Android transport

Chrome DevTools MCP documents Android support through:

```bash
adb forward tcp:9222 localabstract:chrome_devtools_remote
```

On this phone, Termux is the host side. `aek-gpt doctor` only performs the forward after `adb devices` reports one authorized device. It never guesses an unauthorized target.

## Install

From this repository branch:

```bash
bash scripts/install-chatgpt-web-bridge-v2-termux.sh
```

Then open Chrome, sign into ChatGPT, keep one ChatGPT tab open, and run:

```bash
aek-gpt doctor
```

If adb is not connected, enable Android Developer options -> Wireless debugging and pair/connect the Termux adb client first.

## Commands

```bash
aek-gpt doctor
# read-only environment/CDP/upstream bridge diagnostics

aek-gpt temp
# navigate to ChatGPT landing and enable Temporary Chat without sending a prompt

aek-gpt inspect
# upstream read-only ChatGPT page inspection

aek-gpt ask --message-file /absolute/path/brief.txt --timeout 600
# Temporary Chat preflight + guarded GPT send
```

## What is intentionally not done

- no ChatGPT cookies/tokens are extracted or stored
- no OpenAI API key is needed
- no Codex backend is used
- no ChatGPT conversation is used as the durable project memory
- no regular chat is sent when Temporary Chat cannot be verified
- no automatic file upload of source code

## Validation status

Static validation completed before commit:

- Python wrapper compiles with `python -m py_compile`
- installer passes `bash -n`

Runtime Android validation is still required because this branch depends on the phone's current Chrome build, ChatGPT DOM, and ADB wireless-debugging state. The bridge fails closed before sending if Temporary Chat or High reasoning cannot be verified.
