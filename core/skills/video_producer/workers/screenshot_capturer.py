"""Screenshot Capturer Worker: Phase 4 Real Playwright Integration

Captures screenshots of websites/consoles during narration:
1. Parse target URLs from narration or job config
2. Launch browser with Playwright and navigate
3. Wait for dynamic content to load
4. Capture screenshots at narration milestones
5. Save PNG files with metadata
"""

from dataclasses import dataclass
from typing import List, Optional, Dict
import json
import asyncio
import os
from pathlib import Path

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


@dataclass
class ScreenshotResult:
    """Screenshot capture result"""
    screenshots: List[str]  # Paths to captured PNG files
    total_duration_seconds: float
    num_captured: int
    confidence: float
    success: bool = True


class ScreenshotCapturerWorker:
    """Worker Skill: Capture screenshots of websites/consoles

    Phase 4: Real Playwright-based browser automation
    Supports:
    - URL parsing from narration
    - Real Playwright browser automation (chromium, firefox, webkit)
    - Parallel screenshot capture
    - PNG output with metadata
    - Dynamic content waiting
    - Viewport configuration
    """

    def __init__(self, browser_type: str = "chromium", headless: bool = True):
        self.name = "screenshot_capturer"
        self.version = "4.0.0"  # Phase 4
        self.browser_type = browser_type  # chromium, firefox, webkit
        self.headless = headless

    def execute(self, job) -> ScreenshotResult:
        """Execute screenshot capture phase with REAL Playwright

        Args:
            job: VideoJob instance

        Returns:
            ScreenshotResult with captured files and metadata
        """

        screenshots = []
        total_duration = 0.0

        # Run async Playwright in sync context
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            captured = loop.run_until_complete(self._execute_async(job))
            screenshots = captured
        finally:
            loop.close()

        # ======== GATE 3: Visual-Content-Spec (Fail-Closed) ========
        # Must pass BEFORE returning result
        try:
            self._validate_screenshot_content(screenshots)
        except ValueError as e:
            print(f"  ✗ {e}")
            return ScreenshotResult(
                screenshots=[],
                total_duration_seconds=0,
                num_captured=0,
                confidence=0,
                success=False
            )

        return ScreenshotResult(
            screenshots=screenshots,
            total_duration_seconds=total_duration,
            num_captured=len(screenshots),
            confidence=0.91,
        )

    async def _execute_async(self, job) -> List[str]:
        """Async execution of screenshot capture using Playwright

        Args:
            job: VideoJob instance

        Returns:
            List of screenshot file paths
        """

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            # Fallback to mock if Playwright not available
            return self._execute_mock(job)

        screenshots = []

        async with async_playwright() as p:
            # Launch browser
            browser_cls = getattr(p, self.browser_type, p.chromium)
            browser = await browser_cls.launch(headless=self.headless)

            try:
                for i, scene_narration in enumerate(job.narration):
                    # Determine target for this scene
                    target_url = self._parse_target_url(scene_narration)

                    if target_url:
                        # Create new page/context
                        context = await browser.new_context(
                            viewport={"width": 1920, "height": 1080}
                        )
                        page = await context.new_page()

                        try:
                            # Navigate to URL
                            await page.goto(target_url, wait_until="networkidle", timeout=10000)

                            # Wait for common loading indicators to disappear
                            try:
                                await page.wait_for_load_state("domcontentloaded", timeout=5000)
                            except:
                                pass  # Timeout is OK, page might be fully loaded

                            # Capture screenshot
                            screenshot_path = f"/tmp/{job.job_id}_screenshot_{i}.png"
                            await page.screenshot(path=screenshot_path, full_page=True)

                            screenshots.append(screenshot_path)

                        except Exception as e:
                            print(f"Screenshot capture failed for {target_url}: {e}")
                            # Try mock fallback
                            mock_path = self._capture_screenshot_mock(
                                target_url, job.job_id, i
                            )
                            screenshots.append(mock_path)

                        finally:
                            await context.close()

            finally:
                await browser.close()

        return screenshots

    def _execute_mock(self, job) -> List[str]:
        """Mock screenshot capture (fallback if Playwright unavailable)

        Args:
            job: VideoJob instance

        Returns:
            List of mock screenshot paths
        """

        screenshots = []

        for i, scene_narration in enumerate(job.narration):
            target_url = self._parse_target_url(scene_narration)
            if target_url:
                screenshot_path = self._capture_screenshot_mock(
                    target_url, job.job_id, i
                )
                screenshots.append(screenshot_path)

        return screenshots

    def _parse_target_url(self, narration: str) -> Optional[str]:
        """Parse target URL from narration

        Phase 4: Keyword-based URL extraction (simple but effective)

        Args:
            narration: Scene narration text

        Returns:
            Target URL or None if not found
        """

        narration_lower = narration.lower()

        # Pattern matching for common targets
        url_map = {
            "console": "http://localhost:8765/console/",
            "dashboard": "http://localhost:8765/console/dashboard",
            "vibe": "http://localhost:8765/console/vibe",
            "settings": "http://localhost:8765/console/settings",
            "skills": "http://localhost:8765/console/skills",
            "plugins": "http://localhost:8765/console/plugins",
            "audit": "http://localhost:8765/console/audit",
            "learning": "http://localhost:8765/console/learning",
            "video": "http://localhost:8765/console/video-producer",
        }

        for keyword, url in url_map.items():
            if keyword in narration_lower:
                return url

        return None

    def _capture_screenshot_mock(
        self, url: str, job_id: str, scene_index: int
    ) -> str:
        """Create mock screenshot (fallback)

        Args:
            url: URL
            job_id: Job identifier
            scene_index: Scene index

        Returns:
            Path to mock PNG file
        """

        output_path = f"/tmp/{job_id}_screenshot_{scene_index}.png"

        # Create a simple PNG placeholder (1x1 pixel white image)
        # Minimal PNG: 8-byte signature + IHDR chunk + IDAT chunk + IEND chunk
        png_data = (
            b'\x89PNG\r\n\x1a\n'  # PNG signature
            b'\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
            b'\x08\x02\x00\x00\x00\x90wS\xde'  # IHDR
            b'\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05'
            b'\xf7\xce\x1e\xf4'  # IDAT (white pixel)
            b'\x00\x00\x00\x00IEND\xaeB`\x82'  # IEND
        )

        with open(output_path, "wb") as f:
            f.write(png_data)

        # Write metadata JSON
        metadata_path = output_path.replace(".png", "_metadata.json")
        metadata = {
            "url": url,
            "scene_index": scene_index,
            "width": 1920,
            "height": 1080,
            "format": "png",
            "captured_at": "mock",
        }

        with open(metadata_path, "w") as f:
            json.dump(metadata, f)

        return output_path

    def _validate_screenshot_content(
        self,
        screenshot_paths: List[str],
        min_unique_colors: int = 10,
        max_solid_color_threshold: float = 0.95,
    ) -> None:
        """GATE 3: Visual-Content-Spec — Fail-Closed Validation (ADR-0720)

        Rejects screenshots that are placeholder/solid-color images.
        This is a fail-closed gate: if visual content is inadequate, raise immediately.

        Args:
            screenshot_paths: List of screenshot file paths
            min_unique_colors: Minimum number of unique colors for real content
            max_solid_color_threshold: Max percentage of pixels in dominant color (>95% = reject)

        Raises:
            ValueError: If screenshots are placeholders or have insufficient content
        """
        if not screenshot_paths:
            raise ValueError(
                "Visual-Content-Spec Gate FAILED: No screenshots captured. "
                "Video requires visual content."
            )

        # If PIL not available, do basic validation (file exists and non-zero size)
        if not HAS_PIL:
            print("  ℹ PIL not available, skipping advanced color analysis")
            for path in screenshot_paths:
                if not os.path.exists(path):
                    raise ValueError(
                        f"Visual-Content-Spec Gate FAILED: Screenshot not found: {path}"
                    )
                file_size = os.path.getsize(path)
                if file_size < 1000:  # Less than 1KB = placeholder
                    raise ValueError(
                        f"Visual-Content-Spec Gate FAILED: Screenshot suspiciously small "
                        f"({file_size} bytes). Likely a placeholder image."
                    )
            return

        # PIL available: do advanced analysis
        invalid_screenshots = []

        for i, screenshot_path in enumerate(screenshot_paths):
            if not os.path.exists(screenshot_path):
                invalid_screenshots.append(f"Screenshot {i}: File not found")
                continue

            try:
                img = Image.open(screenshot_path)

                # Check 1: Image must have reasonable dimensions
                width, height = img.size
                if width < 100 or height < 100:
                    invalid_screenshots.append(
                        f"Screenshot {i}: Too small ({width}x{height}px, minimum 100x100px)"
                    )
                    continue

                # Check 2: Analyze color diversity
                pixels = list(img.getdata())
                unique_colors = len(set(pixels))

                if unique_colors < min_unique_colors:
                    invalid_screenshots.append(
                        f"Screenshot {i}: Insufficient color diversity "
                        f"({unique_colors} colors < {min_unique_colors} minimum). "
                        f"Likely solid-color placeholder."
                    )
                    continue

                # Check 3: Detect if one color dominates (>95% same color = placeholder)
                if len(pixels) > 0:
                    color_counts = {}
                    for pixel in pixels:
                        color_counts[pixel] = color_counts.get(pixel, 0) + 1

                    dominant_color_ratio = max(color_counts.values()) / len(pixels)

                    if dominant_color_ratio > max_solid_color_threshold:
                        invalid_screenshots.append(
                            f"Screenshot {i}: Dominant color covers {dominant_color_ratio*100:.1f}% "
                            f"(threshold {max_solid_color_threshold*100:.0f}%). "
                            f"Likely solid-color placeholder."
                        )

            except Exception as e:
                invalid_screenshots.append(f"Screenshot {i}: Analysis error - {e}")

        if invalid_screenshots:
            raise ValueError(
                f"Visual-Content-Spec Gate FAILED: Screenshots contain placeholders or insufficient content:\n  - "
                + "\n  - ".join(invalid_screenshots)
            )
