# Bridge patch plan

Observed on 2026-09-28 with chatgpt-free-api-android build 27eae4c:

- hidden WebView is ready at https://chatgpt.com/
- session endpoint returns HTTP 200
- composer is found and text is inserted
- current ChatGPT DOM has no `button[data-testid="send-button"]`
- bridge aborts with `发送按钮不可用，请检查页面验证或输入状态`

## Required upstream patch

Update the WebView composer submit logic so it does not depend on one historical selector. The implementation should:

1. Prefer stable submit controls exposed by the current ChatGPT DOM.
2. Fall back to an Enter-key submit path when a usable send button is absent.
3. Dispatch realistic `beforeinput` / `input` / keyboard events so React sees the composer state change.
4. Re-query the submit control after text insertion rather than caching a pre-input node.
5. Keep stop/cancel controls excluded so the bridge cannot accidentally cancel a running response.
6. Extend `__diag` to report all candidate button test ids / aria-labels without exposing tokens.

The MCP-AEK agent itself does not need a model-routing change for this failure.
