from __future__ import annotations

import argparse

from mcp.server import MCPServer

from . import tools


mcp = MCPServer(
    "MCP-AEK",
    instructions=(
        "Operate only on the active MCP-AEK workspace unless the user explicitly enables raw shell access. "
        "Prefer read/list/search before edits, use run_command for build and analysis tools, verify outputs after changes, "
        "and never claim a tool ran unless a real tool result was returned."
    ),
)


@mcp.tool()
def workspace_info() -> dict:
    """Show the active workspace, path, top-level contents, and shell state."""
    return tools.workspace_info()


@mcp.tool()
def list_files(path: str = ".", recursive: bool = False, limit: int = 500) -> dict:
    """List files and directories under a workspace-relative path."""
    return tools.list_files(path, recursive, limit)


@mcp.tool()
def read_text(path: str, start_line: int = 1, end_line: int = 0) -> dict:
    """Read a text file or a selected line range from the active workspace."""
    return tools.read_text(path, start_line, end_line)


@mcp.tool()
def write_text(path: str, content: str, overwrite: bool = True) -> dict:
    """Create or replace a UTF-8 text file inside the active workspace."""
    return tools.write_text(path, content, overwrite)


@mcp.tool()
def replace_text(path: str, old: str, new: str, count: int = 0) -> dict:
    """Replace exact text in a file; count=0 replaces all matches."""
    return tools.replace_text(path, old, new, count)


@mcp.tool()
def search_text(query: str, path: str = ".", regex: bool = False, max_results: int = 200) -> dict:
    """Search source/text files recursively and return matching lines."""
    return tools.search_text(query, path, regex, max_results)


@mcp.tool()
def file_hash(path: str, algorithm: str = "sha256") -> dict:
    """Calculate a hash for a workspace file."""
    return tools.file_hash(path, algorithm)


@mcp.tool()
def tool_status(names: list[str] | None = None) -> dict:
    """Check which Termux/build/reverse-engineering executables are installed."""
    return tools.tool_status(names)


@mcp.tool()
def run_command(argv: list[str], cwd: str = ".", timeout: int = 0) -> dict:
    """Run one executable directly with argv, no shell expansion, in a workspace directory."""
    return tools.run_command(argv, cwd, timeout)


@mcp.tool()
def shell_exec(command: str, cwd: str = ".", timeout: int = 0) -> dict:
    """Run a bash command in a workspace directory; requires AEK_ENABLE_SHELL=1."""
    return tools.shell_exec(command, cwd, timeout)


@mcp.tool()
def binary_inspect(path: str) -> dict:
    """Inspect an ELF/shared object/binary with available file/readelf/nm/strings tools."""
    return tools.binary_inspect(path)


@mcp.tool()
def git_status() -> dict:
    """Get git status for the active workspace."""
    return tools.git_status()


@mcp.tool()
def git_diff(cached: bool = False, path: str = ".") -> dict:
    """Get git diff for a workspace path."""
    return tools.git_diff(cached, path)


def main() -> None:
    parser = argparse.ArgumentParser(description="MCP-AEK tool server")
    parser.add_argument("--http", action="store_true", help="serve Streamable HTTP instead of stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    if args.http:
        mcp.run(transport="streamable-http", host=args.host, port=args.port)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
