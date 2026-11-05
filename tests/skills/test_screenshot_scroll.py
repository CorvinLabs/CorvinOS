"""Tests for scroll capture in the screenshot capturer (scroll_steps / scroll_amount_px / scroll_to_end).

Pure planner tests run everywhere. The browser tests drive a real headless Chromium against a
generated long HTML page and are skipped when Playwright or its browser is unavailable.
"""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from core.skills.os_skills.video_producer.types import Scene, Storyboard
from core.skills.workers.screenshot_capturer import ScreenshotCapturer
from core.skills.workers.screenshot_capturer.scroll import (
    ScrollConfig,
    capture_scroll_frames,
    plan_scroll_offsets,
)

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

LONG_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>long</title></head>
<body style="margin:0">
<div style="height:3000px;background:linear-gradient(#123,#f80)">scroll test page</div>
</body></html>"""


def _browser_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
        return True
    except Exception:
        return False


needs_browser = pytest.mark.skipif(not _browser_available(), reason="Playwright chromium not available")


def _long_page_url(tmpdir: str) -> str:
    page = Path(tmpdir) / "long.html"
    page.write_text(LONG_PAGE, encoding="utf-8")
    return page.as_uri()


class TestScrollPlanner:
    def test_default_config_is_disabled_single_frame(self):
        config = ScrollConfig()
        assert config.enabled is False
        assert plan_scroll_offsets(3000, 1080, config) == [0]

    def test_steps_with_explicit_amount(self):
        config = ScrollConfig(steps=2, amount_px=300)
        assert plan_scroll_offsets(2000, 500, config) == [0, 300, 600]

    def test_steps_default_to_one_viewport(self):
        config = ScrollConfig(steps=2)
        assert plan_scroll_offsets(5000, 500, config) == [0, 500, 1000]

    def test_to_end_clamps_last_frame_to_page_bottom(self):
        config = ScrollConfig(to_end=True)
        # max offset is 1200 - 500 = 700; the last frame sits exactly there
        assert plan_scroll_offsets(1200, 500, config) == [0, 500, 700]

    def test_no_duplicate_at_page_end_when_steps_overshoot(self):
        config = ScrollConfig(steps=10, amount_px=500)
        assert plan_scroll_offsets(1200, 500, config) == [0, 500, 700]

    def test_short_page_yields_single_frame(self):
        config = ScrollConfig(to_end=True)
        assert plan_scroll_offsets(400, 500, config) == [0]

    def test_max_frames_caps_planning(self):
        config = ScrollConfig(steps=50, amount_px=100, max_frames=4)
        assert plan_scroll_offsets(10000, 500, config) == [0, 100, 200, 300]

    def test_from_mapping_reads_storyboard_keys(self):
        config = ScrollConfig.from_mapping(
            {"scroll_steps": 3, "scroll_amount_px": 400, "scroll_to_end": True, "scroll_settle_ms": 10}
        )
        assert config == ScrollConfig(steps=3, amount_px=400, to_end=True, settle_ms=10)

    def test_from_mapping_without_keys_stays_disabled(self):
        assert ScrollConfig.from_mapping({"title": "x"}).enabled is False
        assert ScrollConfig.from_mapping(None).enabled is False


@needs_browser
class TestScrollDriverBrowser:
    @pytest.mark.asyncio
    async def test_frames_follow_real_scroll_positions(self):
        from playwright.async_api import async_playwright

        with tempfile.TemporaryDirectory() as tmpdir:
            url = _long_page_url(tmpdir)
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                try:
                    page = await browser.new_page(viewport={"width": 800, "height": 1080})
                    await page.goto(url)
                    frames = await capture_scroll_frames(
                        page, ScrollConfig(to_end=True, settle_ms=50)
                    )
                finally:
                    await browser.close()

        # 3000px page, 1080px viewport: max offset 1920 -> 0, 1080, 1920 with no repeat at the end
        assert [y for y, _ in frames] == [0, 1080, 1920]
        assert all(image.startswith(PNG_SIGNATURE) for _, image in frames)

    @pytest.mark.asyncio
    async def test_capture_scenes_writes_numbered_frames(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            url = _long_page_url(tmpdir)
            capturer = ScreenshotCapturer(tmpdir)
            scene = Scene(id="s01", kind="screencast", narration="Long page walkthrough.")
            storyboard = Storyboard(metadata={"scene_count": 1}, scenes=[scene])
            config = ScrollConfig(steps=5, amount_px=1000, settle_ms=50)

            with patch.object(capturer, "_check_console_health") as mock_health, patch.object(
                capturer, "_parse_scene_instructions", return_value={"url": url, "crop_rect": None}
            ):
                mock_health.return_value = {"ok": True, "status_code": 200}
                result = await capturer.capture_scenes(storyboard, scroll=config)

            assert result["scenes_failed"] == 0
            frames = result["scroll_frames"]["s01"]
            # 3000px page, 1080px viewport: max offset 1920. Steps of 1000 give 0, 1000, 2000 -> clamped to 1920,
            # and the repeated end position is dropped.
            names = [Path(f).name for f in frames]
            assert names == [f"s01_{n:03d}.png" for n in range(1, len(frames) + 1)]
            assert len(frames) == 3
            assert result["screenshots"]["s01"] == frames[0]
            assert result["metadata"]["s01"]["scroll_offsets"] == [0, 1000, 1920]
            for frame in frames:
                assert Path(frame).read_bytes().startswith(PNG_SIGNATURE)


class TestScreenshotCapturerDefaultPath:
    @pytest.mark.asyncio
    async def test_no_scroll_parameters_keeps_single_frame_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            capturer = ScreenshotCapturer(tmpdir)
            scene = Scene(id="s01", kind="card", narration="Dashboard panel.")
            storyboard = Storyboard(metadata={"scene_count": 1}, scenes=[scene])

            with patch.object(capturer, "_check_console_health") as mock_health:
                mock_health.return_value = {"ok": True, "status_code": 200}
                with patch.object(capturer, "_capture_scroll_via_playwright") as mock_scroll:
                    result = await capturer.capture_scenes(storyboard)

            mock_scroll.assert_not_called()
            assert result["scroll_frames"] == {}
            assert Path(result["screenshots"]["s01"]).name == "s01.png"

    @pytest.mark.asyncio
    async def test_scroll_failure_marks_scene_failed_not_crash(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            capturer = ScreenshotCapturer(tmpdir)
            scene = Scene(id="s01", kind="card", narration="Dashboard panel.")
            storyboard = Storyboard(metadata={"scroll_steps": 2}, scenes=[scene])

            with patch.object(capturer, "_check_console_health") as mock_health, patch.object(
                capturer, "_capture_scroll_via_playwright", side_effect=RuntimeError("no browser")
            ):
                mock_health.return_value = {"ok": True, "status_code": 200}
                result = await capturer.capture_scenes(storyboard)

            assert result["scenes_failed"] == 1
            assert result["screenshots"] == {}
