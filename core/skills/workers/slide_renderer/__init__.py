"""Slide Renderer Worker: PowerPoint to PNG conversion for video (Phase 3).

Stages:
1. Load PowerPoint file
2. Render each slide to PNG
3. Extract speaker notes (for narration sync)
4. Measure slide display timing
5. Emit QualityFeedbackEvent (ADR-0314)
"""

from .renderer import SlideRenderer

__all__ = ["SlideRenderer"]
