"""Sensory register — Phase 1: migration, store round-trips, pure should_promote, config inert.

All stdlib, no network. The full recall-isolation test (via recall_structured) lands in
Phase 2 once recording is wired; here we assert the store-level isolation (sensory rows
never appear in the facts/recall query).
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from core import service
from core.config import get_config
from core.domain.sensory import should_promote, summarize_snapshot
from core.ports.embedding import HashEmbedding
from core.store import Store


class SensoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="engram-sensory-")
        self.store = Store(Path(self.tmp) / "m.db")
        self.pk = "proj"

    def tearDown(self):
        self.store.close()

    def test_migration_created_sensory_table(self):
        cols = {r[1] for r in self.store.db.execute("PRAGMA table_info(sensory)")}
        self.assertEqual(
            cols,
            {
                "id",
                "project_key",
                "session_id",
                "url",
                "text",
                "created_at",
                "glance_count",
                "attended",
                "promoted_fact_id",
            },
        )

    def test_add_is_upsert_bumping_glance_count(self):
        a = self.store.add_sensory(self.pk, "s1", "https://ex.com/login", "- heading X")
        b = self.store.add_sensory(self.pk, "s1", "https://ex.com/login", "- heading X v2")
        self.assertEqual(a, b)  # same (session, url) → same row
        rows = self.store.sensory_rows(self.pk)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["glance_count"], 2)
        self.assertEqual(rows[0]["text"], "- heading X v2")  # refreshed on re-glance

    def test_sensory_rows_newest_first(self):
        self.store.add_sensory(self.pk, "s1", "u1", "a", now=1000.0)
        self.store.add_sensory(self.pk, "s1", "u2", "b", now=1001.0)
        self.assertEqual(self.store.sensory_rows(self.pk)[0]["url"], "u2")

    def test_mark_attended(self):
        sid = self.store.add_sensory(self.pk, "s1", "u1", "a")
        self.store.mark_attended(sid, "fact123")
        row = self.store.sensory_rows(self.pk)[0]
        self.assertEqual(row["attended"], 1)
        self.assertEqual(row["promoted_fact_id"], "fact123")

    def test_sweep_ttl_hard_deletes_old(self):
        now = 1_000_000.0
        self.store.add_sensory(self.pk, "s1", "old", "a", now=now - 10_000)
        self.store.add_sensory(self.pk, "s1", "new", "b", now=now)
        deleted = self.store.sweep_sensory(self.pk, capacity=0, ttl_seconds=900, now=now)
        self.assertEqual(deleted, 1)
        self.assertEqual([r["url"] for r in self.store.sensory_rows(self.pk)], ["new"])

    def test_sweep_capacity_keeps_newest(self):
        for i in range(5):
            self.store.add_sensory(self.pk, "s1", f"u{i}", "x", now=1000.0 + i)
        deleted = self.store.sweep_sensory(self.pk, capacity=2, ttl_seconds=0)
        self.assertEqual(deleted, 3)
        self.assertEqual({r["url"] for r in self.store.sensory_rows(self.pk)}, {"u3", "u4"})

    def test_store_level_recall_isolation(self):
        # Sensory lives in its own table; the facts/recall query never touches it.
        self.store.add_sensory(self.pk, "s1", "u1", "secret sensory text")
        self.assertEqual(self.store.active_rows_for_project(self.pk), [])


class ShouldPromoteTests(unittest.TestCase):
    def test_promotes_at_or_above_threshold_when_unattended(self):
        self.assertTrue(should_promote({"glance_count": 2, "attended": 0}, promote_after=2))
        self.assertTrue(should_promote({"glance_count": 5, "attended": 0}, promote_after=2))

    def test_not_below_threshold(self):
        self.assertFalse(should_promote({"glance_count": 1, "attended": 0}, promote_after=2))

    def test_never_repromotes_attended(self):
        self.assertFalse(should_promote({"glance_count": 9, "attended": 1}, promote_after=2))


class SensoryConfigTests(unittest.TestCase):
    def test_defaults_off_and_inert(self):
        # Assert the shipped DEFAULT, isolated from ambient ENGRAM_* env (a dev may have
        # ENGRAM_SENSORY set — it's the recommended config) so this reflects the manifest.
        with mock.patch.dict(os.environ, {}, clear=False):
            for k in ("ENGRAM_SENSORY", "CLAUDE_PLUGIN_OPTION_SENSORY", "CLAUDE_PLUGIN_OPTION_sensory"):
                os.environ.pop(k, None)
            cfg = get_config()
        self.assertFalse(cfg.sensory)
        self.assertEqual(cfg.sensory_promote_after, 2)
        self.assertGreater(cfg.sensory_capacity, 0)


class RecordSensoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="engram-sensory2-")
        self.store = Store(Path(self.tmp) / "m.db")
        self.project = {"key": "p", "path": self.tmp, "label": "p"}

    def tearDown(self):
        self.store.close()

    def test_records_when_on(self):
        cfg = replace(get_config(), sensory=True)
        sid = service.record_sensory(self.store, cfg, self.project, "s1", "https://ex.com", "- heading Login")
        self.assertIsNotNone(sid)
        self.assertEqual(len(self.store.sensory_rows("p")), 1)

    def test_noop_when_off(self):
        cfg = replace(get_config(), sensory=False)
        self.assertIsNone(service.record_sensory(self.store, cfg, self.project, "s1", "https://ex.com", "- heading"))
        self.assertEqual(self.store.sensory_rows("p"), [])

    def test_noop_on_empty_text(self):
        cfg = replace(get_config(), sensory=True)
        self.assertIsNone(service.record_sensory(self.store, cfg, self.project, "s1", "u", "   \n"))
        self.assertEqual(self.store.sensory_rows("p"), [])


class RecallIsolationTests(unittest.TestCase):
    """The gate: sensory content must NEVER appear in recall (verified via recall_structured)."""

    def test_sensory_never_enters_recall(self):
        tmp = tempfile.mkdtemp(prefix="engram-sensory-iso-")
        store = Store(Path(tmp) / "m.db")
        cfg = replace(get_config(), sensory=True, min_sim=-1.0, recall_min_confidence=0.0)
        embedder = HashEmbedding(dim=cfg.dim)
        project = {"key": "p", "path": tmp, "label": "p"}
        # A real fact so recall returns something, plus a sensory snapshot carrying a marker.
        service.add_facts(store, embedder, cfg, project, "s1", ["widget alpha handles login"])
        service.record_sensory(store, cfg, project, "s1", "https://ex.com/login", "SENSORY_MARKER_XYZ heading")
        result = service.recall_structured(store, embedder, cfg, project, "widget login")
        store.close()
        self.assertNotIn("SENSORY_MARKER_XYZ", json.dumps(result))  # sensory absent from recall
        self.assertGreaterEqual(result["matched"], 1)  # the real fact matched; sensory did not


class SummarizeSnapshotTests(unittest.TestCase):
    def test_heading_and_controls_dash_format(self):
        text = '- heading "Sign in" [level=1]\n- textbox "Email"\n- button "Continue"\n- link "Home"'
        out = summarize_snapshot("https://ex.com/login", text)
        self.assertTrue(out.startswith("Viewed https://ex.com/login"))
        self.assertIn('"Sign in"', out)
        self.assertIn("Email", out)
        self.assertIn("Continue", out)

    def test_indented_stub_format(self):
        out = summarize_snapshot("u", '    heading "Overview" level=1\n    button "Export"')
        self.assertIn('"Overview"', out)
        self.assertIn("Export", out)

    def test_empty_when_nothing_nameable(self):
        self.assertEqual(summarize_snapshot("u", "banner\n  generic\n  text: hello"), "")

    def test_capped(self):
        text = "\n".join(f'- button "b{i}"' for i in range(50))
        self.assertLessEqual(len(summarize_snapshot("u", text, max_len=60)), 60)


class ViewerStoreTests(unittest.TestCase):
    """The Store queries backing the viewer's Sensory tab (dropdown count + card delete)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="engram-sensory-viewer-")
        self.store = Store(Path(self.tmp) / "m.db")

    def tearDown(self):
        self.store.close()

    def test_sensory_counts_per_project(self):
        self.store.add_sensory("a", "s", "u1", "x")
        self.store.add_sensory("a", "s", "u2", "y")
        self.store.add_sensory("b", "s", "u1", "z")
        counts = self.store.sensory_counts()
        self.assertEqual(counts.get("a"), 2)
        self.assertEqual(counts.get("b"), 1)

    def test_delete_sensory_removes_one(self):
        sid = self.store.add_sensory("a", "s", "u1", "x")
        self.store.add_sensory("a", "s", "u2", "y")
        self.assertEqual(self.store.delete_sensory(sid), 1)
        rows = self.store.sensory_rows("a")
        self.assertEqual([r["url"] for r in rows], ["u2"])

    def test_delete_sensory_missing_is_noop(self):
        self.assertEqual(self.store.delete_sensory("nope"), 0)
        self.assertEqual(self.store.delete_sensory(""), 0)


