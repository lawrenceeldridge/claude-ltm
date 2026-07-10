"""Import an external :class:`~core.ports.memory_source.MemorySource` into engram's store.

Write-side, offline orchestration (not a hook, not the recall path). For each project label
in the source it resolves an engram :class:`~core.project.Project` via the injected ``resolve``
callback (label → ``{path, key}``), then streams that label's records through
:func:`core.service.bulk_add_records`. A label the callback can't map is **skipped and reported**
— never written under a guessed key. ``dry_run`` counts what would import without writing.

The core stays ignorant of *how* a label maps to a path: the composition root (the ``engram
import`` CLI) supplies ``resolve`` (from ``--map`` / existing-project lookup). This keeps label
policy at the edge and the orchestration pure over the port (Dependency Inversion).
"""

from __future__ import annotations

from collections.abc import Callable

from core.config import Config
from core.ports.embedding import EmbeddingGateway
from core.ports.memory_source import MemorySource
from core.project import Project
from core.service import bulk_add_records
from core.store import Store


def import_memory_source(
    store: Store,
    embedder: EmbeddingGateway,
    cfg: Config,
    source: MemorySource,
    resolve: Callable[[str], Project | None],
    *,
    only_label: str | None = None,
    dry_run: bool = False,
    session_id: str = "import",
    batch: int = 256,
    progress: Callable[[dict[str, int]], None] | None = None,
) -> dict:
    """Import records from ``source`` into the store.

    Returns ``{"available": bool, "dry_run": bool, "projects": {label: {...}}, "skipped": [label]}``.
    Each mapped project reports its target ``key`` plus either ``would_import`` (dry-run) or the
    :func:`bulk_add_records` counts (``inserted``/``reinforced``/``batches``).
    """
    if not source.available():
        return {"available": False, "dry_run": dry_run, "projects": {}, "skipped": []}

    labels = [only_label] if only_label is not None else source.project_labels()
    result: dict = {"available": True, "dry_run": dry_run, "projects": {}, "skipped": []}
    for label in labels:
        project = resolve(label)
        if project is None:
            result["skipped"].append(label)
            continue
        if dry_run:
            would = sum(1 for _ in source.iter_records(only_label=label))
            result["projects"][label] = {"key": project["key"], "would_import": would}
            continue
        pairs = ((rec.fact, rec.created_at_epoch) for rec in source.iter_records(only_label=label))
        counts = bulk_add_records(store, embedder, cfg, project, session_id, pairs, batch=batch, progress=progress)
        result["projects"][label] = {"key": project["key"], **counts}
    return result
