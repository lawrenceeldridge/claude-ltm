"""claude-mem source adapter — read a claude-mem SQLite store for a one-way import.

Driven adapter behind the :class:`~core.ports.memory_source.MemorySource` port. It owns
*all* knowledge of claude-mem's schema and maps each row onto engram's
:class:`~core.ports.distill.DistilledFact`, so the importer and core never see the foreign
tables. Stdlib only (``sqlite3`` + ``json``) — no third-party dependency.

Two source tables are read (everything-raw scope):

* ``observations`` — each row's ``facts`` JSON array fans out to **one fact per element**,
  carrying the observation's ``title``/``subtitle``/``narrative``/``type`` and the union of
  ``files_read``+``files_modified``. A row with no usable ``facts`` falls back to a single
  fact built from its title/subtitle (or narrative) so nothing is silently dropped.
* ``session_summaries`` — each non-empty narrative field (``request``, ``investigated``,
  ``learned``, ``completed``, ``next_steps``, ``notes``) becomes one ``type="summary"`` fact.

The DB is opened **read-only** (``mode=ro``) so a live claude-mem is never mutated, and
``available()`` degrades to ``False`` (never raises) when the store is missing or unreadable.
``observation_id`` is stamped (``cm-obs-{id}`` / ``cm-sum-{id}-{field}``) for provenance.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator, Mapping
from pathlib import Path

from core.ports.distill import DistilledFact
from core.ports.memory_source import MemorySource, SourceRecord

_DB_FILENAME = "claude-mem.db"
# Fixed order so a session's summary fields import deterministically.
_SUMMARY_FIELDS = ("request", "investigated", "learned", "completed", "next_steps", "notes")
_EPOCH_MS_THRESHOLD = 1e12  # values above this are milliseconds (claude-mem stores ms)


def resolve_db_path(explicit: str | os.PathLike[str] | None = None) -> Path:
    """Resolve the claude-mem DB path, most-specific source first.

    ``explicit`` argument > ``CLAUDE_MEM_DATA_DIR`` env > ``~/.claude-mem/settings.json``
    (``CLAUDE_MEM_DATA_DIR`` key) > the default ``~/.claude-mem/claude-mem.db``. Never
    reads the empty ``plugins/data`` placebo copy — that is not a source of truth.
    """
    if explicit:
        return Path(explicit).expanduser()
    env_dir = os.environ.get("CLAUDE_MEM_DATA_DIR")
    if env_dir:
        return Path(env_dir).expanduser() / _DB_FILENAME
    home = Path.home() / ".claude-mem"
    try:
        settings = json.loads((home / "settings.json").read_text(encoding="utf-8"))
        cfg_dir = settings.get("CLAUDE_MEM_DATA_DIR")
        if cfg_dir:
            return Path(cfg_dir).expanduser() / _DB_FILENAME
    except (OSError, ValueError):
        pass  # no/broken settings — fall back to the conventional location
    return home / _DB_FILENAME


# ── pure mapping helpers (Functional Core — a row Mapping in, DistilledFacts out) ──


def _json_str_list(raw: object) -> list[str]:
    """Decode a claude-mem JSON string-array column; tolerant — [] on empty/invalid/non-list."""
    if not raw:
        return []
    try:
        val = json.loads(raw)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return []
    if not isinstance(val, list):
        return []
    return [x for x in val if isinstance(x, str)]


def _merge_files(row: Mapping[str, object]) -> list[str]:
    """Union of files_read + files_modified, de-duplicated, order preserved."""
    out: list[str] = []
    seen: set[str] = set()
    for col in ("files_read", "files_modified"):
        for f in _json_str_list(row.get(col)):
            f = f.strip()
            if f and f not in seen:
                seen.add(f)
                out.append(f)
    return out


def _observation_facts(row: Mapping[str, object]) -> Iterator[DistilledFact]:
    """Fan a claude-mem observation out to one DistilledFact per atomic fact string."""
    shared = {
        "title": str(row.get("title") or ""),
        "subtitle": str(row.get("subtitle") or ""),
        "narrative": str(row.get("narrative") or ""),
        "files": _merge_files(row),
        "type": str(row.get("type") or ""),
        "observation_id": f"cm-obs-{row.get('id')}",
    }
    facts = [f.strip() for f in _json_str_list(row.get("facts")) if f.strip()]
    if facts:
        for text in facts:
            yield DistilledFact(text=text, **shared)
        return
    # No usable facts[] — preserve the row via title/subtitle, else narrative.
    fallback = " — ".join(p for p in (shared["title"], shared["subtitle"]) if p) or shared["narrative"].strip()
    if fallback:
        yield DistilledFact(text=fallback, **shared)


def _summary_facts(row: Mapping[str, object]) -> Iterator[DistilledFact]:
    """One DistilledFact per non-empty session-summary field."""
    sid = row.get("id")
    for field in _SUMMARY_FIELDS:
        text = str(row.get(field) or "").strip()
        if text:
            yield DistilledFact(
                text=text,
                type="summary",
                title=field,
                observation_id=f"cm-sum-{sid}-{field}",
            )


def _epoch_seconds(raw: object) -> float | None:
    """Normalise claude-mem's created_at_epoch (milliseconds) to Unix seconds."""
    if raw is None:
        return None
    try:
        v = float(raw)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return None
    return v / 1000.0 if v > _EPOCH_MS_THRESHOLD else v


