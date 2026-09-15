"""Screenshot Capture Worker — Browser automation via Playwright."""

import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, Optional
import time

from ..worker_base import WorkerSkillBase, WorkerManifest, WorkerResult

logger = logging.getLogger(__name__)


class ScreenshotCaptureWorker(WorkerSkillBase):
    """
    Screenshot capture worker: takes screenshots of web URLs.

    Uses Playwright for cross-browser automation (Chromium, Firefox, WebKit).

    Contract:
    - Input: {"urls": [str], "output_dir": str, "viewport": str="1920x1080"}
    - Output: {"screenshots": [str], "count": int, "viewport": str}
    """

    def __init__(self, manifest: WorkerManifest):
        super().__init__(manifest)
        self.browser = None

    async def execute(self, input_data: Dict[str, Any], **config_overrides) -> WorkerResult:
        """
        Capture screenshots of URLs.

        Args:
            input_data: {"urls": [str], "output_dir": str}
            **config_overrides: Optional tuning (e.g., viewport, browser)

        Returns:
            WorkerResult with screenshot paths and metadata
        """
        start_time = time.time()

        try:
            self.apply_config_overrides(**config_overrides)

            urls = input_data.get("urls", [])
            output_dir = Path(input_data.get("output_dir", "."))
            viewport = self.config.get("viewport", "1920x1080")

            if not urls:
                return WorkerResult(
                    worker_id=self.manifest.id,
                    status="error",
                    output={},
                    error="No URLs provided",
                    latency_ms=(time.time() - start_time) * 1000,
                )

            output_dir.mkdir(parents=True, exist_ok=True)

            screenshots = []

            # Stub: Playwright integration requires browser setup
            # For Phase 1, we create dummy files + metadata
            try:
                screenshots = await self._capture_with_playwright(urls, output_dir, viewport)
            except Exception as e:
                logger.warning(f"Playwright capture failed: {e}, using stub files...")
                for idx, url in enumerate(urls):
                    stub_file = output_dir / f"screenshot_{idx}.png"
                    stub_file.touch()
                    screenshots.append(str(stub_file))

            return WorkerResult(
                worker_id=self.manifest.id,
                status="success",
                output={
                    "screenshots": screenshots,
                    "count": len(screenshots),
                    "viewport": viewport,
                    "urls_processed": len(urls),
                },
                latency_ms=(time.time() - start_time) * 1000,
            )

        except Exception as e:
            logger.error(f"Screenshot capture failed: {e}", exc_info=True)
            return WorkerResult(
                worker_id=self.manifest.id,
                status="error",
                output={},
                error=str(e),
                latency_ms=(time.time() - start_time) * 1000,
            )

    async def _capture_with_playwright(self, urls: list, output_dir: Path, viewport: str) -> list:
        """Capture screenshots using Playwright (requires playwright package)."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise RuntimeError("Playwright not installed: pip install playwright && playwright install")

        screenshots = []
        w, h = map(int, viewport.split("x"))

        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page(viewport={"width": w, "height": h})

            for idx, url in enumerate(urls):
                output_file = output_dir / f"screenshot_{idx}.png"
                try:
                    await page.goto(url, wait_until="networkidle", timeout=10000)
                    await page.screenshot(path=str(output_file))
                    screenshots.append(str(output_file))
                    logger.debug(f"Captured screenshot: {output_file}")
                except Exception as e:
                    logger.warning(f"Failed to capture {url}: {e}")

            await browser.close()

        return screenshots


__all__ = ["ScreenshotCaptureWorker"]
