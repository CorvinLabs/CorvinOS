"""Video Assembler Worker: FFmpeg orchestration for video composition (Phase 3).

Stages:
1. Load assets (slides PNG, voice MP3, screenshot PNG)
2. Validate timing (narration duration ≤ slide display time)
3. Build FFmpeg filter graph (slides + audio + captions)
4. Execute encoding (MP4 composition)
5. Emit QualityFeedbackEvent (ADR-0314)
"""

from .assembler import VideoAssembler
from .filter_graph import FilterGraph

__all__ = ["VideoAssembler", "FilterGraph"]
