#!/usr/bin/env python3
"""A bridge turn's task record: opened at pickup, closed exactly once (ADR-2081 P2).

Through the real ``call_claude_streaming`` path with a fake ``claude`` binary
(the engine subprocess boundary), in a sandboxed CORVIN_HOME:

  1. the record exists and is RUNNING before the engine process starts — the
     fake binary reads it from disk when it is spawned;
  2. one turn = one record, closed ``completed`` with the reply as summary;
  3. the chat debug log pairs turn.start with a turn.done;
  4. a turn that fails once and succeeds on its retry ends ``completed`` (the
     first attempt's cleanup runs last and must not overwrite it) — still one
     record;
  5. the console's task list shows the stage of a running turn.

Run: python3 test_adapter_turn_task.py
"""
from __future__ import annotations

import json
import re
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parents[2] / "core" / "console"))
sys.path.insert(0, str(ROOT.parents[2]))

from test_adapter_stream_idle import _fresh_adapter  # noqa: E402

OPERATOR = "op-uid-1"
_ENV = ("PATH", "ADAPTER_INBOX", "ADAPTER_OUTBOX", "CORVIN_HOME", "VOICE_AUDIT_PATH",
        "ADAPTER_BRIDGES_DIR", "CLAUDE_CONFIG_DIR", "ADAPTER_STREAM_IDLE_TIMEOUT",
        "ADAPTER_HEARTBEAT_INTERVAL")


def _fake_claude(tmp: Path, *, hang_first: bool, overflow_first: bool = False) -> Path:
    """A `claude` that records the task records it finds at spawn time, then
    answers — or, on its first call when *hang_first*, hangs until killed."""
    bin_dir = tmp / "fake-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "claude"
    script.write_text(textwrap.dedent(f'''\
        #!/usr/bin/env python3
        import glob, json, os, signal, sys, time
        home = os.environ["CORVIN_HOME"]
        seen = []
        for p in glob.glob(home + "/tenants/*/sessions/voice/*/*/tasks/*.json"):
            d = json.load(open(p))
            ev = open(p[:-5] + ".events.jsonl").read()
            seen.append({{"status": d["status"], "preparing": '"preparing"' in ev}})
        calls = "{tmp}/calls"
        n = int(open(calls).read()) if os.path.exists(calls) else 0
        open(calls, "w").write(str(n + 1))
        with open("{tmp}/probe.jsonl", "a") as fh:
            fh.write(json.dumps(seen) + "\\n")
        sys.stdout.write(json.dumps({{"type": "system", "subtype": "init"}}) + "\\n")
        sys.stdout.flush()
        if {overflow_first!r} and n == 0:
            sys.stdout.write(json.dumps({{"type": "result", "is_error": True,
                                          "result": "autocompact thrashing: prompt is too long"}}) + "\\n")
            sys.stdout.flush()
            sys.exit(1)
        if {hang_first!r} and n == 0:
            signal.signal(signal.SIGTERM, lambda *a: sys.exit(143))
            while True:
                time.sleep(60)
        sys.stdout.write(json.dumps({{"type": "result", "is_error": False,
                                      "result": "all   phases\\n done"}}) + "\\n")
        sys.stdout.flush()
    '''))
    script.chmod(0o755)
    return bin_dir


def _setup(tmp: Path, *, hang_first: bool, overflow_first: bool = False, profiles: dict | None = None):
    bin_dir = _fake_claude(tmp, hang_first=hang_first, overflow_first=overflow_first)
    os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
    os.environ["ADAPTER_INBOX"] = str(tmp / "inbox")
    os.environ["ADAPTER_OUTBOX"] = str(tmp / "outbox")
    os.environ["CORVIN_HOME"] = str(tmp / "home")
    os.environ["VOICE_AUDIT_PATH"] = str(tmp / "audit.jsonl")
    os.environ["CLAUDE_CONFIG_DIR"] = str(tmp / "claude")
    os.environ["ADAPTER_BRIDGES_DIR"] = str(tmp / "bridges")
    (tmp / "bridges" / "discord").mkdir(parents=True, exist_ok=True)
    (tmp / "bridges" / "discord" / "settings.json").write_text(
        json.dumps({"whitelist": [OPERATOR], "chat_profiles": profiles or {}}))
    os.environ["ADAPTER_STREAM_IDLE_TIMEOUT"] = "2"
    os.environ["ADAPTER_HEARTBEAT_INTERVAL"] = "0"
    return _fresh_adapter()


