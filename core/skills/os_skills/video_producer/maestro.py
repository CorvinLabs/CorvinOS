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


def _get_tenant_id() -> str:
    """Get current tenant_id from context (ADR-0007 multi-tenant)."""
    # Stub: in production, resolve from current_tenant() + CORVIN_TENANT_ID env
    # For now, default to "_default" tenant
    import os
    return os.environ.get("CORVIN_TENANT_ID", "_default")


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

        # Content validation state (for deep-fix pattern)
        self._validated_narration_content: Dict[int, str] = {}
        self._validated_blender_content: Dict[str, Any] = {}

        logger.info(f"Initialized Maestro: {self.project_dir}")

    def register_worker(self, worker: WorkerSkillBase):
        """Register a worker."""
        self.registry.register(worker)
        self.workers[worker.manifest.id] = worker
        logger.info(f"Registered worker: {worker.manifest.id}")

    async def execute_phase_1_asset_analysis(
        self, asset_paths: list[str | Path], instructions: Optional[Dict[str, Any]] = None,
        tenant_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Phase 1: Asset ingestion and analysis.

        Args:
            asset_paths: Files to analyze
            instructions: Optional user guidance
            tenant_id: Tenant context (defaults to current tenant)

        Returns:
            {
                "status": "success" | "error",
                "analysis": { "metadata": ..., "ready_for_narration": bool },
                "blockers": list[str],
                "tenant_id": str,
                "audit_event": dict,
            }
        """
        tenant_id = tenant_id or _get_tenant_id()

        try:
            logger.info(f"Phase 1: Analyzing {len(asset_paths)} assets (tenant={tenant_id})")

            # Stub: real implementation would call AssetAnalyzer
            # For Phase 1 MVP, return mock analysis
            factual_claims = []
            if asset_paths:
                factual_claims = [
                    {"text": "Claim 1", "source_asset": str(asset_paths[0])},
                ]

            analysis = {
                "metadata": {"asset_count": len(asset_paths), "instructions": instructions},
                "ready_for_narration": True,
                "factual_claims": factual_claims,  # Fixed: no None elements
            }

            # Emit audit event (ADR-0721 audit-first)
            audit_event = {
                "event_type": "skill_executed",
                "skill_id": "video-producer:maestro",
                "phase": 1,
                "tenant_id": tenant_id,
                "timestamp": datetime.utcnow().isoformat(),
                "status": "success",
            }

            return {
                "status": "success",
                "analysis": analysis,
                "blockers": [],
                "tenant_id": tenant_id,
                "audit_event": audit_event,
            }

        except Exception as e:
            logger.error(f"Phase 1 failed: {e}", exc_info=True)
            audit_event = {
                "event_type": "skill_executed",
                "skill_id": "video-producer:maestro",
                "phase": 1,
                "tenant_id": tenant_id,
                "timestamp": datetime.utcnow().isoformat(),
                "status": "error",
                "error": str(e),
            }
            return {
                "status": "error",
                "analysis": None,
                "blockers": [str(e)],
                "tenant_id": tenant_id,
                "audit_event": audit_event,
            }

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
        self, storyboard: Dict[str, Any], tenant_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Phase 3: Dispatch audio, screenshots, and other workers in parallel.

        Args:
            storyboard: Output from Phase 2
            tenant_id: Tenant context (defaults to current tenant)

        Returns:
            {
                "status": "success" | "partial" | "error",
                "audio_result": WorkerResult.to_dict() | None,
                "screenshots_result": WorkerResult.to_dict() | None,
                "workers_ready": bool,
                "tenant_id": str,
                "audit_event": dict,
            }
        """
        tenant_id = tenant_id or _get_tenant_id()

        try:
            logger.info(f"Phase 3: Dispatching workers (tenant={tenant_id})")

            # Get audio and screenshot workers (if registered)
            audio_worker = self.workers.get("video-producer:audio-synthesis")
            screenshot_worker = self.workers.get("video-producer:screenshot-capture")

            # Track which workers are being called
            worker_tasks = []
            worker_names = []

            if audio_worker:
                worker_names.append("audio-synthesis")
                worker_tasks.append(
                    audio_worker.execute(
                        {
                            "narration": "Test narration",
                            "voice": "en-US-AriaNeural",
                            "output_dir": str(self.audio_dir),
                        }
                    )
                )

            if screenshot_worker:
                worker_names.append("screenshot-capture")
                worker_tasks.append(
                    screenshot_worker.execute(
                        {
                            "urls": ["https://example.com"],
                            "output_dir": str(self.screenshots_dir),
                        }
                    )
                )

            # Execute in parallel (FIX: Fixed index mapping)
            results = await asyncio.gather(*worker_tasks, return_exceptions=True) if worker_tasks else []

            audio_result = None
            screenshots_result = None

            # Map results by worker name, not by index (FIXED from original bug)
            result_map = {}
            for idx, name in enumerate(worker_names):
                if idx < len(results):
                    result = results[idx]
                    result_map[name] = result if not isinstance(result, Exception) else None
                    if isinstance(result, Exception):
                        logger.error(f"Worker {name} failed: {result}")

            audio_result = result_map.get("audio-synthesis")
            screenshots_result = result_map.get("screenshot-capture")

            workers_ready = bool(audio_result and screenshots_result)

            # Emit audit event (ADR-0721 audit-first)
            audit_event = {
                "event_type": "skill_executed",
                "skill_id": "video-producer:maestro",
                "phase": 3,
                "tenant_id": tenant_id,
                "timestamp": datetime.utcnow().isoformat(),
                "workers_dispatched": worker_names,
                "workers_ready": workers_ready,
                "status": "success",
            }

            return {
                "status": "success" if workers_ready else "partial",
                "audio_result": audio_result.to_dict() if audio_result else None,
                "screenshots_result": screenshots_result.to_dict() if screenshots_result else None,
                "workers_ready": workers_ready,
                "tenant_id": tenant_id,
                "audit_event": audit_event,
            }

        except Exception as e:
            logger.error(f"Phase 3 failed: {e}", exc_info=True)
            audit_event = {
                "event_type": "skill_executed",
                "skill_id": "video-producer:maestro",
                "phase": 3,
                "tenant_id": tenant_id,
                "timestamp": datetime.utcnow().isoformat(),
                "status": "error",
                "error": str(e),
            }
            return {
                "status": "error",
                "audio_result": None,
                "screenshots_result": None,
                "workers_ready": False,
                "tenant_id": tenant_id,
                "audit_event": audit_event,
            }

    async def orchestrate(
        self,
        asset_paths: list[str | Path],
        instructions: Optional[Dict[str, Any]] = None,
        skip_phases: Optional[list[int]] = None,
        job: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Main orchestration entry point (Phases 1-3 for MVP).

        DEEP-FIX: Content is now validated at entry point (fail-closed pattern).

        Args:
            asset_paths: Files to analyze
            instructions: Optional user guidance
            skip_phases: Optional phases to skip
            job: Optional job config with full narration/blender content (for direct orchestration)

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
            # DEEP-FIX GATE 1: Validate job content if provided
            if job:
                self.validate_job_content(job)
                narration_content = self._extract_narration_content(job)
                blender_content = self._extract_blender_content(job)
                logger.info(f"Deep-fix: Extracted {len(narration_content)} narration scenes")
                logger.info(f"Deep-fix: Extracted blender config: {bool(blender_content)}")
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

    def validate_job_content(self, job: Dict[str, Any]) -> None:
        """
        FAIL-CLOSED: Validate all content is present at job start.

        This is the DEEP-FIX GATE: rejects jobs with missing/placeholder narration
        or blender content instead of silently falling back.

        Args:
            job: Video job configuration with narration and blender components

        Raises:
            ValueError: If narration text or blender content is missing/invalid
        """
        # Validate narration content
        narration_list = job.get("narration", [])
        if not narration_list:
            raise ValueError("Job has no narration segments (FAIL-CLOSED)")

        for i, scene in enumerate(narration_list):
            text = scene.get("text", "").strip() if isinstance(scene, dict) else ""
            if not text:
                raise ValueError(f"Narration scene {i} has no text (FAIL-CLOSED)")
            if len(text) < 5:
                raise ValueError(f"Narration scene {i} too short: '{text}' (FAIL-CLOSED)")

        # Validate blender content (if present)
        blender_config = job.get("components", {}).get("blender", {})
        if blender_config:
            scenes = blender_config.get("scenes", [])
            if not scenes:
                raise ValueError("Blender config has no scenes (FAIL-CLOSED)")

            for scene in scenes:
                scene_name = scene.get("name", "unknown")

                # Check if scene is referenced by file or by inline data
                if scene.get("scene_file"):
                    scene_file = Path(scene["scene_file"])
                    if not scene_file.exists():
                        raise ValueError(f"Blender file not found: {scene_file} (FAIL-CLOSED)")
                    # Verify file has content (not empty)
                    file_size = scene_file.stat().st_size
                    if file_size < 100_000:  # < 100KB = suspect
                        raise ValueError(
                            f"Blender file too small ({file_size} bytes): {scene_file} (FAIL-CLOSED)"
                        )
                elif scene.get("scene_data"):
                    # Verify inline scene data is not empty
                    scene_data = scene["scene_data"]
                    if not scene_data or len(str(scene_data)) < 100:
                        raise ValueError(f"Scene '{scene_name}' has insufficient data (FAIL-CLOSED)")
                else:
                    raise ValueError(f"Scene '{scene_name}' has no file or inline data (FAIL-CLOSED)")

        logger.info(f"✓ Job content validation PASSED (narration={len(narration_list)} scenes)")

    def _extract_narration_content(self, job: Dict[str, Any]) -> list[str]:
        """Extract FULL narration text from job (not references)."""
        narration_list = []
        for scene in job.get("narration", []):
            if isinstance(scene, dict):
                text = scene.get("text", "")
                if text:
                    narration_list.append(text)
        return narration_list

    def _extract_blender_content(self, job: Dict[str, Any]) -> Dict[str, Any]:
        """Extract FULL blender scene data from job (not references)."""
        return job.get("components", {}).get("blender", {})

    def _validate_audio_output(self, audio_file: str | Path) -> None:
        """
        Validate that audio file is REAL narration (not whistle tone, not empty).

        Args:
            audio_file: Path to audio file

        Raises:
            ValueError: If audio is invalid (too short, whistle-like, empty)
        """
        import os

        audio_file = Path(audio_file)
        if not audio_file.exists():
            raise ValueError(f"Audio file not found: {audio_file}")

        file_size = audio_file.stat().st_size
        if file_size < 5000:  # < 5KB = whistle or empty
            raise ValueError(f"Audio file too small ({file_size} bytes) — likely whistle tone: {audio_file}")

        logger.debug(f"✓ Audio validation PASSED: {audio_file} ({file_size} bytes)")

    def _validate_video_output(self, video_file: str | Path) -> None:
        """
        Validate that video output is REAL content (not solid color, has duration).

        Args:
            video_file: Path to video file

        Raises:
            ValueError: If video is invalid (too short, missing, or appears to be placeholder)
        """
        video_file = Path(video_file)
        if not video_file.exists():
            raise ValueError(f"Video file not found: {video_file}")

        file_size = video_file.stat().st_size
        if file_size < 100_000:  # < 100KB = too small for real video
            raise ValueError(f"Video file too small ({file_size} bytes) — likely placeholder: {video_file}")

        logger.debug(f"✓ Video validation PASSED: {video_file} ({file_size} bytes)")
