"""Renderer Base Class — Unified Interface for All Renderers

Consolidates common patterns across Quick/Premium/ThreeJS renderers:
- Standardized execute() method signature
- Unified result dictionary schema
- Common error handling
- Timeout management
- Metric tracking

All renderers (Quick, Premium, ThreeJS, Slide) inherit from this base.

Result Schema:
  {
    "success": bool,                    # True if render succeeded
    "tier": str,                        # "TIER_1_QUICK", "TIER_2_RICH", etc.
    "output_path": Optional[str],       # Path to output MP4 (if success)
    "render_time_ms": int,              # Milliseconds to render
    "quality_score": float,             # 0.0-1.0 quality estimate
    "error": Optional[str],             # Error message (if failed)
    "fallback_required": bool,          # True if next tier should try
  }

ADR-0742: 3-Tier Animation Architecture (Phase 5)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import time


@dataclass
class RendererRequest:
    """Base request for renderer execution.

    Subclasses may add tier-specific fields (narration_audio, voice_sync_mapping, etc.)
    """
    animation_id: str
    duration_seconds: int
    didactic_level: Optional[str] = None  # e.g., "elementary", "advanced"
    quality_target: Optional[str] = None  # e.g., "fast", "balanced", "high"


@dataclass
class RenderResult:
    """Standardized result from renderer execution.

    Every renderer must return this structure (or dict equivalent).
    """
    success: bool
    tier: str  # "TIER_1_QUICK", "TIER_2_RICH", "TIER_3_PREMIUM", etc.
    output_path: Optional[str] = None
    render_time_ms: int = 0
    quality_score: float = 0.0  # 0.0-1.0
    error: Optional[str] = None
    fallback_required: bool = False
    context_data: Optional[dict] = None  # Renderer-specific metadata

    def to_dict(self) -> dict:
        """Convert to dict for backward compatibility."""
        return {
            "success": self.success,
            "tier": self.tier,
            "output_path": self.output_path,
            "render_time_ms": self.render_time_ms,
            "quality_score": self.quality_score,
            "error": self.error,
            "fallback_required": self.fallback_required,
            **(self.context_data or {}),
        }


class RendererBase(ABC):
    """Base class for all renderer implementations.

    Enforces common interface:
    - __init__(timeout_seconds: int): Constructor with timeout
    - execute(request: RendererRequest) -> dict: Execution method
    - Standardized error handling & timeouts
    - Metric tracking

    Subclasses implement:
    - _render(): Actual rendering logic
    - Renderer-specific configuration

    Example:
        class QuickRendererWorker(RendererBase):
            def __init__(self, timeout_seconds: int = 10):
                super().__init__(
                    name="quick_renderer",
                    version="5.1.0",
                    tier="TIER_1_QUICK",
                    timeout_seconds=timeout_seconds
                )

            def _render(self, request: RendererRequest) -> RenderResult:
                # Implement actual rendering here
                pass
    """

    def __init__(
        self,
        name: str,
        version: str,
        tier: str,
        timeout_seconds: int = 10,
        quality_score_default: float = 0.7,
    ):
        """Initialize renderer base.

        Args:
            name: Renderer name (e.g., "quick_renderer")
            version: Semantic version (e.g., "5.1.0")
            tier: Tier identifier (e.g., "TIER_1_QUICK")
            timeout_seconds: Hard timeout for render operation
            quality_score_default: Default quality score if not overridden
        """
        self.name = name
        self.version = version
        self.tier = tier
        self.timeout = timeout_seconds
        self.quality_score_default = quality_score_default

        # Metrics
        self.total_renders = 0
        self.successful_renders = 0
        self.failed_renders = 0
        self.total_render_time_ms = 0

    def execute(self, request) -> dict:
        """Execute rendering with standard error handling.

        This method handles:
        1. Timeout enforcement
        2. Exception catching
        3. Standardized result dict
        4. Metric tracking

        Subclasses should override _render() instead of execute().

        Args:
            request: Renderer request (RendererRequest or compatible dict)

        Returns:
            Standardized dict with {success, tier, output_path, render_time_ms, ...}
        """
        start_time = time.time()
        self.total_renders += 1

        try:
            # Call subclass-specific render logic
            result = self._render(request)

            # Ensure result is dict (not RenderResult object)
            if isinstance(result, RenderResult):
                result = result.to_dict()

            # Track metrics
            self.total_render_time_ms += result.get("render_time_ms", 0)
            if result.get("success"):
                self.successful_renders += 1
            else:
                self.failed_renders += 1

            return result

        except TimeoutError as e:
            # Handle timeout (should be caught by _render, but fallback here)
            render_time_ms = int((time.time() - start_time) * 1000)
            self.failed_renders += 1
            self.total_render_time_ms += render_time_ms

            return {
                "success": False,
                "tier": self.tier,
                "error": f"Timeout: {str(e)}",
                "render_time_ms": render_time_ms,
                "quality_score": 0.0,
                "fallback_required": True,
            }

        except Exception as e:
            # Catch any other exception
            render_time_ms = int((time.time() - start_time) * 1000)
            self.failed_renders += 1
            self.total_render_time_ms += render_time_ms

            return {
                "success": False,
                "tier": self.tier,
                "error": f"{self.name} error: {str(e)}",
                "render_time_ms": render_time_ms,
                "quality_score": 0.0,
                "fallback_required": True,
            }

    @abstractmethod
    def _render(self, request) -> dict:
        """Subclass-specific rendering logic.

        Must be overridden by subclasses.

        Args:
            request: Renderer request

        Returns:
            Dict with {success, tier, output_path, render_time_ms, quality_score, error}
        """
        pass

    def get_metrics(self) -> dict:
        """Get renderer metrics.

        Returns:
            {
                "total_renders": int,
                "successful_renders": int,
                "failed_renders": int,
                "success_rate": float,  # 0.0-1.0
                "avg_render_time_ms": float,
            }
        """
        total = self.total_renders
        if total == 0:
            return {
                "total_renders": 0,
                "successful_renders": 0,
                "failed_renders": 0,
                "success_rate": 0.0,
                "avg_render_time_ms": 0.0,
            }

        success_rate = self.successful_renders / total
        avg_time = self.total_render_time_ms / total

        return {
            "total_renders": total,
            "successful_renders": self.successful_renders,
            "failed_renders": self.failed_renders,
            "success_rate": success_rate,
            "avg_render_time_ms": avg_time,
        }
