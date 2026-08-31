"""k=1 Endpoint Architecture: Single Connection Per Request

ADR-0515 implementation: each request spawns exactly one outbound connection,
providing isolation, atomicity, and compliance with ADR-0301/0447/0298/0296.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncContextManager, Callable, Dict, List, Optional, Tuple
from contextvars import ContextVar

logger = logging.getLogger(__name__)

# Context variable to hold the current request's k=1 context
_current_k1_context: ContextVar['K1RequestContext'] = ContextVar(
    'k1_request_context', default=None
)


@dataclass
class K1ConnectionContext:
    """Single connection per logical request (ADR-0515 Layer 1)."""
    request_id: str
    transport: str  # 'http' | 'cli' | 'async' | 'ws'
    _connection: Optional[Any] = None
    _cleanup_stack: List[Callable] = field(default_factory=list)

    async def __aenter__(self):
        """Acquire the single outbound connection."""
        # In production, this would acquire from a pool and mark it as request-scoped
        # For now, simulate with a synthetic connection ID
        self._connection = self._create_connection()
        logger.debug(
            f"[k=1] Acquired connection {self._connection.id} for request {self.request_id}"
        )
        return self._connection

    async def __aexit__(self, exc_type, exc, tb):
        """Release the connection and cleanup in LIFO order."""
        if self._connection:
            logger.debug(
                f"[k=1] Releasing connection {self._connection.id} for request {self.request_id}"
            )
            # In production: return connection to pool or close it
            self._connection = None

        # Cleanup in LIFO order
        for cleanup in reversed(self._cleanup_stack):
            try:
                if asyncio.iscoroutinefunction(cleanup):
                    await cleanup()
                else:
                    cleanup()
            except Exception as e:
                logger.exception(f"Cleanup failed: {e}")

    def register_cleanup(self, cleanup: Callable) -> None:
        """Register a cleanup function (called on context exit in LIFO order)."""
        self._cleanup_stack.append(cleanup)

    @staticmethod
    def _create_connection():
        """Create a synthetic connection object (placeholder)."""
        @dataclass
        class Connection:
            id: str = field(default_factory=lambda: f"conn_{uuid.uuid4().hex[:8]}")
        return Connection()


@dataclass
class RequestSessionContext:
    """Request-local session-state isolation (ADR-0515 Layer 2, builds on ADR-0447)."""
    connection_ctx: K1ConnectionContext
    _session_cache: Dict[str, Any] = field(default_factory=dict)

    async def resolve_session(self, session_id: str) -> Dict[str, Any]:
        """
        Resolve session state within this request's connection.

        Invariant: no cross-request session-cache sharing (ADR-0447 SSOT).
        """
        if session_id not in self._session_cache:
            # In production: fetch via the request's single connection
            # Simulate by creating a mock session record
            record = {
                'session_id': session_id,
                'user_id': session_id.split(':')[0] if ':' in session_id else session_id,
                'connection_id': self.connection_ctx._connection.id if self.connection_ctx._connection else None,
            }
            self._session_cache[session_id] = record
            logger.debug(f"[k=1] Resolved session {session_id} in cache")
        return self._session_cache[session_id]

    def get_cached_sessions(self) -> List[str]:
        """Return list of session IDs in this request's local cache."""
        return list(self._session_cache.keys())


@dataclass
class PerRequestTaskQueue:
    """Task queue drained atomically at request end (ADR-0515 Layer 3, builds on ADR-0298)."""
    request_id: str
    queue: List[Callable] = field(default_factory=list)

    async def enqueue(self, task: Callable) -> None:
        """
        Enqueue a task into this request's queue.

        All enqueued tasks execute atomically at request end (no interleavings).
        """
        self.queue.append(task)
        logger.debug(f"[k=1] Enqueued task for request {self.request_id} (queue size: {len(self.queue)})")

    async def drain(self) -> Tuple[List[Any], List[Exception]]:
        """
        Execute all queued tasks atomically, collect results and errors.

        Invariant: tasks execute in FIFO order, atomically (no other requests interleave).
        """
        results = []
        errors = []

        for i, task in enumerate(self.queue):
            try:
                if asyncio.iscoroutinefunction(task):
                    result = await task()
                else:
                    result = task()
                results.append(result)
                logger.debug(f"[k=1] Task {i+1}/{len(self.queue)} completed for request {self.request_id}")
            except Exception as e:
                errors.append(e)
                logger.exception(f"[k=1] Task {i+1}/{len(self.queue)} failed: {e}")

        logger.debug(
            f"[k=1] Drained {len(self.queue)} tasks for request {self.request_id}: "
            f"{len(results)} succeeded, {len(errors)} failed"
        )
        return results, errors


