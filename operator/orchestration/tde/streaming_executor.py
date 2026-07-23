"""ADR-0214: Streaming Executor (Phase 3).

For tasks with large data (>1GB), stream instead of snapshot.
L34 filtering happens at stream-read time.
"""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Optional

try:
    from operator.orchestration.initial_analysis import Step
except ImportError:
    from initial_analysis import Step  # type: ignore

from .adaptive_delegation_executor import StepResult
from .l34_delegation_gate import L34DelegationGate

_logger = logging.getLogger(__name__)


class StreamingExecutor:
    """Execute steps via streaming (>1GB data)."""

    BIG_DATA_THRESHOLD = 1024 * 1024 * 1024  # 1GB

    def __init__(self, l34_gate: L34DelegationGate):
        """Initialize."""
        self.l34_gate = l34_gate

    def should_use_streaming(self, data_volume_bytes: int) -> bool:
        """Check if data is large enough for streaming."""
        return data_volume_bytes > self.BIG_DATA_THRESHOLD

    async def stream_filtered_data(
        self,
        statement: dict[str, Any],
        required_vars: set[str],
        max_classification: str = "INTERNAL",
    ) -> AsyncIterator[dict[str, Any]]:
        """
        Stream statement data with L34 filtering.

        Yields chunks of data that pass L34 checks.
        Skips/redacts unsafe data.

        Args:
            statement: Full statement (potentially GB-sized)
            required_vars: Which variables this step needs
            max_classification: Max allowed classification

        Yields:
            Safe data chunks
        """

        for var_name in required_vars:
            if var_name not in statement:
                continue

            var_value = statement[var_name]

            # Pre-scan: is this variable safe?
            gate_result = self.l34_gate.can_delegate_step(
                None,  # No specific step
                {var_name: var_value},
                max_classification=max_classification,
            )

            if not gate_result.can_delegate:
                # Unsafe: fail-closed
                _logger.warning(f"Streaming: {var_name} unsafe, aborting stream")
                raise PermissionError(f"Cannot stream {var_name}: {gate_result.reason}")

            # Safe: yield in chunks
            # (In real implementation: chunk large values by size)
            yield {var_name: var_value}

    async def execute_streaming(
        self,
        step: Step,
        statement: dict[str, Any],
        executor_fn,  # Async function to process streamed data
    ) -> StepResult:
        """
        Execute step with streaming data.

        Args:
            step: Step to execute
            statement: Large data (~GB)
            executor_fn: Async function that processes stream

        Returns:
            StepResult
        """

        try:
            _logger.info(f"Streaming execution for step {step.step}")

            # Create streaming generator
            stream = self.stream_filtered_data(statement, set(statement.keys()))

            # Pass stream to executor
            result = await executor_fn(step, stream)

            return StepResult(
                step_num=step.step,
                action=step.action,
                success=True,
                output=result,
                was_delegated=False,  # Streaming is local execution
            )

        except PermissionError as e:
            # L34 gate rejected: fail-closed
            return StepResult(
                step_num=step.step,
                action=step.action,
                success=False,
                error=f"Streaming blocked by L34: {e}",
                was_delegated=False,
            )

        except Exception as e:
            return StepResult(
                step_num=step.step,
                action=step.action,
                success=False,
                error=str(e),
                was_delegated=False,
            )
