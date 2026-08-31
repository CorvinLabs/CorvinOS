"""E2E tests for task_supervisor — healing a stopped long-running task.

The boundary driven here is the real one on both sides:

* the entry point is `bg_monitor.run_once()` — the systemd timer's own entry
  point — not `supervise()` called directly, wherever that is what matters;
* a resume really SPAWNS A PROCESS. `test_resume_spawns_a_real_process` runs
  the actual `_spawn_worker` against a stub worker and waits for that process
  to write a file, so "it resumed" means an OS process ran, not that a mock was
  called.

The one thing deliberately NOT driven end-to-end is the real
`bg_task_worker` → `adapter.call_claude_streaming` path: that needs a live
engine and an API budget. Its own contract is covered by
`test_bg_task_worker_supervised.py`, which drives the real worker script as a
real subprocess against a stub adapter.
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    home = tmp_path / "corvin"
    outbox = tmp_path / "outbox"
    home.mkdir()
    outbox.mkdir()
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("ADAPTER_OUTBOX", str(outbox))
    monkeypatch.setenv("SUP_LAUNCH_GRACE", "0")
    monkeypatch.setenv("SUP_BACKOFF_BASE", "0")
    monkeypatch.setenv("TP_MIN_INTERVAL", "0")
    import completion_notify as cn
    import task_progress as tp
    import task_supervisor as sup
    import bg_monitor as bgm
    for m in (cn, tp, sup, bgm):
        importlib.reload(m)
    return {"home": home, "outbox": outbox, "cn": cn, "tp": tp, "sup": sup,
            "bgm": bgm, "tmp": tmp_path}


def _make_run(env, task_id="bgt_1", *, attempts=0, max_attempts=5,
              supervise=True, budget=3600.0):
    env["cn"].register(task_id, channel="discord",
                       chat_id="123456789012345678", sender="uid42",
                       label="long job")
    rec = env["sup"].register_run(
        task_id, instruction="do the long thing", channel="discord",
        chat_key="123456789012345678", sender="uid42",
        max_attempts=max_attempts, total_budget_s=budget,
        supervise_enabled=supervise, progress_enabled=True,
    )
    if attempts:
        rec["attempts"] = attempts
        env["sup"]._atomic_write(env["sup"]._run_path(task_id), rec)
    return task_id


class _Spawns:
    """Records spawn calls in place of launching a real worker."""

    def __init__(self, pid=424242):
        self.calls: list[tuple[dict, str]] = []
        self.pid = pid

    def __call__(self, rec, prompt):
        self.calls.append((rec, prompt))
        return self.pid


# ── the core promise: a dead worker is resumed, not buried ────────────────


def test_dead_worker_is_resumed(env):
    tid = _make_run(env)
    # An attempt ran and its process is gone (pid 1 is init; use a pid that
    # provably does not exist by asking the module itself).
    rec = env["sup"].get_run(tid)
    rec["worker_pid"] = 999_999_999
    rec["worker_boot"] = env["sup"]._host_boot_id()
    rec["attempts"] = 1
    rec["last_attempt_at"] = time.time() - 3600
    env["sup"]._atomic_write(env["sup"]._run_path(tid), rec)

    spawns = _Spawns()
    assert env["sup"].supervise(spawn=spawns) == 1

    assert len(spawns.calls) == 1
    prompt = spawns.calls[0][1]
    assert "RESUMING" in prompt
    assert "do the long thing" in prompt
    assert "Do NOT start over" in prompt


def test_resume_tells_the_user_it_is_healing(env):
    tid = _make_run(env)
    rec = env["sup"].get_run(tid)
    rec.update(worker_pid=999_999_999, worker_boot=env["sup"]._host_boot_id(),
               attempts=1, last_attempt_at=time.time() - 3600)
    env["sup"]._atomic_write(env["sup"]._run_path(tid), rec)

    env["sup"].supervise(spawn=_Spawns())
    env["tp"].deliver_progress(env["outbox"])

    texts = [json.loads(p.read_text()).get("text", "")
             for p in env["outbox"].glob("*.json")]
    assert any("resuming it" in t for t in texts), texts


def test_wedged_worker_with_stale_heartbeat_is_restarted(env, monkeypatch):
    """The failure nothing detected before: pid alive, engine hung forever."""
    monkeypatch.setenv("SUP_HEARTBEAT_STALE", "60")
    sup = importlib.reload(env["sup"])
    env["sup"] = sup
    tid = _make_run(env)
    rec = sup.get_run(tid)
    rec.update(worker_pid=os.getpid(),  # provably ALIVE
               worker_boot=sup._host_boot_id(), attempts=1,
               last_attempt_at=time.time() - 3600)
    sup._atomic_write(sup._run_path(tid), rec)
    # …but its heartbeat stopped an hour ago.
    sup.touch_heartbeat(tid, now=time.time() - 3600)

    terminated = []
    monkeypatch.setattr(sup, "_terminate", terminated.append)
    spawns = _Spawns()
    assert sup.supervise(spawn=spawns) == 1
    assert terminated == [os.getpid()]


def test_a_wedged_worker_is_only_announced_once(env, monkeypatch):
    """Regression: the pid was cleared in memory but not persisted, so a tick
    that then hit the backoff discarded the change — and every following tick
    re-SIGTERMed the same stale pid and re-sent the "wedged" notice, forced
    past the progress rate limit."""
    monkeypatch.setenv("SUP_HEARTBEAT_STALE", "60")
    monkeypatch.setenv("SUP_BACKOFF_BASE", "3600")  # long backoff: no resume yet
    sup = importlib.reload(env["sup"])
    env["sup"] = sup
    tid = _make_run(env)
    rec = sup.get_run(tid)
    rec.update(worker_pid=os.getpid(), worker_boot=sup._host_boot_id(),
               attempts=2, last_attempt_at=time.time() - 3600,
               next_attempt_at=time.time() + 3600)
    sup._atomic_write(sup._run_path(tid), rec)
    sup.touch_heartbeat(tid, now=time.time() - 3600)

    terminated = []
    monkeypatch.setattr(sup, "_terminate", terminated.append)

    for _ in range(3):
        sup.supervise(spawn=_Spawns())

    assert terminated == [os.getpid()], f"terminated {len(terminated)}x"
    env["tp"].deliver_progress(env["outbox"])
    texts = [json.loads(p.read_text()).get("text", "")
             for p in env["outbox"].glob("*.json")]
    assert sum("wedged" in t for t in texts) == 1, texts


def test_budget_message_reads_naturally(env):
    import task_supervisor as sup
    assert sup._human_duration(6 * 3600) == "6-hour"
    assert sup._human_duration(90 * 60) == "90-minute"


def test_healthy_worker_is_left_alone(env):
    tid = _make_run(env)
    rec = env["sup"].get_run(tid)
    rec.update(worker_pid=os.getpid(), worker_boot=env["sup"]._host_boot_id(),
               attempts=1, last_attempt_at=time.time() - 3600)
    env["sup"]._atomic_write(env["sup"]._run_path(tid), rec)
    env["sup"].touch_heartbeat(tid)

    spawns = _Spawns()
    assert env["sup"].supervise(spawn=spawns) == 0
    assert spawns.calls == []


def test_completed_run_is_never_resurrected(env):
    tid = _make_run(env)
    rec = env["sup"].get_run(tid)
    rec.update(worker_pid=999_999_999, worker_boot=env["sup"]._host_boot_id(),
               attempts=1, last_attempt_at=time.time() - 3600)
    env["sup"]._atomic_write(env["sup"]._run_path(tid), rec)
    # The worker DID finish and marked its completion ready.
    env["cn"].mark_done(tid, text="all done", ok=True)

    spawns = _Spawns()
    assert env["sup"].supervise(spawn=spawns) == 0
    assert spawns.calls == []
    assert env["sup"].get_run(tid)["state"] == "done"


# ── the bounds: why "restart until done" is not a fork bomb ───────────────


def test_attempt_budget_ends_in_an_honest_failure(env):
    tid = _make_run(env, attempts=5, max_attempts=5)
    rec = env["sup"].get_run(tid)
    rec.update(worker_pid=999_999_999, worker_boot=env["sup"]._host_boot_id(),
               last_attempt_at=time.time() - 3600,
               attempt_log=[{"n": 5, "at": 0, "ok": False, "resumable": True,
                             "summary": "timed out after 1800s"}])
    env["sup"]._atomic_write(env["sup"]._run_path(tid), rec)

    spawns = _Spawns()
    assert env["sup"].supervise(spawn=spawns) == 0
    assert spawns.calls == []

    cn_rec = env["cn"]._read(env["cn"]._record_path(tid))
    assert cn_rec["state"] == "ready" and cn_rec["ok"] is False
    assert "retry budget" in cn_rec["text"]
    assert "timed out after 1800s" in cn_rec["text"], "must say what was tried"


def test_wall_clock_budget_ends_in_an_honest_failure(env):
    tid = _make_run(env, budget=60.0)
    rec = env["sup"].get_run(tid)
    rec.update(created_at=time.time() - 3600, worker_pid=999_999_999,
               worker_boot=env["sup"]._host_boot_id(), attempts=1,
               last_attempt_at=time.time() - 3600)
    env["sup"]._atomic_write(env["sup"]._run_path(tid), rec)

    assert env["sup"].supervise(spawn=_Spawns()) == 0
    cn_rec = env["cn"]._read(env["cn"]._record_path(tid))
    assert cn_rec["ok"] is False
    assert "time budget" in cn_rec["text"]


def test_backoff_prevents_a_crash_loop(env, monkeypatch):
    monkeypatch.setenv("SUP_BACKOFF_BASE", "300")
    sup = importlib.reload(env["sup"])
    env["sup"] = sup
    tid = _make_run(env)
    sup.attempt_started(tid, pid=999_999_999)
    sup.attempt_finished(tid, ok=False, summary="crashed instantly",
                         resumable=True)

    spawns = _Spawns()
    assert sup.supervise(spawn=spawns) == 0, "must be backing off"
    later = time.time() + 400
    assert sup.supervise(spawn=spawns, now=later) == 1


def _stage_dead_worker(env, tid, *, attempts=1):
    rec = env["sup"].get_run(tid)
    rec.update(worker_pid=999_999_999, worker_boot=env["sup"]._host_boot_id(),
               attempts=attempts, last_attempt_at=time.time() - 3600)
    env["sup"]._atomic_write(env["sup"]._run_path(tid), rec)


def test_a_poller_holding_the_spawn_lock_blocks_the_other(env):
    """The adapter loop and the bg_monitor timer both call supervise(); the
    per-run O_EXCL spawn lock is what makes that safe."""
    tid = _make_run(env)
    _stage_dead_worker(env, tid)
    # Stand in for the other poller being inside its critical section.
    lock = env["sup"]._run_path(tid).with_suffix(".json.spawnlock")
    lock.write_text("")

    spawns = _Spawns()
    assert env["sup"].supervise(spawn=spawns) == 0
    assert spawns.calls == []

    lock.unlink()
    assert env["sup"].supervise(spawn=spawns) == 1


def test_a_resume_is_not_immediately_repeated(env, monkeypatch):
    """A spawned worker that dies instantly must not burn the whole attempt
    budget in one grace period."""
    monkeypatch.setenv("SUP_BACKOFF_BASE", "300")
    sup = importlib.reload(env["sup"])
    env["sup"] = sup
    tid = _make_run(env)
    _stage_dead_worker(env, tid)

    spawns = _Spawns()  # returns a pid that is not alive
    assert sup.supervise(spawn=spawns) == 1
    # Second tick, same second: nothing.
    assert sup.supervise(spawn=spawns) == 0
    # …and still nothing a minute later, because the backoff is armed.
    assert sup.supervise(spawn=spawns, now=time.time() + 60) == 0
    # attempt 2's backoff is base * 2^(2-1) = 600 s.
    assert sup.supervise(spawn=spawns, now=time.time() + 700) == 1


def test_a_freshly_registered_run_is_not_double_spawned(env, monkeypatch):
    """The adapter calls register_run and THEN spawns. A tick landing in that
    window must not start a second worker for the same task."""
    monkeypatch.setenv("SUP_LAUNCH_GRACE", "180")
    sup = importlib.reload(env["sup"])
    env["sup"] = sup
    tid = _make_run(env)  # attempts=0, no worker stamped yet

    spawns = _Spawns()
    assert sup.supervise(spawn=spawns) == 0
    assert spawns.calls == []
    # …but if the adapter's spawn genuinely failed, the grace expires and the
    # supervisor starts the run rather than leaving it dead on arrival.
    assert sup.supervise(spawn=spawns, now=time.time() + 200) == 1


def test_a_zombie_worker_counts_as_dead(env, monkeypatch):
    """The adapter Popen()s workers and never waits, so to the ADAPTER a dead
    worker is a zombie — and a zombie answers os.kill(pid, 0) successfully."""
    sup = env["sup"]
    tid = _make_run(env)
    rec = sup.get_run(tid)
    rec.update(worker_pid=os.getpid(), worker_boot=sup._host_boot_id(),
               attempts=1, last_attempt_at=time.time() - 3600)
    sup._atomic_write(sup._run_path(tid), rec)
    sup.touch_heartbeat(tid)  # fresh heartbeat: only the zombie check can catch it

    assert sup.supervise(spawn=_Spawns()) == 0, "not a zombie → left alone"

    monkeypatch.setattr(sup, "_is_zombie", lambda pid: True)
    spawns = _Spawns()
    assert sup.supervise(spawn=spawns) == 1
    assert len(spawns.calls) == 1


def test_zombie_detection_is_correct_for_a_real_process():
    """_is_zombie must not mislabel a live process (which would restart a
    perfectly healthy worker under itself)."""
    import task_supervisor as sup
    assert sup._is_zombie(os.getpid()) is False
    assert sup._is_zombie(999_999_999) is False  # gone, not a zombie


def test_a_run_gets_exactly_its_configured_number_of_launches(env,
                                                               monkeypatch):
    """Regression: the launch count was incremented TWICE per resume — once by
    supervise() after the spawn and again by the worker's attempt_started() —
    so a run configured for 5 attempts got 3."""
    monkeypatch.setenv("SUP_BACKOFF_BASE", "0")
    monkeypatch.setenv("SUP_LAUNCH_GRACE", "0")
    sup = importlib.reload(env["sup"])
    env["sup"] = sup

    tid = _make_run(env, max_attempts=4)
    launches = 0
    now = time.time()

    def spawn(rec, prompt):
        nonlocal launches
        launches += 1
        # Model a worker that starts, stamps itself, and dies immediately.
        sup.attempt_started(tid, pid=999_999_999, now=now)
        return 999_999_999

    # The adapter's own launch is implicit in register_run (attempt 1).
    sup.attempt_started(tid, pid=999_999_999, now=now)

    for _ in range(10):
        now += 1
        sup.supervise(spawn=spawn, now=now)
        if sup.get_run(tid)["state"] == "done":
            break

    assert launches == 3, (
        f"1 adapter launch + 3 resumes = the 4 configured, got {1 + launches}")
    assert sup.get_run(tid)["state"] == "done"
    cn_rec = env["cn"]._read(env["cn"]._record_path(tid))
    assert "retry budget" in cn_rec["text"]


def test_an_unreadable_completion_store_changes_nothing(env, monkeypatch):
    """Regression: an ImportError reading the completion store was
    indistinguishable from "the record is gone", so ONE transient failure
    retired EVERY active run as orphaned — permanently ending every long task
    in flight."""
    sup = env["sup"]
    tid = _make_run(env)
    _stage_dead_worker(env, tid)

    monkeypatch.setattr(sup, "_completion_state", lambda t: sup._UNKNOWN)
    spawns = _Spawns()
    assert sup.supervise(spawn=spawns) == 0
    assert spawns.calls == []
    assert sup.get_run(tid)["state"] == "active", "the run must be untouched"


def test_a_genuinely_missing_completion_record_retires_the_run(env):
    """…while a record that is really gone (GDPR purge) still retires it:
    there is nowhere left to deliver a result."""
    sup = env["sup"]
    tid = _make_run(env)
    _stage_dead_worker(env, tid)
    env["cn"]._record_path(tid).unlink()

    assert sup.supervise(spawn=_Spawns()) == 0
    assert sup.get_run(tid)["state"] == "done"


def test_supervision_check_does_not_grow_sys_path(env):
    """Regression: completion_notify._supervised() inserted into sys.path on
    every call, and it runs once per pending record per poll tick — sys.path
    grew without bound in the long-running adapter process."""
    cn = env["cn"]
    _make_run(env)
    before = len(sys.path)
    for _ in range(50):
        cn._supervised("bgt_1")
    assert len(sys.path) == before


# ── flag-off must be byte-identical to the pre-feature path ──────────────


def test_supervision_off_never_resumes(env):
    tid = _make_run(env, supervise=False)
    rec = env["sup"].get_run(tid)
    rec.update(worker_pid=999_999_999, worker_boot=env["sup"]._host_boot_id(),
               attempts=1, last_attempt_at=time.time() - 3600)
    env["sup"]._atomic_write(env["sup"]._run_path(tid), rec)

    spawns = _Spawns()
    assert env["sup"].supervise(spawn=spawns) == 0
    assert spawns.calls == []


def test_completion_notify_still_reaps_an_unsupervised_dead_worker(env,
                                                                  monkeypatch):
    """Flag off, the old dead-producer reap must still fire — otherwise the
    /task slot wedges for 7 days, which is the bug it was written to fix."""
    monkeypatch.setenv("CN_PENDING_REAP", "0")
    cn = importlib.reload(env["cn"])
    env["cn"] = cn
    tid = _make_run(env, supervise=False)
    rec = cn._read(cn._record_path(tid))
    rec.update(producer_pid=999_999_999, producer_boot=cn._host_boot_id(),
               created_at=time.time() - 3600)
    cn._atomic_write(cn._record_path(tid), rec)

    cn.deliver_ready(env["outbox"])

    assert cn._read(cn._record_path(tid))["ok"] is False


def test_completion_notify_does_not_reap_a_supervised_run(env, monkeypatch):
    """…and with the flag ON it must NOT, or the user is told the task failed
    while the resume that fixes it is already in flight."""
    monkeypatch.setenv("CN_PENDING_REAP", "0")
    cn = importlib.reload(env["cn"])
    env["cn"] = cn
    tid = _make_run(env, supervise=True)
    rec = cn._read(cn._record_path(tid))
    rec.update(producer_pid=999_999_999, producer_boot=cn._host_boot_id(),
               created_at=time.time() - 3600)
    cn._atomic_write(cn._record_path(tid), rec)

    cn.deliver_ready(env["outbox"])

    assert cn._read(cn._record_path(tid))["state"] == "pending"


# ── the real spawn ────────────────────────────────────────────────────────


def test_resume_spawns_a_real_process(env, monkeypatch, tmp_path):
    """Proof that a resume launches an OS process, not a mock.

    Points `_spawn_worker` at a stub worker script (by replacing the module's
    HERE) and waits for that process to actually write a file.
    """
    sup = env["sup"]
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    marker = tmp_path / "worker_ran.txt"
    (stub_dir / "bg_task_worker.py").write_text(
        "import json, sys, pathlib\n"
        "spec = json.loads(pathlib.Path(sys.argv[1]).read_text())\n"
        f"pathlib.Path({str(marker)!r}).write_text(spec['instruction'])\n"
    )
    monkeypatch.setattr(sup, "HERE", stub_dir)

    tid = _make_run(env)
    rec = sup.get_run(tid)
    rec.update(worker_pid=999_999_999, worker_boot=sup._host_boot_id(),
               attempts=1, last_attempt_at=time.time() - 3600)
    sup._atomic_write(sup._run_path(tid), rec)

    assert sup.supervise() == 1

    for _ in range(100):
        if marker.exists():
            break
        time.sleep(0.05)
    assert marker.exists(), "the resumed worker process never ran"
    assert "RESUMING" in marker.read_text()


def test_bg_monitor_run_once_drives_supervision(env):
    """The systemd timer entry point must actually heal, not just deliver."""
    tid = _make_run(env)
    rec = env["sup"].get_run(tid)
    rec.update(worker_pid=999_999_999, worker_boot=env["sup"]._host_boot_id(),
               attempts=1, last_attempt_at=time.time() - 3600)
    env["sup"]._atomic_write(env["sup"]._run_path(tid), rec)

    calls = []
    real_spawn = env["sup"]._spawn_worker
    env["sup"]._spawn_worker = lambda r, p: (calls.append(p) or 4242)
    try:
        env["bgm"].run_once()
    finally:
        env["sup"]._spawn_worker = real_spawn

    assert len(calls) == 1, "bg_monitor.run_once did not supervise"


# ── robustness ────────────────────────────────────────────────────────────


def test_a_poisoned_record_does_not_starve_the_others(env):
    (env["home"] / "task_runs").mkdir(parents=True, exist_ok=True)
    (env["home"] / "task_runs" / "aaa_broken.json").write_text("{not json")
    tid = _make_run(env, task_id="zzz_good")
    rec = env["sup"].get_run(tid)
    rec.update(worker_pid=999_999_999, worker_boot=env["sup"]._host_boot_id(),
               attempts=1, last_attempt_at=time.time() - 3600)
    env["sup"]._atomic_write(env["sup"]._run_path(tid), rec)

    spawns = _Spawns()
    assert env["sup"].supervise(spawn=spawns) == 1


def test_supervise_never_raises(env, monkeypatch):
    _make_run(env)
    monkeypatch.setattr(env["sup"], "_completion_state",
                        lambda t: (_ for _ in ()).throw(RuntimeError("boom")))
    assert env["sup"].supervise() == 0  # logged and skipped, not raised


def test_purge_user_erases_the_instruction_and_routing(env):
    tid = _make_run(env)
    assert env["sup"].count_active("uid42") == 1

    assert env["sup"].purge_user("uid42") == 1

    assert env["sup"].get_run(tid) is None
    assert list((env["home"] / "task_runs").glob("*.json")) == []
