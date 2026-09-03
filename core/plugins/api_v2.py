"""
CorvinOS Plugin API v2 - Stable interface for third-party plugins.

This is the public contract for all plugins. It defines:
- Immutable plugin base class with lifecycle hooks
- Standard request/response types
- Error handling semantics
- Audit integration

Design:
- Plugins inherit from PluginBase and implement hooks
- Hooks are async and must complete within timeout
- All input/output is validated and audited
- Plugins can't access core internals directly (only via IPC)
- Version 2 has 2-version deprecation grace period (v1 → v2, v3 required in 2 releases)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Literal
from datetime import datetime
from enum import Enum


class PluginAPIVersion:
    """API version for compatibility checking."""
    MAJOR = 2
    MINOR = 0
    PATCH = 0

    @classmethod
    def version_string(cls) -> str:
        return f"{cls.MAJOR}.{cls.MINOR}.{cls.PATCH}"

    @classmethod
    def is_compatible(cls, plugin_version: str) -> bool:
        """Check if plugin API version is compatible with core."""
        try:
            parts = plugin_version.split(".")
            plugin_major = int(parts[0])
            # Only major version matters for compatibility
            # v2 plugins work on v2 core
            return plugin_major == cls.MAJOR
        except (IndexError, ValueError):
            return False


@dataclass(frozen=True)
class ExecutionContext:
    """Immutable context passed to every plugin operation."""

    operation_id: str
    plugin_id: str
    operator_id: str
    tenant_id: str
    version: str  # Plugin version
    started_at: datetime
    deadline: datetime  # When operation must complete
    audit_hash: str  # Hash-chain link for audit trail

    def time_remaining_seconds(self) -> float:
        """Seconds remaining before deadline."""
        now = datetime.utcnow()
        remaining = (self.deadline - now).total_seconds()
        return max(0, remaining)

    def is_deadline_exceeded(self) -> bool:
        """Whether deadline has passed."""
        return self.time_remaining_seconds() <= 0


@dataclass(frozen=True)
class PluginResponse:
    """Immutable plugin operation result."""

    status: Literal["success", "error", "retry"]
    data: Optional[Dict[str, Any]] = None  # Success result
    error: Optional[str] = None  # Error message
    error_code: Optional[str] = None  # Structured error code
    metadata: Dict[str, Any] = None  # Execution stats, timing, etc.
    audit_hash: str = ""  # Hash-chain link

    def __post_init__(self):
        """Validate response invariants."""
        if self.status == "success":
            assert self.data is not None, "Success response must have data"
            assert self.error is None, "Success response must not have error"
        elif self.status == "error":
            assert self.error is not None, "Error response must have error message"
            assert self.data is None, "Error response must not have data"
        # retry status can have either error or data (optional retry logic)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary for IPC."""
        return {
            "status": self.status,
            "data": self.data,
            "error": self.error,
            "error_code": self.error_code,
            "metadata": self.metadata or {},
            "audit_hash": self.audit_hash,
        }

    @classmethod
    def success(cls, data: Dict[str, Any], metadata: Optional[Dict] = None, audit_hash: str = "") -> "PluginResponse":
        """Factory: successful response."""
        return cls(
            status="success",
            data=data,
            metadata=metadata or {},
            audit_hash=audit_hash,
        )

    @classmethod
    def failure(cls, message: str, code: Optional[str] = None, metadata: Optional[Dict] = None, audit_hash: str = "") -> "PluginResponse":
        """Factory: error response.

        Named ``failure`` and NOT ``error``: a classmethod called ``error`` on a
        dataclass replaces the class attribute that IS the ``error`` field's
        default, so every ``success()`` was born with ``error=<classmethod>`` and
        failed its own ``__post_init__`` invariant (2026-09-03 finding A7).
        """
        return cls(
            status="error",
            error=message,
            error_code=code,
            metadata=metadata or {},
            audit_hash=audit_hash,
        )

    @classmethod
    def retry(cls, message: str = "Transient error, retry requested") -> "PluginResponse":
        """Factory: retry response."""
        return cls(
            status="retry",
            error=message,
        )


class PluginException(Exception):
    """Base exception for plugin errors."""
    pass


class PluginTimeoutException(PluginException):
    """Plugin operation exceeded deadline."""
    pass


class PluginSecurityException(PluginException):
    """Plugin violated security constraints."""
    pass


