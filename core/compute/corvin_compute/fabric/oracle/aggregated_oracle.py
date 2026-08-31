"""Aggregated Oracle for N-worker parallel training (ADR-0026 §B).

When N workers run in parallel, each worker pushes EpochMetrics to a shared
oracle_queue. The AggregatedOracleLoop collects all N metrics per batch,
calls the oracle subprocess once with the aggregated view, and broadcasts
the resulting steering vector back to all N workers.

Divergence detection: if per-worker loss variance exceeds divergence_threshold,
the oracle is asked for per-worker steering and the audit event carries
divergence_detected=True.

MUST NOT import anthropic / openai / google.cloud.aiplatform.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Optional

from ..backends.protocol import EpochMetrics, SteeringVector
from .oracle import (
    _DEFAULT_CMD,
    _DEFAULT_TIMEOUT,
    _MAX_CONSECUTIVE_FAILURES,
    _SteerQueue,
    _call_oracle_subprocess,
    _default_emit,
)
from .steering import _parse_steering

log = logging.getLogger(__name__)


class AggregatedOracleLoop:
    """Oracle loop that aggregates N worker metric batches per round.

    One steer_queue per worker — broadcast or per-worker steering depending
    on divergence.
    """

    def __init__(
        self,
        *,
        run_id: str,
        n_workers: int,
        # Each worker has its own asyncio.Queue for pushing metrics
        worker_queues: list[asyncio.Queue],
        # Each worker has its own _SteerQueue for receiving steering
        steer_queues: list[_SteerQueue],
        divergence_threshold: float = 0.05,
        cmd: list[str] = _DEFAULT_CMD,
        timeout_s: float = _DEFAULT_TIMEOUT,
        max_consecutive_failures: int = _MAX_CONSECUTIVE_FAILURES,
        emit_fn: Optional[Callable[..., None]] = None,
    ) -> None:
        if len(worker_queues) != n_workers or len(steer_queues) != n_workers:
            raise ValueError(
                f"worker_queues and steer_queues must each have {n_workers} entries"
            )
        self.run_id = run_id
        self.n_workers = n_workers
        self._worker_queues = worker_queues
        self._steer_queues = steer_queues
        self._divergence_threshold = divergence_threshold
        self._cmd = cmd
        self._timeout_s = timeout_s
        self._max_failures = max_consecutive_failures
        self._emit = emit_fn or _default_emit
        self.consecutive_failures: int = 0
        self._done: bool = False

    def signal_done(self) -> None:
        self._done = True

    async def run(self) -> None:
        """Main loop — collect N batches, call oracle once, broadcast."""
        log.debug(
            "aggregated_oracle started: run_id=%s n_workers=%d",
            self.run_id, self.n_workers,
        )
        while not self._done:
            # Collect one metric from each worker (with timeout)
            batch: list[Optional[EpochMetrics]] = []
            for wq in self._worker_queues:
                try:
                    metrics = await asyncio.wait_for(wq.get(), timeout=60.0)
                    batch.append(metrics)
                except asyncio.TimeoutError:
                    batch.append(None)

            # If all timed out, assume training finished
            if all(m is None for m in batch):
                log.debug("aggregated_oracle: all workers timed out; exiting")
                break

            valid = [m for m in batch if m is not None]
            epoch = max(m.epoch for m in valid)

            # Divergence detection
            values = [m.metric_value for m in valid]
            divergence = _compute_variance(values)
            divergence_detected = divergence > self._divergence_threshold

            # Build aggregated prompt
            raw_output = await _call_oracle_subprocess(
                _aggregate_metrics(valid, epoch),
                cmd=self._cmd,
                timeout_s=self._timeout_s,
                run_id=self.run_id,
            )

            if raw_output is None:
                self.consecutive_failures += 1
                self._emit_failed(epoch, "subprocess_error")
                if self.consecutive_failures >= self._max_failures:
                    log.warning(
                        "aggregated_oracle: %d failures; exiting (workers continue unsteered)",
                        self.consecutive_failures,
                    )
                    break
                continue

            # Try to parse as aggregated response first
            global_steer, per_worker_steers = _parse_aggregated(raw_output, self.n_workers)

            if global_steer is None and not per_worker_steers:
                self.consecutive_failures += 1
                self._emit_failed(epoch, "parse_failed")
                if self.consecutive_failures >= self._max_failures:
                    break
                continue

            self.consecutive_failures = 0

            # Broadcast or per-worker dispatch
            if divergence_detected and per_worker_steers:
                for i, sq in enumerate(self._steer_queues):
                    worker_key = f"worker_{i}"
                    steer = per_worker_steers.get(worker_key) or global_steer
                    if steer:
                        sq.put_nowait(steer)
            else:
                # Broadcast global steering to all workers
                if global_steer:
                    for sq in self._steer_queues:
                        sq.put_nowait(global_steer)

            # Audit event
            steering_keys = sorted(global_steer.vector.keys()) if global_steer else []
            try:
                self._emit(
                    "compute.oracle_steer_applied",
                    run_id=self.run_id,
                    epoch=epoch,
                    steering_keys=steering_keys,
                    divergence_detected=divergence_detected,
                )
            except Exception:  # noqa: BLE001
                pass

        log.debug("aggregated_oracle finished: run_id=%s", self.run_id)

    def _emit_failed(self, epoch: int, reason: str) -> None:
        try:
            self._emit(
                "compute.oracle_subprocess_failed",
                run_id=self.run_id,
                epoch=epoch,
                failure_reason=reason,
            )
        except Exception:  # noqa: BLE001
            pass


def _compute_variance(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return variance


def _aggregate_metrics(metrics: list[EpochMetrics], epoch: int) -> EpochMetrics:
    """Produce a single EpochMetrics representing the aggregate of N workers."""
    avg_metric = sum(m.metric_value for m in metrics) / len(metrics)
    return EpochMetrics(
        epoch=epoch,
        primary_metric=metrics[0].primary_metric if metrics else "loss",
        metric_value=avg_metric,
        extra={
            "n_workers": len(metrics),
            "per_worker_values": [m.metric_value for m in metrics],
        },
    )


def _parse_aggregated(
    raw: str, n_workers: int
) -> tuple[Optional[SteeringVector], dict[str, SteeringVector]]:
    """Parse aggregated oracle response.

    Tries two formats:
    1. {"global_steer": {...}, "divergence_detected": bool, "worker_specific_steer": {...}}
    2. Plain steering vector (broadcast to all workers)
    """
    if not raw or not raw.strip():
        return None, {}

    try:
        data = json.loads(raw.strip())
    except json.JSONDecodeError:
        return None, {}

    if not isinstance(data, dict):
        return None, {}

    # Format 1: aggregated response with worker-specific steering
    if "global_steer" in data:
        global_steer = _parse_steering(json.dumps(data["global_steer"]))
        worker_steers: dict[str, SteeringVector] = {}
        per_worker_raw = data.get("worker_specific_steer", {})
        if isinstance(per_worker_raw, dict):
            for worker_key, steer_raw in per_worker_raw.items():
                if isinstance(steer_raw, dict):
                    parsed = _parse_steering(json.dumps(steer_raw))
                    if parsed:
                        worker_steers[worker_key] = parsed
        return global_steer, worker_steers

    # Format 2: plain steering vector — broadcast
    global_steer = _parse_steering(raw)
    return global_steer, {}


__all__ = [
    "AggregatedOracleLoop",
    "_compute_variance",
    "_parse_aggregated",
]