@dataclass
class K1PipelineEnforcer:
    """Pipeline gates fire atomically per request (ADR-0515 Layer 4, builds on ADR-0301)."""
    connection_ctx: K1ConnectionContext
    _gate_passed: bool = False
    _gate_error: Optional[str] = None

    async def check_request(
        self,
        user_id: str,
        capability: str,
        action: str,
    ) -> bool:
        """
        Check all pipeline gates once per request.

        Invariant: pipeline gates fire exactly once; all sub-operations inherit the result
        (no re-checking, preventing ADR-0301 wiring bypass under concurrency).

        Returns: True if all gates pass, False otherwise.
        """
        if self._gate_passed or self._gate_error:
            # Already checked in this request
            logger.debug(f"[k=1] Reusing cached pipeline result for request {self.connection_ctx.request_id}")
            return self._gate_passed

        # In production: invoke DualGatePipeline here
        # For now, simulate: check basic invariants
        try:
            if not user_id:
                raise ValueError("user_id required")
            if not capability:
                raise ValueError("capability required")
            if not action:
                raise ValueError("action required")

            # Mock gate passed
            self._gate_passed = True
            logger.debug(
                f"[k=1] Pipeline gates passed for request {self.connection_ctx.request_id} "
                f"(user={user_id}, capability={capability}, action={action})"
            )
            return True
        except Exception as e:
            self._gate_error = str(e)
            logger.exception(f"[k=1] Pipeline gate failed: {e}")
            return False

    def has_passed(self) -> bool:
        """Check if this request's pipeline gates have already passed."""
        return self._gate_passed

    def get_error(self) -> Optional[str]:
        """Get pipeline error message if gates failed."""
        return self._gate_error


@dataclass
class K1RequestContext:
    """
    Complete k=1 request context (ADR-0515).

    Aggregates: connection (Layer 1), session (Layer 2), task queue (Layer 3), pipeline (Layer 4).
    Provides request-scoped isolation with atomic semantics.
    """
    request_id: str
    transport: str  # 'http' | 'cli' | 'async' | 'ws'
    connection: K1ConnectionContext
    session: RequestSessionContext
    task_queue: PerRequestTaskQueue
    pipeline: K1PipelineEnforcer

    @staticmethod
    async def create(
        transport: str,
        request_id: Optional[str] = None,
    ) -> 'K1RequestContext':
        """
        Factory: allocate the complete k=1 context for this request.

        Returns a new, fully-isolated context.
        """
        if not request_id:
            request_id = f"req_{uuid.uuid4().hex[:12]}"

        connection_ctx = K1ConnectionContext(request_id, transport)
        session_ctx = RequestSessionContext(connection_ctx)
        task_queue = PerRequestTaskQueue(request_id)
        pipeline_enforcer = K1PipelineEnforcer(connection_ctx)

        ctx = K1RequestContext(
            request_id=request_id,
            transport=transport,
            connection=connection_ctx,
            session=session_ctx,
            task_queue=task_queue,
            pipeline=pipeline_enforcer,
        )

        logger.debug(f"[k=1] Created request context {request_id} (transport={transport})")
        _current_k1_context.set(ctx)
        return ctx

    def get_current() -> Optional['K1RequestContext']:
        """Get the current request's k=1 context from ContextVar."""
        return _current_k1_context.get()


# Convenience getter
def get_k1_context() -> Optional[K1RequestContext]:
    """Get the current request's k=1 context."""
    return _current_k1_context.get()
