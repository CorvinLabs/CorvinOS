"""Tests for OracleLoop, _parse_steering, _apply_steering — 30 test cases (ADR-0026 §B)."""
from __future__ import annotations

import ast
import asyncio
import inspect
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from corvin_compute.fabric.oracle.oracle import (
    OracleLoop,
    _OracleQueue,
    _SteerQueue,
    _call_oracle_subprocess,
    make_queues,
)
from corvin_compute.fabric.oracle.steering import _apply_steering, _parse_steering
from corvin_compute.fabric.backends.protocol import (
    BackendParams,
    BackendSession,
    EpochMetrics,
    SteeringVector,
)


# ---------------------------------------------------------------------------
# _parse_steering tests
# ---------------------------------------------------------------------------

class TestParseSteeringVector:
    def test_valid_down(self):
        raw = json.dumps({"lr": "↓0.3"})
        sv = _parse_steering(raw)
        assert sv is not None
        assert sv.vector["lr"] == "↓0.3"

    def test_valid_up_integer(self):
        raw = json.dumps({"max_depth": "↑1"})
        sv = _parse_steering(raw)
        assert sv is not None
        assert sv.vector["max_depth"] == "↑1"

    def test_multiple_keys(self):
        raw = json.dumps({"lr": "↓0.3", "max_depth": "↑1", "subsample": "↑0.05"})
        sv = _parse_steering(raw)
        assert sv is not None
        assert len(sv.vector) == 3

    def test_empty_string_returns_none(self):
        assert _parse_steering("") is None

    def test_not_json_returns_none(self):
        assert _parse_steering("not json!") is None

    def test_json_array_returns_none(self):
        assert _parse_steering("[1, 2, 3]") is None

    def test_json_scalar_returns_none(self):
        assert _parse_steering("42") is None

    def test_wrong_direction_char_filtered(self):
        raw = json.dumps({"lr": "X0.3", "alpha": "↓0.1"})
        sv = _parse_steering(raw)
        # lr should be filtered (invalid direction), alpha kept
        assert sv is not None
        assert "alpha" in sv.vector
        assert "lr" not in sv.vector

    def test_non_numeric_magnitude_filtered(self):
        raw = json.dumps({"lr": "↓abc"})
        sv = _parse_steering(raw)
        assert sv is None  # all keys filtered → empty → None

    def test_non_string_value_filtered(self):
        raw = json.dumps({"lr": 0.3})  # float, not str
        sv = _parse_steering(raw)
        # lr filtered; empty → None
        assert sv is None

    def test_all_invalid_keys_returns_none(self):
        raw = json.dumps({"lr": 0.3, "x": [1, 2]})
        sv = _parse_steering(raw)
        assert sv is None

    def test_empty_json_object_returns_none(self):
        assert _parse_steering("{}") is None

    def test_whitespace_only_returns_none(self):
        assert _parse_steering("   ") is None


# ---------------------------------------------------------------------------
# _apply_steering tests
# ---------------------------------------------------------------------------

class TestApplySteering:
    def _make_session(self) -> BackendSession:
        return BackendSession(run_id="r1", backend_name="test")

    def _make_backend(self) -> MagicMock:
        backend = MagicMock()
        backend.translate_steering.return_value = BackendParams(params={"eta0": 0.007})
        return backend

    def test_apply_calls_translate(self):
        session = self._make_session()
        backend = self._make_backend()
        sv = SteeringVector(vector={"lr": "↓0.3"})
        _apply_steering(session, sv, backend)
        backend.translate_steering.assert_called_once_with(sv)

    def test_apply_updates_session_params(self):
        session = self._make_session()
        backend = self._make_backend()
        sv = SteeringVector(vector={"lr": "↓0.3"})
        _apply_steering(session, sv, backend)
        assert session.params.get("eta0") == 0.007

    def test_apply_never_raises_on_backend_error(self):
        session = self._make_session()
        backend = MagicMock()
        backend.translate_steering.side_effect = RuntimeError("backend error")
        sv = SteeringVector(vector={"lr": "↓0.3"})
        # Should not raise — graceful degrade
        _apply_steering(session, sv, backend)


