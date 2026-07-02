#!/usr/bin/env python3
"""PreToolUse (Grep|Glob|Task) — nudge toward memory/index before a broad search.

Non-blocking: emits ``additionalContext`` so the tool still runs, but the model sees
a reminder to consult claude-ltm first. Fires at most once per session (a per-session
marker file) so it guides without spamming every search. Pure stdlib and does no plugin
imports, so it adds negligible latency to a search and never needs the managed venv.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

_REMINDER = (
    "claude-ltm memory + index are available and cheaper than a broad search. Before "
    "Grep/Glob/Task, consult: `recall` (prior decisions/facts for this project), then "
    "`search_code` / `search_docs` (the project's indexed symbols / doc sections — ranked "
    "outlines, not file scans), then `get_symbol` / `get_doc_section` to pull the exact span. "
    "Trust confident hits and skip the wide search; widen only if they're weak or empty. "
    "(Reminder fires once per session.)"
)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    session = payload.get("session_id") or str(os.getppid())
    marker = os.path.join(tempfile.gettempdir(), f"ltm-prefer-{session}.seen")
    try:
        # O_EXCL: the first search in a session creates the marker and gets the nudge;
        # every later search finds it already there and stays silent.
        os.close(os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        return 0
    except OSError:
        pass  # can't write a marker — nudge anyway, better than never

    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": _REMINDER}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
