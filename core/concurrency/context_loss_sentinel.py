"""Context Loss Sentinel - Real-time detection of context loss (fail-closed).

Lightweight sentinel for critical paths. Detects context loss in real-time
instead of waiting for daily verification (ADR-0541).

Phase 4 of ADR-0541 Session Bridging solution.
Depends on: ADR-0424 (Context Propagation), ADR-0232 (Audit Chain)
"""

import logging
from contextlib import contextmanager
from typing import Dict, Any, Generator

# FIX #2: Import shared ContextVars from canonical location (not local copies)
from core.concurrency.context_helpers import (
    TASK_ID_VAR,
    WORKTREE_PATH_VAR,
    BASE_COMMIT_VAR,
    PHASE_NAME_VAR,
    SESSION_ID_VAR,
    TenantContextVar,
)

logger = logging.getLogger(__name__)


class ContextLossSentinel:
    """Real-time context loss detection (fail-closed)."""

    @staticmethod
    def assert_task_context() -> str:
        """Assert task_id is set, raise if missing.

        Usage:
            task_id = ContextLossSentinel.assert_task_context()
            # Now safe to use task_id

        Raises:
            ContextLossError if task_id is None (context lost)
        """
        task_id = TASK_ID_VAR.get(None)
        if task_id is None:
            logger.error(
                "CONTEXT LOSS DETECTED: task_id is None at critical path. "
                "Likely cause: async boundary crossing without context propagation."
            )
            raise ContextLossError(
                "task_id lost — context not restored. "
                "Call auto_restore_session_context() or use with_context_propagation()."
            )
        return task_id

    @staticmethod
    def assert_tenant_context() -> str:
        """Assert tenant_id is set, raise if missing."""
        tenant_id = TenantContextVar.get()
        if tenant_id is None:
            logger.error("CONTEXT LOSS DETECTED: tenant_id is None at critical path")
            raise ContextLossError(
                "tenant_id lost — TenantContextVar not set. "
                "Fail-closed: deny on missing tenant scope."
            )
        return tenant_id

    @staticmethod
    def assert_all_context() -> Dict[str, str]:
        """Assert all critical ContextVars are set.

        Returns:
            Dict of all context values if all present

        Raises:
            ContextLossError if any are missing
        """
        vars_dict = {
            "task_id": TASK_ID_VAR.get(None),
            "tenant_id": TenantContextVar.get(),
            "worktree_path": WORKTREE_PATH_VAR.get(None),
            "base_commit": BASE_COMMIT_VAR.get(None),
            "phase_name": PHASE_NAME_VAR.get(None),
        }

        missing = [k for k, v in vars_dict.items() if v is None]

        if missing:
            logger.error(
                f"CONTEXT LOSS DETECTED: Missing context vars: {missing}"
            )
            raise ContextLossError(
                f"Missing context: {', '.join(missing)}. "
                "Context not properly restored or propagated across async boundary."
            )

        return vars_dict

    @staticmethod
    @contextmanager
    def with_context_propagation(
        context_dict: Dict[str, Any],
    ) -> Generator[None, None, None]:
        """Explicit context propagation for async tasks (fail-closed).

        Usage:
            async def my_async_task():
                # Parent has context, child doesn't (yet)
                context = ContextLossSentinel.get_all_context()

                # Spawn child task with explicit context
                child = asyncio.create_task(
                    child_task_with_context(context)
                )

            async def child_task_with_context(context):
                # Restore context in child
                with ContextLossSentinel.with_context_propagation(context):
                    # Now safe to use: task_id = ContextLossSentinel.assert_task_context()
                    pass

        Args:
            context_dict: Dict with keys like task_id, tenant_id, etc.
        """
        tokens = []
        try:
            # Set all context vars from dict
            for var_name, var_value in context_dict.items():
                if var_name == "task_id" and var_value:
                    tokens.append(("task_id", TASK_ID_VAR.set(var_value)))
                elif var_name == "tenant_id" and var_value:
                    tokens.append(("tenant_id", TenantContextVar.set(var_value)))
                elif var_name == "worktree_path" and var_value:
                    tokens.append(("worktree", WORKTREE_PATH_VAR.set(var_value)))
                elif var_name == "base_commit" and var_value:
                    tokens.append(("commit", BASE_COMMIT_VAR.set(var_value)))
                elif var_name == "phase_name" and var_value:
                    tokens.append(("phase", PHASE_NAME_VAR.set(var_value)))

            logger.debug(f"Context propagated to child task: {len(tokens)} vars")
            yield

        finally:
            # Reset all tokens (fail-closed: always reset)
            for var_name, token in tokens:
                try:
                    if var_name == "task_id":
                        _task_id_var.reset(token)
                    elif var_name == "tenant_id":
                        _tenant_id_var.reset(token)
                    elif var_name == "worktree":
                        _worktree_var.reset(token)
                    elif var_name == "commit":
                        _base_commit_var.reset(token)
                    elif var_name == "phase":
                        _phase_var.reset(token)
                except Exception as e:
                    logger.error(f"Failed to reset context var {var_name}: {e}")

    @staticmethod
    def get_all_context() -> Dict[str, Any]:
        """Get current context (for debugging or propagation)."""
        return {
            "task_id": _task_id_var.get(None),
            "tenant_id": _tenant_id_var.get(None),
            "worktree_path": _worktree_var.get(None),
            "base_commit": _base_commit_var.get(None),
            "phase_name": _phase_var.get(None),
        }

    @staticmethod
    def emit_context_loss_warning(
        missing_vars: list[str],
        location: str = "unknown",
    ) -> None:
        """Emit warning when context loss detected (non-fatal).

        Called from catch blocks, not from critical assertions.
        """
        logger.warning(
            f"Context loss warning at {location}: {missing_vars} were None. "
            "This may indicate an async boundary crossing without propagation."
        )


class ContextLossError(Exception):
    """Raised when critical context is missing (fail-closed)."""
    pass


# Usage patterns for critical paths

def example_critical_path_with_assertion():
    """Example: process_tool_response (from the initial prompt)."""

    async def process_tool_response(tool_name, tool_input):
        # ASSERT context exists at path entry
        task_id = ContextLossSentinel.assert_task_context()
        tenant_id = ContextLossSentinel.assert_tenant_context()

        # Now safe to use task_id, tenant_id in the actual work
        logger.info(f"Processing tool={tool_name} for task={task_id}")
        # ... rest of implementation


def example_async_boundary_with_propagation():
    """Example: spawning child tasks that need context."""

    async def parent_task():
        # Parent has context (restored from session)
        context = ContextLossSentinel.get_all_context()

        # Spawn child
        child = asyncio.create_task(
            child_task(context)
        )
        await child

    async def child_task(context):
        # Child explicitly restores parent's context
        with ContextLossSentinel.with_context_propagation(context):
            task_id = ContextLossSentinel.assert_task_context()
            logger.info(f"Child running for task={task_id}")


__all__ = [
    "ContextLossSentinel",
    "ContextLossError",
]
