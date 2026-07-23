"""ADR-0214: Worker IPC (Phase 3).

Interface for delegating steps to remote workers.
Placeholder for Phase 3 implementation.
"""
from __future__ import annotations

import logging
from typing import Any, Protocol, Optional

from .adaptive_delegation_executor import DelegationEnvelope

_logger = logging.getLogger(__name__)


class WorkerIPCInterface(Protocol):
    """Protocol for worker IPC backends."""

    async def send_delegation(
        self,
        envelope: DelegationEnvelope,
    ) -> dict[str, Any]:
        """
        Send step to remote worker.

        Args:
            envelope: DelegationEnvelope with step, plan, snapshot, budget

        Returns:
            Result dict from worker
        """
        ...


class MockWorkerIPC:
    """Mock IPC for testing (Phase 2)."""

    async def send_delegation(self, envelope: DelegationEnvelope) -> dict[str, Any]:
        """Mock: return placeholder result."""
        _logger.debug(f"Mock delegation: step {envelope.step.step}")
        return {
            "step_num": envelope.step.step,
            "output": {"placeholder": "mock result"},
            "success": True,
        }


class A2AWorkerIPC:
    """Real A2A-based IPC (Phase 3).

    Uses ADR-0213 A2A protocol for task distribution.
    Placeholder for future implementation.
    """

    def __init__(self, a2a_client: Optional[Any] = None):
        """Initialize with A2A client."""
        self.a2a_client = a2a_client
        _logger.info("A2AWorkerIPC initialized (Phase 3 stub)")

    async def send_delegation(self, envelope: DelegationEnvelope) -> dict[str, Any]:
        """Send via A2A protocol (not yet implemented)."""
        raise NotImplementedError("A2A delegation coming in Phase 3")


# Global IPC singleton
_ipc_instance: Optional[WorkerIPCInterface] = None


def get_worker_ipc() -> WorkerIPCInterface:
    """Get or create global worker IPC."""
    global _ipc_instance
    if _ipc_instance is None:
        # For Phase 2: use mock
        # For Phase 3: switch to A2AWorkerIPC(a2a_client)
        _ipc_instance = MockWorkerIPC()
    return _ipc_instance


def set_worker_ipc(ipc: WorkerIPCInterface):
    """Set custom worker IPC (for testing)."""
    global _ipc_instance
    _ipc_instance = ipc