# ── the adapter (Imperative Shell — read-only SQLite I/O) ──


class ClaudeMemSource(MemorySource):
    """Read a claude-mem SQLite store as an engram import source (read-only)."""

    def __init__(self, db_path: str | os.PathLike[str] | None = None) -> None:
        self._explicit = db_path
        self._path: Path | None = None
        self._conn: sqlite3.Connection | None = None

    @property
    def path(self) -> Path:
        if self._path is None:
            self._path = resolve_db_path(self._explicit)
        return self._path

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=5.0)
            conn.row_factory = sqlite3.Row
            self._conn = conn
        return self._conn

    def available(self) -> bool:
        try:
            if not self.path.is_file() or self.path.stat().st_size == 0:
                return False
            self._connect().execute("SELECT 1 FROM observations LIMIT 1")
            return True
        except Exception:  # missing table, unreadable file, locked — fail soft
            return False

    def project_labels(self) -> list[str]:
        conn = self._connect()
        seen: dict[str, None] = {}  # ordered set
        for table in ("observations", "session_summaries"):
            for row in conn.execute(
                f"SELECT DISTINCT project FROM {table} WHERE project IS NOT NULL AND project != ''"
            ):
                seen[row["project"]] = None
        return list(seen)

    def iter_records(self, only_label: str | None = None) -> Iterator[SourceRecord]:
        conn = self._connect()
        where = " WHERE project = ?" if only_label is not None else ""
        params = (only_label,) if only_label is not None else ()

        obs_cols = "id, project, type, title, subtitle, facts, narrative, files_read, files_modified, created_at_epoch"
        for raw in conn.execute(f"SELECT {obs_cols} FROM observations{where} ORDER BY id", params):
            row = dict(raw)  # sqlite3.Row → Mapping the pure helpers expect
            at = _epoch_seconds(row["created_at_epoch"])
            for fact in _observation_facts(row):
                yield SourceRecord(project_label=row["project"], fact=fact, created_at_epoch=at)

        sum_cols = "id, project, " + ", ".join(_SUMMARY_FIELDS) + ", created_at_epoch"
        for raw in conn.execute(f"SELECT {sum_cols} FROM session_summaries{where} ORDER BY id", params):
            row = dict(raw)
            at = _epoch_seconds(row["created_at_epoch"])
            for fact in _summary_facts(row):
                yield SourceRecord(project_label=row["project"], fact=fact, created_at_epoch=at)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
