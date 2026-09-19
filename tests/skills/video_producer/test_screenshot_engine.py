"""
Unit Tests for Screenshot Engine.

Tests:
- Stub browser implementation
- Screenshot capture + resize
- Annotation system (arrows, boxes, text)
- Error handling + fallbacks
- Convenience functions
"""

import pytest
import sys
from pathlib import Path
from PIL import Image
from io import BytesIO

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from assets.screenshot_engine import (
    ScreenshotEngine,
    StubBrowser,
    StubBrowserContext,
    AnnotationSpec,
    screenshot_console,
    screenshot_with_annotation,
)


# ============================================================================
# Tier 1: Stub Browser
# ============================================================================

class TestStubBrowser:
    """Test StubBrowser (basic implementation)."""

    def test_stub_browser_creation(self):
        """Can create StubBrowser."""
        browser = StubBrowser()
        assert not browser.is_closed

    def test_stub_browser_navigate(self):
        """StubBrowser can navigate to URL."""
        browser = StubBrowser()
        browser.goto("http://localhost:8765/console/")
        assert browser.url == "http://localhost:8765/console/"

    def test_stub_browser_screenshot(self):
        """StubBrowser can take screenshot."""
        browser = StubBrowser()
        browser.goto("http://localhost:8765/console/")

        screenshot_bytes = browser.screenshot()

        assert isinstance(screenshot_bytes, bytes)
        assert len(screenshot_bytes) > 0

        # Verify it's a valid PNG
        image = Image.open(BytesIO(screenshot_bytes))
        assert image.width > 0
        assert image.height > 0

    def test_stub_browser_wait_for_selector(self):
        """StubBrowser can wait for selector (no-op)."""
        browser = StubBrowser()
        browser.goto("http://localhost:8765/console/")

        # Should not raise
        browser.wait_for_selector(".some-selector")

    def test_stub_browser_close(self):
        """StubBrowser can be closed."""
        browser = StubBrowser()
        browser.close()
        assert browser.is_closed

    def test_stub_browser_rejects_operations_after_close(self):
        """StubBrowser rejects operations after close."""
        browser = StubBrowser()
        browser.close()

        with pytest.raises(RuntimeError):
            browser.goto("http://test.com")

        with pytest.raises(RuntimeError):
            browser.screenshot()


class TestStubBrowserContext:
    """Test StubBrowserContext (context manager)."""

    def test_context_manager(self):
        """StubBrowserContext works as context manager."""
        with StubBrowserContext() as browser:
            assert not browser.is_closed
            browser.goto("http://test.com")

        # Browser should be closed after context
        assert browser.is_closed


# ============================================================================
# Tier 2: Screenshot Engine
# ============================================================================

class TestScreenshotEngine:
    """Test ScreenshotEngine core functionality."""

    def test_screenshot_engine_creation(self):
        """Can create ScreenshotEngine."""
        engine = ScreenshotEngine(browser_type="stub")
        assert engine.browser_type == "stub"

    def test_screenshot_engine_screenshot(self):
        """ScreenshotEngine can capture screenshot."""
        engine = ScreenshotEngine(browser_type="stub")

        image = engine.screenshot("http://localhost:8765/console/", wait_ms=0)

        assert isinstance(image, Image.Image)
        assert image.width == 1920
        assert image.height == 1080
        assert image.mode == "RGB"

    def test_screenshot_with_selector(self):
        """ScreenshotEngine waits for selector if specified."""
        engine = ScreenshotEngine(browser_type="stub")

        # Should not raise
        image = engine.screenshot(
            "http://localhost:8765/console/",
            selector=".dashboard",
            wait_ms=0
        )

        assert isinstance(image, Image.Image)

    def test_screenshot_with_wait(self):
        """ScreenshotEngine respects wait_ms."""
        import time

        engine = ScreenshotEngine(browser_type="stub")

        # Quick screenshot (no wait)
        start = time.time()
        engine.screenshot("http://test.com", wait_ms=0)
        duration_no_wait = time.time() - start

        # With wait
        start = time.time()
        engine.screenshot("http://test.com", wait_ms=100)
        duration_with_wait = time.time() - start

        # Duration with wait should be longer
        assert duration_with_wait > duration_no_wait


# ============================================================================
# Tier 2: Annotation System
# ============================================================================

class TestAnnotationSpec:
    """Test AnnotationSpec dataclass."""

    def test_annotation_creation(self):
        """Can create AnnotationSpec."""
        ann = AnnotationSpec(type="arrow", x=100, y=200, text="Click", color="red")

        assert ann.type == "arrow"
        assert ann.x == 100
        assert ann.y == 200
        assert ann.text == "Click"
        assert ann.color == "red"


