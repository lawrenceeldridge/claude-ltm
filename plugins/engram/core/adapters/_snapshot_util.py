"""Shared page -> PageView mapping for the browser-backed snapshotters.

Duck-typed over a Playwright ``Page`` — there is deliberately **no** ``playwright``
import here, so this module stays import-clean and unit-testable with a fake page.
The browser library is imported lazily inside each adapter's ``snapshot`` method.
"""

from __future__ import annotations

from core.ports.snapshot import PageView


def view_from_page(page) -> PageView:
    """Build a PageView from a live page via its accessibility (ARIA) snapshot.

    Uses ``locator("body").aria_snapshot()`` — the YAML accessibility-tree text
    that is the token-cheap, recommended representation (Playwright's
    ``browser_snapshot`` equivalent). An empty tree yields an empty PageView
    (the Null-Object 'nothing to show' case).
    """
    text = page.locator("body").aria_snapshot() or ""
    viewport = page.viewport_size or {}
    return PageView(
        text=text,
        url=getattr(page, "url", None),
        width=viewport.get("width"),
        height=viewport.get("height"),
    )


def _norm_url(url: str | None) -> str:
    return (url or "").rstrip("/")


def _is_visible(page) -> bool:
    """Whether a tab is the foreground (visible) tab of its window. Fail-safe: any
    ``evaluate`` error (a crashed or closing page) counts as not visible."""
    try:
        return page.evaluate("document.visibilityState") == "visible"
    except Exception:
        return False


def pick_page(pages, target: str | None = None):
    """Choose which open tab to snapshot when attached to a shared browser (CDP).

    - With a ``target`` URL: the already-open tab at that URL (trailing-slash
      insensitive), or None when none matches — the caller decides whether to open it.
    - Without a target: the **visible/foreground** tab (what the controller is
      looking at), falling back to the first tab.

    This is what makes the chrome-devtools backend snapshot the page the caller
    (e.g. Chrome DevTools MCP) just navigated, rather than an arbitrary background
    tab (``pages[0]``) on a multi-tab browser.
    """
    if not pages:
        return None
    if target:
        wanted = _norm_url(target)
        return next((p for p in pages if _norm_url(getattr(p, "url", "")) == wanted), None)
    return next((p for p in pages if _is_visible(p)), pages[0])
