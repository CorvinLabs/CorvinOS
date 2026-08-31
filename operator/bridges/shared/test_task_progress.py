"""E2E tests for task_progress — durable intermediate notifications.

The transport boundary these tests drive is the REAL one: a file in the shared
outbox directory that the messenger daemons poll. Nothing here calls a
daemon-internal helper or asserts on an in-memory object; the assertions are on
the envelope that a daemon would actually pick up, and the entry point driven
is `bg_monitor.run_once()` — the systemd timer's own entry point — not the
module under test.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """A sandboxed CORVIN_HOME + outbox, with the modules reloaded against it."""
    home = tmp_path / "corvin"
    outbox = tmp_path / "outbox"
    home.mkdir()
    outbox.mkdir()
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("ADAPTER_OUTBOX", str(outbox))
    monkeypatch.setenv("TP_MIN_INTERVAL", "120")
    monkeypatch.setenv("TP_MAX_UPDATES", "40")
    import completion_notify as cn
    import task_progress as tp
    import task_supervisor as sup
    import bg_monitor as bgm
    for m in (cn, tp, sup, bgm):
        importlib.reload(m)
    return {"home": home, "outbox": outbox, "cn": cn, "tp": tp, "sup": sup,
            "bgm": bgm}


def _register(cn, task_id="bgt_abc", chat_id="123456789012345678"):
    cn.register(task_id, channel="discord", chat_id=chat_id, sender="uid42",
                tenant_id="_default", label="do the long thing")
    return task_id


def _outbox_envelopes(outbox: Path) -> list[dict]:
    """Envelopes in DELIVERY order.

    Sorted by the envelope's own `ts`, never by filename: the file name carries
    a random token, so alphabetical order is not chronological order.
    """
    envs = [json.loads(p.read_text()) for p in outbox.glob("*.json")]
    return sorted(envs, key=lambda e: e.get("ts", 0))


# ── the core promise: an update reaches the outbox the daemon polls ────────


def test_update_reaches_the_outbox_the_daemon_polls(env):
    tid = _register(env["cn"])
    assert env["tp"].emit(tid, "phase 1 of 4 done") is not None

    n = env["tp"].deliver_progress(env["outbox"])

    assert n == 1
    envs = _outbox_envelopes(env["outbox"])
    assert len(envs) == 1
    e = envs[0]
    assert e["channel"] == "discord"
    # Routing is inherited from the completion record — one routing store.
    assert e["chat_id"] == "123456789012345678"
    assert "phase 1 of 4 done" in e["text"]
    assert e["_task_progress"] is True
    # NOT the daemon's sticky progress: a sticky message is deleted by the next
    # real reply, which is exactly wrong for an out-of-band run.
    assert "_progress" not in e
    assert "_heartbeat" not in e
    # An intermediate update is not the final answer.
    assert e.get("_final") is not True


def test_chat_id_stays_a_string(env):
    """A Discord snowflake is 19 digits (> 2^53) and loses precision as a JSON
    number when the daemon re-parses it with float64 JSON.parse."""
    tid = _register(env["cn"], chat_id="987654321098765432")
    env["tp"].emit(tid, "working")
    env["tp"].deliver_progress(env["outbox"])
    raw = next(iter(env["outbox"].glob("*.json"))).read_text()
    assert '"chat_id": "987654321098765432"' in raw


def test_bg_monitor_run_once_delivers_progress(env):
    """The systemd timer's own entry point must carry progress, not just
    completions — otherwise an idle adapter means a silent run."""
    tid = _register(env["cn"])
    env["tp"].emit(tid, "still working")

    env["bgm"].run_once()

    envs = _outbox_envelopes(env["outbox"])
    assert any("still working" in e.get("text", "") for e in envs)


# ── rate limiting: the reason this is safe to switch on ───────────────────


def test_emits_inside_the_window_coalesce_into_one_delivery(env):
    tid = _register(env["cn"])
    for i in range(25):
        env["tp"].emit(tid, f"iteration {i}")

    n = env["tp"].deliver_progress(env["outbox"])

    assert n == 1, "25 emits inside the window must not become 25 messages"
    envs = _outbox_envelopes(env["outbox"])
    assert len(envs) == 1
    # The LATEST state wins — a backlog of stale lines helps nobody.
    assert "iteration 24" in envs[0]["text"]


def test_second_delivery_is_blocked_until_the_window_passes(env):
    tid = _register(env["cn"])
    env["tp"].emit(tid, "first")
    assert env["tp"].deliver_progress(env["outbox"]) == 1

    env["tp"].emit(tid, "second")
    assert env["tp"].deliver_progress(env["outbox"]) == 0, "inside the window"

    later = time.time() + 121
    env["tp"].emit(tid, "third", now=later)
    assert env["tp"].deliver_progress(env["outbox"], now=later) == 1
    assert "third" in _outbox_envelopes(env["outbox"])[-1]["text"]


def test_forced_update_bypasses_the_window(env):
    """A state change (a heal/resume) must not be swallowed by a routine
    progress line that happened to be emitted a second earlier."""
    tid = _register(env["cn"])
    env["tp"].emit(tid, "working")
    env["tp"].deliver_progress(env["outbox"])

    env["tp"].emit(tid, "resuming it", kind="resume", force=True)
    assert env["tp"].deliver_progress(env["outbox"]) == 1
    assert "resuming it" in _outbox_envelopes(env["outbox"])[-1]["text"]


def test_force_survives_coalescing(env):
    """Regression: a forced update folded into an already-queued routine
    progress record lost its force flag, so the resume notice it carried was
    then throttled by the delivery interval like ordinary progress."""
    tid = _register(env["cn"])
    env["tp"].emit(tid, "working")
    env["tp"].deliver_progress(env["outbox"])          # arms the interval

    env["tp"].emit(tid, "still working")               # queues a routine record
    env["tp"].emit(tid, "resuming it", kind="resume", force=True)  # coalesces in

    assert env["tp"].deliver_progress(env["outbox"]) == 1
    assert any("resuming it" in e["text"]
               for e in _outbox_envelopes(env["outbox"]))


def test_orphaned_counters_are_pruned(env):
    """Nothing calls finish() for a task whose records are gone, so without a
    prune the per-task counters accumulate forever."""
    tid = _register(env["cn"])
    env["tp"].emit(tid, "one")
    env["tp"].deliver_progress(env["outbox"])
    ctr = env["home"] / "task_progress" / f"_ctr_{tid}.json"
    assert ctr.exists()

    env["tp"].deliver_progress(env["outbox"], now=time.time() + 25 * 3600)
    assert not ctr.exists()


def test_hard_ceiling_stops_a_wedged_producer(env, monkeypatch):
    monkeypatch.setenv("TP_MAX_UPDATES", "3")
    monkeypatch.setenv("TP_MIN_INTERVAL", "0")
    tp = importlib.reload(env["tp"])
    tid = _register(env["cn"])

    for i in range(20):
        tp.emit(tid, f"spam {i}")
        tp.deliver_progress(env["outbox"])

    assert len(_outbox_envelopes(env["outbox"])) == 3


# ── failure modes ─────────────────────────────────────────────────────────


def test_unknown_task_emits_nothing(env):
    """A run with no messenger origin (console/CLI) has nowhere to send."""
    assert env["tp"].emit("no_such_task", "hello") is None
    assert env["tp"].deliver_progress(env["outbox"]) == 0


def test_unroutable_record_is_dropped_not_retried_forever(env):
    env["cn"].register("bgt_x", channel="discord", chat_id=None, sender="u")
    env["tp"].emit("bgt_x", "orphan")
    assert env["tp"].deliver_progress(env["outbox"]) == 0
    # …and it is gone, not re-scanned on every future poll.
    assert env["tp"].deliver_progress(env["outbox"]) == 0
    assert env["tp"].pending_count("bgt_x") == 0


def test_delivery_is_exactly_once_across_two_pollers(env):
    tid = _register(env["cn"])
    env["tp"].emit(tid, "once please")

    a = env["tp"].deliver_progress(env["outbox"])
    b = env["tp"].deliver_progress(env["outbox"])

    assert (a, b) == (1, 0)
    assert len(_outbox_envelopes(env["outbox"])) == 1


def test_stale_update_is_dropped_rather_than_replayed(env):
    tid = _register(env["cn"])
    env["tp"].emit(tid, "old news")
    much_later = time.time() + 3600
    assert env["tp"].deliver_progress(env["outbox"], now=much_later) == 0
    assert _outbox_envelopes(env["outbox"]) == []


def test_emit_never_raises(env, monkeypatch):
    """A status line must never be able to kill the work it reports on."""
    tid = _register(env["cn"])
    monkeypatch.setattr(env["tp"], "_atomic_write",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    assert env["tp"].emit(tid, "boom") is None


# ── GDPR Art. 17 ──────────────────────────────────────────────────────────


def test_purge_user_erases_updates_and_counters(env):
    tid = _register(env["cn"])
    env["tp"].emit(tid, "carries a chat id and a uid")
    assert env["tp"].pending_count(tid) == 1

    removed = env["tp"].purge_user("uid42")

    assert removed == 1
    assert env["tp"].pending_count(tid) == 0
    leftovers = list((env["home"] / "task_progress").glob("*.json"))
    assert leftovers == [], f"PII left on disk: {leftovers}"
