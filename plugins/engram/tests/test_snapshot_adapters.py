"""Browser-backed snapshotters — shared mapping (fake page), fail-open, and a live
snapshot that self-skips when no working Playwright browser is present.

Design notes:
- ``view_from_page`` is duck-typed, so the mapping is validated with a fake page (no browser).
- Fail-open is validated with *unreachable* targets (port 1), so the assertion holds whether
  or not a browser can launch — a failed browser snapshot must degrade to an empty PageView.
- The live test uses a ``data:`` URL (no network) and skips unless chromium actually launches.
"""

from __future__ import annotations

import unittest

from core.adapters._snapshot_util import pick_page, view_from_page
from core.adapters.chrome_devtools_snap import ChromeDevToolsSnapshotter
from core.adapters.playwright_snap import PlaywrightSnapshotter
from core.ports.snapshot import PageView


class _FakeLocator:
    def __init__(self, yaml: str) -> None:
        self._yaml = yaml

    def aria_snapshot(self, **_kw) -> str:
        return self._yaml


class _FakePage:
    def __init__(self, yaml: str, url, viewport) -> None:
        self._yaml = yaml
        self.url = url
        self.viewport_size = viewport

    def locator(self, _selector: str) -> _FakeLocator:
        return _FakeLocator(self._yaml)


class ViewFromPageTests(unittest.TestCase):
    def test_maps_aria_snapshot_to_pageview(self):
        page = _FakePage(
            '- heading "Sign in"\n- button "Go"',
            "https://example.com",
            {"width": 1024, "height": 768},
        )
        view = view_from_page(page)
        self.assertIsInstance(view, PageView)
        self.assertIn("Sign in", view.text)
        self.assertEqual(view.url, "https://example.com")
        self.assertEqual((view.width, view.height), (1024, 768))
        self.assertFalse(view.is_empty)

    def test_empty_tree_and_missing_viewport(self):
        view = view_from_page(_FakePage("", None, None))
        self.assertTrue(view.is_empty)
        self.assertIsNone(view.width)
        self.assertIsNone(view.height)


class _FakeTab:
    """A duck-typed page for pick_page: exposes .url and .evaluate(visibilityState)."""

    def __init__(self, url: str, visible: bool = False, raises: bool = False) -> None:
        self.url = url
        self._visible = visible
        self._raises = raises

    def evaluate(self, _expr: str) -> str:
        if self._raises:
            raise RuntimeError("page crashed")
        return "visible" if self._visible else "hidden"


class PickPageTests(unittest.TestCase):
    """Which tab the chrome-devtools backend snapshots on a shared multi-tab browser."""

    def test_target_matches_existing_tab(self):
        pages = [_FakeTab("https://a.com"), _FakeTab("https://ex.com/login"), _FakeTab("https://b.com")]
        self.assertIs(pick_page(pages, "https://ex.com/login"), pages[1])

    def test_target_is_trailing_slash_insensitive(self):
        pages = [_FakeTab("http://127.0.0.1:7801/")]
        self.assertIs(pick_page(pages, "http://127.0.0.1:7801"), pages[0])

    def test_target_no_match_returns_none(self):
        self.assertIsNone(pick_page([_FakeTab("https://a.com")], "https://x.com"))

    def test_no_target_picks_the_visible_tab(self):
        pages = [_FakeTab("https://bg.com"), _FakeTab("https://active.com", visible=True), _FakeTab("https://c.com")]
        self.assertIs(pick_page(pages, None), pages[1])

    def test_no_target_no_visible_falls_back_to_first(self):
        pages = [_FakeTab("https://a.com"), _FakeTab("https://b.com")]
        self.assertIs(pick_page(pages, None), pages[0])

    def test_visibility_eval_error_counts_as_not_visible(self):
        pages = [_FakeTab("https://crash.com", raises=True), _FakeTab("https://ok.com", visible=True)]
        self.assertIs(pick_page(pages, None), pages[1])

    def test_empty_pages_returns_none(self):
        self.assertIsNone(pick_page([], None))
        self.assertIsNone(pick_page([], "https://x.com"))


class FailOpenTests(unittest.TestCase):
    def test_playwright_fails_open_to_empty(self):
        # Unreachable target (port 1): launch may or may not succeed, but the snapshot
        # cannot complete, so it must degrade to an empty PageView without raising.
        view = PlaywrightSnapshotter(timeout_ms=1500).snapshot("http://127.0.0.1:1/")
        self.assertIsInstance(view, PageView)
        self.assertTrue(view.is_empty)

    def test_chrome_devtools_fails_open_to_empty(self):
        # Nothing listening on the CDP port -> attach fails -> empty PageView, no raise.
        view = ChromeDevToolsSnapshotter(cdp_url="http://127.0.0.1:1", timeout_ms=1500).snapshot()
        self.assertIsInstance(view, PageView)
        self.assertTrue(view.is_empty)


class LiveSnapshotTests(unittest.TestCase):
    def test_live_playwright_snapshot(self):
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as pw:
                pw.chromium.launch(headless=True).close()
        except Exception as exc:  # no dep / browser build mismatch -> skip, don't fail
            self.skipTest(f"no working Playwright browser: {exc}")
        view = PlaywrightSnapshotter().snapshot("data:text/html,<h1>Sign in</h1><button>Go</button>")
        self.assertFalse(view.is_empty)
        self.assertIn("Sign in", view.text)


if __name__ == "__main__":
    unittest.main()