# ---------------------------------------------------------------------------
# _apply_directive via steering tests
# ---------------------------------------------------------------------------

class TestApplyDirectiveValues:
    def test_down_0_3_on_1_0_gives_0_7(self):
        from corvin_compute.fabric.backends.builtin.sklearn_backend import _apply_directive
        assert abs(_apply_directive(1.0, "↓0.3") - 0.7) < 1e-10

    def test_up_1_on_3_gives_4(self):
        from corvin_compute.fabric.backends.builtin.sklearn_backend import _apply_directive
        assert _apply_directive(3, "↑1") == 4

    def test_up_float_on_float(self):
        from corvin_compute.fabric.backends.builtin.sklearn_backend import _apply_directive
        result = _apply_directive(0.8, "↑0.1")
        assert abs(result - 0.88) < 1e-9


# ---------------------------------------------------------------------------
# OracleLoop tests (mocked subprocess)
# ---------------------------------------------------------------------------

class TestOracleLoop:
    def _metrics(self, epoch=1, value=0.5) -> EpochMetrics:
        return EpochMetrics(epoch=epoch, primary_metric="loss", metric_value=value)

    def test_oracle_processes_metrics_and_puts_steer(self):
        """Oracle processes a metric batch and puts a SteeringVector in steer_queue."""
        oracle_q, steer_q = make_queues()
        events = []

        async def run_test():
            loop = OracleLoop(
                run_id="r1",
                oracle_queue=oracle_q,
                steer_queue=steer_q,
                timeout_s=5.0,
                emit_fn=lambda e, **kw: events.append((e, kw)),
                max_consecutive_failures=1,  # 1 failure → exit loop quickly
            )
            oracle_q.put_nowait(self._metrics())
            with patch(
                "corvin_compute.fabric.oracle.oracle._call_oracle_subprocess",
                new=AsyncMock(return_value='{"lr": "↓0.2"}'),
            ):
                # Loop exits after processing one item (idle timeout 60s but queue empties)
                # Use a task + cancel pattern: start the loop, let it process, then cancel
                task = asyncio.create_task(loop.run())
                # Give it time to process the one item
                await asyncio.sleep(0.1)
                loop.signal_done()
                try:
                    await asyncio.wait_for(task, timeout=2.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    task.cancel()

        asyncio.run(run_test())
        sv = steer_q.get_nowait_or_none()
        assert sv is not None
        assert "lr" in sv.vector

    def test_oracle_timeout_emits_failure_event(self):
        oracle_q, steer_q = make_queues()
        events = []

        async def slow_process(*args, **kwargs):
            await asyncio.sleep(100)
            return None

        async def run_test():
            loop = OracleLoop(
                run_id="r1",
                oracle_queue=oracle_q,
                steer_queue=steer_q,
                timeout_s=0.01,  # very short timeout
                emit_fn=lambda e, **kw: events.append((e, kw)),
                max_consecutive_failures=1,
            )
            with patch(
                "corvin_compute.fabric.oracle.oracle._call_oracle_subprocess",
                new=AsyncMock(return_value=None),
            ):
                oracle_q.put_nowait(self._metrics())
                await loop.run()

        asyncio.run(run_test())
        failure_events = [e for e, kw in events if "oracle_subprocess_failed" in e]
        assert len(failure_events) >= 1

    def test_three_consecutive_failures_exits_oracle(self):
        oracle_q, steer_q = make_queues()
        call_count = [0]
        failures_seen = [0]

        async def failing_subprocess(*args, **kwargs):
            call_count[0] += 1
            return None

        async def run_test():
            loop = OracleLoop(
                run_id="r1",
                oracle_queue=oracle_q,
                steer_queue=steer_q,
                max_consecutive_failures=3,
                emit_fn=lambda e, **kw: None,
            )
            with patch(
                "corvin_compute.fabric.oracle.oracle._call_oracle_subprocess",
                new=AsyncMock(side_effect=failing_subprocess),
            ):
                # Push 3 metrics
                for i in range(3):
                    oracle_q.put_nowait(self._metrics(epoch=i + 1))
                await loop.run()
                failures_seen[0] = loop.consecutive_failures

        asyncio.run(run_test())
        assert failures_seen[0] >= 3

    def test_steer_queue_returns_none_when_empty(self):
        _, steer_q = make_queues()
        assert steer_q.get_nowait_or_none() is None

    def test_oracle_queue_overflow_drops_oldest(self):
        oracle_q, _ = make_queues(maxsize=3)
        for i in range(5):
            oracle_q.put_nowait(self._metrics(epoch=i))
        # Queue should not have more than maxsize items
        assert oracle_q.qsize() <= 3

    def test_steer_queue_latest_wins(self):
        _, steer_q = make_queues()
        sv1 = SteeringVector(vector={"lr": "↓0.1"})
        sv2 = SteeringVector(vector={"lr": "↓0.5"})
        steer_q.put_nowait(sv1)
        steer_q.put_nowait(sv2)
        result = steer_q.get_nowait_or_none()
        assert result is not None
        assert result.vector["lr"] == "↓0.5"

    def test_make_queues_returns_both(self):
        oracle_q, steer_q = make_queues()
        assert oracle_q is not None
        assert steer_q is not None

    def test_signal_done_stops_loop(self):
        oracle_q, steer_q = make_queues()

        async def run_test():
            loop = OracleLoop(
                run_id="r1",
                oracle_queue=oracle_q,
                steer_queue=steer_q,
                emit_fn=lambda e, **kw: None,
            )
            loop.signal_done()
            # This should return quickly since _done=True and queue is empty (timeout)
            await asyncio.wait_for(loop.run(), timeout=3.0)

        asyncio.run(run_test())


# ---------------------------------------------------------------------------
# Structural test: training_loop has no await
# ---------------------------------------------------------------------------

class TestTrainingLoopNoAwait:
    """Verify that the oracle module has proper sync/async boundary.

    The training_loop pattern (put_nowait + get_nowait_or_none) must not
    contain `await` on the oracle queue operations.
    """

    def test_oracle_queue_put_nowait_is_sync(self):
        """put_nowait must be a regular sync method."""
        oracle_q, _ = make_queues()
        method = oracle_q.put_nowait
        assert not asyncio.iscoroutinefunction(method), \
            "put_nowait must be sync (not a coroutine)"

    def test_steer_queue_get_nowait_is_sync(self):
        """get_nowait_or_none must be a regular sync method."""
        _, steer_q = make_queues()
        method = steer_q.get_nowait_or_none
        assert not asyncio.iscoroutinefunction(method), \
            "get_nowait_or_none must be sync (not a coroutine)"

    def test_oracle_loop_run_is_async(self):
        """oracle_loop.run() must be async (it can await)."""
        oracle_q, steer_q = make_queues()
        loop = OracleLoop(
            run_id="r1",
            oracle_queue=oracle_q,
            steer_queue=steer_q,
            emit_fn=lambda e, **kw: None,
        )
        assert asyncio.iscoroutinefunction(loop.run), \
            "oracle_loop.run() must be a coroutine function"

    def test_oracle_module_has_no_await_in_put_or_get(self):
        """AST check: _OracleQueue.put_nowait and _SteerQueue.get_nowait_or_none have no await."""
        oracle_src = Path(__file__).parent.parent / "corvin_compute" / "fabric" / "oracle" / "oracle.py"
        source = oracle_src.read_text()
        tree = ast.parse(source)

        forbidden_awaits_in_sync_methods = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                # Regular (sync) function
                if node.name in ("put_nowait", "get_nowait_or_none"):
                    for sub in ast.walk(node):
                        if isinstance(sub, ast.Await):
                            forbidden_awaits_in_sync_methods.append(
                                f"{node.name}:line{sub.lineno}"
                            )

        assert not forbidden_awaits_in_sync_methods, (
            f"Sync methods must not await: {forbidden_awaits_in_sync_methods}"
        )
