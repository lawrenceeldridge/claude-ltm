"""Chrome DevTools snapshotter — attaches over CDP to an already-running Chrome.

'Chrome DevTools' here means the Chrome DevTools Protocol: this adapter connects
to a Chrome started with ``--remote-debugging-port`` (the caller's browser) and
reads its accessibility snapshot, rather than launching its own. Playwright is
used purely as the CDP transport — the stdlib has no websocket client — so a
dependency-free raw-CDP transport could replace it later without touching the
core. Imported lazily; fails open to an empty PageView.
"""

from __future__ import annotations

import sys

from core.adapters._snapshot_util import pick_page, view_from_page
from core.ports.snapshot import PageView, SnapshotGateway


class ChromeDevToolsSnapshotter(SnapshotGateway):
    """Attaches to an existing Chrome over CDP and snapshots one of its open tabs.

    Pass a ``target`` URL (what the controller — e.g. Chrome DevTools MCP — navigated
    to) to snapshot exactly that already-open tab, without re-navigating any other tab —
    this is the reliable "control there, snapshot here" contract. With no target it is
    best-effort: the visible/foreground tab if one reports as visible, else the first tab
    (a backgrounded browser reports no tab visible, so prefer passing the URL).
    """

    def __init__(self, cdp_url: str = "http://localhost:9222", timeout_ms: int = 5000) -> None:
        self._cdp_url = cdp_url
        self._timeout_ms = timeout_ms

    def snapshot(self, target: str | None = None) -> PageView:
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:  # CDP transport absent — fail open
            print(f"[engram] playwright (CDP transport) unavailable ({exc}); empty snapshot", file=sys.stderr)
            return PageView(text="")
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.connect_over_cdp(self._cdp_url, timeout=self._timeout_ms)
                try:
                    # Scan every tab across all contexts and pick the active/target one —
                    # NOT contexts[0].pages[0], which is an arbitrary background tab on a
                    # multi-tab browser (e.g. the one Chrome DevTools MCP is driving).
                    pages = [p for ctx in browser.contexts for p in ctx.pages]
                    page = pick_page(pages, target)
                    if page is None:
                        # No open tab matches: open a fresh one (navigate to target if given).
                        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
                        page = ctx.new_page()
                        page.set_default_timeout(self._timeout_ms)
                        if target:
                            page.goto(target, wait_until="load")
                    else:
                        page.set_default_timeout(self._timeout_ms)
                    return view_from_page(page)
                finally:
                    browser.close()
        except Exception as exc:  # no Chrome at cdp_url / attach failure — fail open
            print(
                f"[engram] chrome-devtools (CDP {self._cdp_url}) snapshot failed ({exc}); empty snapshot",
                file=sys.stderr,
            )
            return PageView(text="")
