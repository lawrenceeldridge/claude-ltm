"""Usage ledger — the two-sided token accounting behind `ltm stats`.

Cost side: injections recorded by recall_prompt_block / recall_core_block. Saving side
aggregation via usage_stats. Stdlib unittest, hash embedder, no network.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import service  # noqa: E402
from core.config import get_config  # noqa: E402
from core.ports.embedding import HashEmbedding  # noqa: E402
from core.store import Store  # noqa: E402


class UsageLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["LTM_DATA_DIR"] = self.tmp.name
        self.cfg = replace(get_config(), distiller="heuristic", min_sim=-1.0)  # always inject a match
        self.store = Store(self.cfg.db_path)
        self.embedder = HashEmbedding(dim=self.cfg.dim)
        self.project = {"key": "p", "path": "/tmp/p", "label": "p"}

    def tearDown(self):
        self.store.close()
        os.environ.pop("LTM_DATA_DIR", None)
        self.tmp.cleanup()

    def _add(self, text: str) -> None:
        service.add_facts(self.store, self.embedder, self.cfg, self.project, "s", [text])

    def test_record_and_aggregate(self):
        self.store.record_usage("p", "inject_prompt", bytes_in=120)
        self.store.record_usage("p", "inject_prompt", bytes_in=80)
        self.store.record_usage("p", "pull_symbol", bytes_saved=500)
        s = self.store.usage_stats("p")
        self.assertEqual(s["inject_prompt"], {"n": 2, "bytes_in": 200, "bytes_saved": 0})
        self.assertEqual(s["pull_symbol"]["bytes_saved"], 500)

    def test_recall_prompt_block_records_injection_cost(self):
        self._add("the deploy target is fly.io")
        block = service.recall_prompt_block(self.store, self.embedder, self.cfg, self.project, "deploy target")
        self.assertTrue(block)
        entry = self.store.usage_stats("p")["inject_prompt"]
        self.assertEqual(entry["n"], 1)
        self.assertEqual(entry["bytes_in"], len(block))

    def test_recall_core_block_records_injection_cost(self):
        self._add("fact one")
        block = service.recall_core_block(self.store, self.cfg, self.project)
        self.assertTrue(block)
        self.assertEqual(self.store.usage_stats("p")["inject_core"]["n"], 1)

    def test_empty_block_records_nothing(self):
        block = service.recall_prompt_block(self.store, self.embedder, self.cfg, self.project, "nothing stored")
        self.assertEqual(block, "")  # empty store → Null Object → no cost row
        self.assertEqual(self.store.usage_stats("p"), {})


if __name__ == "__main__":
    unittest.main()
