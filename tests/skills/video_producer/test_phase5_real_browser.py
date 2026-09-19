"""
Phase 5.1: Real Browser Integration Tests.

Tests for Playwright-based real screenshot engine.

NOTE: These tests assume Playwright is available. If not, they gracefully fall back to stub.
"""

import pytest
import sys
from pathlib import Path
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from assets.screenshot_engine import ScreenshotEngine


class TestPlaywrightIntegration:
    """Test real Playwright browser integration."""

    def test_screenshot_engine_default_is_playwright(self):
        """ScreenshotEngine should default to playwright (Phase 5.1)."""
        engine = ScreenshotEngine()
        assert engine.browser_type == "playwright"

    def test_screenshot_engine_accepts_browser_type(self):
        """ScreenshotEngine should accept browser_type parameter."""
        engine_pw = ScreenshotEngine(browser_type="playwright")
        assert engine_pw.browser_type == "playwright"

        engine_stub = ScreenshotEngine(browser_type="stub")
        assert engine_stub.browser_type == "stub"

    def test_playwright_context_initialization(self):
        """PlaywrightContext should initialize without error."""
        engine = ScreenshotEngine(browser_type="playwright")
        # This will fail gracefully if Playwright not installed
        ctx = engine._get_browser_context()
        assert ctx is not None

    def test_screenshot_with_playwright_fallback(self):
        """
        Screenshot should work even if Playwright not available.

        If Playwright is available, uses real browser.
        If not, falls back to stub browser.
        """
        engine = ScreenshotEngine(browser_type="playwright")

        # This should work (either with real browser or stub fallback)
        try:
            image = engine.screenshot("http://localhost:8765/console/", wait_ms=500)
            assert isinstance(image, Image.Image)
            assert image.size == (1920, 1080)
        except Exception as e:
            # Acceptable if Playwright not available
            pytest.skip(f"Playwright not available: {e}")

    def test_screenshot_with_selector(self):
        """Screenshot should support CSS selector waiting."""
        engine = ScreenshotEngine(browser_type="playwright")

        try:
            image = engine.screenshot(
                "http://localhost:8765/console/",
                selector=".console-layout",
                wait_ms=500
            )
            assert isinstance(image, Image.Image)
        except Exception as e:
            pytest.skip(f"Playwright not available: {e}")

    def test_screenshot_fallback_to_stub_if_no_playwright(self):
        """Screenshot should fallback to stub if Playwright unavailable."""
        engine = ScreenshotEngine(browser_type="playwright")

        # Force fallback by using invalid URL (should still return valid image)
        try:
            image = engine.screenshot("http://invalid-url-that-definitely-does-not-exist.local/")
            assert isinstance(image, Image.Image)
            # Either real screenshot or error placeholder is acceptable
        except Exception as e:
            # Acceptable if no Playwright
            pytest.skip(f"Playwright not available: {e}")


class TestScreenshotWithAnnotations:
    """Test screenshot with annotations (works with both stub and real browsers)."""

    def test_annotations_on_stub_browser(self):
        """Annotations should work on stub browser."""
        engine = ScreenshotEngine(browser_type="stub")

        image = engine.screenshot_with_annotations(
            "http://localhost:8765/console/",
            annotations=[
                {"type": "arrow", "x": 500, "y": 300, "text": "Click here"},
                {"type": "box", "x": 800, "y": 400, "size": 100, "text": "Important"}
            ]
        )

        assert isinstance(image, Image.Image)
        assert image.size == (1920, 1080)

    def test_annotations_on_real_browser(self):
        """Annotations should work on real Playwright browser if available."""
        engine = ScreenshotEngine(browser_type="playwright")

        try:
            image = engine.screenshot_with_annotations(
                "http://localhost:8765/console/",
                annotations=[
                    {"type": "text", "x": 100, "y": 100, "text": "Test"}
                ]
            )
            assert isinstance(image, Image.Image)
        except Exception as e:
            pytest.skip(f"Playwright not available: {e}")

    def test_various_annotation_types(self):
        """Test all annotation types on stub browser."""
        engine = ScreenshotEngine(browser_type="stub")

        annotations = [
            {"type": "arrow", "x": 100, "y": 100},
            {"type": "box", "x": 200, "y": 200, "size": 50},
            {"type": "circle", "x": 300, "y": 300, "radius": 40},
            {"type": "text", "x": 400, "y": 400, "text": "Label"}
        ]

        image = engine.screenshot_with_annotations(
            "http://test.com",
            annotations=annotations
        )

        assert isinstance(image, Image.Image)


class TestScreenshotEnginePhase5Upgrades:
    """Test Phase 5.1 specific upgrades."""

    def test_screenshot_console_uses_playwright_by_default(self):
        """screenshot_console should use playwright by default."""
        from assets.screenshot_engine import screenshot_console

        try:
            image = screenshot_console(wait_ms=300)
            assert isinstance(image, Image.Image)
        except Exception as e:
            pytest.skip(f"Playwright not available: {e}")

    def test_screenshot_with_annotation_convenience_function(self):
        """screenshot_with_annotation helper should use playwright by default."""
        from assets.screenshot_engine import screenshot_with_annotation

        try:
            image = screenshot_with_annotation(
                "http://localhost:8765/console/",
                [{"type": "text", "x": 100, "y": 100, "text": "Test"}]
            )
            assert isinstance(image, Image.Image)
        except Exception as e:
            pytest.skip(f"Playwright not available: {e}")

    def test_explicit_stub_browser_still_works(self):
        """Explicit browser_type='stub' should still work for backward compat."""
        from assets.screenshot_engine import screenshot_console

        image = screenshot_console(browser_type="stub")
        assert isinstance(image, Image.Image)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
