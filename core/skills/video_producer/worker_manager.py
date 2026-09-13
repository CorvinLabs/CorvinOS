"""Worker registration and pipeline management for Video Producer Skill 2.0"""

from typing import Dict, Any, List
from .maestro import MaestroOrchestrator, VideoJobPhase


class WorkerRegistration:
    """Registry for Worker Skills with lifecycle management"""

    def __init__(self):
        self.workers: Dict[str, Any] = {}
        self.enabled_workers: set = set()

    def register(self, phase_name: str, worker_instance):
        """Register a Worker Skill for a phase

        Args:
            phase_name: Name of the phase (ANALYSIS, VOICE, etc.)
            worker_instance: Instantiated worker object
        """
        self.workers[phase_name] = worker_instance
        self.enabled_workers.add(phase_name)

    def get_worker(self, phase_name: str):
        """Get Worker Skill for a phase"""
        return self.workers.get(phase_name)

    def list_workers(self) -> Dict[str, str]:
        """List all registered workers with versions"""
        return {
            phase: f"{worker.name}@{worker.version}"
            for phase, worker in self.workers.items()
        }

    def is_enabled(self, phase_name: str) -> bool:
        """Check if worker is enabled for a phase"""
        return phase_name in self.enabled_workers

    def enable_worker(self, phase_name: str):
        """Enable a worker for a phase"""
        if phase_name in self.workers:
            self.enabled_workers.add(phase_name)

    def disable_worker(self, phase_name: str):
        """Disable a worker for a phase"""
        if phase_name in self.enabled_workers:
            self.enabled_workers.discard(phase_name)


class VideoProductionPipeline:
    """End-to-end video production pipeline

    Orchestrates:
    - Job creation
    - Worker registration
    - Phase execution with feedback loops
    - Learning integration
    """

    def __init__(self, maestro: MaestroOrchestrator = None):
        self.maestro = maestro or MaestroOrchestrator()
        self.worker_registry = WorkerRegistration()

    def register_worker(self, phase: VideoJobPhase, worker_class):
        """Register a Worker Skill for a phase"""
        # If it's already an instance, use it directly
        if isinstance(worker_class, type):
            worker_instance = worker_class()
        else:
            worker_instance = worker_class

        self.maestro.register_worker(phase, worker_instance)
        self.worker_registry.register(phase.name, worker_instance)

    def create_and_process_job(
        self,
        topic: str,
        duration: int,
        audience: str,
        narration: List[str],
        auto_execute: bool = False,
    ) -> str:
        """Create a video job and optionally begin processing

        Args:
            topic: Video topic
            duration: Duration in seconds
            audience: Target audience
            narration: List of scene narrations
            auto_execute: If True, execute phases automatically

        Returns:
            job_id: Created job identifier
        """

        # Create job
        job_id = self.maestro.create_job(topic, duration, audience, narration)

        # Auto-execute phases if requested
        if auto_execute:
            self._auto_execute_phases(job_id)

        return job_id

    def _auto_execute_phases(self, job_id: str):
        """Automatically execute all phases in order"""
        while True:
            job = self.maestro.get_job(job_id)
            if job.current_phase == VideoJobPhase.COMPLETE:
                break

            phase_name = job.current_phase.name
            if self.worker_registry.is_enabled(phase_name):
                try:
                    self.maestro.execute_phase(job_id)
                except Exception as e:
                    print(f"Phase {phase_name} failed: {e}")
                    break
            else:
                print(f"Worker for {phase_name} not enabled, skipping")
                break

    def get_job_status(self, job_id: str) -> Dict[str, Any]:
        """Get comprehensive job status

        Returns:
            Dict with job phase, progress, feedback, and results
        """
        job = self.maestro.get_job(job_id)
        if not job:
            return {"error": f"Job {job_id} not found"}

        return {
            "job_id": job_id,
            "topic": job.topic,
            "current_phase": job.current_phase.name,
            "duration_seconds": job.duration_seconds,
            "audience": job.audience,
            "num_scenes": len(job.narration),
            "num_feedback_events": len(job.feedback_history),
            "analysis_complete": job.analysis_result is not None,
            "voice_complete": job.voice_result is not None,
            "screenshots_complete": job.screenshots_result is not None,
            "video_complete": job.video_result is not None,
            "youtube_complete": job.youtube_result is not None,
        }

    def get_workers_status(self) -> Dict[str, Any]:
        """Get worker registration status"""
        return {
            "registered_workers": self.worker_registry.list_workers(),
            "total_registered": len(self.worker_registry.workers),
            "total_enabled": len(self.worker_registry.enabled_workers),
        }
