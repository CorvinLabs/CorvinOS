"""Console Renderers — M2–M6 Progressive Feature Implementation

Renderer types (tiers):
- Tier 1: Slide (baseline, always succeeds) — M2
- Tier 1.5: SVG (vector graphics) — M3
- Tier 2: Chart (data viz + learning loop) — M4
- Tier 3: Blender-3D (async, 3D rendering) — M5
- Tier 3.5: Screencast-Overlay (live annotation) — M6

All renderers implement RendererBase protocol.
Dispatcher routes via fallback chain (fail to lower tier).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class RendererBase(ABC):
    """Abstract base for all console renderers.

    Each renderer must implement execute(request) → output_dict.
    """
    name: str
    version: str = "1.0.0"
    tier: int = 1  # Tier number (1, 1.5, 2, 3, 3.5)
    timeout_seconds: int = 10

    @abstractmethod
    async def execute(self, request: Any) -> Dict[str, Any]:
        """Execute rendering request.

        Args:
            request: ConsoleRequest with payload

        Returns:
            Output dictionary {format, content, quality_score, etc.}

        Raises:
            Exception on failure (caught by dispatcher + fallback chain)
        """
        pass


__all__ = ["RendererBase"]
