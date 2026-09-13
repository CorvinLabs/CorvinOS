"""Video Producer Skill 2.0 — Orchestrated Multi-Skill System

Implements the complete video production pipeline:
- Phase 1: Maestro Orchestrator + Asset Analyzer Worker
- Phase 2: Voice Synthesizer + Screenshot Capturer Workers
- Phase 3: Video Assembler + YouTube Uploader Workers

All phases enforce preconditions and capture per-scene feedback for learning loops.
"""

from .maestro import MaestroOrchestrator, VideoJob, VideoJobPhase
from .worker_manager import VideoProductionPipeline, WorkerRegistration

__all__ = [
    "MaestroOrchestrator",
    "VideoJob",
    "VideoJobPhase",
    "VideoProductionPipeline",
    "WorkerRegistration",
]

__version__ = "2.0.0"
