#!/usr/bin/env python3
"""Tests for completion_notify.py — the durable background-completion backbone.

Proves the whole contract in isolation (no live engine):
  1. register → mark_done → deliver_ready writes a correctly-routed envelope
     into the shared outbox, with the ORIGINATING channel + chat_id preserved.
  2. Acknowledgement / exactly-once: a second deliver_ready is a no-op, and the
     record is marked delivered.
  3. mark_done on an unknown id returns False (no envelope).
  4. Nothing is delivered until mark_done is called (a pending record is inert).
  5. whatsapp routes on `to`.
  6. purge_user (GDPR Art. 17) drops a user's records.
  7. delivered records prune after TTL; abandoned pending prune after MAX_AGE.

Run: python3 operator/bridges/shared/test_completion_notify.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def _fresh(home: Path):
    os.environ["CORVIN_HOME"] = str(home)
    for m in list(sys.modules):
        if m == "completion_notify":
            del sys.modules[m]
    import completion_notify  # type: ignore

    return completion_notify


def test_register_done_deliver_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as td:
        home, outbox = Path(td) / "home", Path(td) / "outbox"
        cn = _fresh(home)
        tid = cn.register(channel="discord", chat_id="987654321",
                          sender="u1", tenant_id="acme", label="backtest run")
        # 4. inert until done
        assert cn.deliver_ready(outbox) == 0
        assert not list(outbox.glob("*.json")) if outbox.exists() else True

        assert cn.mark_done(tid, text="Sharpe 1.8, 3 trades", ok=True) is True
        n = cn.deliver_ready(outbox)
        assert n == 1, f"expected 1 delivered, got {n}"
        files = list(outbox.glob("cn_*.json"))
        assert len(files) == 1
        env = json.loads(files[0].read_text())
        assert env["channel"] == "discord"
        assert env["chat_id"] == "987654321"  # STRING — never int-coerced
        assert env["tenant_id"] == "acme"
        assert "backtest run finished" in env["text"]
        assert "Sharpe 1.8" in env["text"]
        assert env["text"].startswith("✅")
        # 2. exactly-once: second deliver is a no-op
        assert cn.deliver_ready(outbox) == 0
        assert len(list(outbox.glob("cn_*.json"))) == 1
        print("PASS: register→done→deliver routes to origin channel, exactly once")


def test_mark_done_unknown_id() -> None:
    with tempfile.TemporaryDirectory() as td:
        cn = _fresh(Path(td) / "home")
        assert cn.mark_done("does-not-exist", text="x") is False
        print("PASS: mark_done on unknown id → False")


def test_failure_status_marker() -> None:
    with tempfile.TemporaryDirectory() as td:
        home, outbox = Path(td) / "home", Path(td) / "outbox"
        cn = _fresh(home)
        tid = cn.register(channel="telegram", chat_id="42", label="import job")
        cn.mark_done(tid, text="disk full", ok=False)
        cn.deliver_ready(outbox)
        env = json.loads(next(outbox.glob("cn_*.json")).read_text())
        assert env["text"].startswith("⚠️"), env["text"]
        assert env["chat_id"] == "42"
        print("PASS: failure completion carries ⚠️ marker")


def test_discord_snowflake_id_survives_as_string() -> None:
    """Regression for the JS float64 precision bug: a 19-digit Discord channel
    snowflake (> 2^53) MUST be delivered as a string, byte-for-byte, and MUST
    survive a JSON file round-trip (what the daemon's JSON.parse does)."""
    with tempfile.TemporaryDirectory() as td:
        home, outbox = Path(td) / "home", Path(td) / "outbox"
        cn = _fresh(home)
        snowflake = "1501540900529246251"  # real 19-digit channel id
        tid = cn.register(channel="discord", chat_id=snowflake, label="job")
        cn.mark_done(tid, text="ok")
        cn.deliver_ready(outbox)
        raw = next(outbox.glob("cn_*.json")).read_text()
        env = json.loads(raw)
        assert env["chat_id"] == snowflake, f"snowflake corrupted: {env['chat_id']}"
        assert isinstance(env["chat_id"], str), "chat_id must be a JSON string"
        assert f'"{snowflake}"' in raw, "snowflake must be quoted (string) in JSON"
        print("PASS: 19-digit Discord snowflake preserved exactly as a string")


def test_whatsapp_routes_on_to() -> None:
    with tempfile.TemporaryDirectory() as td:
        home, outbox = Path(td) / "home", Path(td) / "outbox"
        cn = _fresh(home)
        tid = cn.register(channel="whatsapp", to="49123@s.whatsapp.net",
                          label="report")
        cn.mark_done(tid, text="done")
        cn.deliver_ready(outbox)
        env = json.loads(next(outbox.glob("cn_*.json")).read_text())
        assert env["channel"] == "whatsapp"
        assert env["to"] == "49123@s.whatsapp.net"
        print("PASS: whatsapp routes on `to` (JID)")


def test_purge_user() -> None:
    with tempfile.TemporaryDirectory() as td:
        cn = _fresh(Path(td) / "home")
        cn.register(channel="discord", chat_id="1", sender="victim")
        cn.register(channel="discord", chat_id="2", sender="other")
        removed = cn.purge_user("victim")
        assert removed == 1, removed
        print("PASS: purge_user removes only the target user's records")


def test_prune_delivered_and_abandoned() -> None:
    with tempfile.TemporaryDirectory() as td:
        home, outbox = Path(td) / "home", Path(td) / "outbox"
        cn = _fresh(home)
        # abandoned pending → pruned after MAX_AGE
        tid = cn.register(channel="discord", chat_id="9", label="ghost")
        import time
        future = time.time() + cn.CN_PENDING_MAX_AGE + 10
        assert cn.deliver_ready(outbox, now=future) == 0
        assert not list((home / "pending_notifications").glob(f"*{tid[-6:]}*")) \
            or cn._read(cn._record_path(tid)) is None
        print("PASS: abandoned pending record pruned after MAX_AGE")


def test_task_engine_producer_shape() -> None:
    """Mirror the task_worker_pool wiring: a messenger-origin task registers at
    creation, the worker calls mark_done at completion (task_id only, no PII),
    and the completion reaches the origin messenger."""
    with tempfile.TemporaryDirectory() as td:
        home, outbox = Path(td) / "home", Path(td) / "outbox"
        cn = _fresh(home)
        # At task creation (messenger context available) the producer registers:
        cn.register("task-abc123", channel="discord", chat_id="700",
                    sender="u9", tenant_id="_default", label="task task-abc123")
        # The worker's _notify_task_done(...) does exactly this — task_id only:
        assert cn.mark_done("task-abc123",
                            text="completed in 4200 ms (12 events).", ok=True)
        assert cn.deliver_ready(outbox) == 1
        env = json.loads(next(outbox.glob("cn_*.json")).read_text())
        assert env["channel"] == "discord" and env["chat_id"] == "700"
        assert "completed in 4200 ms" in env["text"]
        # A console task nobody registered → mark_done is a harmless no-op.
        assert cn.mark_done("console-task-none", text="done") is False
        print("PASS: task-engine producer shape → messenger, console task no-op")


def _age_record(cn, tid: str, seconds: float) -> None:
    p = cn._record_path(tid)
    r = cn._read(p)
    r["created_at"] = r.get("created_at", 0) - seconds
    cn._atomic_write(p, r)


def test_unclaimed_long_job_not_reaped_result_delivers() -> None:
    """H3-reaper regression: an UNCLAIMED record (e.g. a compute worker that
    never calls claim()) running past CN_PENDING_REAP must NOT be reaped — else
    a legitimately long (>30min) L24/L25 job got a false 'worker stopped' AND
    its real result was dropped when it finally finished."""
    with tempfile.TemporaryDirectory() as td:
        home, outbox = Path(td) / "home", Path(td) / "outbox"
        cn = _fresh(home)
        cn.CN_PENDING_REAP = 0.0  # any age qualifies for a reap check
        tid = cn.register(channel="discord", chat_id="1", sender="u1",
                          tenant_id="acme", label="compute")
        _age_record(cn, tid, 100)
        cn.deliver_ready(outbox)
        assert cn._read(cn._record_path(tid))["state"] == "pending", \
            "unclaimed long job was wrongly reaped"
        # the real result must still deliver
        assert cn.mark_done(tid, text="REAL RESULT", ok=True) is True
        assert cn.deliver_ready(outbox) == 1
        print("PASS: unclaimed >reap-age job not reaped, real result delivered")


def test_claimed_dead_producer_reaped() -> None:
    """A CLAIMED record whose producer pid is dead must be reaped to failed."""
    with tempfile.TemporaryDirectory() as td:
        home, outbox = Path(td) / "home", Path(td) / "outbox"
        cn = _fresh(home)
        cn.CN_PENDING_REAP = 0.0
        tid = cn.register(channel="discord", chat_id="2", sender="u2",
                          tenant_id="acme", label="task")
        p = cn._record_path(tid)
        r = cn._read(p)
        r["producer_pid"] = 999999  # not alive
        r["producer_boot"] = cn._host_boot_id()
        cn._atomic_write(p, r)
        _age_record(cn, tid, 100)
        cn.deliver_ready(outbox)
        rec = cn._read(p)
        assert rec["state"] == "ready" and rec.get("ok") is False, \
            "claimed dead-producer record was not reaped"
        print("PASS: claimed dead-producer record reaped to failed")


def test_claimed_live_producer_not_reaped() -> None:
    """A CLAIMED record whose producer is THIS (live) process must NOT be
    reaped. Also asserts _pid_alive is a non-destructive probe."""
    with tempfile.TemporaryDirectory() as td:
        home, outbox = Path(td) / "home", Path(td) / "outbox"
        cn = _fresh(home)
        cn.CN_PENDING_REAP = 0.0
        assert cn._pid_alive(os.getpid()) is True
        assert cn._pid_alive(999999) is False
        tid = cn.register(channel="discord", chat_id="3", sender="u3",
                          tenant_id="acme", label="task")
        p = cn._record_path(tid)
        r = cn._read(p)
        r["producer_pid"] = os.getpid()
        r["producer_boot"] = cn._host_boot_id()
        cn._atomic_write(p, r)
        _age_record(cn, tid, 100)
        cn.deliver_ready(outbox)
        assert cn._read(p)["state"] == "pending", \
            "claimed live-producer record was wrongly reaped"
        print("PASS: claimed live-producer record not reaped")


def main() -> int:
    tests = [
        test_register_done_deliver_roundtrip,
        test_discord_snowflake_id_survives_as_string,
        test_task_engine_producer_shape,
        test_mark_done_unknown_id,
        test_failure_status_marker,
        test_whatsapp_routes_on_to,
        test_purge_user,
        test_prune_delivered_and_abandoned,
        test_unclaimed_long_job_not_reaped_result_delivers,
        test_claimed_dead_producer_reaped,
        test_claimed_live_producer_not_reaped,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL {t.__name__}: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
    print()
    print(f"{'ALL PASSED' if not failed else str(failed) + ' FAILED'} "
          f"({len(tests) - failed}/{len(tests)})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
