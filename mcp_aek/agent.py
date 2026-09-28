from __future__ import annotations

import asyncio
import time
import json
import os
import sys
from urllib.parse import urlsplit
from typing import Any

import httpx
from mcp import Client, StdioServerParameters

from .config import Settings
from .sessions import context_messages, sanitize


SYSTEM_PROMPT = """You are MCP-AEK, an engineering agent operating on a real Termux workspace.
Use tools whenever the task depends on files, build output, command output, git state, binaries, or installed software.
Never claim that a file was read, changed, built, decompiled, inspected, or tested unless a real tool result proves it.
Inspect before editing. After functional changes, validate with the narrowest useful command. When a command fails, read its real stderr and repair from evidence.
Stay inside the active workspace for file operations. Prefer run_command over shell_exec. Use shell_exec only when shell syntax is actually necessary and the user enabled it.
For ELF/.so analysis, begin with binary_inspect/tool_status and then use available readelf/objdump/nm/strings/rizin/radare2 tools through run_command as needed.
For Lua/LUAS/Python/SDK reconstruction, preserve evidence, keep generated artifacts in the workspace, and clearly distinguish recovered facts from inference.
Do not stop at a plan when the requested work can be executed with the available tools.
"""


def _tool_result_text(result: Any) -> str:
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        return json.dumps(structured, ensure_ascii=False, default=str)

    chunks: list[str] = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text is not None:
            chunks.append(text)
        else:
            chunks.append(str(block))
    if not chunks:
        chunks.append(json.dumps({"is_error": bool(getattr(result, "is_error", False))}))
    return "\n".join(chunks)


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"tool arguments are not valid JSON: {raw[:500]}") from exc
        if not isinstance(value, dict):
            raise ValueError("tool arguments must decode to an object")
        return value
    raise ValueError(f"unsupported tool arguments type: {type(raw).__name__}")


