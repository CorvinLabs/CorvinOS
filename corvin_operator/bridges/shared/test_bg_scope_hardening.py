"""Regression tests for the findings of the adversarial review of ADR-2236 (round 2).

Each scenario is a REAL captured stream with ONE deliberate mutation (``bgscope_testkit.write_fixture``),
replayed by the fake CLI as a real subprocess and driven through ``adapter.process_one``:

  1  an error AFTER the first result must not re-run the prompt (the child would start twice and the
     first answer would be delivered twice)
  2  the process ending while a child is open is a kill/crash, never a clean finish
  5  an empty last wake-up result must not hand the user the first answer again as "final"
  6  interim text goes through the same output sentinel as the final one
  7  the spoken facts survive a <voice> override
  8  a cap kills a REAL child process
 14  milestone voice is capped (3 per scope) and every failure is still written
"""
from __future__ import annotations

import copy
import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE / "tests", HERE.parents[1] / "forge"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import bgscope_testkit as kit  # noqa: E402
from test_bg_scope_completion import _adapter, _audit, box  # noqa: E402,F401
from test_bg_scope_voice_e2e import Tts, _voiced  # noqa: E402

pytestmark = pytest.mark.skipif(sys.platform.startswith("win"),
                                reason="POSIX process semantics in the harness")


def _results(events):
    return [e for e in events if e.get("type") == "result"]


def _derive(box_, name, base, mutate) -> Path:
    events = copy.deepcopy(kit.load_fixture(base))
    mutate(events)
    return kit.write_fixture(box_ / "derived", name, events)


def _turn(monkeypatch, box_, fixture, *, speedup=4, env=None, settings=None, tts=None,
          child=False, adapter_hook=None, msg_id="m1"):
    for k, v in kit.fake_env(box_, fixture, speedup=speedup, child=child).items():
        monkeypatch.setenv(k, v)
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("ADAPTER_DISABLE_VOICE", raising=False)
    adapter = _adapter()
    tts = tts or Tts(box_)
    adapter.build_voice_summary = tts.summary
    adapter.synthesize_voice_note = tts.synth
    if adapter_hook:
        adapter_hook(adapter)
    f = Path(os.environ["ADAPTER_INBOX"]) / f"{msg_id}.json"
    f.write_text(json.dumps({"id": msg_id, "channel": "sandbox-bg", "from": "u42",
                             "chat_id": "chan-1", "text": "please do it", "ts": 0}))
    adapter.process_one(f, settings={"whitelist": ["u42"], "voice_summary_mode": "always",
                                     **(settings or {})})
    files = sorted(Path(os.environ["ADAPTER_OUTBOX"]).glob(f"{msg_id}_*.json"))
    return [(p.name, json.loads(p.read_text())) for p in files], tts


def _texts(msgs):
    # what the user READS as messages — not the sticky progress line the status lines are edited into
    return [e["text"] for _, e in msgs if e.get("text") and not e.get("_progress") and not e.get("_heartbeat")]


def _final(msgs):
    f = [e for _, e in msgs if e.get("_final") and e.get("text")]
    assert len(f) == 1, [t[:60] for t in _texts(msgs)]
    return f[0]["text"]


# ----------------------------------------------------------------------------------- 1

def test_an_error_after_the_first_result_is_not_retried_and_does_not_restart_the_child(box, monkeypatch):
    def mutate(ev):
        last = [e for e in ev if e.get("type") == "result"][-1]
        last.update({"is_error": True, "result": "API Error: 529 overloaded", "api_error_status": 529})

    fx = _derive(box, "error_after_first", "bash_bg_ok", mutate)
    msgs, _ = _turn(monkeypatch, box, fx)
    assert kit.spawn_count(box) == 1, f"the prompt was re-run {kit.spawn_count(box)}x (child started twice)"
    texts = _texts(msgs)
    assert sum("gestartet" in t for t in texts) == 1, "the first answer was delivered twice"
    final = _final(msgs)
    assert "Claude API call failed" in final and "Background work that was already started" in final
    assert "not run again" in final
    assert _audit(box)[-1]["details"]["end_reason"] == "error"


# ----------------------------------------------------------------------------------- 2

def _crash_after_first_result(ev):
    cut = next(i for i, e in enumerate(ev) if e.get("type") == "result")
    t = ev[cut]["_t"]
    del ev[cut + 1:]
    # a real crash exit code. (137/143 = 128+SIGKILL/SIGTERM now count as "stopped from outside".)
    ev.append({"type": "_eof", "_t": t + 0.6, "rc": 1})


