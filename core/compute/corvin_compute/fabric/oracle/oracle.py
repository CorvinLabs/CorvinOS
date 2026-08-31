"""Async Gradient Oracle — OracleLoop + subprocess call (ADR-0026 §B).

The Oracle runs concurrently with the Worker training loop via asyncio.gather().
Communication is strictly via two asyncio.Queue objects:

  oracle_queue: Worker → Oracle  (metrics, fire-and-forget)
  steer_queue:  Oracle → Worker  (steering vectors, non-blocking peek)

CRITICAL: training_loop MUST NEVER await — only put_nowait / get_nowait_or_none.
          The oracle_loop is the async side.

MUST NOT import anthropic / openai / google.cloud.aiplatform.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable, Optional

from ..backends.protocol import EpochMetrics, SteeringVector
from .steering import _parse_steering

log = logging.getLogger(__name__)

# Maximum number of metric batches buffered in oracle_queue before dropping oldest
_DEFAULT_QUEUE_MAX = 8
# Default subprocess command
_DEFAULT_CMD = ["claude", "-p", "--max-turns", "1", "--tools", ""]
# Default per-call timeout (seconds)
_DEFAULT_TIMEOUT = 30.0
# Consecutive failures before oracle loop exits
_MAX_CONSECUTIVE_FAILURES = 3


class _OracleQueue:
    """Thin wrapper around asyncio.Queue with overflow management.

    Overflow policy: drop oldest + emit WARNING audit event.
    """

    def __init__(self, maxsize: int = _DEFAULT_QUEUE_MAX) -> None:
        self._maxsize = maxsize
        self._q: asyncio.Queue = asyncio.Queue()

    def put_nowait(self, item: Any) -> None:
        """Non-blocking put — drops oldest on overflow and logs a warning."""
        if self._q.qsize() >= self._maxsize:
            try:
                dropped = self._q.get_nowait()
                log.warning(
                    "oracle_queue overflow: dropped oldest metrics batch (epoch=%s)",
                    getattr(dropped, "epoch", "?"),
                )
            except asyncio.QueueEmpty:
                pass
        self._q.put_nowait(item)

    async def get(self) -> Any:
        """Blocking async get — used by oracle_loop."""
        return await self._q.get()

    def qsize(self) -> int:
        return self._q.qsize()


class _SteerQueue:
    """Single-slot non-blocking steering queue for Worker consumption.

    Worker calls get_nowait_or_none() — always returns immediately.
    Oracle calls put_nowait() — overwrites if full (latest steering wins).
    """

    def __init__(self) -> None:
        self._q: asyncio.Queue = asyncio.Queue(maxsize=1)

    def put_nowait(self, item: Any) -> None:
        # Drain old item (latest steering always wins)
        try:
            self._q.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            self._q.put_nowait(item)
        except asyncio.QueueFull:
            pass

    def get_nowait_or_none(self) -> Optional[Any]:
        """Non-blocking peek — returns None if no steering available."""
        try:
            return self._q.get_nowait()
        except asyncio.QueueEmpty:
            return None


async def _call_oracle_subprocess(
    metrics: EpochMetrics,
    *,
    cmd: list[str] = _DEFAULT_CMD,
    timeout_s: float = _DEFAULT_TIMEOUT,
    run_id: str = "unknown",
) -> Optional[str]:
    """Call the oracle subprocess and return its stdout, or None on failure.

    The subprocess receives a JSON prompt via stdin and must emit a JSON
    steering vector to stdout.

    CRITICAL: uses asyncio.create_subprocess_exec — NEVER asyncio.create_subprocess_shell.
    """
    prompt = json.dumps({
        "task": "gradient_oracle",
        "run_id": run_id,
        "epoch": metrics.epoch,
        "primary_metric": metrics.primary_metric,
        "metric_value": metrics.metric_value,
        "extra": metrics.extra,
        "instruction": (
            "Analyze the training metrics and suggest parameter adjustments. "
            "Respond with ONLY a JSON object where keys are abstract parameter names "
            "(e.g. 'lr', 'max_depth', 'subsample') and values are direction strings "
            "like '\\u21930.3' (decrease by 30%) or '\\u25911' (increase integer by 1). "
            "No prose, no explanation — pure JSON only."
        ),
    })

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(prompt.encode("utf-8")),
            timeout=timeout_s,
        )
        output = stdout.decode("utf-8", errors="replace").strip()
        if stderr:
            log.debug("oracle subprocess stderr: %s", stderr.decode("utf-8", errors="replace")[:200])
        return output if output else None
    except asyncio.TimeoutError:
        log.warning(
            "oracle subprocess timed out after %.1fs (run_id=%s epoch=%s)",
            timeout_s, run_id, metrics.epoch,
        )
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
        return None
    except FileNotFoundError:
        log.warning("oracle subprocess command not found: %s", cmd[0])
        return None
    except Exception as exc:  # noqa: BLE001
        log.warning("oracle subprocess failed: %s", exc)
        return None


class OracleLoop:
    """Async oracle that reads from oracle_queue and writes to steer_queue.

    Lifecycle:
      - Instantiate with run_id + config.
      - Pass oracle_queue and steer_queue to training_loop (non-blocking side).
      - await oracle_loop.run() inside asyncio.gather() with training_loop().

    Failure contract:
      - Subprocess timeout/crash → audit WARNING, graceful continue.
      - 3 consecutive failures → oracle exits; Worker continues unsteered.
    """

    def __init__(
        self,
        *,
        run_id: str,
        oracle_queue: _OracleQueue,
        steer_queue: _SteerQueue,
        cmd: list[str] = _DEFAULT_CMD,
        timeout_s: float = _DEFAULT_TIMEOUT,
        max_consecutive_failures: int = _MAX_CONSECUTIVE_FAILURES,
        # Inject audit emit function for testability
        emit_fn: Optional[Callable[..., None]] = None,
    ) -> None:
        self.run_id = run_id
        self._oracle_q = oracle_queue
        self._steer_q = steer_queue
        self._cmd = cmd
        self._timeout_s = timeout_s
        self._max_failures = max_consecutive_failures
        self._emit = emit_fn or _default_emit
        # Public counters for observability
        self.consecutive_failures: int = 0
        self.total_steers_applied: int = 0
        self._done: bool = False

    def signal_done(self) -> None:
        """Signal the oracle loop to stop after the current call."""
        self._done = True

    async def run(self) -> None:
        """Oracle event loop — runs until done signal or max failures."""
        log.debug("oracle_loop started for run_id=%s", self.run_id)
        while not self._done:
            try:
                # Block until training_loop pushes metrics
                metrics = await asyncio.wait_for(
                    self._oracle_q.get(), timeout=60.0
                )
            except asyncio.TimeoutError:
                # No metrics for 60s — assume training finished
                log.debug("oracle_loop idle timeout; exiting (run_id=%s)", self.run_id)
                break

            raw_output = await _call_oracle_subprocess(
                metrics,
                cmd=self._cmd,
                timeout_s=self._timeout_s,
                run_id=self.run_id,
            )

            if raw_output is None:
                self.consecutive_failures += 1
                self._emit_subprocess_failed(metrics)
                if self.consecutive_failures >= self._max_failures:
                    log.warning(
                        "oracle_loop: %d consecutive failures; exiting oracle "
                        "(training continues unsteered, run_id=%s)",
                        self.consecutive_failures, self.run_id,
                    )
                    break
                continue

            steering = _parse_steering(raw_output)
            if steering is None:
                self.consecutive_failures += 1
                self._emit_subprocess_failed(metrics, reason="parse_failed")
                if self.consecutive_failures >= self._max_failures:
                    log.warning(
                        "oracle_loop: %d consecutive parse failures; exiting oracle",
                        self.consecutive_failures,
                    )
                    break
                continue

            # Reset on success
            self.consecutive_failures = 0
            self._steer_q.put_nowait(steering)
            self.total_steers_applied += 1

            self._emit_steer_applied(metrics, steering)

        log.debug("oracle_loop finished for run_id=%s", self.run_id)

    def _emit_subprocess_failed(
        self, metrics: EpochMetrics, reason: str = "subprocess_error"
    ) -> None:
        try:
            self._emit(
                "compute.oracle_subprocess_failed",
                run_id=self.run_id,
                epoch=metrics.epoch,
                failure_reason=reason,
            )
        except Exception:  # noqa: BLE001
            pass

    def _emit_steer_applied(
        self, metrics: EpochMetrics, steering: SteeringVector
    ) -> None:
        try:
            self._emit(
                "compute.oracle_steer_applied",
                run_id=self.run_id,
                epoch=metrics.epoch,
                # VALUES never in audit — only key names
                steering_keys=sorted(steering.vector.keys()),
                divergence_detected=False,
            )
        except Exception:  # noqa: BLE001
            pass


def _default_emit(event: str, **kwargs: Any) -> None:
    log.debug("audit event: %s kwargs=%s", event, kwargs)


# ---------------------------------------------------------------------------
# Synchronous training_loop helpers (no await!)
# ---------------------------------------------------------------------------

def make_queues(*, maxsize: int = _DEFAULT_QUEUE_MAX) -> tuple[_OracleQueue, _SteerQueue]:
    """Create a paired (oracle_queue, steer_queue) for one training session."""
    return _OracleQueue(maxsize=maxsize), _SteerQueue()


__all__ = [
    "OracleLoop",
    "_OracleQueue",
    "_SteerQueue",
    "_call_oracle_subprocess",
    "make_queues",
]
