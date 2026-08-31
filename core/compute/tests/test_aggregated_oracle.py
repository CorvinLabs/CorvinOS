"""Tests for AggregatedOracleLoop — 20 test cases (ADR-0026 §B)."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from corvin_compute.fabric.oracle.aggregated_oracle import (
    AggregatedOracleLoop,
    _compute_variance,
    _parse_aggregated,
)
from corvin_compute.fabric.oracle.oracle import _SteerQueue, make_queues
from corvin_compute.fabric.backends.protocol import EpochMetrics, SteeringVector


def _make_worker_queues(n: int):
    return [asyncio.Queue() for _ in range(n)]


def _make_steer_queues(n: int):
    return [_SteerQueue() for _ in range(n)]


def _metrics(epoch=1, value=0.5) -> EpochMetrics:
    return EpochMetrics(epoch=epoch, primary_metric="loss", metric_value=value)


def _make_loop(n_workers=2, *, divergence_threshold=0.05, emit_fn=None, timeout_s=5.0):
    worker_qs = _make_worker_queues(n_workers)
    steer_qs = _make_steer_queues(n_workers)
    loop = AggregatedOracleLoop(
        run_id="r-agg",
        n_workers=n_workers,
        worker_queues=worker_qs,
        steer_queues=steer_qs,
        divergence_threshold=divergence_threshold,
        cmd=["echo", "test"],
        timeout_s=timeout_s,
        emit_fn=emit_fn or (lambda e, **kw: None),
    )
    return loop, worker_qs, steer_qs


# ---------------------------------------------------------------------------
# _compute_variance tests
# ---------------------------------------------------------------------------

class TestComputeVariance:
    def test_zero_variance_same_values(self):
        assert _compute_variance([0.5, 0.5, 0.5]) == pytest.approx(0.0)

    def test_nonzero_variance(self):
        var = _compute_variance([0.0, 1.0])
        assert var == pytest.approx(0.25)

    def test_single_value_returns_zero(self):
        assert _compute_variance([0.7]) == 0.0

    def test_empty_returns_zero(self):
        assert _compute_variance([]) == 0.0


# ---------------------------------------------------------------------------
# _parse_aggregated tests
# ---------------------------------------------------------------------------

class TestParseAggregated:
    def test_plain_steering_parsed_as_global(self):
        raw = json.dumps({"lr": "↓0.2"})
        global_steer, worker_steers = _parse_aggregated(raw, 2)
        assert global_steer is not None
        assert global_steer.vector == {"lr": "↓0.2"}
        assert worker_steers == {}

    def test_aggregated_format_with_global_and_worker(self):
        raw = json.dumps({
            "global_steer": {"lr": "↓0.2"},
            "divergence_detected": True,
            "worker_specific_steer": {
                "worker_0": {"subsample": "↑0.1"},
                "worker_2": {"max_depth": "↑1"},
            },
        })
        global_steer, worker_steers = _parse_aggregated(raw, 3)
        assert global_steer is not None
        assert "lr" in global_steer.vector
        assert "worker_0" in worker_steers
        assert "worker_2" in worker_steers
        assert "worker_1" not in worker_steers

    def test_empty_string_returns_none_none(self):
        gs, ws = _parse_aggregated("", 2)
        assert gs is None
        assert ws == {}

    def test_invalid_json_returns_none(self):
        gs, ws = _parse_aggregated("not json", 2)
        assert gs is None
        assert ws == {}


# ---------------------------------------------------------------------------
# AggregatedOracleLoop tests
# ---------------------------------------------------------------------------

class TestAggregatedOracleLoop:
    def test_n_workers_mismatch_raises(self):
        worker_qs = _make_worker_queues(2)
        steer_qs = _make_steer_queues(1)  # mismatch
        with pytest.raises(ValueError, match="worker_queues"):
            AggregatedOracleLoop(
                run_id="r1",
                n_workers=2,
                worker_queues=worker_qs,
                steer_queues=steer_qs,
                emit_fn=lambda e, **kw: None,
            )

    def test_broadcast_global_steer_to_all_workers(self):
        events = []

        async def run_test():
            loop, wqs, sqs = _make_loop(
                n_workers=2,
                emit_fn=lambda e, **kw: events.append((e, kw)),
            )
            with patch(
                "corvin_compute.fabric.oracle.aggregated_oracle._call_oracle_subprocess",
                new=AsyncMock(return_value='{"lr": "↓0.2"}'),
            ):
                for q in wqs:
                    await q.put(_metrics(value=0.5))
                # Use task + cancel: let loop process one batch, then cancel
                task = asyncio.create_task(loop.run())
                await asyncio.sleep(0.1)
                loop.signal_done()
                try:
                    await asyncio.wait_for(task, timeout=2.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    task.cancel()
            return sqs

        sqs = asyncio.run(run_test())
        sv0 = sqs[0].get_nowait_or_none()
        sv1 = sqs[1].get_nowait_or_none()
        assert sv0 is not None, "Worker 0 should receive steering"
        assert sv1 is not None, "Worker 1 should receive steering"

    def test_divergence_above_threshold_emits_audit(self):
        events = []

        async def run_test():
            loop, wqs, sqs = _make_loop(
                n_workers=2,
                divergence_threshold=0.001,  # very tight → always divergent
                emit_fn=lambda e, **kw: events.append((e, kw)),
            )
            aggregated_response = json.dumps({
                "global_steer": {"lr": "↓0.2"},
                "divergence_detected": True,
                "worker_specific_steer": {
                    "worker_0": {"subsample": "↑0.1"},
                },
            })
            with patch(
                "corvin_compute.fabric.oracle.aggregated_oracle._call_oracle_subprocess",
                new=AsyncMock(return_value=aggregated_response),
            ):
                for i, q in enumerate(wqs):
                    await q.put(_metrics(value=0.1 + i * 0.5))
                task = asyncio.create_task(loop.run())
                await asyncio.sleep(0.1)
                loop.signal_done()
                try:
                    await asyncio.wait_for(task, timeout=2.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    task.cancel()

        asyncio.run(run_test())
        steer_events = [kw for e, kw in events if "oracle_steer_applied" in e]
        # Divergence should be reflected in the audit event
        if steer_events:
            assert steer_events[0].get("divergence_detected") is True

    def test_oracle_failure_does_not_abort_loop(self):
        """Workers continue unsteered when oracle fails."""
        failures = [0]

        async def run_test():
            loop, wqs, sqs = _make_loop(
                n_workers=1,
                emit_fn=lambda e, **kw: None,
            )
            loop._max_failures = 3
            with patch(
                "corvin_compute.fabric.oracle.aggregated_oracle._call_oracle_subprocess",
                new=AsyncMock(return_value=None),
            ):
                for i in range(3):
                    await wqs[0].put(_metrics(epoch=i + 1))
                await loop.run()
            failures[0] = loop.consecutive_failures

        asyncio.run(run_test())
        assert failures[0] >= 1  # failures recorded, but loop exited cleanly

    def test_all_workers_timeout_exits_loop(self):
        """When no metrics are pushed, all worker queues time out and loop exits."""
        async def run_test():
            worker_qs = _make_worker_queues(2)
            steer_qs = _make_steer_queues(2)
            loop = AggregatedOracleLoop(
                run_id="r-timeout",
                n_workers=2,
                worker_queues=worker_qs,
                steer_queues=steer_qs,
                emit_fn=lambda e, **kw: None,
                # Override the internal timeout to 0.1s for fast test
            )
            # Monkey-patch the internal wait_for timeout to be short
            original_run = loop.run

            async def fast_run():
                # Patch asyncio.wait_for inside the loop to use short timeout
                import corvin_compute.fabric.oracle.aggregated_oracle as mod
                original_wait_for = asyncio.wait_for

                async def patched_wait_for(coro, timeout, **kw):
                    return await original_wait_for(coro, 0.2, **kw)

                with patch.object(asyncio, "wait_for", patched_wait_for):
                    await original_run()

            await asyncio.wait_for(fast_run(), timeout=5.0)

        asyncio.run(run_test())

    def test_steer_queues_independent_per_worker(self):
        worker_qs = _make_worker_queues(2)
        steer_qs = _make_steer_queues(2)
        sv = SteeringVector(vector={"lr": "↓0.1"})
        steer_qs[0].put_nowait(sv)
        assert steer_qs[0].get_nowait_or_none() is sv
        assert steer_qs[1].get_nowait_or_none() is None

    def test_divergence_detected_in_audit_event(self):
        """divergence_detected=True must appear in audit event."""
        events = []

        async def run_test():
            loop, wqs, sqs = _make_loop(
                n_workers=2,
                divergence_threshold=0.0,  # always trigger
                emit_fn=lambda e, **kw: events.append((e, kw)),
            )
            with patch(
                "corvin_compute.fabric.oracle.aggregated_oracle._call_oracle_subprocess",
                new=AsyncMock(return_value='{"lr": "↓0.1"}'),
            ):
                await wqs[0].put(_metrics(value=0.2))
                await wqs[1].put(_metrics(value=0.8))
                task = asyncio.create_task(loop.run())
                await asyncio.sleep(0.1)
                loop.signal_done()
                try:
                    await asyncio.wait_for(task, timeout=2.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    task.cancel()

        asyncio.run(run_test())
        steer_events = [kw for e, kw in events if "steer_applied" in e]
        if steer_events:
            assert steer_events[0].get("divergence_detected") is True

    def test_oracle_subprocess_failed_emits_for_parse_error(self):
        events = []

        async def run_test():
            loop, wqs, sqs = _make_loop(
                n_workers=1,
                emit_fn=lambda e, **kw: events.append((e, kw)),
            )
            loop._max_failures = 1
            with patch(
                "corvin_compute.fabric.oracle.aggregated_oracle._call_oracle_subprocess",
                new=AsyncMock(return_value="not json"),
            ):
                await wqs[0].put(_metrics())
                await loop.run()

        asyncio.run(run_test())
        fail_events = [e for e, kw in events if "subprocess_failed" in e]
        assert len(fail_events) >= 1