def test_the_process_ending_with_an_open_child_is_not_a_clean_finish(box, monkeypatch):
    fx = _derive(box, "crash_open_child", "bash_bg_ok", _crash_after_first_result)
    msgs, tts = _turn(monkeypatch, box, fx)
    final = _final(msgs)
    assert "ended while 1 background task was still running" in final and "bash" in final
    assert "incomplete" in final
    assert final.strip() != "gestartet", "the first answer was handed over again as the closing message"
    audit = _audit(box)
    types = [r["event_type"] for r in audit]
    assert "bgscope.cancelled" in types and types[-1] == "bgscope.completed", types
    assert audit[types.index("bgscope.cancelled")]["details"]["reason_code"] == "process_died"
    assert audit[-1]["details"]["end_reason"] == "process_died"
    # the lost child is closed in the books (not left "running" forever)
    assert any(r["event_type"] == "bgscope.child_finished" and r["details"].get("healed") for r in audit)
    # ...and it is said aloud, in the language of the summary
    assert any(w in tts.calls[-1] for w in ("interrupted", "unterbrochen")), tts.calls[-1]
    assert kit.spawn_count(box) == 1


# ----------------------------------------------------------------------------------- 5

def test_an_empty_last_wakeup_result_is_closed_with_a_deterministic_line(box, monkeypatch):
    def mutate(ev):
        _results(ev)[-1]["result"] = ""

    fx = _derive(box, "empty_wakeup", "bash_bg_ok", mutate)
    msgs, _ = _turn(monkeypatch, box, fx)
    assert _final(msgs) == "✅ Background work finished: 1 task done."
    assert sum("gestartet" in t for t in _texts(msgs)) == 1, "the first answer was repeated as the final"


# ----------------------------------------------------------------------------------- 6

def test_interim_text_goes_through_the_output_sentinel_like_the_final(box, monkeypatch):
    def hook(adapter):
        adapter._output_sentinel = object()           # opt-in persona/tenant: the sentinel is active
        adapter._apply_output_sentinel = lambda prompt, text, **kw: "[withheld by the output sentinel]"

    msgs, _ = _turn(monkeypatch, box, "bash_bg_ok", adapter_hook=hook)
    interim = [e["text"] for n, e in msgs if "_-" in n and e.get("text")]
    assert interim and all(t.startswith("[withheld by the output sentinel]") for t in interim), interim
    assert not any("gestartet" in t for t in _texts(msgs)), "unfiltered model text reached the user"


# ----------------------------------------------------------------------------------- 7

def test_the_spoken_facts_survive_a_voice_override(box, monkeypatch):
    def mutate(ev):
        _results(ev)[-1]["result"] = "Fertig. <voice>Alles erledigt.</voice>"

    fx = _derive(box, "voice_override", "bash_bg_fail", mutate)
    msgs, tts = _turn(monkeypatch, box, fx)
    spoken = tts.calls[-1]
    assert spoken.startswith("Alles erledigt."), spoken            # the override is honoured...
    assert "fehlgeschlagen" in spoken or "failed" in spoken, spoken  # ...and the failure is not lost
    assert "<voice>" not in _final(msgs), "the voice tag must never reach the chat text"


# ----------------------------------------------------------------------------------- 8

def test_a_cap_kills_a_real_child_process(box, monkeypatch):
    msgs, _ = _turn(monkeypatch, box, "bash_bg_ok", speedup=1, child=True,
                    env={"CORVIN_BG_CHILD_MAX": "3"})
    assert "Stopped after 3 s" in _final(msgs)
    assert not kit.pid_alive(box / "fake.pid"), "the CLI is still alive"
    assert not kit.pid_alive(box / "child.pid"), "the background child outlived the cap"


# ---------------------------------------------------------------------------------- 14

def _four_failing_children(ev):
    """Replace the stream by: 4 children start, first answer, then each fails, closing wake-up."""
    head = [e for e in ev if e.get("type") == "system" and e.get("subtype") == "init"][:1]
    res0 = next(e for e in ev if e.get("type") == "result" and "origin" not in e)
    res_last = copy.deepcopy([e for e in ev if e.get("type") == "result"][-1])
    ev[:] = head
    ids = [f"c{i}" for i in range(4)]
    tasks = lambda live: [{"task_id": i, "run_id": f"r{i}", "task_type": "local_bash",
                           "description": f"job {i}"} for i in live]
    ev.append({"type": "system", "subtype": "background_tasks_changed", "tasks": tasks(ids), "_t": 1.0})
    for i in ids:
        ev.append({"type": "system", "subtype": "task_started", "task_id": i, "run_id": f"r{i}",
                   "task_type": "local_bash", "description": f"job {i}", "is_backgrounded": True, "_t": 1.0})
    r0 = copy.deepcopy(res0)
    r0["_t"] = 1.5
    ev.append(r0)
    for n, i in enumerate(ids):
        t = 3.0 + n
        live = ids[n + 1:]
        ev.append({"type": "system", "subtype": "task_updated", "task_id": i,
                   "patch": {"status": "failed"}, "_t": t})
        ev.append({"type": "system", "subtype": "task_notification", "task_id": i, "status": "failed",
                   "summary": f'Background command "job {i}" failed with exit code 3', "_t": t})
        ev.append({"type": "system", "subtype": "background_tasks_changed", "tasks": tasks(live), "_t": t})
    res_last["_t"] = 8.0
    ev.append(res_last)
    ev.append({"type": "_eof", "_t": 8.4, "rc": 0})


