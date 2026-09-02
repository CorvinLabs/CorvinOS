#!/usr/bin/env python3
"""test_self_delegation.py — ADR-0553 Phase 3: self-delegation.

The assistant hands LONG background work off to itself by emitting a
``⟦bgtask-run:<label>|<instruction>⟧`` marker in its FINAL reply; the adapter
reply-hook then spawns a DETACHED bg_task_worker (the SAME turn-surviving
backbone `/task` uses) that outlives the turn and later delivers a final
Text+Voice summary.

Ship-dark behind ``bridge_self_delegation`` (default OFF). Flag OFF ⇒ the marker
is stripped (never leaks) but NOTHING is spawned. Flag ON ⇒ exactly one detached
worker per DISTINCT marker, registered with the right origin + want_voice.

Levels:
  1. Parser: parse_run_markers / strip / display-vs-run separation.
  2. Flag OFF via the REAL process_one reply-hook: no spawn, no leak.
  3. Flag ON via the REAL process_one reply-hook: one detached worker, correct
     register(channel/chat_id/want_voice) + instruction; marker stripped.
  4. Reachability: source-assert that process_one calls the hook and the hook
     gates on the flag + spawns.
  5. never-raise: a broken spawn does not break the turn.
  6. Idempotency: a duplicated marker spawns exactly ONE worker.

Run: .venv/bin/python operator/bridges/shared/test_self_delegation.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mid_turn_heartbeat as mth  # noqa: E402

# adapter.subprocess IS the stdlib subprocess singleton — restore Popen after
# any test that mocks it (same discipline as test_bg_task.py).
_REAL_POPEN = subprocess.Popen


# ── Level 1: parser / strip / family separation ─────────────────────────────

def test_parse_run_marker() -> None:
    assert mth.parse_run_markers("⟦bgtask-run:Build|run the tests⟧") == [
        ("Build", "run the tests")
    ]
    # Strip removes it entirely.
    assert "⟦bgtask-run" not in mth.strip_markers(
        "pre ⟦bgtask-run:Build|run the tests⟧ post"
    )
    assert mth.strip_markers("pre ⟦bgtask-run:Build|run the tests⟧ post").strip() \
        == "pre  post".strip()
    # A DISPLAY marker ⟦bgtask:…⟧ is NOT a run marker …
    assert mth.parse_run_markers("⟦bgtask:Build⟧") == []
    # … and a run marker is NOT parsed as a display start marker.
    assert mth.parse_markers("⟦bgtask-run:Build|x⟧") == []
    # Dedup: the same marker twice → one spawn request.
    assert mth.parse_run_markers(
        "⟦bgtask-run:A|do x⟧ then ⟦bgtask-run:A|do x⟧"
    ) == [("A", "do x")]
    # Non-str never raises.
    assert mth.parse_run_markers(None) == []
    print("PASS: parse_run_markers + strip + display/run separation + dedup")


# ── adapter harness (mirrors test_bg_task._fresh_adapter) ───────────────────

def _fresh_adapter(env_overrides: dict):
    os.environ["CORVIN_OS_ENGINE"] = "claude_code"
    xdg = Path(tempfile.mkdtemp(prefix="selfdel-xdg-"))
    (xdg / "corvin-voice").mkdir(parents=True)
    (xdg / "corvin-voice" / "profile.json").write_text("{}")
    os.environ["XDG_CONFIG_HOME"] = str(xdg)
    os.environ["ADAPTER_DISABLE_VOICE"] = "1"
    sys.modules.pop("profile", None)
    for k, v in env_overrides.items():
        os.environ[k] = v
    sys.modules.pop("adapter", None)
    sys.path.insert(0, str(HERE))
    import adapter  # type: ignore
    adapter._house_rules_classifier = lambda task, rules, auth, **_kw: ("", 1.0, "test-benign")
    # Short-circuit the engine + voice so the turn reaches the reply-hook cleanly
    # with a canned answer carrying the marker. The component UNDER TEST is the
    # reply-hook + spawn, not the engine.
    adapter._maybe_delegate_worker = lambda prompt, **kw: (None, prompt)
    adapter._synthesize_voice_for_turn = lambda *a, **kw: (None, False)
    return adapter


def _spawn_worker_calls(captured: list) -> list:
    return [c for c in captured
            if len(c.get("args", [])) >= 2 and str(c["args"][1]).endswith("bg_task_worker.py")]


def _run_turn(adapter, inbox: Path, outbox: Path, *, text: str, answer: str,
              captured: list) -> None:
    adapter.call_claude_streaming = lambda prompt, **kw: answer

    class _FakePopen:
        def __init__(self, args, **kw):
            captured.append({"args": list(args), "kw": kw})

    adapter.subprocess.Popen = _FakePopen
    env = {"id": "msg-sd", "channel": "sandbox-task", "from": "u42",
           "chat_id": "chan-99", "text": text, "ts": 0}
    f = inbox / "msg-sd.json"
    f.write_text(json.dumps(env))
    adapter.process_one(f, settings={"whitelist": ["u42"]})


# ── Level 2: flag OFF — no spawn, no leak (real reply-hook) ──────────────────

def test_flag_off_no_spawn_no_leak() -> None:
    base = Path(tempfile.mkdtemp(prefix="selfdel-off-"))
    inbox, outbox, processed, home = (base / "inbox", base / "outbox",
                                      base / "processed", base / "home")
    for p in (inbox, outbox, processed, home):
        p.mkdir(parents=True)
    try:
        adapter = _fresh_adapter({
            "ADAPTER_INBOX": str(inbox), "ADAPTER_OUTBOX": str(outbox),
            "ADAPTER_PROCESSED": str(processed), "CORVIN_HOME": str(home),
        })
        adapter._bg_flag = lambda flag_id: False  # everything ship-dark OFF

        captured: list = []
        answer = ("Ich erledige das im Hintergrund. "
                  "⟦bgtask-run:Nightly|analyse all logs and summarise⟧")
        _run_turn(adapter, inbox, outbox, text="do the long thing",
                  answer=answer, captured=captured)

        # No detached worker was spawned.
        assert _spawn_worker_calls(captured) == [], captured
        # No completion record was registered by self-delegation.
        assert not list((home / "pending_notifications").glob("*.json"))
        # And the marker NEVER leaked into any outbox message.
        for pth in outbox.glob("*.json"):
            body = pth.read_text()
            assert "bgtask-run" not in body, f"marker leaked: {pth.name}: {body}"
        print("PASS: flag OFF — no spawn, no registration, no marker leak")
    finally:
        adapter.subprocess.Popen = _REAL_POPEN
        shutil.rmtree(base, ignore_errors=True)


# ── Level 3: flag ON — one detached worker, correct spec, marker stripped ───

def test_flag_on_spawns_one_detached_worker() -> None:
    base = Path(tempfile.mkdtemp(prefix="selfdel-on-"))
    inbox, outbox, processed, home = (base / "inbox", base / "outbox",
                                      base / "processed", base / "home")
    for p in (inbox, outbox, processed, home):
        p.mkdir(parents=True)
    try:
        adapter = _fresh_adapter({
            "ADAPTER_INBOX": str(inbox), "ADAPTER_OUTBOX": str(outbox),
            "ADAPTER_PROCESSED": str(processed), "CORVIN_HOME": str(home),
        })
        # Phase-3 flag on. proactive_voice_completion also on so the self-
        # delegated completion opts into voice (LB-voice: the worker voice
        # opt-in now gates on BOTH that flag AND voice_summary_mode, matching the
        # /task path — not voice_summary_mode alone).
        adapter._bg_flag = lambda flag_id: flag_id in (
            "bridge_self_delegation", "proactive_voice_completion")

        captured: list = []
        instruction = "analyse all logs and write a summary report"
        answer = (f"Alles klar, das läuft im Hintergrund. "
                  f"⟦bgtask-run:Nightly|{instruction}⟧")
        _run_turn(adapter, inbox, outbox, text="do the long thing",
                  answer=answer, captured=captured)

        # Exactly ONE detached worker was spawned …
        worker_calls = _spawn_worker_calls(captured)
        assert len(worker_calls) == 1, worker_calls
        call = worker_calls[0]
        # … detached (start_new_session=True), spec passed via a 0600 FILE
        #   (not argv — argv leaks PII), instruction NOT on argv.
        assert call["kw"].get("start_new_session") is True, call["kw"]
        argv = call["args"]
        spec_path = Path(argv[2])
        assert spec_path.is_file(), f"spec must be a file path, got {argv[2]!r}"
        assert instruction not in " ".join(argv), "instruction must NOT be on argv"
        spec = json.loads(spec_path.read_text())
        assert spec["instruction"] == instruction, spec
        assert spec["channel"] == "sandbox-task"
        assert spec["chat_key"] == "chan-99"
        assert spec["want_voice"] is True, spec  # default voice pref → Text+Voice
        spec_path.unlink(missing_ok=True)

        # A pending completion carrying the origin channel/chat/sender + want_voice.
        recs = list((home / "pending_notifications").glob("*.json"))
        assert len(recs) == 1, recs
        rec = json.loads(recs[0].read_text())
        assert rec["channel"] == "sandbox-task"
        assert rec["chat_id"] == "chan-99"
        assert rec["sender"] == "u42"
        assert rec["want_voice"] is True, rec
        assert rec["state"] == "pending"

        # The marker was stripped from every outbox message the user sees.
        for pth in outbox.glob("*.json"):
            assert "bgtask-run" not in pth.read_text(), pth.name
        print("PASS: flag ON — one detached worker, correct spec + register + "
              "want_voice, marker stripped")
    finally:
        adapter.subprocess.Popen = _REAL_POPEN
        shutil.rmtree(base, ignore_errors=True)


def test_flag_on_duplicate_marker_spawns_once() -> None:
    """Idempotency: the same marker twice in one reply spawns exactly ONE worker."""
    base = Path(tempfile.mkdtemp(prefix="selfdel-dup-"))
    inbox, outbox, processed, home = (base / "inbox", base / "outbox",
                                      base / "processed", base / "home")
    for p in (inbox, outbox, processed, home):
        p.mkdir(parents=True)
    try:
        adapter = _fresh_adapter({
            "ADAPTER_INBOX": str(inbox), "ADAPTER_OUTBOX": str(outbox),
            "ADAPTER_PROCESSED": str(processed), "CORVIN_HOME": str(home),
        })
        adapter._bg_flag = lambda flag_id: flag_id == "bridge_self_delegation"

        captured: list = []
        answer = ("⟦bgtask-run:Job|crunch the dataset⟧ und nochmal "
                  "⟦bgtask-run:Job|crunch the dataset⟧")
        _run_turn(adapter, inbox, outbox, text="go", answer=answer,
                  captured=captured)

        assert len(_spawn_worker_calls(captured)) == 1, "duplicate marker must spawn once"
        assert len(list((home / "pending_notifications").glob("*.json"))) == 1
        print("PASS: duplicate marker → exactly one worker (idempotent)")
    finally:
        adapter.subprocess.Popen = _REAL_POPEN
        shutil.rmtree(base, ignore_errors=True)


# ── MB2: self-delegation respects the /task concurrency cap ─────────────────

def test_self_delegation_respects_concurrency_cap() -> None:
    """MB2: when the sender already has CORVIN_BG_TASK_MAX active background
    tasks, a ⟦bgtask-run⟧ marker must NOT spawn another worker (fork-bomb guard,
    same as the /task handler)."""
    base = Path(tempfile.mkdtemp(prefix="selfdel-cap-"))
    inbox, outbox, processed, home = (base / "inbox", base / "outbox",
                                      base / "processed", base / "home")
    for p in (inbox, outbox, processed, home):
        p.mkdir(parents=True)
    _prev_max = os.environ.get("CORVIN_BG_TASK_MAX")
    try:
        os.environ["CORVIN_BG_TASK_MAX"] = "2"
        adapter = _fresh_adapter({
            "ADAPTER_INBOX": str(inbox), "ADAPTER_OUTBOX": str(outbox),
            "ADAPTER_PROCESSED": str(processed), "CORVIN_HOME": str(home),
        })
        adapter._bg_flag = lambda flag_id: flag_id == "bridge_self_delegation"

        # Pre-fill the cap: 2 active (pending) completion records for sender u42.
        sys.modules.pop("completion_notify", None)
        import completion_notify as cn  # type: ignore
        cn.register(channel="sandbox-task", chat_id="chan-99", sender="u42",
                    tenant_id="_default", label="a")
        cn.register(channel="sandbox-task", chat_id="chan-99", sender="u42",
                    tenant_id="_default", label="b")
        assert cn.count_active(sender="u42") == 2

        captured: list = []
        answer = "Ok. ⟦bgtask-run:Job|do the long thing⟧"
        _run_turn(adapter, inbox, outbox, text="go", answer=answer,
                  captured=captured)

        # Cap reached → NO new worker spawned, NO third record registered.
        assert _spawn_worker_calls(captured) == [], captured
        assert cn.count_active(sender="u42") == 2, "cap must not be exceeded"
        print("PASS: self-delegation honours the concurrency cap — no spawn over cap")
    finally:
        if _prev_max is None:
            os.environ.pop("CORVIN_BG_TASK_MAX", None)
        else:
            os.environ["CORVIN_BG_TASK_MAX"] = _prev_max
        adapter.subprocess.Popen = _REAL_POPEN
        shutil.rmtree(base, ignore_errors=True)


# ── LB-voice: worker voice opt-in gates on proactive_voice_completion too ────

def test_self_delegation_voice_gated_on_flag() -> None:
    """LB-voice: with self-delegation ON but proactive_voice_completion OFF, the
    spawned worker's want_voice must be False (gating consistent with /task) —
    voice_summary_mode alone must not opt a self-delegated completion into voice."""
    base = Path(tempfile.mkdtemp(prefix="selfdel-voiceoff-"))
    inbox, outbox, processed, home = (base / "inbox", base / "outbox",
                                      base / "processed", base / "home")
    for p in (inbox, outbox, processed, home):
        p.mkdir(parents=True)
    try:
        adapter = _fresh_adapter({
            "ADAPTER_INBOX": str(inbox), "ADAPTER_OUTBOX": str(outbox),
            "ADAPTER_PROCESSED": str(processed), "CORVIN_HOME": str(home),
        })
        # Self-delegation ON, voice flag OFF; voice preference default (allows).
        adapter._bg_flag = lambda flag_id: flag_id == "bridge_self_delegation"
        adapter.load_settings = lambda: {"voice_summary_mode": "always"}

        captured: list = []
        answer = "Ok. ⟦bgtask-run:Job|analyse the logs⟧"
        _run_turn(adapter, inbox, outbox, text="go", answer=answer,
                  captured=captured)

        worker_calls = _spawn_worker_calls(captured)
        assert len(worker_calls) == 1, worker_calls
        spec = json.loads(Path(worker_calls[0]["args"][2]).read_text())
        assert spec["want_voice"] is False, spec        # voice flag OFF → no voice
        Path(worker_calls[0]["args"][2]).unlink(missing_ok=True)
        rec = json.loads(next((home / "pending_notifications").glob("*.json")).read_text())
        assert rec["want_voice"] is False, rec
        print("PASS: self-delegation voice opt-in gated on proactive_voice_completion")
    finally:
        adapter.subprocess.Popen = _REAL_POPEN
        shutil.rmtree(base, ignore_errors=True)


# ── Level 5: never-raise — a broken spawn does not break the turn ───────────

def test_broken_spawn_does_not_break_turn() -> None:
    base = Path(tempfile.mkdtemp(prefix="selfdel-raise-"))
    inbox, outbox, processed, home = (base / "inbox", base / "outbox",
                                      base / "processed", base / "home")
    for p in (inbox, outbox, processed, home):
        p.mkdir(parents=True)
    try:
        adapter = _fresh_adapter({
            "ADAPTER_INBOX": str(inbox), "ADAPTER_OUTBOX": str(outbox),
            "ADAPTER_PROCESSED": str(processed), "CORVIN_HOME": str(home),
        })
        adapter._bg_flag = lambda flag_id: flag_id == "bridge_self_delegation"

        class _BoomPopen:
            def __init__(self, args, **kw):
                raise OSError("simulated spawn failure")

        adapter.subprocess.Popen = _BoomPopen
        adapter.call_claude_streaming = lambda prompt, **kw: (
            "Ok. ⟦bgtask-run:Job|do the long thing⟧")

        env = {"id": "msg-boom", "channel": "sandbox-task", "from": "u42",
               "chat_id": "chan-99", "text": "go", "ts": 0}
        f = inbox / "msg-boom.json"
        f.write_text(json.dumps(env))
        # Must NOT raise — a broken hand-off degrades to a normal reply.
        adapter.process_one(f, settings={"whitelist": ["u42"]})

        # The user still got their reply, with the marker stripped.
        finals = [json.loads(p.read_text()) for p in outbox.glob("msg-boom_*.json")]
        assert finals, "the turn must still produce a reply"
        for e in finals:
            assert "bgtask-run" not in json.dumps(e), e
        print("PASS: broken spawn is swallowed — turn completes, marker stripped")
    finally:
        adapter.subprocess.Popen = _REAL_POPEN
        shutil.rmtree(base, ignore_errors=True)


# ── Level 4: reachability — source-asserts on the real wiring ───────────────

def test_reply_hook_wired_into_process_one() -> None:
    """Source-assert (like C1-B): process_one calls the self-delegation hook,
    and the hook gates on the flag + performs the detached spawn. A true
    transport E2E of the full path needs a live bridge + model, so the actual
    execution is proven by the process_one-driven tests above; this asserts the
    static call graph so the hook can never be silently orphaned."""
    src = (HERE / "adapter.py").read_text()
    # process_one calls the hook …
    assert "_maybe_self_delegate(answer" in src, "process_one must call the hook"
    # … the hook gates on the ship-dark flag …
    assert '_bg_flag("bridge_self_delegation")' in src, "hook must gate on the flag"
    # … parses the run marker …
    assert ".parse_run_markers(" in src, "hook must parse the run marker"
    # … and reuses the detached-spawn core (register + detached Popen).
    assert "_spawn_detached_bg_worker(" in src, "hook must call the spawn core"
    assert "start_new_session=True" in src, "spawn must be detached"
    assert "want_voice=" in src and ".register(" in src, "spawn must register origin+voice"
    # The strip runs UNCONDITIONALLY (flag-independent) so the marker never leaks.
    assert ".strip_markers(answer)" in src, "marker must always be stripped"
    print("PASS: reachability — process_one → _maybe_self_delegate → spawn core, "
          "flag-gated, unconditional strip")


def test_emission_block_gated_on_flag() -> None:
    """The EMISSION side: system_prompt_for teaches the ⟦bgtask-run⟧ protocol
    ONLY when the flag is on (ship-dark). Grep-level reachability (same
    infeasibility exception as C1-B: a live model is needed to emit)."""
    src = (HERE / "adapter.py").read_text()
    assert '_bg_flag("bridge_self_delegation")' in src
    assert "⟦bgtask-run:" in src, "the emission block must document the run marker"
    print("PASS: emission-side marker protocol is flag-gated")


def main() -> int:
    tests = [
        test_parse_run_marker,
        test_flag_off_no_spawn_no_leak,
        test_flag_on_spawns_one_detached_worker,
        test_flag_on_duplicate_marker_spawns_once,
        test_self_delegation_respects_concurrency_cap,
        test_self_delegation_voice_gated_on_flag,
        test_broken_spawn_does_not_break_turn,
        test_reply_hook_wired_into_process_one,
        test_emission_block_gated_on_flag,
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
    print(f"{'ALL PASSED' if not failed else str(failed)+' FAILED'} "
          f"({len(tests)-failed}/{len(tests)})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
