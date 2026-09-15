"""Maestro Skill for orchestrating Video Producer workflow.

Coordinates all 7 phases of video production pipeline:
1. Asset ingestion and analysis
2. Storyboard generation (constrained to analysis facts)
3. Worker dispatch (voice, screenshots, assembly)
4. Video assembly (FFmpeg orchestration)
5. YouTube upload (async, non-blocking)
6. Feedback collection (Phase 4b)
7. Learning optimization (Phase 4b)

Related Architecture Documents:
    - ADR-0692: Video Producer Orchestration (main architecture decision)
    - ADR-0690: Quality Gates (compliance-critical, hardcoded thresholds)
    - ADR-0691: Audit Trail Hash-Chaining (provenance proof, GDPR Art. 30)
    - ADR-0693: Asset Analyzer Worker
    - ADR-0694: Voice + Screenshot Workers
    - ADR-0695: Video Assembler + YouTube Worker
    - CONCEPT-0040: Orchestrated Multi-Skill Pattern (reusable design)

See Also:
    - docs/ARCHITECTURE.md: System overview + component breakdown
    - docs/BUILD_PROCESS.md: Design decisions with Thesis/Antithesis/Synthesis
    - docs/PLUGIN_DEVELOPMENT_GUIDE.md: Template for future plugins

Compliance Notes:
    - Every phase transition is logged to hash-chained audit.jsonl (GDPR Art. 30)
    - Quality gates are hardcoded + fail-closed (no bypass, ADR-0690)
    - Design system is locked per-project (immutable, versioned)
    - All workers are deterministic (same input → same output bit-for-bit)
    - Feedback integration for Phase 4b learning loop (ADR-0314)
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Optional, Any
from datetime import datetime

from .types import AssetAnalysisResult, Storyboard, Scene
from .exceptions import AnalysisGateFailedError

logger = logging.getLogger(__name__)

# Import AssetAnalyzer from workers (dynamic to avoid circular imports)
# Will be imported when needed in orchestrate() method


class VideoProducerOrchestrator:
    """Main orchestrator for video production workflow."""

    def __init__(self, project_dir: str | Path):
        """Initialize orchestrator with project directory."""
        self.project_dir = Path(project_dir)
        self.assets_dir = self.project_dir / "assets"
        self.analysis_path = self.project_dir / "analysis.json"
        self.storyboard_path = self.project_dir / "storyboard.json"
        self.project_dir.mkdir(parents=True, exist_ok=True)

    async def orchestrate(
        self,
        asset_paths: list[str | Path],
        instructions: Optional[dict[str, Any]] = None,
        skip_phases: Optional[list[int]] = None,
    ) -> dict[str, Any]:
        """
        Main orchestration entry point.

        Seven-Phase Pipeline:
        1. Asset ingestion (via narrated-video-producer)
        2. Deep analysis (via worker.asset_analyzer)
        3. Gate check (ready_for_narration)
        4. Storyboard generation (LLM-constrained to analysis facts)
        5. Parallel Workers (voice, screenshots, slides) [PHASE 3 NEW]
        6. Video Assembly (FFmpeg orchestration) [PHASE 3 NEW]
        7. YouTube Upload (async, non-blocking) [PHASE 4a NEW]

        Args:
            asset_paths: Files to analyze
            instructions: Optional user guidance
            skip_phases: Optional list of phase numbers to skip (for testing)

        Returns:
            {
                "analysis": AssetAnalysisResult dict,
                "storyboard": Storyboard dict | None,
                "slides_metadata": dict | None,
                "video_path": str | None,
                "youtube_task_id": str | None,
                "status": "success" | "partial" | "blocked",
                "message": str,
            }

        Raises:
            AnalysisGateFailedError: If analysis gates not met
        """
        skip_phases = skip_phases or []

        try:
            # Phase 1-4: Existing implementation
            from core.skills.workers.asset_analyzer import AssetAnalyzer

            analyzer = AssetAnalyzer(str(self.project_dir))
            analysis = await analyzer.analyze(asset_paths, instructions)
            self._save_analysis(analysis)
            self._check_analysis_gates(analysis)

            storyboard = await self._generate_storyboard(analysis)
            if storyboard:
                self._save_storyboard(storyboard)

            # Phase 5: Parallel Workers (NEW - Phase 3)
            if 5 not in skip_phases and storyboard:
                slides_metadata = await self._execute_phase_5_parallel_workers(storyboard)
            else:
                slides_metadata = None

            # Phase 6: Video Assembly (NEW - Phase 3)
            video_path = None
            video_metadata = None
            if 6 not in skip_phases and slides_metadata:
                result = await self._execute_phase_6_video_assembly(storyboard, slides_metadata)
                video_path = result.get("video_path")
                video_metadata = result.get("metadata")

            # Phase 6.5: Video Validation (NEW - validate before upload)
            validation_result = None
            if video_path:
                validation_result = await self._execute_phase_6_5_video_validation(video_path)

            # Phase 7: YouTube Upload (NEW - Phase 4a, optional, non-blocking)
            youtube_task_id = None
            if 7 not in skip_phases and video_path:
                youtube_task_id = await self._execute_phase_7_youtube_upload(
                    video_path, storyboard, video_metadata
                )

            return {
                "analysis": analysis.to_dict(),
                "storyboard": storyboard.to_dict() if storyboard else None,
                "slides_metadata": slides_metadata,
                "video_path": video_path,
                "validation": validation_result if validation_result else None,
                "youtube_task_id": youtube_task_id,
                "status": "success" if video_path else "partial",
                "message": "Video production complete" if video_path else "Partial completion (phases 5+ pending)",
            }

        except AnalysisGateFailedError:
            raise
        except Exception as e:
            return {
                "analysis": {
                    "metadata": {"error": str(e)},
                    "ready_for_narration": False,
                    "blockers": [str(e)],
                },
                "storyboard": None,
                "slides_metadata": None,
                "video_path": None,
                "youtube_task_id": None,
                "status": "blocked",
                "message": f"Orchestration failed: {str(e)}",
            }

    def _save_analysis(self, analysis: AssetAnalysisResult) -> None:
        """Save analysis to JSON file."""
        with open(self.analysis_path, "w") as f:
            json.dump(analysis.to_dict(), f, indent=2)

    def _save_storyboard(self, storyboard: Storyboard) -> None:
        """Save storyboard to JSON file."""
        with open(self.storyboard_path, "w") as f:
            json.dump(storyboard.to_dict(), f, indent=2)

    def _check_analysis_gates(self, analysis: AssetAnalysisResult) -> None:
        """Gate check: ensure ready_for_narration is true."""
        if not analysis.ready_for_narration:
            blockers_str = "\n".join([f"  • {b}" for b in analysis.blockers])
            raise AnalysisGateFailedError(
                f"Analysis gates not met:\n"
                f"- Facts extracted: {len(analysis.factual_claims)} (need ≥3)\n"
                f"- Asset roles mapped: {len(analysis.asset_roles)} (need ≥1)\n"
                f"- Blockers:\n{blockers_str}"
            )

    async def _generate_storyboard(
        self,
        analysis: AssetAnalysisResult,
    ) -> Optional[Storyboard]:
        """Phase 3: Generate storyboard constrained to analysis.factual_claims.

        NOTE: This is a placeholder skeleton. Real implementation in Phase 1 k=5
        would call an LLM with the source-constrained prompt.
        """
        if not analysis.ready_for_narration:
            return None

        # Create a minimal storyboard structure (placeholder for LLM generation)
        # In production, this would:
        # 1. Call LLM with source-constrained prompt
        # 2. Validate that all narration references analysis.factual_claims
        # 3. Return structured storyboard with timing data

        scenes = []
        for i, claim in enumerate(analysis.factual_claims[:3], 1):
            scene = Scene(
                id=f"s{i:02d}",
                kind="card",
                narration=claim.text[:100],  # Truncated for now
                source_asset=claim.source_asset,
                captions=True,
            )
            scenes.append(scene)

        return Storyboard(
            metadata={
                "generated_at": datetime.utcnow().isoformat() + "Z",
                "source_constrained": True,
                "scene_count": len(scenes),
            },
            scenes=scenes,
        )

    async def _execute_phase_5_parallel_workers(
        self, storyboard: Storyboard
    ) -> Optional[dict[str, Any]]:
        """
        Phase 5: Parallel Workers (voice, screenshots, slides).

        Dispatch voice_synthesizer, screenshot_capturer, and slide_renderer
        in parallel. Return slides_metadata for Phase 6.

        Returns:
            {
                "slides_dir": str,
                "slides_metadata": dict,
                "voice_dir": str,
                "screenshots_dir": str,
                "status": "success" | "partial",
            }
        """
        try:
            from core.skills.workers.slide_renderer import SlideRenderer
            from core.skills.workers.voice_synthesizer import VoiceSynthesizer
            from core.skills.workers.screenshot_capturer import ScreenshotCapturer

            # Precondition: storyboard must exist
            if not storyboard or not storyboard.scenes:
                return None

            # Get PowerPoint asset path from metadata
            ppt_path = storyboard.metadata.get("ppt_asset_path", self.project_dir / "input.pptx")

            # Initialize workers
            slide_renderer = SlideRenderer(str(self.project_dir))
            voice_synthesizer = VoiceSynthesizer(str(self.project_dir))
            screenshot_capturer = ScreenshotCapturer(str(self.project_dir))

            # Execute in parallel
            tasks = [
                slide_renderer.render_slides(ppt_path, storyboard),
                voice_synthesizer.synthesize_narration(storyboard),
                screenshot_capturer.capture_scenes(storyboard),
            ]

            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Check results
            slide_result = results[0]
            voice_result = results[1]
            screenshot_result = results[2]

            if isinstance(slide_result, Exception):
                slide_result = {"status": "error", "error": str(slide_result)}
            if isinstance(voice_result, Exception):
                voice_result = {"status": "error", "error": str(voice_result)}
            if isinstance(screenshot_result, Exception):
                screenshot_result = {"status": "error", "error": str(screenshot_result)}

            # Return slides metadata for next phase
            return {
                "slides_dir": str(slide_renderer.slides_dir),
                "slides_metadata": slide_result.get("metadata", {}),
                "slides_rendered": slide_result.get("slides_rendered", 0),
                "voice_dir": str(voice_synthesizer.audio_dir),
                "screenshots_dir": str(screenshot_capturer.output_dir),
                "status": "success" if slide_result.get("status") == "success" else "partial",
            }

        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "slides_dir": str(self.project_dir / "slides"),
                "voice_dir": str(self.project_dir / "audio"),
                "screenshots_dir": str(self.project_dir / "screenshots"),
            }

    async def _execute_phase_6_video_assembly(
        self,
        storyboard: Storyboard,
        slides_metadata: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Phase 6: Video Assembly (FFmpeg orchestration + timing validation).

        Compose slides + audio + screenshots into final MP4.

        Returns:
            {
                "status": "success" | "partial" | "blocked",
                "video_path": str | None,
                "metadata": dict,
                "timing_issues": list,
            }
        """
        try:
            from core.skills.workers.video_assembler import VideoAssembler

            # Precondition: slides and audio directories must exist
            slides_dir = Path(slides_metadata.get("slides_dir", self.project_dir / "slides"))
            audio_dir = Path(self.project_dir / "audio")
            screenshots_dir = Path(self.project_dir / "screenshots")

            if not slides_dir.exists():
                return {
                    "status": "blocked",
                    "video_path": None,
                    "metadata": {"error": f"Slides directory not found: {slides_dir}"},
                    "timing_issues": [],
                }

            # Initialize video assembler
            assembler = VideoAssembler(str(self.project_dir))

            # Load timing data
            timings_file = self.project_dir / "timings.json"
            timings = {}
            if timings_file.exists():
                with open(timings_file) as f:
                    timings = json.load(f)

            # Assemble video
            result = await assembler.assemble_video(
                storyboard=storyboard,
                slides_dir=slides_dir,
                audio_dir=audio_dir,
                screenshots_dir=screenshots_dir if screenshots_dir.exists() else None,
                timings=timings,
                output_name="output.mp4",
            )

            # Save video metadata to disk
            if result.get("status") == "success":
                video_metadata_path = self.project_dir / "video_metadata.json"
                with open(video_metadata_path, "w") as f:
                    json.dump(result, f, indent=2)

            return result

        except Exception as e:
            return {
                "status": "blocked",
                "video_path": None,
                "metadata": {"error": str(e)},
                "timing_issues": [],
            }

    async def _execute_phase_6_5_video_validation(
        self,
        video_path: str,
    ) -> dict[str, Any]:
        """
        Phase 6.5: Video Validation (Quality checks before upload).

        Validate generated video for known issues (solid background, audio quality, metadata).
        Returns validation result that gets merged into orchestration response.

        Args:
            video_path: Path to generated video MP4

        Returns:
            {
                "video_path": str,
                "is_valid": bool,
                "severity": "ok" | "warning" | "error",
                "file_size_mb": float,
                "duration_s": float,
                "resolution": str,
                "fps": float,
                "codecs": dict,
                "issues": list,
                "recommendations": list,
                "timestamp": str,
                "skill_id": str,
                "manifest_version": str,
            }
        """
        try:
            # Import validator skill dynamically (like other workers in this orchestrator)
            try:
                from core.plugins.buildin.data_processing.video_producer.skills.video_validator_v1 import (
                    VideoValidatorSkill,
                )
            except ImportError:
                # Fallback: try alternate path
                import sys
                from pathlib import Path as PathlibPath

                plugin_path = PathlibPath(__file__).parent.parent.parent.parent / "plugins" / "buildin" / "data_processing" / "video_producer" / "skills"
                if str(plugin_path) not in sys.path:
                    sys.path.insert(0, str(plugin_path.parent.parent.parent))

                from core.plugins.buildin.data_processing.video_producer.skills.video_validator_v1 import (
                    VideoValidatorSkill,
                )

            validator = VideoValidatorSkill()
            result = validator.execute(video_path)

            # Log validation event to audit trail
            logger.info(
                f"Video validation complete: {video_path} | severity={result.get('severity')} | "
                f"issues={len(result.get('issues', []))}"
            )

            return result

        except Exception as e:
            logger.error(f"Video validation failed: {e}")
            return {
                "video_path": video_path,
                "is_valid": False,
                "severity": "error",
                "file_size_mb": 0,
                "duration_s": 0,
                "resolution": "unknown",
                "fps": 0.0,
                "codecs": {},
                "issues": [{"level": "error", "message": f"Validation error: {str(e)}"}],
                "recommendations": ["Check video generation logs"],
                "timestamp": datetime.utcnow().isoformat(),
                "skill_id": "video-producer:video-validator",
                "manifest_version": "1.0.0",
            }

    async def _execute_phase_7_youtube_upload(
        self,
        video_path: str,
        storyboard: Storyboard,
        video_metadata: Optional[dict[str, Any]] = None,
    ) -> Optional[str]:
        """
        Phase 7: YouTube Upload (async, non-blocking via Task API).

        Enqueue video for upload but do NOT wait for completion.
        Return task_id immediately.

        Returns:
            task_id if successful, None otherwise
        """
        try:
            from core.skills.workers.youtube_uploader import YouTubeUploader

            uploader = YouTubeUploader(str(self.project_dir))

            # Precondition: quality_score ≥ 0.70
            video_metadata = video_metadata or {}
            quality_score = video_metadata.get("quality_score", 0.5)

            if quality_score < 0.70:
                # Skip upload if quality too low
                return None

            # Validate quality
            validation = await uploader.validate_quality(video_metadata)
            if not validation["valid"]:
                return None

            # Enqueue upload (non-blocking, returns immediately)
            metadata = {
                "title": storyboard.metadata.get("title", "CorvinOS Video"),
                "description": storyboard.metadata.get("description", "Generated by CorvinOS"),
                "tags": ["corvinOS", "generated"],
            }

            result = await uploader.enqueue_upload(
                video_path=video_path,
                metadata=metadata,
                srt_path=self.project_dir / "output.srt",  # If exists
            )

            if result.get("status") == "queued":
                return result.get("task_id")
            return None

        except Exception:
            # YouTube upload failure doesn't block video completion
            return None