class TestAnnotations:
    """Test annotation rendering."""

    def test_annotation_arrow(self):
        """Arrow annotation can be applied."""
        engine = ScreenshotEngine(browser_type="stub")

        image = engine.screenshot("http://test.com", wait_ms=0)
        annotated = engine.screenshot_with_annotations(
            "http://test.com",
            annotations=[{"type": "arrow", "x": 500, "y": 300, "text": "Click here"}]
        )

        assert isinstance(annotated, Image.Image)
        assert annotated.size == (1920, 1080)

    def test_annotation_box(self):
        """Box annotation can be applied."""
        engine = ScreenshotEngine(browser_type="stub")

        image = engine.screenshot_with_annotations(
            "http://test.com",
            annotations=[{"type": "box", "x": 100, "y": 200, "size": 50, "text": "Box"}]
        )

        assert isinstance(image, Image.Image)

    def test_annotation_circle(self):
        """Circle annotation can be applied."""
        engine = ScreenshotEngine(browser_type="stub")

        image = engine.screenshot_with_annotations(
            "http://test.com",
            annotations=[{"type": "circle", "x": 960, "y": 540, "radius": 100, "text": "Circle"}]
        )

        assert isinstance(image, Image.Image)

    def test_annotation_text(self):
        """Text annotation can be applied."""
        engine = ScreenshotEngine(browser_type="stub")

        image = engine.screenshot_with_annotations(
            "http://test.com",
            annotations=[{"type": "text", "x": 100, "y": 100, "text": "Important Note"}]
        )

        assert isinstance(image, Image.Image)

    def test_multiple_annotations(self):
        """Multiple annotations can be applied."""
        engine = ScreenshotEngine(browser_type="stub")

        image = engine.screenshot_with_annotations(
            "http://test.com",
            annotations=[
                {"type": "arrow", "x": 500, "y": 300, "text": "Arrow"},
                {"type": "box", "x": 100, "y": 100, "size": 50},
                {"type": "circle", "x": 800, "y": 800, "radius": 40},
                {"type": "text", "x": 50, "y": 50, "text": "Label"},
            ]
        )

        assert isinstance(image, Image.Image)


# ============================================================================
# Tier 2: Error Handling
# ============================================================================

class TestErrorHandling:
    """Test error handling + resilience."""

    def test_invalid_url_returns_error_placeholder(self):
        """Invalid URLs return error placeholder."""
        engine = ScreenshotEngine(browser_type="stub")

        # This would fail in a real browser, but stub returns placeholder
        image = engine.screenshot("invalid-url-that-does-not-exist", wait_ms=0)

        assert isinstance(image, Image.Image)
        assert image.width == 1920
        assert image.height == 1080


# ============================================================================
# Tier 2: Convenience Functions
# ============================================================================

class TestConvenienceFunctions:
    """Test convenience helper functions."""

    def test_screenshot_console(self):
        """screenshot_console() convenience function works."""
        image = screenshot_console()

        assert isinstance(image, Image.Image)
        assert image.width == 1920
        assert image.height == 1080

    def test_screenshot_console_with_panel(self):
        """screenshot_console() with panel parameter."""
        image = screenshot_console(panel="video-producer")

        assert isinstance(image, Image.Image)

    def test_screenshot_with_annotation(self):
        """screenshot_with_annotation() convenience function."""
        image = screenshot_with_annotation(
            "http://localhost:8765/console/",
            [
                {"type": "arrow", "x": 500, "y": 300, "text": "Click here"},
                {"type": "box", "x": 100, "y": 100, "size": 50},
            ]
        )

        assert isinstance(image, Image.Image)


# ============================================================================
# Integration Tests
# ============================================================================

class TestScreenshotSequence:
    """Test capturing multiple screenshots in sequence."""

    def test_capture_sequence(self):
        """Can capture multiple screenshots in sequence."""
        engine = ScreenshotEngine(browser_type="stub")

        urls = [
            "http://localhost:8765/console/",
            "http://localhost:8765/console/?panel=video-producer",
            "http://localhost:8765/console/?panel=settings",
        ]

        images = [engine.screenshot(url, wait_ms=0) for url in urls]

        assert len(images) == 3
        assert all(isinstance(img, Image.Image) for img in images)
        assert all(img.size == (1920, 1080) for img in images)


# ============================================================================
# Run Tests
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
