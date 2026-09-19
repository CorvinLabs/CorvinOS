"""
VibeOrchestrator + WorkerPool Integration (F-C2 Wiring)

F-C2 Fix: Wire WorkerPool to real VibeOrchestrator subsystem.
Makes WorkerPool reachable + productively used.

LDD E2E Wiring Proof: VibeOrchestrator.orchestrate_checkpoint() → WorkerPool.submit()
"""

import asyncio
from typing import Optional, Any
from dataclasses import dataclass

from core.concurrency.worker_pool import WorkerPool
from core.vibe_engineering.checkpoint_manager import CheckpointManager


@dataclass
class CheckpointRequest:
    """Request to create checkpoint."""
    task_id: str
    context: dict
    priority: int = 0


@dataclass
class CheckpointResult:
    """Result from checkpoint creation."""
    checkpoint_id: str
    task_id: str
    created_at: str
    status: str = "created"


class VibeOrchestrator:
    """
    Vibe Engineering Orchestrator: coordinates checkpoint creation + task management.

    F-C2 Integration: Uses WorkerPool for bounded checkpoint creation.
    """

    def __init__(
        self,
        max_concurrent_checkpoints: int = 4,
        checkpoint_queue_size: int = 1000,
        checkpoint_timeout: float = 30,
    ):
        self.checkpoint_manager = CheckpointManager()
        self.worker_pool = WorkerPool(
            max_workers=max_concurrent_checkpoints,
            queue_size=checkpoint_queue_size,
            timeout_seconds=checkpoint_timeout,
            enable_auto_scaling=True,
        )
        self.initialized = False

    async def initialize(self):
        """Start orchestrator (spawn worker pool)."""
        if not self.initialized:
            await self.worker_pool.start()
            self.initialized = True

    async def shutdown(self):
        """Graceful shutdown."""
        if self.initialized:
            await self.worker_pool.stop()
            self.initialized = False

    async def orchestrate_checkpoint(
        self,
        request: CheckpointRequest,
    ) -> CheckpointResult:
        """
        Create checkpoint via WorkerPool.

        F-C2 E2E Wiring: Real production call to WorkerPool.

        Args:
            request: Checkpoint request (task_id, context, priority)

        Returns:
            CheckpointResult with checkpoint_id and metadata

        Raises:
            QueueOverflowError: If WorkerPool queue full (backpressure)
        """

        # Submit checkpoint creation as a background task
        result = await self.worker_pool.submit(
            self._create_checkpoint_impl,
            request,
            timeout_seconds=request.priority if request.priority > 0 else None,
        )

        return result

    async def _create_checkpoint_impl(
        self,
        request: CheckpointRequest,
    ) -> CheckpointResult:
        """
        Actual checkpoint creation logic (runs in WorkerPool).

        Isolated in its own method so it can be offloaded to worker pool.
        """

        checkpoint = await self.checkpoint_manager.create(
            task_id=request.task_id,
            context=request.context,
        )

        return CheckpointResult(
            checkpoint_id=checkpoint.id,
            task_id=request.task_id,
            created_at=checkpoint.created_at,
            status="created",
        )

    async def orchestrate_multiple_checkpoints(
        self,
        requests: list[CheckpointRequest],
    ) -> list[CheckpointResult]:
        """
        Create multiple checkpoints concurrently via WorkerPool.

        F-C2 E2E Proof: Concurrent batch processing shows real WorkerPool usage.
        """

        tasks = [
            self.orchestrate_checkpoint(req)
            for req in requests
        ]

        results = await asyncio.gather(*tasks)
        return results

    def get_pool_stats(self) -> dict:
        """Get WorkerPool statistics (for monitoring)."""
        stats = self.worker_pool.get_stats()
        return {
            "active_workers": stats.active_workers,
            "queue_size": stats.queue_size,
            "queue_capacity": stats.queue_capacity,
            "tasks_processed": stats.tasks_processed,
            "overflow_errors": stats.overflow_errors,
            "avg_latency_ms": stats.avg_latency_ms,
        }


# Singleton instance (for easy access)
_orchestrator: Optional[VibeOrchestrator] = None


async def get_vibe_orchestrator() -> VibeOrchestrator:
    """Get or create singleton VibeOrchestrator instance."""
    global _orchestrator

    if _orchestrator is None:
        _orchestrator = VibeOrchestrator()
        await _orchestrator.initialize()

    return _orchestrator
