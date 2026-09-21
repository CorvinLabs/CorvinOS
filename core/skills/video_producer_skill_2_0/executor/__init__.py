"""Executor — Phase 2

Orchestrates full video generation pipeline.
"""

from .orchestrator import VideoExecutionOrchestrator, execute_video_spec

__all__ = ["VideoExecutionOrchestrator", "execute_video_spec"]
