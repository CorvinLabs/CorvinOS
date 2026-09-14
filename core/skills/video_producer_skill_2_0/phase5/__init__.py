"""Video Producer Phase 5.2 — 3-Tier Animation System + Learning

3-Tier Animation System (Phase 5.2):
- Tier 1 (Quick): ASCII + Simple SVG (<10s)
- Tier 2 (Rich): Manim Mathematical Animation (<60s)
- Tier 3 (Premium): Hand-Crafted/Blender (async, no blocking)

Features (Phase 5.2):
- TierDispatcher: Route requests with deterministic fallback chain
- LearningOptimizer: Track feedback, learn tier preferences
- Console API: Real-time metrics + job status
- Full E2E Pipeline: Quick→Rich→Premium with learning loop

Integration: Video Producer Skill 2.0 (Phases 5.1–5.4)
Audit: All rendering decisions logged + hash-chained
Learning: Feedback-based tier optimization (ADR-0314)
"""

from .manim_animator import (
    ManimAnimatorWorker,
    AnimationRequest,
    AnimationResult,
)

from .voice_sync_mapper import (
    VoiceSyncMapper,
    NarrationAudio,
    VoiceSyncMapping,
    Keyframe,
)

from .asset_library import (
    AssetLibraryManifest,
    AssetMetadata,
)

# Phase 5.2: Tier system + learning
from .quick_renderer import QuickRendererWorker
from .premium_renderer import PremiumAsyncQueue, PremiumRenderJob
from .tier_dispatcher import TierDispatcher, AnimationRequest as TierAnimationRequest, TierLevel
from .learning_integration import LearningOptimizer, RenderFeedback

__all__ = [
    # Phase 5.1
    "ManimAnimatorWorker",
    "AnimationRequest",
    "AnimationResult",
    "VoiceSyncMapper",
    "NarrationAudio",
    "VoiceSyncMapping",
    "Keyframe",
    "AssetLibraryManifest",
    "AssetMetadata",
    # Phase 5.2
    "QuickRendererWorker",
    "PremiumAsyncQueue",
    "PremiumRenderJob",
    "TierDispatcher",
    "TierLevel",
    "LearningOptimizer",
    "RenderFeedback",
]

__version__ = "5.2.0"
