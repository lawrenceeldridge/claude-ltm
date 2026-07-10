"""Vector scorer (port) + a stdlib pure-Python default.

The cosine scan of a query against a project's active-fact vectors is recall's dominant
cost — O(n·dim). At small stores (the stdlib / ``hash`` default) the pure-Python scan is
sub-10ms; at large stores (a big import under ``fastembed``) it runs into seconds, which
blows the recall hook's 5s ceiling. So the scan is a swappable seam: ``PurePythonScorer``
is the zero-dependency default, and a numpy adapter (``core/adapters/numpy_scorer.py``)
vectorises the *same* maths for large stores. The result is identical — only the execution
differs (Ports & Adapters, mirroring ``embedding.py`` / ``fastembed_gw.py``).
"""

from __future__ import annotations

import sqlite3
from abc import ABC, abstractmethod

from core.domain.quantize import cosine, dequantize_int8

# A row whose stored vector dimension doesn't match the query embedder is not comparable.
# Scoring it as -inf sorts it below any real similarity, so the ``min_sim`` gate skips it —
# the same effect as the old explicit dim-mismatch ``continue``, but uniform across scorers.
DIM_MISMATCH = float("-inf")


class VectorScorer(ABC):
    """Port: cosine of a query against every candidate row's stored vector."""

    @abstractmethod
    def cosine_all(self, rows: list[sqlite3.Row], query_vec: list[float]) -> list[float]:
        """Cosine similarities parallel to ``rows``; ``DIM_MISMATCH`` for a dim-mismatched row."""


class PurePythonScorer(VectorScorer):
    """Zero-dependency scan (the historical behaviour). Fine at small stores."""

    def cosine_all(self, rows: list[sqlite3.Row], query_vec: list[float]) -> list[float]:
        qdim = len(query_vec)
        return [
            DIM_MISMATCH
            if (row["dim"] and row["dim"] != qdim)
            else cosine(query_vec, dequantize_int8(row["vec_int8"], row["scale"]))
            for row in rows
        ]


def get_scorer(cfg) -> VectorScorer:
    """Composition-root selection — Plugin pattern (mirrors ``get_embedder``).

    Prefers the numpy adapter (vectorised — required for large stores to clear the 5s recall
    ceiling); falls back to the pure-Python scan when numpy or the adapter is unavailable.
    numpy is present wherever ``embedding=fastembed`` (a fastembed dependency); the stdlib /
    ``hash`` default keeps the pure-Python path (and never has stores large enough to matter).
    """
    if getattr(cfg, "scorer", "auto") != "python":
        try:
            from core.adapters.numpy_scorer import NumpyScorer

            return NumpyScorer()
        except Exception:  # numpy or the adapter absent — fall back, never break recall
            pass
    return PurePythonScorer()