class PluginBase(ABC):
    """
    Base class for all v2 plugins.

    Plugins must implement init() and at least one hook.
    All methods are async and must respect deadline.

    Example:
    ```python
    class MyPlugin(PluginBase):
        async def init(self, context: ExecutionContext) -> None:
            self.context = context
            self.config = await load_config()

        async def on_task_start(self, context: ExecutionContext, task_id: str) -> PluginResponse:
            # Called when a task starts
            return PluginResponse.success({"status": "noted"})

        async def on_task_complete(self, context: ExecutionContext, task_id: str, result: Dict) -> PluginResponse:
            # Called when a task completes
            return PluginResponse.success({"processed": True})
    ```
    """

    __version__ = "2.0.0"  # Subclasses override this
    __plugin_id__ = None  # Subclasses override this

    @abstractmethod
    async def init(self, context: ExecutionContext) -> None:
        """
        Initialize plugin for this execution.

        Called once per operation, before any hooks.
        Use this to:
        - Validate environment
        - Load configuration
        - Check dependencies
        - Warm up caches

        If this raises an exception, the plugin operation is cancelled
        and an error response is returned.

        Args:
            context: ExecutionContext with metadata

        Raises:
            PluginException: If initialization fails
        """
        pass

    async def on_task_start(
        self,
        context: ExecutionContext,
        task_id: str,
        task_type: str,
        metadata: Dict[str, Any],
    ) -> PluginResponse:
        """
        Hook: task execution started.

        Called after task has been enqueued but before execution starts.
        Plugin may:
        - Log analytics
        - Update task metadata
        - Check prerequisites
        - Reject task (return error)

        Args:
            context: ExecutionContext
            task_id: Unique task identifier
            task_type: Task classification (e.g., "auth", "compute")
            metadata: Task metadata

        Returns:
            PluginResponse with status

        Default: no-op (returns success)
        """
        return PluginResponse.success({"noted": True})

    async def on_task_complete(
        self,
        context: ExecutionContext,
        task_id: str,
        result: Dict[str, Any],
        duration_ms: float,
    ) -> PluginResponse:
        """
        Hook: task execution completed.

        Called after task has finished (success or failure).
        Plugin may:
        - Log execution metrics
        - Store decision for learning
        - Trigger followup tasks
        - Update user model

        Args:
            context: ExecutionContext
            task_id: Task that completed
            result: Task result (success/error data)
            duration_ms: Execution duration in milliseconds

        Returns:
            PluginResponse

        Default: no-op
        """
        return PluginResponse.success({"processed": True})

    async def on_operator_decision(
        self,
        context: ExecutionContext,
        decision_id: str,
        decision_type: str,
        options: List[Dict[str, Any]],
        chosen: Dict[str, Any],
        metadata: Dict[str, Any],
    ) -> PluginResponse:
        """
        Hook: operator made a decision.

        Called after decision has been logged to audit trail.
        Plugin may:
        - Log decision for analytics
        - Update operator fingerprint
        - Suggest alternatives
        - Train decision classifier

        Args:
            context: ExecutionContext
            decision_id: Unique decision identifier
            decision_type: Type of decision
            options: List of options presented
            chosen: Option chosen by operator
            metadata: Additional metadata

        Returns:
            PluginResponse

        Default: no-op
        """
        return PluginResponse.success({"recorded": True})

    async def on_error(
        self,
        context: ExecutionContext,
        error_id: str,
        error_type: str,
        error_message: str,
        stack_trace: Optional[str],
    ) -> PluginResponse:
        """
        Hook: core encountered an error.

        Called after error has been logged.
        Plugin may:
        - Log error metrics
        - Suggest recovery actions
        - Escalate to admin

        Args:
            context: ExecutionContext
            error_id: Unique error identifier
            error_type: Error class name
            error_message: Error message
            stack_trace: Optional stack trace (if available)

        Returns:
            PluginResponse

        Default: no-op
        """
        return PluginResponse.success({"noted": True})

    async def on_shutdown(
        self,
        context: ExecutionContext,
    ) -> PluginResponse:
        """
        Hook: core is shutting down.

        Called before plugin process is terminated.
        Plugin should:
        - Flush buffers
        - Close connections
        - Save state

        Must complete within 5 seconds or will be forcekilled.

        Args:
            context: ExecutionContext

        Returns:
            PluginResponse

        Default: no-op
        """
        return PluginResponse.success({"shutdown": True})

    async def get_plugin_metadata(self) -> Dict[str, Any]:
        """
        Return plugin metadata.

        Used during registration and marketplace listing.
        Must not change during plugin lifetime.

        Returns:
            {
                "plugin_id": "my-plugin",
                "version": "1.0.0",
                "name": "My Plugin",
                "description": "...",
                "author": "...",
                "supported_task_types": ["auth", "compute"],
                "required_syscalls": ["read", "write"],
                "network_access": false,
            }
        """
        return {
            "plugin_id": self.__plugin_id__,
            "version": self.__version__,
        }

    def validate_deadline(self) -> None:
        """Check if operation has exceeded deadline, raise if so."""
        if hasattr(self, "context") and self.context.is_deadline_exceeded():
            raise PluginTimeoutException("Operation deadline exceeded")