class AEKAgent:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings.load()
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=20.0), trust_env=urlsplit(self.settings.upstream_base_url).hostname not in {"127.0.0.1", "localhost", "::1"})

    async def close(self) -> None:
        await self._http.aclose()

    def _stdio_server(self) -> StdioServerParameters:
        return StdioServerParameters(
            command=sys.executable,
            args=["-m", "mcp_aek.server"],
            env={**os.environ, "AEK_WORKSPACE_ROOT": str(self.settings.workspace_root), "AEK_PINNED_WORKSPACE": self.settings.active_workspace},
        )

    async def _upstream_models(self) -> list[str]:
        headers = {"Authorization": f"Bearer {self.settings.upstream_api_key}"}
        response = await self._http.get(f"{self.settings.upstream_base_url}/models", headers=headers)
        response.raise_for_status()
        payload = response.json()
        return [str(item.get("id")) for item in payload.get("data", []) if item.get("id")]

    async def doctor(self) -> dict[str, Any]:
        info: dict[str, Any] = {
            "base_url": self.settings.upstream_base_url,
            "configured_model": self.settings.model,
            "workspace": str(self.settings.workspace),
            "shell_enabled": self.settings.enable_shell,
        }
        try:
            models = await self._upstream_models()
            info["upstream_ok"] = True
            info["models"] = models
            info["model_available"] = self.settings.model in models
        except Exception as exc:  # diagnostic command should report, not crash
            info["upstream_ok"] = False
            info["upstream_error"] = f"{type(exc).__name__}: {exc}"

        try:
            async with Client(self._stdio_server()) as client:
                listed = await client.list_tools()
                info["mcp_ok"] = True
                info["mcp_protocol"] = str(client.protocol_version)
                info["mcp_tools"] = [tool.name for tool in listed.tools]
        except Exception as exc:
            info["mcp_ok"] = False
            info["mcp_error"] = f"{type(exc).__name__}: {exc}"
        return sanitize(info, (self.settings.upstream_api_key,))

    async def _completion(self, messages: list[dict[str, Any]], openai_tools: list[dict[str, Any]]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.settings.upstream_api_key}",
            "Content-Type": "application/json; charset=utf-8",
        }
        body: dict[str, Any] = {
            "model": self.settings.model,
            "messages": messages,
            "stream": False,
            "history_and_training_disabled": True,
        }
        if openai_tools:
            body["tools"] = openai_tools
            body["tool_choice"] = "auto"

        response = await self._http.post(
            f"{self.settings.upstream_base_url}/chat/completions",
            headers=headers,
            json=body,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"upstream HTTP {response.status_code}: {response.text[:2000]}")
        payload = response.json()
        choices = payload.get("choices") or []
        if not choices:
            raise RuntimeError(f"upstream returned no choices: {json.dumps(payload, ensure_ascii=False)[:2000]}")
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise RuntimeError("upstream choice has no message object")
        return message

    async def run(self, prompt: str, history: list[dict[str, Any]] | None = None, *, store=None, session_id=None, emit=None, recorded=False, task_id=None) -> tuple[str, list[dict[str, Any]]]:
        messages = list(history or [])
        async def event(kind, **data):
            if emit:
                emit(sanitize({"type": kind, **data}, (self.settings.upstream_api_key,)))

        async def record(message):
            messages.append(message)
            if store:
                await asyncio.to_thread(store.append, session_id, message, task_id)

        if not recorded:
            await record({"role": "user", "content": prompt})
        await event("request_started")

        async with Client(self._stdio_server()) as client:
            listed = await client.list_tools()
            openai_tools: list[dict[str, Any]] = []
            for tool in listed.tools:
                openai_tools.append(
                    {
                        "type": "function",
                        "function": {
                            "name": tool.name,
                            "description": tool.description or tool.title or tool.name,
                            "parameters": tool.input_schema,
                        },
                    }
                )

            for _round in range(self.settings.max_tool_rounds):
                await event("model_started")
                summary = (await asyncio.to_thread(store.info, session_id))["summary"] if store else ""
                request = context_messages(messages, SYSTEM_PROMPT, self.settings.context_bytes, summary)
                assistant = await self._completion(request, openai_tools)
                await event("model_completed")
                content = assistant.get("content") or ""
                tool_calls = assistant.get("tool_calls") or []

                if not tool_calls:
                    await record({"role": "assistant", "content": content})
                    await event("final_answer", content=content)
                    return content, messages

                for index, call in enumerate(tool_calls):
                    call["id"] = str(call.get("id") or f"call_{_round}_{index}")
                await record(
                    {
                        "role": "assistant",
                        "content": content,
                        "tool_calls": tool_calls,
                    }
                )

                for index, call in enumerate(tool_calls):
                    function = call.get("function") or {}
                    name = str(function.get("name") or "")
                    call_id = str(call.get("id") or f"call_{_round}_{index}")
                    started = time.monotonic()
                    failed = False
                    await event("tool_started", name=name, call_id=call_id, arguments=function.get("arguments"))
                    if not name:
                        failed = True
                        tool_text = json.dumps({"error": "tool call missing function name"})
                    else:
                        try:
                            args = _parse_arguments(function.get("arguments"))
                            result = await client.call_tool(name, args)
                            tool_text = _tool_result_text(result)
                            failed = bool(getattr(result, "is_error", False))
                            structured = getattr(result, "structured_content", None)
                            if isinstance(structured, dict):
                                failed = failed or bool(structured.get("exit_code", 0))
                        except Exception as exc:
                            failed = True
                            tool_text = json.dumps(
                                {"error": f"{type(exc).__name__}: {exc}"},
                                ensure_ascii=False,
                            )

                    await event("tool_failed" if failed else "tool_completed", name=name, call_id=call_id, duration_ms=round((time.monotonic()-started)*1000), result=tool_text[:12000])
                    await record(
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "name": name,
                            "content": tool_text,
                        }
                    )

        raise RuntimeError(
            f"tool loop exceeded AEK_MAX_TOOL_ROUNDS={self.settings.max_tool_rounds}; "
            "increase it only if the task legitimately needs more iterations"
        )


async def one_shot(prompt: str) -> str:
    agent = AEKAgent()
    try:
        answer, _ = await agent.run(prompt)
        return answer
    finally:
        await agent.close()
