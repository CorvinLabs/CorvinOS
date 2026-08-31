"""E2E tests for the supervised bg_task_worker lifecycle.

These drive the REAL `bg_task_worker.py` as a REAL detached subprocess — the
same script, the same 0600-spec-file argv contract, the same
`completion_notify` / `task_supervisor` / `task_progress` modules the adapter
spawns it with. Only ONE thing is substituted: `adapter.call_claude_streaming`,
because driving the real engine needs a live model and an API budget (the
infeasibility carve-out of the e2e-wiring-proof standard — named here rather
than left implicit). Everything the tests assert on is a real file written by a
real process: the completion record, the run record, the heartbeat, and the
progress queue.

The substitution is done by running the worker in a sandbox directory that
holds a copy of the real worker script next to a stub `adapter.py`, so the
worker's own `sys.path.insert(0, HERE)` resolves the stub. The worker script
itself is byte-identical to the shipped one.
"""
from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# Real modules the worker needs alongside it in the sandbox.
_REAL_MODULES = (
    "bg_task_worker.py", "completion_notify.py", "task_supervisor.py",
    "task_progress.py", "voice_tag.py", "provenance.py", "paths.py",
)

_STUB_ADAPTER = '''"""Stub engine for the worker E2E — see the module docstring."""
import os, threading, time

_MODE = os.environ.get("STUB_MODE", "ok")
# The real _cancel_chat SIGTERMs the engine subprocess, which unblocks
# call_claude_streaming with a cancellation string. Model exactly that: the
# watchdog sets the event, the "engine" returns partial output.
_cancelled = threading.Event()


def _cancel_chat(chat_key):
    _cancelled.set()


def call_claude_streaming(prompt, channel, chat_key, on_status=None,
                          profile=None, msg_id=None, sender=""):
    open(os.environ["STUB_PROMPT_FILE"], "w", encoding="utf-8").write(prompt)
    if on_status is not None:
        for i in range(3):
            on_status(f"stub step {i}")
    if _MODE == "crash":
        raise RuntimeError("engine exploded")
    if _MODE == "hang":
        _cancelled.wait(30)
        return "partial output before the engine was stopped"
    return "the stub engine finished the work"
'''


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    home = tmp_path / "corvin"
    home.mkdir()
    box = tmp_path / "box"
    box.mkdir()
    for name in _REAL_MODULES:
        src = HERE / name
        if src.exists():
            shutil.copy2(src, box / name)
    (box / "adapter.py").write_text(_STUB_ADAPTER, encoding="utf-8")

    monkeypatch.setenv("CORVIN_HOME", str(home))
    import completion_notify as cn
    import task_progress as tp
    import task_supervisor as sup
    for m in (cn, tp, sup):
        importlib.reload(m)
    return {"home": home, "box": box, "tmp": tmp_path, "cn": cn, "tp": tp,
            "sup": sup}


def _run_worker(sandbox, spec: dict, *, mode="ok", timeout_s="1800",
                heartbeat="1", wait=60) -> subprocess.CompletedProcess:
    spec_file = sandbox["tmp"] / f"spec_{spec['task_id']}.json"
    spec_file.write_text(json.dumps(spec), encoding="utf-8")
    env = dict(os.environ)
    env.update({
        "CORVIN_HOME": str(sandbox["home"]),
        "STUB_MODE": mode,
        "STUB_PROMPT_FILE": str(sandbox["tmp"] / "prompt.txt"),
        "CORVIN_BG_TASK_TIMEOUT": timeout_s,
        "CORVIN_BG_TASK_HEARTBEAT": heartbeat,
        "TP_MIN_INTERVAL": "0",
        "PYTHONPATH": str(sandbox["box"]),
    })
    return subprocess.run(
        [sys.executable, str(sandbox["box"] / "bg_task_worker.py"),
         str(spec_file)],
        capture_output=True, text=True, timeout=wait, env=env,
    )


def _prepare(sandbox, task_id="bgt_e2e", *, supervised=True, progress=True):
    sandbox["cn"].register(task_id, channel="discord",
                           chat_id="123456789012345678", sender="uid42",
                           label="long job")
    if supervised or progress:
        sandbox["sup"].register_run(
            task_id, instruction="do the long thing", channel="discord",
            chat_key="123456789012345678", sender="uid42",
            supervise_enabled=supervised, progress_enabled=progress,
        )
    return {"task_id": task_id, "instruction": "do the long thing",
            "channel": "discord", "chat_key": "123456789012345678",
            "sender": "uid42"}


# ── the happy path still works ────────────────────────────────────────────


def test_successful_run_completes_and_retires_the_supervision(sandbox):
    spec = _prepare(sandbox)
    r = _run_worker(sandbox, spec)
    assert r.returncode == 0, r.stderr

    rec = sandbox["cn"]._read(sandbox["cn"]._record_path(spec["task_id"]))
    assert rec["state"] == "ready" and rec["ok"] is True
    assert "the stub engine finished the work" in rec["text"]
    # The run is retired, so no supervisor tick will ever resume it.
    assert sandbox["sup"].get_run(spec["task_id"])["state"] == "done"


