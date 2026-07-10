"""MemorySource — a read-only port over an *external* memory store to import from.

The seam for one-way migration: another tool (e.g. ``claude-mem``) holds a project's
history in its own store; an adapter reads that store and yields records already mapped
onto engram's own :class:`~core.ports.distill.DistilledFact`, so the core learns nothing
of the foreign schema. The importer (``core/migrate.py``) and the CLI composition root
(``engram import``) depend only on this ABC, never on a concrete source.

Separated Interface (Hexagonal port): the mapping from a source row to a
:class:`DistilledFact` lives entirely in the adapter (``core/adapters/*_source.py``);
adding a new source is a new adapter behind this port, not a branch in the core. Purely
write-side and offline — never touched on the recall hot path.

``available()`` is the fail-soft gate: a missing or unreadable source returns ``False``
rather than raising, so the composition root can report cleanly instead of tracebacking.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass

from core.ports.distill import DistilledFact


@dataclass(frozen=True)
class SourceRecord:
    """One record pulled from an external store. Value Object / import DTO.

    ``fact`` is already mapped onto engram's :class:`DistilledFact` by the adapter, so the
    importer never sees the source schema. ``project_label`` is the source's own project
    name (e.g. ``"ukh-world"``) — it carries no filesystem path, so the composition root
    is responsible for mapping the label to an engram project ``{path, key}`` before any
    write. ``created_at_epoch`` preserves the record's original timestamp (Unix seconds)
    when the source has one, so history can be imported with its real ages rather than
    all stamped "now"; ``None`` when the source does not record a time.
    """

    project_label: str
    fact: DistilledFact
    created_at_epoch: float | None = None


class MemorySource(ABC):
    """Port: a read-only external memory store an import can pull records from."""

    @abstractmethod
    def available(self) -> bool:
        """True when the source exists and is readable. Never raises — a missing or
        broken source returns False so the caller can fail soft."""

    @abstractmethod
    def project_labels(self) -> list[str]:
        """Distinct project labels present in the source (for the dry-run summary)."""

    @abstractmethod
    def iter_records(self, only_label: str | None = None) -> Iterator[SourceRecord]:
        """Stream every record, optionally restricted to one ``project_label``.

        Streaming (not a materialised list) so a large source imports with bounded memory.
        """

    def close(self) -> None:
        """Release any resources (e.g. a DB connection). No-op by default."""


def get_memory_source(kind: str, *, db_path: str | None = None) -> MemorySource:
    """Composition-root selection — Plugin pattern.

    One source today: ``claude-mem`` (a read-only SQLite store). Adapters are imported
    lazily so the port module stays dependency-light and a source's absence never breaks
    an unrelated command. A new source is a new adapter + a new branch here — never a
    conditional in the core.
    """
    if kind == "claude-mem":
        from core.adapters.claude_mem_source import ClaudeMemSource

        return ClaudeMemSource(db_path=db_path)
    raise ValueError(f"unknown memory source: {kind!r}")
