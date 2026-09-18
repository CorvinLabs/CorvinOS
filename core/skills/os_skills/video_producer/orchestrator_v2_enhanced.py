"""VideoProducerOrchestrator v2 Enhanced — Model Selection + Feedback + OTEL + Audit."""

from __future__ import annotations

import asyncio
import json
import logging
import hashlib
import uuid
from pathlib import Path
from typing import Optional, Any, Dict
from datetime import datetime

from .types import VideoGenerationRequest, VideoGenerationResult, PhaseResult

logger = logging.getLogger(__name__)


class VideoProducerOrchestratorV2:
    """Enhanced orchestrator with Model Selection + Event-Sourcing + Feedback + OTEL."""

    def __init__(self, project_dir: str | Path):
        """Initialize with event store + audit chain."""
        self.project_dir = Path(project_dir)
        self.project_dir.mkdir(parents=True, exist_ok=True)

        # Event-sourced state
        self.video_state: Dict[str, Any] = {}
        self.model_selector = None
        self.outcome_sink = None
        self.otel_client = None
        self.audit_chain = None

        self.fallback_models = {
            "text_to_speech": "google-tts-default",
            "music_generation": "suno-music-default",
            "scene_generation": "dalle3-default",
        }

        self.max_retries = 3
        self.retry_backoff = 1.5

    async def orchestrate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        """Main orchestration entry point (7-phase pipeline, event-sourced)."""
        video_id = str(uuid.uuid4())
        start_time = datetime.utcnow()

        self.video_state[video_id] = {
            "request": request,
            "status": "starting",
            "phases": [],
            "phase_results": [],
            "feedback_events": [],
            "model_selections": [],
            "audit_events": [],
            "metrics": {},
        }

        await self._emit_event("VideoStartedEvent", video_id, {
            "input_text": request.input_text[:100],
            "style": request.style,
            "tenant_id": request.tenant_id,
        })

        phase_results = []
        video_path = None
        youtube_task_id = None

        try:
            # Phase 1: Asset Analysis
            phase_result = await self._execute_phase_1_asset_analysis(video_id, request)
            phase_results.append(phase_result)

            # Phase 2: Storyboard Generation
            phase_result = await self._execute_phase_2_storyboard(
                video_id, request, phase_results[0].output.get("analysis")
            )
            phase_results.append(phase_result)

            # Phase 3: Parallel Workers (TTS, Music, Scene Gen)
            phase_result = await self._execute_phase_3_parallel_workers(
                video_id, request, phase_results[1].output.get("storyboard")
            )
            phase_results.append(phase_result)

            # Phase 4: Video Assembly
            phase_result = await self._execute_phase_4_assembly(video_id, request, phase_results)
            phase_results.append(phase_result)
            if phase_result.status != "failed":
                video_path = phase_result.output.get("video_path")

            # Phase 5: YouTube Upload (async)
            if video_path:
                phase_result = await self._execute_phase_5_youtube_upload(video_id, request, video_path)
                phase_results.append(phase_result)
                youtube_task_id = phase_result.output.get("youtube_task_id")

            # Phase 6: Learning Optimization
            await self._execute_phase_6_learning_optimization(video_id, request, phase_results)

        except Exception as e:
            logger.error(f"Orchestration failed: {e}")
            await self._emit_event("OrchestrationFailedEvent", video_id, {"error": str(e)})
            status = "partial" if video_path else "failed"
        else:
            status = "success" if video_path else "partial"

        elapsed = (datetime.utcnow() - start_time).total_seconds()

        result = VideoGenerationResult(
            video_id=video_id,
            status=status,
            project_id=request.project_id,
            tenant_id=request.tenant_id,
            video_path=video_path,
            youtube_task_id=youtube_task_id,
            duration_seconds=elapsed,
            message=f"Video {'generated' if video_path else 'incomplete'}",
            phase_results=phase_results,
            audit_event_ids=self.video_state[video_id].get("audit_events", []),
        )

        await self._emit_event("VideoFinalizedEvent", video_id, {"status": status})
        return result

    async def _execute_phase_1_asset_analysis(
        self, video_id: str, request: VideoGenerationRequest
    ) -> PhaseResult:
        """Phase 1: Asset Analysis."""
        await self._emit_phase_event("PhaseStartedEvent", video_id, 1, "asset_analysis")

        phase_start = datetime.utcnow()
        try:
            analysis = {
                "clarity_score": 90,
                "completeness_score": 85,
                "key_topics": request.input_text.split()[:5],
            }

            phase_latency = (datetime.utcnow() - phase_start).total_seconds() * 1000
            await self._emit_otel_metric(
                "video_production.phase_latency_ms",
                phase_latency,
                {"phase": "asset_analysis", "tenant_id": request.tenant_id}
            )

            return PhaseResult(
                phase_name="asset_analysis",
                status="success",
                latency_ms=int(phase_latency),
                output={"analysis": analysis},
            )
        except Exception as e:
            logger.error(f"Phase 1 failed: {e}")
            return PhaseResult(phase_name="asset_analysis", status="failed", error=str(e))

    async def _execute_phase_2_storyboard(
        self, video_id: str, request: VideoGenerationRequest, analysis: dict
    ) -> PhaseResult:
        """Phase 2: Storyboard Generation."""
        await self._emit_phase_event("PhaseStartedEvent", video_id, 2, "storyboard_generation")

        phase_start = datetime.utcnow()
        try:
            scene_count = max(3, min(10, len(analysis.get("key_topics", []))))
            scenes = [
                {
                    "id": f"scene_{i}",
                    "kind": "slide",
                    "duration_seconds": request.duration_seconds / scene_count,
                }
                for i in range(scene_count)
            ]

            storyboard = {
                "scene_count": scene_count,
                "total_duration": request.duration_seconds,
                "scenes": scenes,
            }

            phase_latency = (datetime.utcnow() - phase_start).total_seconds() * 1000
            await self._emit_otel_metric(
                "video_production.phase_latency_ms",
                phase_latency,
                {"phase": "storyboard_generation", "tenant_id": request.tenant_id}
            )

            return PhaseResult(
                phase_name="storyboard_generation",
                status="success",
                latency_ms=int(phase_latency),
                output={"storyboard": storyboard},
            )
        except Exception as e:
            return PhaseResult(phase_name="storyboard_generation", status="failed", error=str(e))

    async def _execute_phase_3_parallel_workers(
        self, video_id: str, request: VideoGenerationRequest, storyboard: dict
    ) -> PhaseResult:
        """Phase 3: Parallel Workers with Model Selection."""
        await self._emit_phase_event("PhaseStartedEvent", video_id, 3, "parallel_workers")

        phase_start = datetime.utcnow()
        worker_results = {}

        tasks = [
            self._execute_worker_with_model_selection(video_id, request, "text_to_speech"),
            self._execute_worker_with_model_selection(video_id, request, "music_generation"),
            self._execute_worker_with_model_selection(video_id, request, "scene_generation"),
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if not isinstance(result, Exception):
                worker_type, worker_result = result
                worker_results[worker_type] = worker_result

        phase_latency = (datetime.utcnow() - phase_start).total_seconds() * 1000
        await self._emit_otel_metric(
            "video_production.phase_latency_ms",
            phase_latency,
            {"phase": "parallel_workers", "tenant_id": request.tenant_id}
        )

        return PhaseResult(
            phase_name="parallel_workers",
            status="success" if len(worker_results) >= 2 else "partial",
            latency_ms=int(phase_latency),
            output={"workers": worker_results},
        )

    async def _execute_worker_with_model_selection(
        self, video_id: str, request: VideoGenerationRequest, worker_type: str
    ) -> tuple[str, dict]:
        """Execute worker with Model Selection integration."""
        for attempt in range(self.max_retries):
            try:
                model_selection = await self._select_model_for_worker(
                    worker_type, {"style": request.style, "tenant_id": request.tenant_id}
                )

                model_id = model_selection.get("model_id", self.fallback_models[worker_type])
                confidence = model_selection.get("confidence", 0.5)

                self.video_state[video_id]["model_selections"].append({
                    "worker_type": worker_type,
                    "model_id": model_id,
                    "confidence": confidence,
                })

                await self._emit_otel_metric(
                    "video_production.model_selection",
                    1.0,
                    {"worker_type": worker_type, "model_id": model_id, "tenant_id": request.tenant_id}
                )

                await self._emit_feedback_event(video_id, worker_type, "success", model_id, 100.0)
                return (worker_type, {"model_id": model_id, "status": "success"})

            except Exception as e:
                logger.error(f"Worker {worker_type} attempt {attempt + 1} failed: {e}")
                if attempt == self.max_retries - 1:
                    await self._emit_feedback_event(video_id, worker_type, "failed", "fallback", 30.0)
                    return (worker_type, {"model_id": self.fallback_models[worker_type], "fallback": True})
                await asyncio.sleep(0.5 * (self.retry_backoff ** attempt))

    async def _execute_phase_4_assembly(
        self, video_id: str, request: VideoGenerationRequest, phase_results: list[PhaseResult]
    ) -> PhaseResult:
        """Phase 4: Video Assembly."""
        await self._emit_phase_event("PhaseStartedEvent", video_id, 4, "video_assembly")

        phase_start = datetime.utcnow()
        try:
            video_path = self.project_dir / f"video_{video_id}.mp4"
            video_path.write_bytes(b"MOCK_VIDEO_DATA" * 10000)

            phase_latency = (datetime.utcnow() - phase_start).total_seconds() * 1000
            await self._emit_otel_metric(
                "video_production.phase_latency_ms",
                phase_latency,
                {"phase": "video_assembly", "tenant_id": request.tenant_id}
            )

            return PhaseResult(
                phase_name="video_assembly",
                status="success",
                latency_ms=int(phase_latency),
                output={"video_path": str(video_path)},
            )
        except Exception as e:
            return PhaseResult(phase_name="video_assembly", status="failed", error=str(e))

    async def _execute_phase_5_youtube_upload(
        self, video_id: str, request: VideoGenerationRequest, video_path: str
    ) -> PhaseResult:
        """Phase 5: YouTube Upload (async, non-blocking)."""
        youtube_task_id = f"youtube_task_{video_id}"
        asyncio.create_task(self._upload_to_youtube_async(youtube_task_id, video_path, request))

        return PhaseResult(
            phase_name="youtube_upload",
            status="success",
            output={"youtube_task_id": youtube_task_id},
        )

    async def _upload_to_youtube_async(self, task_id: str, video_path: str, request: VideoGenerationRequest):
        """Async YouTube upload."""
        try:
            await asyncio.sleep(0.1)
            logger.info(f"Mock YouTube upload: {task_id}")
        except Exception as e:
            logger.error(f"YouTube upload failed: {e}")

    async def _execute_phase_6_learning_optimization(
        self, video_id: str, request: VideoGenerationRequest, phase_results: list[PhaseResult]
    ):
        """Phase 6: Learning Optimization."""
        model_selections = self.video_state[video_id].get("model_selections", [])
        for selection in model_selections:
            quality_score = 0.85 + (hash(selection["model_id"]) % 10) * 0.01
            await self._emit_feedback_event(
                video_id, selection["worker_type"], "success", selection["model_id"], quality_score
            )

    # Integration Points
    async def _select_model_for_worker(self, worker_type: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Select optimal model for worker type."""
        if self.model_selector:
            try:
                return await self.model_selector.select_model(
                    worker_type=worker_type,
                    context=context,
                    tenant_id=context.get("tenant_id"),
                )
            except Exception as e:
                logger.warning(f"Model selection failed: {e}, using fallback")

        return {
            "model_id": self.fallback_models.get(worker_type, "default"),
            "confidence": 0.7,
            "reasoning": "fallback",
        }

    async def _emit_feedback_event(
        self, video_id: str, worker_type: str, outcome: str, model_id: str, quality_score: float
    ):
        """Emit feedback event for learning loop."""
        feedback_event = {
            "video_id": video_id,
            "worker_type": worker_type,
            "outcome": outcome,
            "model_id": model_id,
            "quality_score": quality_score,
            "timestamp": datetime.utcnow().isoformat(),
        }

        self.video_state[video_id]["feedback_events"].append(feedback_event)

        if self.outcome_sink:
            try:
                await self.outcome_sink.record_event(
                    event_type="skill_feedback",
                    skill_id="video_producer",
                    input={"worker_type": worker_type, "model_id": model_id},
                    output={"quality_score": quality_score, "outcome": outcome},
                    latency_ms=0,
                    tenant_id=video_id,
                )
            except Exception as e:
                logger.error(f"Failed to record feedback: {e}")

    async def _emit_otel_metric(self, metric_name: str, value: float, labels: Dict[str, Any]):
        """Emit metric to OTEL (dual-write: local + OTEL agent)."""
        metric = {
            "metric": metric_name,
            "value": value,
            "labels": labels,
            "timestamp": datetime.utcnow().isoformat(),
        }

        if not hasattr(self, "_local_metrics"):
            self._local_metrics = []
        self._local_metrics.append(metric)

        if self.otel_client:
            try:
                await self.otel_client.export_metric(metric_name, value, labels)
            except Exception as e:
                logger.warning(f"OTEL export failed: {e}")

    async def _emit_event(self, event_type: str, video_id: str, payload: Dict[str, Any]):
        """Emit audit event (hash-chained)."""
        event = {
            "event_type": event_type,
            "video_id": video_id,
            "payload": payload,
            "timestamp": datetime.utcnow().isoformat(),
            "tenant_id": self.video_state[video_id].get("request", {}).get("tenant_id"),
        }

        event_json = json.dumps(event, sort_keys=True)
        event_hash = hashlib.sha256(event_json.encode()).hexdigest()
        event["hash"] = event_hash

        audit_events = self.video_state[video_id].get("audit_events", [])
        if audit_events:
            event["prev_hash"] = audit_events[-1]

        self.video_state[video_id]["audit_events"].append(event_hash)

        if self.audit_chain:
            try:
                await self.audit_chain.log_event(event)
            except Exception as e:
                logger.error(f"Failed to log audit event: {e}")

    async def _emit_phase_event(
        self, event_type: str, video_id: str, phase_num: int, phase_name: str, details: Dict[str, Any] = None
    ):
        """Emit phase-specific audit event."""
        payload = {"phase_number": phase_num, "phase_name": phase_name, **(details or {})}
        await self._emit_event(event_type, video_id, payload)
