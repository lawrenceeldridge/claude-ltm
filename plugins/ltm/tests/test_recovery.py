"""Heuristic-fallback recovery: park degraded captures, re-distil them later.

Verifies the shared recovery queue and the replace-on-success path without a live LLM,
by stubbing the distiller. Run: python3 -m unittest discover -s plugins/ltm/tests
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import service  # noqa: E402
from core.config import get_config  # noqa: E402
from core.distill import DistilledFact  # noqa: E402
from core.embedding import HashEmbedding  # noqa: E402
from core.store import Store, _SCHEMA_VERSION  # noqa: E402


class _StubDistiller:
    def __init__(self, records):
        self._records = records

    def distill(self, text, existing):
        return [DistilledFact(**r) for r in self._records]

    def summarize(self, text):
        return None


def _titled():
    return [{"text": "uses gemma3 for distillation", "title": "Distiller", "type": "feature", "degraded": False}]


def _degraded():
    return [{"text": "some raw line", "type": "discovery", "degraded": True}]


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["LTM_DATA_DIR"] = self.tmp.name
        os.environ["LTM_DISTILLER"] = "ollama"  # an LLM distiller, so recovery is active
        self.cfg = get_config()
        self.store = Store(self.cfg.db_path)
        self.embedder = HashEmbedding(dim=self.cfg.dim)
        self.project = {"key": "p", "path": "/tmp/p", "label": "p"}

    def tearDown(self):
        self.store.close()
        for k in ("LTM_DATA_DIR", "LTM_DISTILLER"):
            os.environ.pop(k, None)
        self.tmp.cleanup()

    def test_migration_created_queue(self):
        self.assertEqual(self.store.db.execute("PRAGMA user_version").fetchone()[0], _SCHEMA_VERSION)
        names = {r[0] for r in self.store.db.execute("SELECT name FROM sqlite_master")}
        self.assertIn("pending_redistill", names)

    def test_enqueue_list_clear(self):
        self.store.enqueue_redistill("p", "s1", "raw text", ["fid1", "fid2"])
        rows = self.store.list_redistill("p")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["text"], "raw text")
        self.store.clear_redistill(rows[0]["id"])
        self.assertEqual(self.store.list_redistill("p"), [])

    def test_bump_drops_after_max(self):
        self.store.enqueue_redistill("p", "s1", "x", [])
        eid = self.store.list_redistill("p")[0]["id"]
        for _ in range(2):
            self.store.bump_redistill(eid, max_attempts=3)
        self.assertEqual(len(self.store.list_redistill("p")), 1)  # attempts=2 < 3, still queued
        self.store.bump_redistill(eid, max_attempts=3)
        self.assertEqual(self.store.list_redistill("p"), [])  # attempts=3 -> dropped

    def test_delete_facts_keeps_fts_in_sync(self):
        service.add_facts(self.store, self.embedder, self.cfg, self.project, "s1", ["alpha widget deploy"])
        fid = self.store.fact_id("p", "alpha widget deploy")
        self.assertEqual(len(self.store.fts_search("p", "widget")), 1)
        self.store.delete_facts([fid])
        self.assertEqual(self.store.fts_search("p", "widget"), [])
        self.assertIsNone(self.store.get(fid))

    def test_capture_text_enqueues_on_degraded(self):
        with mock.patch.object(service, "get_distiller", return_value=_StubDistiller(_degraded())):
            service.capture_text(self.store, self.embedder, self.cfg, self.project, "s1", "a failing delta")
        self.assertEqual(len(self.store.list_redistill("p")), 1)

    def test_capture_text_does_not_enqueue_titled(self):
        with mock.patch.object(service, "get_distiller", return_value=_StubDistiller(_titled())):
            service.capture_text(self.store, self.embedder, self.cfg, self.project, "s1", "a good delta")
        self.assertEqual(self.store.list_redistill("p"), [])

    def test_recover_replaces_heuristic_with_titled(self):
        # 1) a capture degrades -> heuristic fact + queued entry
        with mock.patch.object(service, "get_distiller", return_value=_StubDistiller(_degraded())):
            service.capture_text(self.store, self.embedder, self.cfg, self.project, "s1", "raw delta text")
        heuristic_id = self.store.fact_id("p", "some raw line")
        self.assertIsNotNone(self.store.get(heuristic_id))
        self.assertEqual(len(self.store.list_redistill("p")), 1)
        # 2) recovery runs with a now-working LLM -> heuristic replaced by the titled fact
        with mock.patch.object(service, "get_distiller", return_value=_StubDistiller(_titled())):
            recovered = service.recover_pending(self.store, self.embedder, self.cfg, self.project)
        self.assertEqual(recovered, 1)
        self.assertIsNone(self.store.get(heuristic_id))  # heuristic fact gone
        self.assertIsNotNone(self.store.get(self.store.fact_id("p", "uses gemma3 for distillation")))
        self.assertEqual(self.store.list_redistill("p"), [])  # queue drained

    def test_recover_bumps_when_still_failing(self):
        self.store.enqueue_redistill("p", "s1", "raw", [])
        with mock.patch.object(service, "get_distiller", return_value=_StubDistiller(_degraded())):
            recovered = service.recover_pending(self.store, self.embedder, self.cfg, self.project)
        self.assertEqual(recovered, 0)
        self.assertEqual(self.store.list_redistill("p")[0]["attempts"], 1)

    def test_recover_noop_for_heuristic_distiller(self):
        os.environ["LTM_DISTILLER"] = "heuristic"
        cfg = get_config()
        self.store.enqueue_redistill("p", "s1", "raw", [])
        self.assertEqual(service.recover_pending(self.store, self.embedder, cfg, self.project), 0)
        self.assertEqual(len(self.store.list_redistill("p")), 1)  # left intact — heuristic never recovers


if __name__ == "__main__":
    unittest.main()