def _records(workdir: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in (workdir / "tasks").glob("*.json")]


def _run(fn) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="adapter-turntask-"))
    saved = {k: os.environ.get(k) for k in _ENV}
    try:
        fn(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_opened_at_pickup_and_closed_once() -> None:
    def body(tmp: Path) -> None:
        adapter = _setup(tmp, hang_first=False)
        ans = adapter.call_claude_streaming("ship it", channel="discord", chat_key="tt1", msg_id="m-1",
                                            sender=OPERATOR)
        assert "phases" in ans, ans
        [probe] = [json.loads(line) for line in (tmp / "probe.jsonl").read_text().splitlines()]
        assert probe == [{"status": "running", "preparing": True}], probe
        workdir = adapter._session_dir("discord", "tt1")
        [rec] = _records(workdir)
        assert rec["status"] == "completed", rec
        assert rec["result_summary"] == "all phases done", rec
        events = [json.loads(line) for line in (workdir / "tasks" / f"{rec['task_id']}.events.jsonl")
                  .read_text().splitlines()]
        kinds = [e["event"] for e in events]
        assert kinds.count("task.started") == 1 and "task.engine_started" in kinds, kinds
        assert kinds.count("task.completed") == 1, kinds
        dbg = [json.loads(line) for line in (workdir / "chat_debug.jsonl").read_text().splitlines()]
        done = [d for d in dbg if d["event"] == "turn.done"]
        assert len(done) == 1 and done[0]["msg_id"] == "m-1" and done[0]["status"] == "completed", dbg
        starts = [d for d in dbg if d["event"] == "turn.start"]
        assert len(starts) == 1, dbg
        # someone else's turn: its reply text is not kept, only its length
        adapter.call_claude_streaming("ship it", channel="discord", chat_key="tt1b", msg_id="m-1b",
                                      sender="stranger")
        [other] = _records(adapter._session_dir("discord", "tt1b"))
        assert other["status"] == "completed" and not other.get("result_summary"), other
        print("PASS: task opened at pickup (running, preparing), closed once with summary, turn.done logged")
    _run(body)


def test_recovered_retry_ends_completed() -> None:
    def body(tmp: Path) -> None:
        adapter = _setup(tmp, hang_first=True)
        workdir = adapter._session_dir("discord", "tt2")
        workdir.mkdir(parents=True, exist_ok=True)
        (workdir / ".session_started").touch()   # a stream idle on a live session retries
        ans = adapter.call_claude_streaming("ship it", channel="discord", chat_key="tt2", msg_id="m-2")
        assert int((tmp / "calls").read_text()) == 2, "the retry did not run"
        assert "phases" in ans, ans
        [rec] = _records(workdir)
        assert rec["status"] == "completed", rec
        dbg = [json.loads(line) for line in (workdir / "chat_debug.jsonl").read_text().splitlines()]
        kinds = [d["event"] for d in dbg if d["event"].startswith("turn.")]
        assert kinds.count("turn.start") == 1 and kinds.count("turn.done") == 1, kinds
        assert "turn.retry" in kinds, kinds
        print("PASS: a turn recovered by its retry is one record, completed")
    _run(body)


def test_task_list_shows_stage_of_running_turn() -> None:
    def body(tmp: Path) -> None:
        _setup(tmp, hang_first=False)
        import importlib  # noqa: PLC0415
        from corvin_console import task_sources  # noqa: PLC0415
        importlib.reload(task_sources)
        adapter = sys.modules["adapter"]
        turn = adapter._open_turn_task(prompt="x", channel="discord", chat_key="tt3",
                                       profile=None, msg_id="m-3", sender="")
        by_id = {r["id"]: r for r in task_sources.collect("_default")["records"]}
        rec = by_id[f"chat:{turn.task_id}"]
        assert rec["status"] == "running" and rec["detail"].startswith("preparing context"), rec
        adapter._close_turn_task(turn, "ok", None, "m-3")
        print("PASS: a running turn shows its stage in the task list")
    _run(body)


def test_engine_pid_keeps_a_live_turn_from_the_reaper() -> None:
    """A bridge turn is task.started at pickup (no process yet) and logs its
    engine pid on task.engine_started. The boot reaper must see that pid: a
    live engine is not an orphan, a dead one is."""
    def body(tmp: Path) -> None:
        adapter = _setup(tmp, hang_first=True)
        tm_mod = adapter._task_manager
        # a real, live `claude` process (the fake binary hangs on its first call);
        # the reaper also checks /proc/<pid>/cmdline for "claude" against pid reuse
        engine = subprocess.Popen([str(tmp / "fake-bin" / "claude")], stdout=subprocess.DEVNULL,
                                  env={**os.environ})
        try:
            alive_dir, dead_dir = tmp / "alive" / "tasks", tmp / "dead" / "tasks"
            live = tm_mod.TaskManager(alive_dir)
            tid = live.create_task(chat_key="c", instruction="x", check_quota=False)
            live.record_event(tid, {"event": "task.started", "stage": "preparing"})
            live.record_event(tid, {"event": "task.engine_started", "engine": "ClaudeCodeEngine", "pid": engine.pid})
            assert live._last_started_pid(tid) == engine.pid
            assert live.reap_stale_running() == [], "a live engine was reaped as orphaned"
        finally:
            engine.terminate()
            engine.wait(timeout=10)
        dead = tm_mod.TaskManager(dead_dir)
        did = dead.create_task(chat_key="c", instruction="x", check_quota=False)
        dead.record_event(did, {"event": "task.started", "stage": "preparing"})
        dead.record_event(did, {"event": "task.engine_started", "engine": "ClaudeCodeEngine", "pid": 2 ** 22 + 7})
        assert dead.reap_stale_running() == [did]
        print("PASS: the reaper reads the engine pid from task.engine_started")
    _run(body)


def test_refusal_is_not_a_completed_turn() -> None:
    """A refused turn (here: the budget gate) returns explanatory text; the
    record must say failed, not completed with the refusal as its reply."""
    def body(tmp: Path) -> None:
        adapter = _setup(tmp, hang_first=False)
        adapter._budget_preflight = lambda chat_key, prompt: (False, "⛔ Token budget for this chat is exhausted")
        ans = adapter.call_claude_streaming("ship it", channel="discord", chat_key="tt5", msg_id="m-5")
        assert ans.startswith("⛔"), ans
        [rec] = _records(adapter._session_dir("discord", "tt5"))
        # nothing ran: a refusal, not a model failure — the learning loop
        # (task.completed/task.failed only) must not see it
        assert rec["status"] == "cancelled", rec
        assert rec.get("result_summary") == "refused: budget", rec
        assert not (tmp / "calls").exists(), "the engine ran despite the refusal"
        dbg_file = adapter._session_dir("discord", "tt5") / "chat_debug.jsonl"
        dbg = dbg_file.read_text().splitlines() if dbg_file.exists() else []
        assert not any('"turn.done"' in l for l in dbg), "turn.done without a turn.start"
        print("PASS: a budget refusal is recorded as refused (cancelled), not completed or failed")
    _run(body)


def test_outcome_rules() -> None:
    """The last attempt decides; an exception outranks any report."""
    def body(tmp: Path) -> None:
        adapter = _setup(tmp, hang_first=False)
        tm = adapter._task_manager.TaskManager(tmp / "u" / "tasks")

        def turn():
            tid = tm.create_task(chat_key="c", instruction="x", check_quota=False)
            tm.record_event(tid, {"event": "task.started", "stage": "preparing"})
            return adapter._TurnTask(tm, tid, tmp / "u" / "tasks", tmp / "u", owned=True)

        # engine reported completed, then post-processing raised: no reply went out
        t1 = turn()
        t1.report(0, {"event": "task.completed", "exit_code": 0})
        adapter._close_turn_task(t1, None, RuntimeError("voice override"), "m")
        assert tm.get_task(t1.task_id).status.value == "failed"
        # attempt 0 failed on claude, the retry answered on an engine that never reports
        t2 = turn()
        t2.report(0, {"event": "task.failed", "exit_code": 1})
        t2.attempt = 1
        adapter._close_turn_task(t2, "the answer", None, "m")
        assert tm.get_task(t2.task_id).status.value == "completed"
        # attempt 0's finally reporting AFTER the successful retry changes nothing
        t3 = turn()
        t3.attempt = 1
        t3.report(1, {"event": "task.completed", "exit_code": 0})
        t3.report(0, {"event": "task.failed", "exit_code": 1})
        adapter._close_turn_task(t3, "ok", None, "m")
        assert tm.get_task(t3.task_id).status.value == "completed"
        print("PASS: last attempt decides, an exception outranks any report")
    _run(body)


# Helpers that hand a denial BACK to their caller, which reports it — their
# own returns do not end a turn.
_RETURNS_TO_CALLER = {"_run_pre_dispatch_gates"}
_GATE_RETURN = re.compile(r"^\s+return (\w*gate_denial|\w*_msg|_m7_hr|refusal or .*)\s*$")


def test_every_gate_return_reports() -> None:
    """Static guard: a gate/refusal branch that returns its message without
    `_turn_refused(...)` right before it records the refusal as a completed
    reply. Positive control first: the sweep must find the known gates."""
    lines = (ROOT / "adapter.py").read_text(encoding="utf-8").splitlines()
    enclosing, fn = [], ""
    for l in lines:
        if l.startswith("def "):
            fn = l[4:].split("(", 1)[0]
        enclosing.append(fn)
    hits = [(n, l) for n, l in enumerate(lines)
            if _GATE_RETURN.match(l) and enclosing[n] not in _RETURNS_TO_CALLER]
    assert len(hits) >= 10, f"sweep found only {len(hits)} gate returns"
    missing = []
    for n, l in hits:
        prev = next((lines[k] for k in range(n - 1, max(n - 4, 0), -1) if lines[k].strip()), "")
        if "_turn_refused(" not in prev:
            missing.append(f"adapter.py:{n + 1}: {l.strip()}")
    assert not missing, "gate returns without _turn_refused:\n" + "\n".join(missing)
    print(f"PASS: all {len(hits)} gate returns report the refusal")


def test_escalation_retry_ends_completed() -> None:
    """A context-overflow on Haiku is retried on the higher model by recursing
    STRAIGHT into the engine function (not through the wrapper). The attempt
    that answered decides — the first attempt's failure, reported last by its
    finally, must not."""
    def body(tmp: Path) -> None:
        adapter = _setup(tmp, hang_first=False, overflow_first=True)
        ans = adapter.call_claude_streaming("ship it", channel="discord", chat_key="tt7", msg_id="m-7",
                                            sender=OPERATOR, profile={"model": "claude-haiku-4-5-20251001"})
        assert int((tmp / "calls").read_text()) == 2, "the escalation retry did not run"
        assert "phases" in ans, ans
        [rec] = _records(adapter._session_dir("discord", "tt7"))
        assert rec["status"] == "completed", rec
        print("PASS: a turn recovered by the model-escalation retry is completed")
    _run(body)


def test_cancel_is_cancelled_not_failed() -> None:
    """/cancel on a running claude turn: recorded cancelled (the learning loop
    ignores it), not a model failure."""
    import threading
    def body(tmp: Path) -> None:
        adapter = _setup(tmp, hang_first=True)
        os.environ["ADAPTER_STREAM_IDLE_TIMEOUT"] = "30"
        threading.Timer(3.0, lambda: adapter._cancel_chat("tt8")).start()
        adapter.call_claude_streaming("ship it", channel="discord", chat_key="tt8", msg_id="m-8", sender=OPERATOR)
        [rec] = _records(adapter._session_dir("discord", "tt8"))
        assert rec["status"] == "cancelled", rec
        print("PASS: /cancel is recorded cancelled")
    _run(body)


def test_round3_rules() -> None:
    def body(tmp: Path) -> None:
        adapter = _setup(tmp, hang_first=False, profiles={"groupchat": {"audience": "all"}})
        tm = adapter._task_manager.TaskManager(tmp / "u" / "tasks")

        def turn(chat_key="c"):
            tid = tm.create_task(chat_key=chat_key, instruction="x", check_quota=False)
            tm.record_event(tid, {"event": "task.started", "stage": "preparing",
                                  "owner_pid": os.getpid(), "owner_start": adapter._proc_start_time(os.getpid())})
            return adapter._TurnTask(tm, tid, tmp / "u" / "tasks", tmp / "u", owned=True, chat_key=chat_key)

        # an empty reply nobody cancelled is a silent engine failure …
        t = turn()
        adapter._close_turn_task(t, "", None, "m")
        assert tm.get_task(t.task_id).status.value == "failed"
        # … and after /cancel it is a cancellation
        t = turn("cx")
        adapter._cancel_chat("cx")
        adapter._close_turn_task(t, "", None, "m")
        assert tm.get_task(t.task_id).status.value == "cancelled"
        # a live owning process keeps a turn with no engine pid from the reaper;
        # the same pid with another start time is a recycled pid → reaped
        live = turn()
        assert tm.reap_stale_running() == [], "a live turn (owner alive) was reaped"
        recycled = tm.create_task(chat_key="r", instruction="x", check_quota=False)
        tm.record_event(recycled, {"event": "task.started", "stage": "preparing",
                                   "owner_pid": os.getpid(), "owner_start": "1"})
        assert tm.reap_stale_running() == [recycled]
        adapter._close_turn_task(live, "ok", None, "m")
        # audience: all — the whitelisted operator is not "the operator" there
        assert adapter._sender_is_operator("discord", OPERATOR, "tt9") is True
        assert adapter._sender_is_operator("discord", OPERATOR, "groupchat") is False
        # an engine without a subprocess still shows it is running
        from corvin_console import task_sources  # noqa: PLC0415
        t = turn()
        adapter._TURN_TASK.cur = t
        try:
            adapter._turn_engine_started("hermes")
        finally:
            adapter._TURN_TASK.cur = None
        assert task_sources._turn_stage(tmp / "u" / "tasks" / f"{t.task_id}.events.jsonl") == "engine running"
        print("PASS: empty reply / cancel / owner reaper / audience-all / engine stage")
    _run(body)


if __name__ == "__main__":
    test_opened_at_pickup_and_closed_once()
    test_recovered_retry_ends_completed()
    test_task_list_shows_stage_of_running_turn()
    test_engine_pid_keeps_a_live_turn_from_the_reaper()
    test_refusal_is_not_a_completed_turn()
    test_outcome_rules()
    test_every_gate_return_reports()
    test_escalation_retry_ends_completed()
    test_cancel_is_cancelled_not_failed()
    test_round3_rules()
