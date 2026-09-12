"""Video Producer Skill 2.0: Orchestrated video production system.

Main components:
- Orchestrator: Maestro skill coordinating workflow
- AssetAnalyzer: Deep-read asset analysis (worker)
- StoryboardGenerator: LLM-constrained storyboard creation
- (Phases 2–4: Voice, Screenshots, Assembly, YouTube)
"""

from .orchestrator import VideoProducerOrchestrator
from .storyboard_generator import StoryboardGenerator
from .types import AssetAnalysisResult, Storyboard, Scene, FactualClaim
from .exceptions import (
    VideoProducerError,
    AssetIngestionError,
    AnalysisIncompleteError,
    AnalysisGateFailedError,
)

__all__ = [
    "VideoProducerOrchestrator",
    "StoryboardGenerator",
    "AssetAnalysisResult",
    "Storyboard",
    "Scene",
    "FactualClaim",
    "VideoProducerError",
    "AssetIngestionError",
    "AnalysisIncompleteError",
    "AnalysisGateFailedError",
]
