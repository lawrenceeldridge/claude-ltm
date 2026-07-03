"""MCP server smoke tests (bin/mcp_server.py) — driven as a subprocess over stdio.

The server speaks newline-delimited JSON-RPC 2.0. These assert the handshake surfaces
the always-present memory-first `instructions` and that tools list correctly, without
importing the module (its import runs the bootstrap re-exec).
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _rpc(*requests: dict) -> list[dict]:
    payload = "".join(json.dumps(r) + "\n" for r in requests)
    proc = subprocess.run(
        [sys.executable, str(ROOT / "bin" / "mcp_server.py")],
        input=payload,
        text=True,
        capture_output=True,
        timeout=30,
    )
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


class McpInitializeTests(unittest.TestCase):
    def test_initialize_surfaces_memory_first_instructions(self):
        resp = _rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertTrue(resp, "no response from server")
        result = resp[0]["result"]
        self.assertIn("instructions", result)
        text = result["instructions"]
        self.assertIn("recall", text)
        self.assertIn("search_code", text)
        self.assertIn("FIRST", text)

    def test_tools_list_includes_recall_and_search_code(self):
        resp = _rpc(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        )
        by_id = {r.get("id"): r for r in resp}
        names = {t["name"] for t in by_id[2]["result"]["tools"]}
        self.assertIn("recall", names)
        self.assertIn("search_code", names)


if __name__ == "__main__":
    unittest.main()