class PromoteSensoryTests(unittest.TestCase):
    def _setup(self, promote_after: int = 2):
        tmp = tempfile.mkdtemp(prefix="engram-sensory-promote-")
        store = Store(Path(tmp) / "m.db")
        cfg = replace(
            get_config(),
            sensory=True,
            sensory_promote_after=promote_after,
            min_sim=-1.0,
            recall_min_confidence=0.0,
        )
        return store, cfg, HashEmbedding(dim=cfg.dim), {"key": "p", "path": tmp, "label": "p"}

    def test_promotes_attended_into_stm_and_recall(self):
        store, cfg, embedder, project = self._setup(promote_after=2)
        snap = '- heading "Sign in"\n- button "Continue"'
        store.add_sensory("p", "s1", "https://ex.com/login", snap)  # glance 1
        store.add_sensory("p", "s1", "https://ex.com/login", snap)  # glance 2 → attended
        n = service.promote_sensory(store, embedder, cfg, project)
        self.assertEqual(n, 1)
        row = store.sensory_rows("p")[0]
        self.assertEqual(row["attended"], 1)
        self.assertIsNotNone(row["promoted_fact_id"])
        facts = store.active_rows_for_project("p")
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["tier"], "stm")  # promoted into short-term memory
        result = service.recall_structured(store, embedder, cfg, project, "Sign in login")
        store.close()
        self.assertIn("Sign in", json.dumps(result))  # the promoted fact is now recallable

    def test_below_threshold_not_promoted(self):
        store, cfg, embedder, project = self._setup(promote_after=3)
        store.add_sensory("p", "s1", "u", '- heading "X"')  # glance_count 1 < 3
        n = service.promote_sensory(store, embedder, cfg, project)
        store.close()
        self.assertEqual(n, 0)

    def test_idempotent_no_duplicate(self):
        store, cfg, embedder, project = self._setup(promote_after=1)
        store.add_sensory("p", "s1", "u", '- heading "X"\n- button "Go"')
        first = service.promote_sensory(store, embedder, cfg, project)
        second = service.promote_sensory(store, embedder, cfg, project)
        facts = len(store.active_rows_for_project("p"))
        store.close()
        self.assertEqual((first, second), (1, 0))  # attended row skipped on the second pass
        self.assertEqual(facts, 1)  # no duplicate fact

    def test_noop_when_off(self):
        store, cfg, embedder, project = self._setup(promote_after=1)
        store.add_sensory("p", "s1", "u", '- heading "X"\n- button "Go"')
        n = service.promote_sensory(store, embedder, replace(cfg, sensory=False), project)
        store.close()
        self.assertEqual(n, 0)

    def test_empty_summary_marked_attended_not_promoted(self):
        store, cfg, embedder, project = self._setup(promote_after=1)
        store.add_sensory("p", "s1", "u", "banner\n  text: hello")  # nothing nameable
        n = service.promote_sensory(store, embedder, cfg, project)
        row = store.sensory_rows("p")[0]
        facts = len(store.active_rows_for_project("p"))
        store.close()
        self.assertEqual((n, facts), (0, 0))
        self.assertEqual(row["attended"], 1)  # marked so it isn't reconsidered every sweep


if __name__ == "__main__":
    unittest.main()
