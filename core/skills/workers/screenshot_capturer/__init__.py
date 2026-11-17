"""Screenshot Capturer Worker: Browser automation for video screenshots (Phase 2).

Stages:
1. Console health check (precondition)
2. Instruction parser (scene-specific directions)
3. Screenshot capture via Playwright (optional scroll capture: one frame per position)
4. Crop + transform per instructions
5. OCR + UI element detection (stub)
6. Metadata extraction
7. SceneRenderedEvent emission (ADR-0314 feedback)
"""

from .capturer import ScreenshotCapturer
from .scroll import ScrollCaptureError, ScrollConfig

__all__ = ["ScreenshotCapturer", "ScrollCaptureError", "ScrollConfig"]
