# Troubleshooting

## ChatGPT bridge returns `发送按钮不可用，请检查页面验证或输入状态`

This error comes from the Android bridge's hidden ChatGPT WebView, not from MCP-AEK itself. MCP-AEK may still report `upstream_ok: true` because `/v1/models` can succeed while the WebView composer cannot submit a prompt.

### Confirm the condition

```bash
curl -s -H 'Authorization: Bearer sk-cgfree-local' http://127.0.0.1:5656/__diag | jq
curl -s -H 'Authorization: Bearer sk-cgfree-local' http://127.0.0.1:5656/__state | jq
curl -s -H 'Authorization: Bearer sk-cgfree-local' http://127.0.0.1:5656/__log | jq
```

A diagnostic state like this confirms the bridge UI path is failing:

- `pageReady: true`
- prompt input found and populated
- `sendBtn: "missing"`
- request log ends with the send-button error

### What it means

The bridge version currently used by MCP-AEK locates the ChatGPT composer using DOM selectors such as `#prompt-textarea` and `button[data-testid="send-button"]`. ChatGPT's current web UI may expose a different submit control or keep the button absent until the composer receives the exact input events React expects.

This is a bridge-side DOM compatibility problem. Changing the MCP-AEK model name or rebuilding Python dependencies will not fix it.

### Temporary checks

Open the bridge app's visible ChatGPT/WebView page and confirm there is no verification/login/session-expired screen. If normal manual chat can be sent in that same WebView but `/v1/chat/completions` still fails with `sendBtn: missing`, the bridge APK needs an updated composer/send implementation.

Do not paste `/__diag` output containing the `session` or `accessToken` into public issues or chats. Treat those values like a password.
