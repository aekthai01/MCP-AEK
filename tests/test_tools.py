from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp_aek import tools


class ToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(
            os.environ,
            {
                "AEK_WORKSPACE_ROOT": self.tmp.name,
                "AEK_ACTIVE_WORKSPACE": "default",
                "AEK_ENABLE_SHELL": "0",
                "AEK_MAX_FILE_BYTES": "1048576",
            },
            clear=False,
        )
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def test_file_roundtrip_search_replace_and_hash(self) -> None:
        tools.write_text("src/main.lua", "print('hello')\nlocal x = 1\n")
        read = tools.read_text("src/main.lua")
        self.assertIn("print('hello')", read["content"])

        replaced = tools.replace_text("src/main.lua", "local x = 1", "local x = 2")
        self.assertEqual(replaced["replacements"], 1)

        found = tools.search_text("x = 2")
        self.assertEqual(found["results"][0]["path"], "src/main.lua")

        digest = tools.file_hash("src/main.lua")
        self.assertEqual(digest["algorithm"], "sha256")
        self.assertEqual(len(digest["digest"]), 64)

    def test_path_escape_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            tools.read_text("../../outside.txt")

    def test_run_command_uses_workspace_cwd(self) -> None:
        result = tools.run_command(["python", "-c", "import os; print(os.getcwd())"])
        self.assertEqual(result["exit_code"], 0)
        self.assertIn(str(Path(self.tmp.name) / "default"), result["stdout"])

    def test_shell_is_off_by_default(self) -> None:
        with self.assertRaises(PermissionError):
            tools.shell_exec("echo unsafe")


if __name__ == "__main__":
    unittest.main()
