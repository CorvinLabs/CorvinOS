"""E2E test for task_supervisor failure classification (ADR-2107).

Drives classify_failure() + attempt_finished() and verifies:
1. Three failure classes (TRANSIENT, PERMANENT, UNKNOWN) work correctly
2. Streak cap (max 2 consecutive TRANSIENT) escalates to UNKNOWN
3. Backoff timing adjusts per failure class
"""

import os
import sys
import tempfile
import time
from pathlib import Path

# Setup imports
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


def test_classify_transient_process_death():
    """TRANSIENT: worker pid is None (process died without reporting)."""
    import task_supervisor as sup

    failure_class, reason_code = sup.classify_failure(
        task_id="test_transient_1",
        worker_pid=None,
        exit_error=None,
        heartbeat_age_s=100.0,
        engine_response=None,
        attempt_log=[],
    )

    assert failure_class == sup.FailureClass.TRANSIENT
    assert reason_code == "process_dead_or_wedged"


def test_classify_transient_heartbeat_stale():
    """TRANSIENT: heartbeat is stale (wedged)."""
    import task_supervisor as sup

    failure_class, reason_code = sup.classify_failure(
        task_id="test_transient_2",
        worker_pid=999,
        exit_error=None,
        heartbeat_age_s=700.0,
        engine_response=None,
        attempt_log=[],
    )

    assert failure_class == sup.FailureClass.TRANSIENT
    assert reason_code == "process_dead_or_wedged"


def test_classify_transient_throttle():
    """TRANSIENT: HTTP 429 (throttle)."""
    import task_supervisor as sup

    failure_class, reason_code = sup.classify_failure(
        task_id="test_transient_3",
        worker_pid=999,
        exit_error=None,
        heartbeat_age_s=10.0,
        engine_response={"http_status": 429},
        attempt_log=[],
    )

    assert failure_class == sup.FailureClass.TRANSIENT
    assert reason_code == "http_throttled"


def test_classify_permanent_model_not_found():
    """PERMANENT: engine returns model_not_found."""
    import task_supervisor as sup

    failure_class, reason_code = sup.classify_failure(
        task_id="test_permanent_1",
        worker_pid=999,
        exit_error=None,
        heartbeat_age_s=10.0,
        engine_response={"terminal_reason": "model_not_found"},
        attempt_log=[],
    )

    assert failure_class == sup.FailureClass.PERMANENT
    assert "engine_model_not_found" in reason_code


def test_classify_permanent_auth_error():
    """PERMANENT: engine returns auth_error."""
    import task_supervisor as sup

    failure_class, reason_code = sup.classify_failure(
        task_id="test_permanent_2",
        worker_pid=999,
        exit_error=None,
        heartbeat_age_s=10.0,
        engine_response={"terminal_reason": "auth_error"},
        attempt_log=[],
    )

    assert failure_class == sup.FailureClass.PERMANENT
    assert "engine_auth_error" in reason_code


def test_classify_permanent_gate_refusal():
    """PERMANENT: gate refusal (L44)."""
    import task_supervisor as sup

    failure_class, reason_code = sup.classify_failure(
        task_id="test_permanent_3",
        worker_pid=999,
        exit_error="L44GateRefusal",
        heartbeat_age_s=10.0,
        engine_response=None,
        attempt_log=[],
    )

    assert failure_class == sup.FailureClass.PERMANENT
    assert reason_code == "gate_refusal"


def test_classify_unknown_default():
    """UNKNOWN: unrecognized failure (default)."""
    import task_supervisor as sup

    failure_class, reason_code = sup.classify_failure(
        task_id="test_unknown_1",
        worker_pid=999,
        exit_error="SomeUnknownException",
        heartbeat_age_s=10.0,
        engine_response=None,
        attempt_log=[],
    )

    assert failure_class == sup.FailureClass.UNKNOWN
    assert reason_code == "unrecognized"


def test_classify_streak_cap():
    """Streak cap: 2 consecutive TRANSIENT → escalate to UNKNOWN."""
    import task_supervisor as sup

    attempt_log = [
        {"failure_class": "transient", "reason_code": "process_dead_or_wedged"},
        {"failure_class": "transient", "reason_code": "process_dead_or_wedged"},
    ]

    failure_class, reason_code = sup.classify_failure(
        task_id="test_streak_1",
        worker_pid=None,
        exit_error=None,
        heartbeat_age_s=100.0,
        engine_response=None,
        attempt_log=attempt_log,
    )

    assert failure_class == sup.FailureClass.UNKNOWN
    assert reason_code == "transient_streak_capped"


def test_attempt_finished_transient():
    """attempt_finished() with TRANSIENT failure → no backoff."""
    import task_supervisor as sup

    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["CORVIN_HOME"] = tmpdir

        task_id = "test_transient_finish"
        now = time.time()

        sup.register_run(
            task_id=task_id,
            instruction="test",
            channel="test",
            chat_key="test:test",
            sender="test",
        )

        sup.attempt_finished(
            task_id=task_id,
            ok=False,
            summary="crashed",
            worker_pid=None,
            exit_error=None,
            engine_response=None,
            now=now + 10,
        )

        rec = sup._read(sup._run_path(task_id))
        assert rec is not None
        assert rec["attempt_log"][-1].get("failure_class") == "transient"
        assert rec.get("next_attempt_at") == now + 10


def test_attempt_finished_permanent():
    """attempt_finished() with PERMANENT failure → no retry."""
    import task_supervisor as sup

    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["CORVIN_HOME"] = tmpdir

        task_id = "test_permanent_finish"
        now = time.time()

        sup.register_run(
            task_id=task_id,
            instruction="test",
            channel="test",
            chat_key="test:test",
            sender="test",
        )

        # Set fresh heartbeat so classifier doesn't think it's wedged
        sup.touch_heartbeat(task_id, now=now)

        sup.attempt_finished(
            task_id=task_id,
            ok=False,
            summary="auth failed",
            worker_pid=999,
            exit_error=None,
            engine_response={"terminal_reason": "auth_error"},
            now=now + 10,
        )

        rec = sup._read(sup._run_path(task_id))
        assert rec is not None
        assert rec["attempt_log"][-1].get("failure_class") == "permanent"


def test_attempt_finished_unknown():
    """attempt_finished() with UNKNOWN failure → normal backoff."""
    import task_supervisor as sup

    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["CORVIN_HOME"] = tmpdir

        task_id = "test_unknown_finish"
        now = time.time()

        sup.register_run(
            task_id=task_id,
            instruction="test",
            channel="test",
            chat_key="test:test",
            sender="test",
        )

        # Set fresh heartbeat
        sup.touch_heartbeat(task_id, now=now)

        sup.attempt_finished(
            task_id=task_id,
            ok=False,
            summary="unknown error",
            worker_pid=999,
            exit_error="UnknownError",
            engine_response=None,
            now=now + 10,
        )

        rec = sup._read(sup._run_path(task_id))
        assert rec is not None
        assert rec["attempt_log"][-1].get("failure_class") == "unknown"
        expected = now + 10 + sup.SUP_BACKOFF_BASE
        assert rec.get("next_attempt_at") == expected


if __name__ == "__main__":
    test_classify_transient_process_death()
    test_classify_transient_heartbeat_stale()
    test_classify_transient_throttle()
    test_classify_permanent_model_not_found()
    test_classify_permanent_auth_error()
    test_classify_permanent_gate_refusal()
    test_classify_unknown_default()
    test_classify_streak_cap()
    test_attempt_finished_transient()
    test_attempt_finished_permanent()
    test_attempt_finished_unknown()
    print("✅ All 11 tests passed")