def test_milestone_voice_is_capped_but_every_failure_is_still_written(box, monkeypatch):
    fx = _derive(box, "four_failures", "bash_bg_ok", _four_failing_children)
    msgs, tts = _turn(monkeypatch, box, fx, speedup=4)
    written = [t for t in _texts(msgs) if "failed (exit 3)" in t]
    assert len(written) == 4, written
    milestone_voice = [c for c in tts.calls if "failed (exit 3)" in c]
    assert len(milestone_voice) == 3, f"{len(milestone_voice)} milestone voice notes (cap is 3)"
    assert len(tts.calls) == 4          # 3 milestones + the closing summary
    assert any("4 of 4 tasks failed" in c or "4 von 4" in c for c in tts.calls[-1:]), tts.calls[-1]


# --------------------------------------------------------------------------------- unit

def test_a_stuck_process_is_killed_after_the_grace_but_a_polite_one_only_terminated():
    """chat_runtime._stop_proc: SIGTERM first (the real CLI then ends its children), SIGKILL
    only if it ignores it. (Measured: SIGKILL straight away orphans the children.)"""
    import asyncio

    sys.path.insert(0, str(HERE.parents[1] / "core" / "console"))
    from corvin_console import chat_runtime as cr  # noqa: PLC0415

    async def run():
        stubborn = await asyncio.create_subprocess_exec(
            sys.executable, "-c",
            "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('up', flush=True); time.sleep(30)",
            stdout=asyncio.subprocess.PIPE)
        await stubborn.stdout.readline()
        polite = await asyncio.create_subprocess_exec(sys.executable, "-c", "import time; time.sleep(30)")
        await asyncio.sleep(0.3)
        cr._stop_proc(stubborn, grace_s=0.6)
        cr._stop_proc(polite, grace_s=0.6)
        await asyncio.sleep(0.2)
        assert polite.returncode == -15 and stubborn.returncode is None   # TERM took the polite one only
        await asyncio.wait_for(stubborn.wait(), 5)
        return stubborn.returncode, polite.returncode

    stubborn_rc, polite_rc = asyncio.run(run())
    assert stubborn_rc == -9 and polite_rc == -15


# ------------------------------------------------------------- review round 2 ---

def _cancel_after(delay_s):
    """adapter_hook: a real operator /cancel (adapter._cancel_chat) `delay_s` after the CLI started."""
    def hook(adapter):
        def canceller():
            t0 = time.time()
            keys = []
            while time.time() - t0 < 30 and not keys:
                with adapter._running_subprocs_guard:
                    keys = [k for k, v in adapter._running_subprocs.items() if v]
                time.sleep(0.05)
            time.sleep(delay_s)
            if keys:
                adapter._cancel_chat(keys[0])
        threading.Thread(target=canceller, daemon=True).start()
    return hook


def test_an_operator_cancel_with_an_open_child_is_silent_like_every_other_cancel(box, monkeypatch):
    """/cancel after the first answer, child still running. The flag is set only AFTER the process
    is gone, so the stop must be recognised from the signal itself — the user is not told 'the
    Claude process ended' and nothing is spoken."""
    # child=True: like the real CLI (measured) the fake ends its child on SIGTERM and exits 143 —
    # a stub that simply dies by signal (-15) is not what the adapter sees in production
    msgs, tts = _turn(monkeypatch, box, "bash_bg_ok", speedup=1, child=True,
                      adapter_hook=_cancel_after(4.0))
    assert [n for n, e in msgs if e.get("_final") and e.get("text")] == [], _texts(msgs)
    assert not any("ended while" in t or "API call failed" in t for t in _texts(msgs)), _texts(msgs)
    assert tts.calls == [], "a cancelled scope must not be spoken"
    reasons = [r["details"].get("reason_code") for r in _audit(box) if r["event_type"] == "bgscope.cancelled"]
    assert reasons == ["user_cancel"], reasons
    assert kit.spawn_count(box) == 1


