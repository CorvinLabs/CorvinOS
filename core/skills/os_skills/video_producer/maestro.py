"""
Video Producer Maestro (Orchestrator) — Skill Forge v2.0 Compliance

Coordinates the 7-phase video production pipeline:
1. Asset ingestion + analysis
2. Storyboard generation
3. Worker dispatch (Phase 1: audio, screenshots; Phase 2+: effects, validation, upload)
4. Video assembly (FFmpeg mux)
5. Validation (quality gates)
6. YouTube upload (async)
7. Learning optimization

Phase 1 includes: orchestrator bootstrap, worker registration, Phases 1-3 execution.
"""

import asyncio
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Any, Dict

from .worker_base import WorkerRegistry, WorkerSkillBase, WorkerResult

logger = logging.getLogger(__name__)


class VideoProducerMaestro:
    """Main orchestrator for Video Producer Skill 2.0."""

    def __init__(self, project_dir: str | Path):
        """
        Initialize maestro with project directory.

        Args:
            project_dir: Project root (assets, audio, screenshots, output stored here)
        """
        self.project_dir = Path(project_dir)
        self.project_dir.mkdir(parents=True, exist_ok=True)

        # Create subdirs
        self.assets_dir = self.project_dir / "assets"
        self.audio_dir = self.project_dir / "audio"
        self.screenshots_dir = self.project_dir / "screenshots"
        self.output_dir = self.project_dir / "output"

        for d in [self.assets_dir, self.audio_dir, self.screenshots_dir, self.output_dir]:
            d.mkdir(parents=True, exist_ok=True)

        # Worker registry (will be populated by caller)
        self.registry = WorkerRegistry()
        self.workers: Dict[str, WorkerSkillBase] = {}

        logger.info(f"Initialized Maestro: {self.project_dir}")

    def register_worker(self, worker: WorkerSkillBase):
        """Register a worker."""
        self.registry.register(worker)
        self.workers[worker.manifest.id] = worker
        logger.info(f"Registered worker: {worker.manifest.id}")

    async def execute_phase_1_asset_analysis(
        self, asset_paths: list[str | Path], instructions: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Phase 1: Asset ingestion and analysis.

        Returns:
            {
                "status": "success" | "error",
                "analysis": { "metadata": ..., "ready_for_narration": bool },
                "blockers": list[str],
            }
        """
        try:
            logger.info(f"Phase 1: Analyzing {len(asset_paths)} assets")

            # Stub: real implementation would call AssetAnalyzer
            # For Phase 1 MVP, return mock analysis
            analysis = {
                "metadata": {"asset_count": len(asset_paths), "instructions": instructions},
                "ready_for_narration": True,
                "factual_claims": [
                    {"text": "Claim 1", "source_asset": str(asset_paths[0])} if asset_paths else None,
                ],
            }

            return {
                "status": "success",
                "analysis": analysis,
                "blockers": [],
            }

        except Exception as e:
            logger.error(f"Phase 1 failed: {e}")
            return {"status": "error", "analysis": None, "blockers": [str(e)]}

    async def execute_phase_2_storyboard(self, analysis: Dict[str, Any]) -> Dict[str, Any]:
        """
        Phase 2: Storyboard generation (LLM-constrained to analysis facts).

        Returns:
            {
                "status": "success" | "error",
                "storyboard": { "scenes": [...] },
            }
        """
        try:
            logger.info("Phase 2: Generating storyboard")

            # Stub: real implementation would call LLM with fact constraints
            storyboard = {
                "metadata": {"generated_at": datetime.utcnow().isoformat(), "source_constrained": True},
                "scenes": [
                    {"id": "s01", "narration": "Introduction", "duration_seconds": 30.0},
                    {"id": "s02", "narration": "Main content", "duration_seconds": 60.0},
                ],
            }

            return {"status": "success", "storyboard": storyboard}

        except Exception as e:
            logger.error(f"Phase 2 failed: {e}")
            return {"status": "error", "storyboard": None}

    async def execute_phase_3_worker_dispatch(
        self, storyboard: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Phase 3: Dispatch audio, screenshots, and other workers in parallel.

        Returns:
            {
                "status": "success" | "partial" | "error",
                "audio_result": WorkerResult.to_dict() | None,
                "screenshots_result": WorkerResult.to_dict() | None,
                "workers_ready": bool,
            }
        """
        try:
            logger.info("Phase 3: Dispatching workers")

            # Get audio and screenshot workers (if registered)
            audio_worker = self.workers.get("video-producer:audio-synthesis")
            screenshot_worker = self.workers.get("video-producer:screenshot-capture")

            # Prepare worker tasks
            tasks = []
            if audio_worker:
                tasks.append(
                    audio_worker.execute(
                        {
                            "narration": "Test narration",
                            "voice": "en-US-AriaNeural",
                            "output_dir": str(self.audio_dir),
                        }
                    )
                )

            if screenshot_worker:
                tasks.append(
                    screenshot_worker.execute(
                        {
                            "urls": ["https://example.com"],
                            "output_dir": str(self.screenshots_dir),
                        }
                    )
                )

            # Execute in parallel
            results = await asyncio.gather(*tasks, return_exceptions=True) if tasks else []

            audio_result = None
            screenshots_result = None

            if len(results) > 0:
                audio_result = results[0] if not isinstance(results[0], Exception) else None
                logger.debug(f"Audio result: {audio_result}")

            if len(results) > 1:
                screenshots_result = results[1] if not isinstance(results[1], Exception) else None
                logger.debug(f"Screenshots result: {screenshots_result}")

            workers_ready = bool(audio_result and screenshots_result)

            return {
                "status": "success" if workers_ready else "partial",
                "audio_result": audio_result.to_dict() if audio_result else None,
                "screenshots_result": screenshots_result.to_dict() if screenshots_result else None,
                "workers_ready": workers_ready,
            }

        except Exception as e:
            logger.error(f"Phase 3 failed: {e}")
            return {
                "status": "error",
                "audio_result": None,
                "screenshots_result": None,
                "workers_ready": False,
            }

    async def orchestrate(
        self,
        asset_paths: list[str | Path],
        instructions: Optional[Dict[str, Any]] = None,
        skip_phases: Optional[list[int]] = None,
    ) -> Dict[str, Any]:
        """
        Main orchestration entry point (Phases 1-3 for MVP).

        Returns:
            {
                "status": "success" | "partial" | "error",
                "analysis": dict,
                "storyboard": dict,
                "workers_ready": bool,
                "message": str,
            }
        """
        skip_phases = skip_phases or []

        try:
            # Phase 1: Analysis
            phase1 = await self.execute_phase_1_asset_analysis(asset_paths, instructions)
            if phase1["status"] == "error":
                return {
                    "status": "blocked",
                    "analysis": None,
                    "storyboard": None,
                    "workers_ready": False,
                    "message": f"Phase 1 blocked: {phase1['blockers']}",
                }

            # Phase 2: Storyboard (if Phase 1 succeeded)
            phase2 = await self.execute_phase_2_storyboard(phase1["analysis"]) if 2 not in skip_phases else {"status": "skipped", "storyboard": None}

            # Phase 3: Worker Dispatch (if Phase 2 succeeded)
            phase3 = (
                await self.execute_phase_3_worker_dispatch(phase2["storyboard"])
                if phase2.get("storyboard") and 3 not in skip_phases
                else {"status": "skipped", "workers_ready": False}
            )

            return {
                "status": "success" if phase3.get("workers_ready") else "partial",
                "analysis": phase1.get("analysis"),
                "storyboard": phase2.get("storyboard"),
                "workers_ready": phase3.get("workers_ready", False),
                "message": "Phases 1-3 complete" if phase3.get("workers_ready") else "Workers not ready (Phase 1-3 partial)",
            }

        except Exception as e:
            logger.error(f"Orchestration failed: {e}", exc_info=True)
            return {
                "status": "error",
                "analysis": None,
                "storyboard": None,
                "workers_ready": False,
                "message": f"Orchestration error: {str(e)}",
            }

    def get_worker(self, worker_id: str) -> Optional[WorkerSkillBase]:
        """Get worker by ID."""
        return self.workers.get(worker_id)

    def get_all_workers(self) -> Dict[str, WorkerSkillBase]:
        """Get all registered workers."""
        return self.workers.copy()

    def validate_registry(self) -> list[str]:
        """Validate all registered workers are Skill Forge v2.0 compliant."""
        return self.registry.validate_manifests()
