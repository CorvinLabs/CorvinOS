"""Screenshot Capturer implementation: Browser automation + OCR + feedback."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Optional, Any
from datetime import datetime
from dataclasses import asdict

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from os_skills.video_producer.types import Scene, Storyboard
from core.learning.event_emitter import EventEmitter  # ADR-0314 feedback


class ScreenshotCapturer:
    """Worker for browser-based screenshot capture."""

    def __init__(self, workdir: str | Path, console_url: str = "http://127.0.0.1:8765"):
        """Initialize with working directory and console URL."""
        self.workdir = Path(workdir)
        self.screenshots_dir = self.workdir / "screenshots"
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self.console_url = console_url
        self.event_emitter = EventEmitter()  # For SceneRenderedEvent emission

    async def capture_scenes(
        self,
        storyboard: Storyboard,
    ) -> dict[str, Any]:
        """
        Capture screenshots for all scenes in storyboard.

        Stages:
        1. Console health check (precondition)
        2. For each scene: parse instructions → capture → crop
        3. OCR + UI detection (stub)
        4. Emit SceneRenderedEvent per scene
        5. Collect metadata

        Args:
            storyboard: Scene list + metadata

        Returns:
            {
                "status": "success" | "partial",
                "scenes_processed": int,
                "scenes_failed": int,
                "screenshots": {scene_id: screenshot_path},
                "metadata": {scene_id: {width, height, elements, ocr_text}},
                "health": dict
            }
        """
        # 0. Console health check (precondition)
        health = await self._check_console_health()
        if not health.get("ok"):
            return {
                "status": "blocked",
                "scenes_processed": 0,
                "scenes_failed": len(storyboard.scenes),
                "screenshots": {},
                "metadata": {},
                "health": health,
                "error": "Console health check failed",
            }

        results = {
            "status": "success",
            "scenes_processed": 0,
            "scenes_failed": 0,
            "screenshots": {},
            "metadata": {},
            "health": health,
        }

        # Capture each scene (parallel)
        tasks = [
            self._capture_scene(scene, results)
            for scene in storyboard.scenes
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

        return results

    async def _check_console_health(self) -> dict[str, Any]:
        """Check console health via HTTP GET."""
        try:
            import urllib.request
            import urllib.error

            url = f"{self.console_url}/health"
            req = urllib.request.Request(url, method='GET')

            # Async wrapper around sync urllib
            def check():
                try:
                    with urllib.request.urlopen(req, timeout=5) as response:
                        if response.status == 200:
                            return {
                                "ok": True,
                                "status_code": 200,
                                "timestamp": datetime.utcnow().isoformat() + "Z",
                            }
                except urllib.error.HTTPError as e:
                    return {
                        "ok": False,
                        "status_code": e.code,
                        "error": str(e),
                    }
                except Exception as e:
                    return {
                        "ok": False,
                        "error": f"Console not reachable: {str(e)}",
                    }

            # Run in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, check)

        except Exception as e:
            return {
                "ok": False,
                "error": f"Health check failed: {str(e)}",
            }

    async def _capture_scene(
        self,
        scene: Scene,
        results: dict[str, Any],
    ) -> None:
        """Capture a single scene's screenshot."""
        try:
            # 1. Parse scene instructions
            capture_params = await self._parse_scene_instructions(scene)

            # 2. Capture screenshot (stub: Playwright)
            start_time = time.time()
            screenshot_bytes = await self._capture_via_playwright(
                url=capture_params.get("url", f"{self.console_url}/console"),
                selector=capture_params.get("selector"),
            )
            capture_latency_ms = (time.time() - start_time) * 1000

            # 3. Crop if needed
            if capture_params.get("crop_rect"):
                screenshot_bytes = await self._crop_image(
                    screenshot_bytes,
                    capture_params["crop_rect"],
                )

            # 4. Save screenshot
            screenshot_path = self.screenshots_dir / f"{scene.id}.png"
            await self._write_screenshot_file(screenshot_bytes, screenshot_path)

            # 5. OCR + UI detection (stub)
            metadata = await self._extract_metadata(
                screenshot_path,
                screenshot_bytes,
            )

            # 6. Emit SceneRenderedEvent for feedback
            await self._emit_scene_rendered_event(
                scene_id=scene.id,
                capture_latency_ms=capture_latency_ms,
                image_size_bytes=len(screenshot_bytes),
                elements_detected=len(metadata.get("ui_elements", [])),
                confidence="medium",  # Depends on OCR quality
            )

            # 7. Update results
            results["screenshots"][scene.id] = str(screenshot_path)
            results["metadata"][scene.id] = metadata
            results["scenes_processed"] += 1

        except Exception as e:
            results["scenes_failed"] += 1
            print(f"Failed to capture screenshot for scene {scene.id}: {str(e)}")

    async def _parse_scene_instructions(self, scene: Scene) -> dict[str, Any]:
        """Parse scene narration for screenshot instructions."""
        # Stub: simple heuristics
        # Production: LLM to parse instructions from narration
        params = {
            "url": f"{self.console_url}/console",
            "selector": None,
            "crop_rect": None,
        }

        # Heuristic: if narration mentions a panel/page, try to extract it
        if scene.narration and "panel" in scene.narration.lower():
            # Extract panel name from narration
            words = scene.narration.lower().split()
            panel_idx = words.index("panel") if "panel" in words else -1
            if panel_idx >= 0 and panel_idx + 1 < len(words):
                panel_name = words[panel_idx + 1].strip(".,;:")
                params["url"] = f"{self.console_url}/console?panel={panel_name}"

        return params

    async def _capture_via_playwright(
        self,
        url: str,
        selector: Optional[str] = None,
    ) -> bytes:
        """
        Capture screenshot via Playwright.

        Stub: returns dummy PNG bytes.
        Production: use playwright.async_api to automate browser.
        """
        # Stub: generate dummy PNG
        # A minimal 1x1 PNG (in PNG format)
        # Real implementation would use Playwright:
        # async with async_playwright() as p:
        #   browser = await p.chromium.launch()
        #   page = await browser.new_page()
        #   await page.goto(url)
        #   if selector:
        #       await page.wait_for_selector(selector)
        #   screenshot = await page.screenshot()
        #   await browser.close()

        # Stub PNG (1x1 white pixel)
        return (
            b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
            b'\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f'
            b'\x00\x00\x01\x01\x00\x05\x18r\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
        )

    async def _crop_image(
        self,
        image_bytes: bytes,
        crop_rect: tuple[int, int, int, int],
    ) -> bytes:
        """Crop image to specified rectangle."""
        # Stub: return original (real: use PIL/OpenCV)
        return image_bytes

    async def _write_screenshot_file(
        self,
        image_bytes: bytes,
        path: Path,
    ) -> None:
        """Write screenshot to PNG file."""
        path.write_bytes(image_bytes)

    async def _extract_metadata(
        self,
        screenshot_path: Path,
        image_bytes: bytes,
    ) -> dict[str, Any]:
        """Extract metadata via OCR + UI detection."""
        # Stub: return minimal metadata
        # Production: use pytesseract, detect_text(), ui_element_detection()
        return {
            "width": 1920,  # Estimated
            "height": 1080,  # Estimated
            "ocr_text": "",
            "ui_elements": [],
            "file_size_bytes": len(image_bytes),
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    async def _emit_scene_rendered_event(
        self,
        scene_id: str,
        capture_latency_ms: float,
        image_size_bytes: int,
        elements_detected: int,
        confidence: str,
    ) -> None:
        """Emit SceneRenderedEvent for per-scene feedback (ADR-0314)."""
        event_data = {
            "event_type": "scene_screenshot_captured",
            "scene_id": scene_id,
            "capture_latency_ms": capture_latency_ms,
            "image_size_bytes": image_size_bytes,
            "elements_detected": elements_detected,
            "confidence": confidence,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

        # Fire-and-forget to event emitter
        try:
            await self.event_emitter.emit("scene_screenshot_captured", event_data)
        except Exception as e:
            # Emit failure doesn't block workflow (fail-closed: log, continue)
            print(f"Failed to emit screenshot event for {scene_id}: {str(e)}")
