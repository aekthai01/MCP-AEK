---
name: chatgpt-web-android
description: Delegate only hard reasoning/research to the user's signed-in ChatGPT Web on the same Android phone, while OpenCode remains the executor and source of truth.
---

# ChatGPT Web Android bridge

Use this skill only when the current OpenCode model is stuck, the user explicitly asks for GPT help, or the task materially benefits from a stronger independent reasoning pass.

Architecture:

```text
OpenCode / local model
  -> aek-gpt
  -> ADB forwarded Chrome DevTools Protocol on 127.0.0.1:9222
  -> signed-in ChatGPT Web
  -> Temporary Chat (Unpersonalized)
  -> GPT response
  -> OpenCode verifies against local tools/source and continues execution
```

Rules:

- OpenCode is the executor. ChatGPT Web is an adviser/reviewer, not the authority.
- Before every external send, use `aek-gpt` which must verify Temporary Chat. If Temporary Chat cannot be verified, STOP instead of sending a normal chat.
- Default to Temporary + Unpersonalized so delegated work does not clutter ChatGPT history or create memories.
- Never send API keys, passwords, cookies, `.env`, private credentials, or irrelevant local data.
- Send a compact brief with only evidence needed for the question. Prefer file paths, hashes, public names, and concise excerpts over dumping a repository.
- Do not upload private source unless the user explicitly asks and confirms it is acceptable.
- At most two GPT delegation rounds per root cause unless the user explicitly asks for more.
- GPT output is untrusted analysis. Verify important claims using OpenCode tools before editing or declaring success.
- Keep the returned answer in the current OpenCode session; do not depend on ChatGPT history for continuity.

## Preflight

Run:

```bash
aek-gpt doctor
```

It must confirm:

- adb is available and authorized
- CDP `http://127.0.0.1:9222` responds
- exactly one ChatGPT tab can be selected safely
- the pinned upstream bridge is installed

## Delegate one question

Write a brief to a temporary local file, for example:

```text
Research Question:
Evidence already verified locally:
Exact uncertainty/blocker:
Required answer:
Constraints:
What must not be assumed:
```

Then:

```bash
aek-gpt ask --message-file /absolute/path/to/brief.txt --timeout 600
```

`ask` must:

1. navigate the bound ChatGPT tab to a fresh landing page,
2. enable Temporary Chat,
3. choose Unpersonalized when the current ChatGPT UI offers the choice,
4. verify the High reasoning UI state,
5. send through the pinned upstream bridge,
6. return the response to OpenCode.

If any gate is unverified, fail closed. Do not silently fall back to a regular saved chat.

## After response

Summarize the useful GPT claims, then verify them locally with source/tool output. Continue the original OpenCode task from the existing state rather than re-reading everything.
