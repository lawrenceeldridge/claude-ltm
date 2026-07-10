"""Vectorised cosine scan (numpy) — the fast ``VectorScorer`` for large stores.

Optional driven adapter (Ports & Adapters): numpy is imported **lazily in ``__init__``**
(mirroring ``fastembed_gw.py``), so the module imports fine without it and ``get_scorer``
falls back to ``PurePythonScorer``. numpy is present wherever ``embedding=fastembed`` (it is
a fastembed dependency), which is exactly the config that grows stores large enough to matter.

It reproduces ``dequantize_int8`` (``x/127*scale``) + ``cosine`` (``dot/(‖q‖·‖r‖)``, 0 on a
zero norm) so the ranking matches the pure-Python scan — only the execution is vectorised.
That turns recall's O(n·dim) scan from seconds at 10⁵ facts to milliseconds, keeping the
``UserPromptSubmit`` hook under its 5s ceiling. A row whose stored dim differs from the query
embedder scores ``DIM_MISMATCH``; a rare odd-length legacy vector falls back to the exact
pure cosine so its zip-truncation semantics are preserved byte-for-byte.
"""

from __future__ import annotations

import sqlite3

from core.domain.quantize import cosine, dequantize_int8
from core.ports.scorer import DIM_MISMATCH, VectorScorer


class NumpyScorer(VectorScorer):
    def __init__(self) -> None:
        import numpy as np  # lazy: its absence makes get_scorer fall back to pure-Python

        self._np = np

    def cosine_all(self, rows: list[sqlite3.Row], query_vec: list[float]) -> list[float]:
        np = self._np
        n = len(rows)
        if n == 0:
            return []
        qdim = len(query_vec)
        q = np.asarray(query_vec, dtype=np.float32)
        qnorm = float(np.sqrt(q @ q))

        out: list[float] = [DIM_MISMATCH] * n  # dim-mismatch rows keep this
        vec_idx: list[int] = []
        blobs: list[bytes] = []
        scales: list[float] = []
        for i, row in enumerate(rows):
            dim = row["dim"]
            if dim and dim != qdim:
                continue  # incomparable embedder — stays DIM_MISMATCH
            blob = row["vec_int8"]
            if len(blob) != qdim:
                # Rare legacy row (null/odd dim): exact pure cosine keeps zip semantics.
                out[i] = cosine(query_vec, dequantize_int8(blob, row["scale"]))
                continue
            vec_idx.append(i)
            blobs.append(blob)
            scales.append(row["scale"])

        if vec_idx:
            m = np.frombuffer(b"".join(blobs), dtype=np.int8).astype(np.float32).reshape(len(vec_idx), qdim)
            m /= 127.0
            m *= np.asarray(scales, dtype=np.float32)[:, None]
            sims = m @ q
            norms = np.sqrt((m * m).sum(axis=1))
            denom = norms * qnorm
            # cosine() returns 0.0 when either norm is zero — reproduce, avoiding 0/0 warnings.
            with np.errstate(invalid="ignore", divide="ignore"):
                sims = np.where(denom == 0.0, np.float32(0.0), sims / denom)
            for j, i in enumerate(vec_idx):
                out[i] = float(sims[j])
        return out