def test_an_operator_cancel_before_any_answer_is_silent_not_an_api_failure(box, monkeypatch):
    def mutate(ev):
        cut = next(i for i, e in enumerate(ev) if e.get("type") == "result")
        del ev[cut:]
        ev.append({"type": "_eof", "_t": 30.0, "rc": 0})

    fx = _derive(box, "no_answer_yet", "bash_bg_ok", mutate)
    msgs, tts = _turn(monkeypatch, box, fx, speedup=1, child=True, adapter_hook=_cancel_after(3.5))
    assert not any("API call failed" in t for t in _texts(msgs)), _texts(msgs)
    assert [n for n, e in msgs if e.get("_final") and e.get("text")] == []
    assert kit.spawn_count(box) == 1, "a cancel must not re-run the prompt"
    reasons = [r["details"].get("reason_code") for r in _audit(box) if r["event_type"] == "bgscope.cancelled"]
    assert reasons == ["signal_stop"], reasons


def test_a_bad_ending_is_spoken_even_in_long_only_mode(box, monkeypatch):
    msgs, tts = _turn(monkeypatch, box, "bash_bg_fail", settings={"voice_summary_mode": "long_only"})
    assert tts.calls, "long_only silenced a scope that ended badly"
    assert "fehlgeschlagen" in tts.calls[-1] or "failed" in tts.calls[-1], tts.calls[-1]


def test_a_clean_short_ending_stays_silent_in_long_only_mode(box, monkeypatch):
    msgs, tts = _turn(monkeypatch, box, "bash_bg_ok", settings={"voice_summary_mode": "long_only"})
    assert tts.calls == [], tts.calls


def test_the_facts_are_spoken_even_when_the_summariser_returns_nothing(box, monkeypatch):
    def hook(adapter):
        adapter.build_voice_summary = lambda *a, **k: ""

    msgs, tts = _turn(monkeypatch, box, "bash_bg_fail", adapter_hook=hook)
    final_voice = tts.calls[-1]
    assert "failed" in final_voice or "fehlgeschlagen" in final_voice, tts.calls
    assert len(final_voice) < 120, "only the facts sentence was expected"


def test_a_clean_exit_with_a_never_announced_child_end_is_not_reported_as_a_death(box, monkeypatch):
    """The CLI exits 0 only after its children ended; if the end was never announced (dropped
    events) the scope is simply done — not 'the process ended while a task was running'."""
    def mutate(ev):
        ev[:] = [e for e in ev if e.get("subtype") not in ("task_updated", "task_notification")
                 and not (e.get("subtype") == "background_tasks_changed" and not e.get("tasks"))]

    fx = _derive(box, "end_never_announced", "bash_bg_ok", mutate)
    msgs, _ = _turn(monkeypatch, box, fx)
    texts = _texts(msgs)
    assert "ended while" not in " ".join(texts), texts
    # the end was never announced, so the tracker still counted the child when the wake-up came:
    # that text is delivered as an update, and the scope is closed by the deterministic line
    assert any("abgeschlossen" in t.lower() for t in texts), texts
    assert _final(msgs).startswith("■ Background work ended") or _final(msgs).startswith("✅"), _final(msgs)
    assert _audit(box)[-1]["details"]["end_reason"] == "quiescent"


def test_the_cancel_flag_is_stamped_before_the_process_is_signalled(monkeypatch, tmp_path):
    """The turn thread wakes the instant the process is gone and reads the flag: it must already be
    there. And a /cancel that stopped nothing must not leave a stale flag behind."""
    for k, v in {"CORVIN_HOME": tmp_path / "c", "XDG_CONFIG_HOME": tmp_path / "x", "FORGE_ROOT": tmp_path / "f",
                 "ADAPTER_INBOX": tmp_path / "i", "ADAPTER_OUTBOX": tmp_path / "o",
                 "VOICE_AUDIT_PATH": tmp_path / "a.jsonl"}.items():
        monkeypatch.setenv(k, str(v))
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(tmp_path / "k"))
    adapter = _adapter()
    seen = {}

    def impl_stops(key):
        seen["flag_during"] = adapter._cancel_requested(key)
        return 1

    monkeypatch.setattr(adapter, "_cancel_chat_impl", impl_stops)
    assert adapter._cancel_chat("chat-a") == 1
    assert seen["flag_during"] is True and adapter._cancel_requested("chat-a") is True

    monkeypatch.setattr(adapter, "_cancel_chat_impl", lambda key: 0)
    assert adapter._cancel_chat("chat-b") == 0
    assert adapter._cancel_requested("chat-b") is False, "a /cancel that stopped nothing left a stale flag"

    def impl_raises(key):
        raise RuntimeError("boom")

    monkeypatch.setattr(adapter, "_cancel_chat_impl", impl_raises)
    with pytest.raises(RuntimeError):
        adapter._cancel_chat("chat-c")
    assert adapter._cancel_requested("chat-c") is False


@pytest.mark.parametrize("rc,expected", [(-15, True), (-9, True), (143, True), (137, True),
                                         (0, False), (1, False), (2, False), (-11, False)])
def test_which_exit_codes_count_as_stopped_from_outside(box, rc, expected):
    adapter = _adapter()
    assert (rc in adapter._STOPPED_EXIT_CODES) is expected

