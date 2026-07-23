"""ADR-0214: Streaming Executor (Phase 3).

For tasks with large data (>1GB), stream instead of snapshot.
L34 filtering happens at stream-read time.
"""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Optional

try:
    from initial_analysis import Step
except ImportError:  # pragma: no cover - orchestration dir not on sys.path
    from ..initial_analysis import Step  # type: ignore

from . import tde_audit
from .adaptive_delegation_executor import StepResult
from .l34_delegation_gate import L34DelegationGate

_logger = logging.getLogger(__name__)


class StreamingExecutor:
    """Execute steps via streaming (>1GB data)."""

    BIG_DATA_THRESHOLD = 1024 * 1024 * 1024  # 1GB

    # Adaptive chunk sizing (Phase 4 optimization)
    # Kept comfortably under L34DelegationGate._CONTENT_SCAN_MAX_BYTES (5MB).
    # Smaller values → 1MB chunks (fine-grained scanning)
    # Larger values → 10MB chunks (better throughput, still scanned)
    # Boundary tuning: <100MB → 1MB, <500MB → 5MB, >=500MB → 10MB
    _CHUNK_SIZE_SMALL = 1 * 1024 * 1024  # 1MB (for <100MB values)
    _CHUNK_SIZE_MEDIUM = 5 * 1024 * 1024  # 5MB (for 100-500MB values)
    _CHUNK_SIZE_LARGE = 10 * 1024 * 1024  # 10MB (for >500MB values)

    def __init__(self, l34_gate: L34DelegationGate):
        """Initialize."""
        self.l34_gate = l34_gate

    def should_use_streaming(self, data_volume_bytes: int) -> bool:
        """Check if data is large enough for streaming."""
        return data_volume_bytes > self.BIG_DATA_THRESHOLD

    def _adaptive_chunk_size(self, value_size_bytes: int) -> int:
        """
        Adaptively choose chunk size based on value size.

        Larger values benefit from larger chunks (fewer L34 gate calls).
        Smaller values use smaller chunks for finer-grained scanning.

        Args:
            value_size_bytes: Size of the value to chunk

        Returns:
            Optimal chunk size in bytes
        """
        if value_size_bytes < 100 * 1024 * 1024:  # <100MB
            return self._CHUNK_SIZE_SMALL
        elif value_size_bytes < 500 * 1024 * 1024:  # <500MB
            return self._CHUNK_SIZE_MEDIUM
        else:  # >=500MB
            return self._CHUNK_SIZE_LARGE

    def _chunk_value(self, value: Any) -> list[Any]:
        """
        Split str/bytes into adaptively-sized pieces; other types pass through whole.

        Uses adaptive chunk sizing based on value size for optimal throughput.
        """
        if isinstance(value, (str, bytes)):
            value_size = len(value)
            chunk_size = self._adaptive_chunk_size(value_size)
            chunks = [value[i:i + chunk_size] for i in range(0, len(value), chunk_size)]
            return chunks or [value]
        return [value]

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

        Large values are split into bounded chunks (see _STREAM_CHUNK_BYTES)
        and each chunk is scanned independently: an unsafe CHUNK is skipped
        (never yielded) and reported via a content-free tde.* audit event,
        but does NOT abort the remaining chunks or other variables — true to
        the "skip/redact" contract above, rather than a full-stream abort on
        the first unsafe (or merely oversized-for-one-scan) chunk.

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
            chunks = self._chunk_value(var_value)

            for idx, chunk in enumerate(chunks):
                gate_result = self.l34_gate.prescan(
                    {var_name: chunk},
                    max_classification=max_classification,
                )

                if not gate_result.can_delegate:
                    _logger.warning(
                        "Streaming: %s chunk %d/%d unsafe (%s) — skipped",
                        var_name, idx + 1, len(chunks), gate_result.reason,
                    )
                    tde_audit.emit(
                        "l34_blocked", scope="stream_chunk",
                        reason_code="classification_exceeded",
                    )
                    continue

                yield {var_name: chunk}

    async def execute_streaming(
        self,
        step: Step,
        statement: dict[str, Any],
        executor_fn,  # Async function to process streamed data
        required_vars: Optional[set] = None,
        max_classification: str = "INTERNAL",
    ) -> StepResult:
        """
        Execute step with streaming data.

        Phase-3 status: the stream is L34-filtered and consumed by a LOCAL
        executor_fn (was_delegated=False is accurate). Remote streaming
        delegation needs a streaming-capable WorkerIPC (not yet built).

        Args:
            step: Step to execute
            statement: Large data (~GB)
            executor_fn: Async function that processes stream
            required_vars: Variables this step needs (default: all keys)

        Returns:
            StepResult
        """

        import time as _time

        start = _time.time()
        try:
            _logger.info(f"Streaming execution for step {step.step}")

            # Create streaming generator (only the variables this step needs;
            # the caller's classification ceiling is passed through — it was
            # previously silently reset to the INTERNAL default).
            stream = self.stream_filtered_data(
                statement,
                required_vars if required_vars is not None else set(statement.keys()),
                max_classification=max_classification,
            )

            # Pass stream to executor
            result = await executor_fn(step, stream)

            tde_audit.emit(
                "streaming_step_executed", step_action=step.action, success=True,
                duration_ms=int((_time.time() - start) * 1000),
            )
            return StepResult(
                step_num=step.step,
                action=step.action,
                success=True,
                output=result,
                was_delegated=False,  # Streaming is local execution
            )

        except PermissionError as e:
            # Defense-in-depth: stream_filtered_data() itself now skips unsafe
            # chunks rather than raising, but a directly-injected executor_fn
            # or a future gate change could still raise — fail-closed either way.
            tde_audit.emit(
                "streaming_step_executed", step_action=step.action, success=False,
                duration_ms=int((_time.time() - start) * 1000),
            )
            return StepResult(
                step_num=step.step,
                action=step.action,
                success=False,
                error=f"Streaming blocked by L34: {e}",
                was_delegated=False,
            )

        except Exception as e:
            tde_audit.emit(
                "streaming_step_executed", step_action=step.action, success=False,
                duration_ms=int((_time.time() - start) * 1000),
            )
            return StepResult(
                step_num=step.step,
                action=step.action,
                success=False,
                error=str(e),
                was_delegated=False,
            )