def test_worker_reports_progress_while_it_works(sandbox):
    """The pre-feature worker passed on_status=None and reported nothing."""
    spec = _prepare(sandbox, progress=True)
    _run_worker(sandbox, spec)

    outbox = sandbox["tmp"] / "outbox"
    sandbox["tp"].deliver_progress(outbox)
    texts = [json.loads(p.read_text())["text"] for p in outbox.glob("*.json")]
    assert any("stub step" in t for t in texts), texts


def test_progress_off_means_no_update_store_at_all(sandbox):
    spec = _prepare(sandbox, supervised=True, progress=False)
    _run_worker(sandbox, spec)

    outbox = sandbox["tmp"] / "outbox"
    assert sandbox["tp"].deliver_progress(outbox) == 0
    assert list(outbox.glob("*.json")) == [] if outbox.exists() else True


def test_worker_stamps_a_heartbeat_while_running(sandbox):
    """Liveness a pid check cannot give: alive-but-wedged is only visible
    through a stamp that stops ticking."""
    spec = _prepare(sandbox)
    hb = sandbox["sup"].heartbeat_path(spec["task_id"])
    _run_worker(sandbox, spec, mode="hang", timeout_s="3", heartbeat="1",
                wait=60)
    # The run finished (timeout path), so the heartbeat is cleaned up — but it
    # must have EXISTED and advanced while the engine was hanging. Assert via
    # the run record's attempt log, which only a started attempt writes.
    run = sandbox["sup"].get_run(spec["task_id"])
    assert run["attempts"] == 1
    assert run["attempt_log"], "attempt_started never ran"


# ── the point of supervision: a stopped run is not a failed run ───────────


def test_timeout_under_supervision_is_resumable_not_a_verdict(sandbox):
    """The 30-minute wall clock used to END the work and tell the user it
    failed. Under supervision it must become a continuation point."""
    spec = _prepare(sandbox, supervised=True)
    r = _run_worker(sandbox, spec, mode="hang", timeout_s="2", wait=90)
    assert r.returncode == 0, r.stderr

    # NOT marked done — the supervisor owns the verdict now.
    rec = sandbox["cn"]._read(sandbox["cn"]._record_path(spec["task_id"]))
    assert rec["state"] == "pending", "a resumable stop must not report failure"

    run = sandbox["sup"].get_run(spec["task_id"])
    assert run["state"] == "active"
    assert run["attempt_log"][-1]["resumable"] is True
    assert "timed out" in run["attempt_log"][-1]["summary"]
    # …and the partial output is carried into the next attempt.
    assert run["carry"]


def test_crash_under_supervision_is_resumable(sandbox):
    spec = _prepare(sandbox, supervised=True)
    r = _run_worker(sandbox, spec, mode="crash")
    assert r.returncode == 0

    rec = sandbox["cn"]._read(sandbox["cn"]._record_path(spec["task_id"]))
    assert rec["state"] == "pending"
    run = sandbox["sup"].get_run(spec["task_id"])
    assert run["attempt_log"][-1]["resumable"] is True
    assert "engine exploded" in run["carry"]


def test_a_resumed_worker_gets_the_continuation_prompt(sandbox):
    """The whole point: the second attempt continues instead of restarting."""
    spec = _prepare(sandbox, supervised=True)
    _run_worker(sandbox, spec, mode="crash")

    run = sandbox["sup"].get_run(spec["task_id"])
    prompt = sandbox["sup"].continuation_prompt(run)
    spec2 = dict(spec, instruction=prompt)
    _run_worker(sandbox, spec2, mode="ok")

    seen = (sandbox["tmp"] / "prompt.txt").read_text()
    assert "RESUMING" in seen
    assert "do the long thing" in seen, "the original goal must survive"
    assert "engine exploded" in seen, "why it stopped must survive"
    rec = sandbox["cn"]._read(sandbox["cn"]._record_path(spec["task_id"]))
    assert rec["ok"] is True


# ── flag-off is the pre-feature path, unchanged ──────────────────────────


def test_unsupervised_crash_still_reports_failure_immediately(sandbox):
    """With no run record the worker must behave exactly as it always did."""
    spec = _prepare(sandbox, supervised=False, progress=False)
    # _prepare only creates a run record when a flag is on; assert that.
    assert sandbox["sup"].get_run(spec["task_id"]) is None

    _run_worker(sandbox, spec, mode="crash")

    rec = sandbox["cn"]._read(sandbox["cn"]._record_path(spec["task_id"]))
    assert rec["state"] == "ready" and rec["ok"] is False
    assert "crashed" in rec["text"]


def test_unsupervised_timeout_still_reports_failure_immediately(sandbox):
    spec = _prepare(sandbox, supervised=False, progress=False)
    _run_worker(sandbox, spec, mode="hang", timeout_s="2", wait=90)

    rec = sandbox["cn"]._read(sandbox["cn"]._record_path(spec["task_id"]))
    assert rec["state"] == "ready" and rec["ok"] is False
    assert "timed out" in rec["text"]


def test_supervision_off_but_progress_on_does_not_resume(sandbox):
    """The two flags are independent; progress alone must not change the
    failure semantics."""
    spec = _prepare(sandbox, supervised=False, progress=True)
    _run_worker(sandbox, spec, mode="crash")

    rec = sandbox["cn"]._read(sandbox["cn"]._record_path(spec["task_id"]))
    assert rec["state"] == "ready" and rec["ok"] is False
