from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .agent import AEKAgent
from .config import Settings, list_workspaces, set_active_workspace, clone_workspace
from .sessions import SessionStore, sanitize


async def _cmd_run(prompt: str) -> int:
    agent = AEKAgent()
    try:
        answer, _ = await agent.run(prompt)
        print(answer)
        return 0
    finally:
        await agent.close()


async def _cmd_chat() -> int:
    agent = AEKAgent()
    store = SessionStore(agent.settings.workspace, (agent.settings.upstream_api_key,))
    sid = store.current()["id"]
    print(f"MCP-AEK | workspace={agent.settings.active_workspace} | model={agent.settings.model}")
    print("Type /exit to quit, /doctor for diagnostics, /clear to start a new session (old history is retained).")
    try:
        while True:
            try:
                prompt = input("\nYou> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if not prompt:
                continue
            if prompt in {"/exit", "/quit"}:
                return 0
            if prompt == "/clear":
                with store.execution_lock():
                    sid = store.create()["id"]
                print("new session created; previous history retained")
                continue
            if prompt == "/doctor":
                print(json.dumps(await agent.doctor(), ensure_ascii=False, indent=2))
                continue
            try:
                with store.execution_lock():
                    history = store.history(sid, agent.settings.context_bytes)
                    answer, _ = await agent.run(prompt, history=history, store=store, session_id=sid)
                print(f"\nAEK> {answer}")
            except Exception as exc:
                print(sanitize(f"\nERROR: {type(exc).__name__}: {exc}", (agent.settings.upstream_api_key,)), file=sys.stderr)
    finally:
        await agent.close()


async def _cmd_doctor() -> int:
    agent = AEKAgent()
    try:
        result = await agent.doctor()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("upstream_ok") and result.get("mcp_ok") else 1
    finally:
        await agent.close()


def _workspace_command(args: argparse.Namespace) -> int:
    settings = Settings.load()
    if args.workspace_action == "list":
        for name in list_workspaces():
            marker = "*" if name == settings.active_workspace else " "
            print(f"{marker} {name}")
        return 0
    if args.workspace_action == "create":
        path = set_active_workspace(args.name)
        print(f"created and activated: {path}")
        return 0
    if args.workspace_action == "use":
        path = set_active_workspace(args.name)
        print(f"active workspace: {path}")
        return 0
    if args.workspace_action == "info":
        s = Settings.load()
        print(json.dumps({
            "active_workspace": s.active_workspace,
            "workspace": str(s.workspace),
            "workspace_root": str(s.workspace_root),
            "shell_enabled": s.enable_shell,
        }, ensure_ascii=False, indent=2))
        return 0
    if args.workspace_action == "clone":
        print(f"cloned: {clone_workspace(args.url, args.name)}")
        return 0
    raise RuntimeError("unknown workspace action")


def _serve(http: bool, host: str, port: int) -> int:
    from .server import mcp

    if http:
        mcp.run(transport="streamable-http", host=host, port=port)
    else:
        mcp.run()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aek", description="MCP-AEK mobile engineering agent")
    sub = parser.add_subparsers(dest="command", required=True)

    chat = sub.add_parser("chat", help="persistent workspace chat and sessions")
    chat.add_argument("action", nargs="?", choices=["new", "list", "use", "rename", "info", "delete", "summary", "clear-all"])
    chat.add_argument("value", nargs="?")
    chat.add_argument("--yes", action="store_true", help="confirm session deletion")
    ui = sub.add_parser("ui", help="start the local mobile chat UI")
    ui.add_argument("--port", type=int, default=8766)
    ui.add_argument("--open", action="store_true")

    run_p = sub.add_parser("run", help="execute one task and print the final answer")
    run_p.add_argument("prompt")

    sub.add_parser("doctor", help="check ChatGPT bridge and MCP tool server")

    ws = sub.add_parser("workspace", help="manage isolated project workspaces")
    ws_sub = ws.add_subparsers(dest="workspace_action", required=True)
    ws_sub.add_parser("list")
    ws_sub.add_parser("info")
    ws_create = ws_sub.add_parser("create")
    ws_create.add_argument("name")
    ws_use = ws_sub.add_parser("use")
    ws_use.add_argument("name")
    ws_clone = ws_sub.add_parser("clone")
    ws_clone.add_argument("url")
    ws_clone.add_argument("name")

    serve = sub.add_parser("serve", help="run the MCP server manually")
    serve.add_argument("--http", action="store_true")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)

    return parser


def _session_command(args):
    settings = Settings.load()
    store = SessionStore(settings.workspace, (settings.upstream_api_key,))
    if args.action in ('use', 'rename', 'summary', 'delete') and not args.value:
        print(f'chat {args.action} requires a value', file=sys.stderr)
        return 2
    if args.action in ('delete', 'clear-all') and not args.yes:
        print('Deletion requires --yes; history will be permanently removed.', file=sys.stderr)
        return 2
    if args.action == 'list':
        active = store.current_id()
        for item in store.list():
            print(f"{'*' if item['id'] == active else ' '} {item['id']}  {item['title']}  {item['message_count']} messages  {item['updated_at']}")
        return 0
    if args.action == 'info' and store.current_id():
        print(json.dumps(store.current(), ensure_ascii=False, indent=2))
        return 0
    with store.execution_lock():
        if args.action == 'new':
            result = store.create(args.value or 'New session')
        elif args.action == 'info':
            result = store.current()
        elif args.action == 'use':
            result = store.use(args.value)
        elif args.action == 'rename':
            result = store.rename(store.current()['id'], args.value)
        elif args.action == 'summary':
            store.set_summary(store.current()['id'], args.value)
            result = store.current()
        elif args.action == 'delete':
            deleted = store.resolve(args.value)
            store.delete(deleted)
            result = {'deleted': deleted}
        elif args.action == 'clear-all':
            result = store.clear_all()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "ui":
        from .web import serve
        serve(args.port, args.open)
        return
    if args.command == "chat" and args.action:
        raise SystemExit(_session_command(args))
    if args.command == "chat":
        raise SystemExit(asyncio.run(_cmd_chat()))
    if args.command == "run":
        raise SystemExit(asyncio.run(_cmd_run(args.prompt)))
    if args.command == "doctor":
        raise SystemExit(asyncio.run(_cmd_doctor()))
    if args.command == "workspace":
        raise SystemExit(_workspace_command(args))
    if args.command == "serve":
        raise SystemExit(_serve(args.http, args.host, args.port))
    raise SystemExit(2)


if __name__ == "__main__":
    main()
