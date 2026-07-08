"""Pure sensory-register decisions (Functional Core).

The sensory tier's one genuinely-pure decision: whether an ephemeral snapshot has been
"attended" enough to promote into STM. This is the rehearsal analogue of the STM→LTM
``promote_after_freq`` — a page re-glanced enough times is attended. No I/O, no clock.

(Decay is a simple capacity + TTL SQL sweep in ``Store.sweep_sensory``, mirroring the
facts TTL sweep, so it needs no pure selector here.)
"""

from __future__ import annotations

import re
from typing import Any

# An accessibility-tree line naming a control — tolerant of both the dash-prefixed ARIA
# YAML (Playwright: `- button "Go"`) and the indented tree the stub emits (`button "Go"`),
# with or without a trailing `[ref=…]`/`level=…`.
_A11Y_LINE = re.compile(
    r'^\s*-?\s*(heading|button|link|textbox|checkbox|radio|tab|menuitem|combobox|option)\b[^"]*"([^"]+)"',
    re.I,
)


def should_promote(row: Any, promote_after: int) -> bool:
    """True when a sensory snapshot is "attended" — re-glanced at least ``promote_after``
    times and not already promoted. ``row`` is any keyed mapping with ``glance_count`` and
    ``attended`` (a ``sqlite3.Row`` or a dict)."""
    if row["attended"]:
        return False
    return int(row["glance_count"]) >= max(1, promote_after)


def summarize_snapshot(url: str, text: str, max_len: int = 240) -> str:
    """Deterministic sense-specific framing: build one STM-fact line from an a11y snapshot —
    the page heading plus a few named controls. No LLM, so promotion stays offline and
    testable (the heuristic path; an LLM distiller could refine it later). Returns ``""``
    when nothing nameable is found."""
    heading = ""
    controls: list[str] = []
    for line in text.splitlines():
        m = _A11Y_LINE.match(line)
        if not m:
            continue
        role, name = m.group(1).lower(), m.group(2).strip()
        if role == "heading":
            heading = heading or name
        elif name not in controls:
            controls.append(name)
    if not heading and not controls:
        return ""
    parts = []
    if heading:
        parts.append(f'"{heading}"')
    if controls:
        parts.append("controls: " + ", ".join(controls[:6]))
    return f"Viewed {(url or 'a page').strip()} — {'; '.join(parts)}"[:max_len]
