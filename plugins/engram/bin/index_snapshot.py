#!/usr/bin/env python3
"""PostToolUse (browser snapshot tools) — record the perceived page in the sensory register.

Fires on Chrome DevTools MCP ``take_snapshot`` / Playwright MCP ``browser_snapshot``. Reads the
accessibility text (and the page URL, if the tool reports one) from the tool RESULT and lands it
in the Atkinson-Shiffrin sensory register (the intake stage). Cheap and fail-open: a plain insert
plus attention marking (re-perception of the same page) — NO embedding. Promotion into the index
(the visual long-term-store column) is deferred to the detached capture worker, so this hook adds
no interactive latency and never touches recall.

Skips ``filePath``-mode captures (the a11y text was written to a file, not returned) and anything
it cannot parse. engram never *takes* snapshots — it consumes the ones the browser tools produce.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time

from _bootstrap import plugin_root

# A page URL reported in the snapshot header (Playwright emits "- Page URL: …"); else the first
# http(s) URL near the top of the result. Used only as the re-perception key for attention.
_URL_LABEL = re.compile(r"(?im)^\s*-?\s*(?:page\s+url|url)\s*[:=]\s*(\S+)")
_URL_ANY = re.compile(r"https?://\S+")
_MIN_A11Y_CHARS = 16  # below this there is no perception worth registering (e.g. "Saved to …")


def _result_text(tool_response) -> str:
    """The tool result as plain text, tolerant of the shapes an MCP result can arrive in
    (a string, ``{"content": [{"type": "text", "text": …}]}``, a bare list of blocks, or a
    ``result``/``output`` string). Unknown shapes yield ``""`` so the hook simply no-ops."""
    resp = tool_response
    if resp is None:
        return ""
    if isinstance(resp, str):
        return resp
    if isinstance(resp, list):
        return "\n".join(b["text"] for b in resp if isinstance(b, dict) and isinstance(b.get("text"), str))
    if isinstance(resp, dict):
        content = resp.get("content")
        if isinstance(content, list):
            return "\n".join(b["text"] for b in content if isinstance(b, dict) and isinstance(b.get("text"), str))
        for key in ("text", "result", "output", "stdout"):
            if isinstance(resp.get(key), str):
                return resp[key]
    return ""


def _extract_url(text: str) -> str | None:
    """Best-effort page URL from the snapshot text — a labelled ``Page URL:`` line, else the first
    http(s) URL near the top. ``None`` when absent (attention then falls back to content match)."""
    m = _URL_LABEL.search(text)
    if m:
        return m.group(1).strip().rstrip(".,);")
    m = _URL_ANY.search(text[:2000])
    return m.group(0).rstrip(".,);") if m else None


def main() -> int:
    from _bootstrap import hooks_disabled

    if hooks_disabled():
        return 0  # inside an engram-spawned `claude -p` — stay inert
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    # filePath mode: Chrome DevTools take_snapshot(filePath=…) writes the a11y tree to a file and
    # returns only a confirmation, so there is nothing inline to register.
    if (payload.get("tool_input") or {}).get("filePath"):
        return 0

    text = _result_text(payload.get("tool_response")).strip()
    if len(text) < _MIN_A11Y_CHARS:
        return 0

    try:
        from core.config import get_config
        from core.project import resolve_project
        from core.service import record_visual_perception
        from core.store import Store

        cfg = get_config()
        if not cfg.sensory_enabled:
            return 0
        project = resolve_project(
            cfg.project_dir or os.getcwd(), cfg.markers, identity=cfg.identity, project_dir=cfg.project_dir
        )
        store = Store(cfg.db_path)
        try:
            record_visual_perception(store, cfg, project, _extract_url(text), text, time.time())
        finally:
            store.close()
    except Exception as exc:  # fail-open — a broken intake must never break the turn
        print(f"[engram] snapshot intake failed: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    plugin_root()  # cheap intake stays on the ambient interpreter — no venv/embedding here
    sys.exit(main())
