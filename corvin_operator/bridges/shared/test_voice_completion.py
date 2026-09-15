#!/usr/bin/env python3
"""test_voice_completion.py — ADR-0554 Phase 0: voice summary on /task completion.

Proves the "final voice summary on a background /task completion" slice, which
ships DARK behind the `proactive_voice_completion` flag (default OFF) and uses
approach (a): the detached worker synthesizes a spoken SUMMARY at completion
time and stamps its voice_path on the durable record BEFORE mark_done, so
whichever poller delivers attaches it — poller-independent.

What is proven here (real record→delivery transport, NOT a direct call into the
TTS helpers):

  1. E2E — flag on: register(want_voice) → attach_voice(path) → mark_done →
     deliver_ready → the OUTBOX envelope carries voice_path. (mutation target)
  2. Poller independence: the SAME stored voice_path is attached whether
     deliver_ready runs WITH a synthesize_voice callback (adapter loop) or
     WITHOUT one (bg_monitor timer), and the callback is NOT re-invoked when a
     stored path already exists.
  3. Ship-dark: want_voice=False (flag off) ⇒ text-only, NO voice_path key,
     byte-identical to before the feature existed.
  4. Fail-open: a producer whose TTS returned None never calls attach_voice ⇒
     text is still delivered; and a synthesize_voice callback that RAISES still
     delivers the text.
  5. Worker reachability + fail-open through the REAL detached worker subprocess:
     a want_voice completion runs bg_task_worker to mark_done and is delivered
     as text, with the voice block reachable and never blocking delivery.
  6. Worker success path: bg_task_worker._maybe_voice, with only the (CI-
     undriveable) TTS engine stubbed, drives the real attach_voice + deliver_ready
     transport so the envelope carries the synthesized voice_path.
  7. Handler wiring: the real `/task` handler (adapter.process_one) sets
     want_voice on BOTH the record and the worker spec when the flag+preference
     allow, and leaves them false when the flag is off.

Run: python3 operator/bridges/shared/test_voice_completion.py
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
WORKER = HERE / "bg_task_worker.py"
_BASE_PATH = "/usr/bin:/bin:/usr/local/bin:" + os.path.expanduser("~/.local/bin")
sys.path.insert(0, str(HERE))


def _fresh_cn(home: Path):
    os.environ["CORVIN_HOME"] = str(home)
    sys.modules.pop("completion_notify", None)
    import completion_notify  # type: ignore
    return completion_notify


# ── 1. E2E: flag on ⇒ stored voice_path reaches the outbox envelope ──────────


def test_flag_on_voice_path_reaches_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        home, outbox = Path(td) / "home", Path(td) / "outbox"
        cn = _fresh_cn(home)
        tid = cn.register(channel="discord", chat_id="987654321", sender="u1",
                          tenant_id="acme", label="nightly backtest",
                          want_voice=True)
        # Approach (a): the worker attaches the voice_path BEFORE mark_done.
        fake = str(Path(td) / "note.ogg")
        assert cn.attach_voice(tid, fake) is True
        assert cn.mark_done(tid, text="Sharpe 1.9 — report attached.", ok=True)

        # Real delivery path — NO callback (bg_monitor-style poller).
        assert cn.deliver_ready(outbox) == 1
        env = json.loads(next(outbox.glob("cn_*.json")).read_text())
        assert env["voice_path"] == fake, env            # <-- mutation target
        assert "Sharpe 1.9" in env["text"]               # text still present
        assert env["channel"] == "discord"
    print("PASS: flag on — completion envelope carries the stored voice_path "
          "(text intact), delivered with no callback")


# ── 2. Poller independence: with AND without a synthesize_voice callback ─────


def test_poller_independence_stored_path_wins() -> None:
    with tempfile.TemporaryDirectory() as td:
        home = Path(td) / "home"
        cn = _fresh_cn(home)
        fake = str(Path(td) / "note.ogg")

        # (a) bg_monitor-style: deliver_ready WITHOUT a callback.
        obx_a = Path(td) / "outbox_a"
        t1 = cn.register(channel="discord", chat_id="c1", sender="u1",
                         want_voice=True)
        cn.attach_voice(t1, fake)
        cn.mark_done(t1, text="done A", ok=True)
        assert cn.deliver_ready(obx_a) == 1
        env_a = json.loads(next(obx_a.glob("cn_*.json")).read_text())
        assert env_a["voice_path"] == fake, env_a

        # (b) adapter-loop-style: deliver_ready WITH a callback — the stored path
        # must WIN and the callback must NOT be re-invoked (no double synthesis).
        obx_b = Path(td) / "outbox_b"
        cb_calls: list[str] = []

        def _cb(text: str) -> str:
            cb_calls.append(text)
            return "/tmp/should-not-be-used.ogg"

        t2 = cn.register(channel="discord", chat_id="c2", sender="u1",
                         want_voice=True)
        cn.attach_voice(t2, fake)
        cn.mark_done(t2, text="done B", ok=True)
        assert cn.deliver_ready(obx_b, synthesize_voice=_cb) == 1
        env_b = json.loads(next(obx_b.glob("cn_*.json")).read_text())
        assert env_b["voice_path"] == fake, env_b
        assert cb_calls == [], "callback must not run when a stored path exists"
    print("PASS: poller-independent — both pollers attach the stored voice_path; "
          "the callback never overrides it")


# ── 3. Ship-dark: flag off ⇒ text-only, byte-identical ──────────────────────


def test_flag_off_is_text_only() -> None:
    with tempfile.TemporaryDirectory() as td:
        home, outbox = Path(td) / "home", Path(td) / "outbox"
        cn = _fresh_cn(home)
        # Flag off ⇒ register never sets want_voice, worker never attaches.
        tid = cn.register(channel="discord", chat_id="c9", sender="u1",
                          label="task", want_voice=False)
        cn.mark_done(tid, text="plain result", ok=True)
        assert cn.deliver_ready(outbox) == 1
        env = json.loads(next(outbox.glob("cn_*.json")).read_text())
        assert "voice_path" not in env, env
        assert "plain result" in env["text"]
    print("PASS: ship-dark — flag off delivers text only, no voice_path key")


# ── 4. Fail-open: TTS None / callback raises ⇒ text still delivered ──────────


def test_fail_open_text_always_delivered() -> None:
    with tempfile.TemporaryDirectory() as td:
        home = Path(td) / "home"
        cn = _fresh_cn(home)

        # (a) worker TTS returned None ⇒ attach_voice never called; want_voice
        #     still True on the record. deliver_ready (no callback) ⇒ text-only.
        obx_a = Path(td) / "obx_a"
        t1 = cn.register(channel="discord", chat_id="c1", sender="u1",
                         want_voice=True)
        cn.mark_done(t1, text="result one", ok=True)
        assert cn.deliver_ready(obx_a) == 1
        env_a = json.loads(next(obx_a.glob("cn_*.json")).read_text())
        assert "voice_path" not in env_a, env_a
        assert "result one" in env_a["text"]

        # (b) a synthesize_voice callback that RAISES must never block the
        #     already-ready text delivery.
        obx_b = Path(td) / "obx_b"
        t2 = cn.register(channel="discord", chat_id="c2", sender="u1",
                         want_voice=True)
        cn.mark_done(t2, text="result two", ok=True)

        def _boom(_text: str) -> str:
            raise RuntimeError("TTS engine exploded")

        assert cn.deliver_ready(obx_b, synthesize_voice=_boom) == 1
        env_b = json.loads(next(obx_b.glob("cn_*.json")).read_text())
        assert "voice_path" not in env_b, env_b
        assert "result two" in env_b["text"]
    print("PASS: fail-open — TTS None and a raising callback both still deliver "
          "the text (no block, no voice_path)")


# ── 5. Real detached worker: reachable + fail-open (TTS off via env hook) ────


def test_worker_want_voice_reachable_and_fail_open() -> None:
    with tempfile.TemporaryDirectory() as td:
        home, outbox = Path(td) / "home", Path(td) / "outbox"
        env = os.environ.copy()
        env["PATH"] = env.get("PATH") or _BASE_PATH
        env["CORVIN_HOME"] = str(home)
        env["ADAPTER_OUTBOX"] = str(outbox)
        env["ADAPTER_FAKE_CLAUDE"] = "1"
        env["ADAPTER_FAKE_DELAY"] = "0"
        # Keep the worker's voice block deterministic + offline: the same hook
        # the live turn honours short-circuits real TTS so the block runs but
        # attaches nothing → proves it is reachable AND never blocks delivery.
        env["ADAPTER_DISABLE_VOICE"] = "1"

        reg = (f"import sys; sys.path.insert(0, r'{HERE}'); "
               "import completion_notify as cn; "
               "cn.register('bgt_voice', channel='signal', chat_id='+49150', "
               "sender='+49150', label='voice job', want_voice=True)")
        r = subprocess.run([sys.executable, "-c", reg], env=env,
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr

        spec = Path(td) / "spec.json"
        spec.write_text(json.dumps({
            "task_id": "bgt_voice", "instruction": "summarise the logs",
            "channel": "signal", "chat_key": "+49150", "want_voice": True,
        }))
        w = subprocess.run([sys.executable, str(WORKER), str(spec)], env=env,
                           capture_output=True, text=True, timeout=60)
        assert w.returncode == 0, f"worker failed: {w.stderr}"

        dl = (f"import sys; sys.path.insert(0, r'{HERE}'); "
              "import completion_notify as cn; "
              f"print(cn.deliver_ready(r'{outbox}'))")
        d = subprocess.run([sys.executable, "-c", dl], env=env,
                           capture_output=True, text=True)
        assert d.returncode == 0, d.stderr
        assert d.stdout.strip().endswith("1"), d.stdout
        env_json = json.loads(next(outbox.glob("cn_*.json")).read_text())
        assert "summarise the logs" in env_json["text"], env_json["text"]
        # TTS disabled ⇒ no voice_path, but the completion was still delivered.
        assert "voice_path" not in env_json, env_json
    print("PASS: real detached worker with want_voice runs to a delivered text "
          "completion (voice block reachable, never blocks)")


# ── 6. Worker success path: _maybe_voice → attach_voice → delivered envelope ─


def test_worker_maybe_voice_attaches_voice_path() -> None:
    with tempfile.TemporaryDirectory() as td:
        home, outbox = Path(td) / "home", Path(td) / "outbox"
        cn = _fresh_cn(home)
        import bg_task_worker as w  # type: ignore
        import adapter as _ad  # type: ignore

        # Stub ONLY the (CI-undriveable) TTS engine; the real attach_voice +
        # deliver_ready transport still runs.
        fake_ogg = Path(td) / "voice.ogg"
        fake_ogg.write_bytes(b"OggS-fake")
        _orig_bvs = _ad.build_voice_summary
        _orig_svn = _ad.synthesize_voice_note
        _ad.build_voice_summary = lambda text, **kw: "Kurzfassung des Ergebnisses."
        _ad.synthesize_voice_note = lambda spoken, **kw: fake_ogg
        try:
            tid = cn.register(channel="discord", chat_id="c7", sender="u1",
                              want_voice=True)
            attached = w._maybe_voice(cn, tid, want_voice=True,
                                      text="A long result about the logs.")
            assert attached is True, "worker _maybe_voice must attach the path"
            cn.mark_done(tid, text="A long result about the logs.", ok=True)
            assert cn.deliver_ready(outbox) == 1
            env = json.loads(next(outbox.glob("cn_*.json")).read_text())
            assert env["voice_path"] == str(fake_ogg), env
        finally:
            _ad.build_voice_summary = _orig_bvs
            _ad.synthesize_voice_note = _orig_svn
    print("PASS: worker _maybe_voice synthesizes + attaches → envelope carries "
          "the voice_path (only the TTS engine stubbed)")


# ── 7. /task handler wiring: want_voice gated by flag + preference ──────────


def _fresh_adapter(env_overrides: dict):
    os.environ["CORVIN_OS_ENGINE"] = "claude_code"
    xdg = Path(tempfile.mkdtemp(prefix="voicecomp-xdg-"))
    (xdg / "corvin-voice").mkdir(parents=True)
    (xdg / "corvin-voice" / "profile.json").write_text("{}")
    os.environ["XDG_CONFIG_HOME"] = str(xdg)
    sys.modules.pop("profile", None)
    for k, v in env_overrides.items():
        os.environ[k] = v
    sys.modules.pop("adapter", None)
    sys.path.insert(0, str(HERE))
    import adapter  # type: ignore
    adapter._house_rules_classifier = lambda task, rules, auth, **_kw: ("", 1.0, "ok")
    return adapter


def _run_task_and_read_records(*, flag_on: bool):
    base = Path(tempfile.mkdtemp(prefix="voicecomp-"))
    inbox, outbox, processed, home = (base / "inbox", base / "outbox",
                                      base / "processed", base / "home")
    for p in (inbox, outbox, processed, home):
        p.mkdir(parents=True)
    _REAL_POPEN = subprocess.Popen
    try:
        adapter = _fresh_adapter({
            "ADAPTER_INBOX": str(inbox), "ADAPTER_OUTBOX": str(outbox),
            "ADAPTER_PROCESSED": str(processed), "CORVIN_HOME": str(home),
        })
        captured = {}

        class _FakePopen:
            def __init__(self, args, **kw):
                captured["args"] = args

        adapter.subprocess.Popen = _FakePopen
        adapter._bg_flag = (lambda fid: fid == "proactive_voice_completion") if flag_on \
            else (lambda fid: False)
        # Voice preference "always" (the default) allows voice.
        adapter.load_settings = lambda: {"voice_summary_mode": "always"}

        env = {"id": "msg-v1", "channel": "sandbox-task", "from": "u42",
               "chat_id": "chan-99", "text": "/task crunch the numbers", "ts": 0}
        f = inbox / "msg-v1.json"
        f.write_text(json.dumps(env))
        adapter.process_one(f, settings={"whitelist": ["u42"]})

        rec = json.loads(next((home / "pending_notifications").glob("*.json")).read_text())
        spec = json.loads(Path(captured["args"][2]).read_text())
        Path(captured["args"][2]).unlink(missing_ok=True)
        return rec, spec
    finally:
        try:
            adapter.subprocess.Popen = _REAL_POPEN  # noqa: F821
        except Exception:
            pass
        shutil.rmtree(base, ignore_errors=True)


def test_task_handler_sets_want_voice_when_flag_on() -> None:
    rec, spec = _run_task_and_read_records(flag_on=True)
    assert rec["want_voice"] is True, rec
    assert spec["want_voice"] is True, spec
    print("PASS: /task handler sets want_voice on record + spec when flag on")


def test_task_handler_want_voice_false_when_flag_off() -> None:
    rec, spec = _run_task_and_read_records(flag_on=False)
    assert rec["want_voice"] is False, rec
    assert spec.get("want_voice") is False, spec
    print("PASS: /task handler leaves want_voice false when flag off (ship-dark)")


def main() -> int:
    tests = [
        test_flag_on_voice_path_reaches_envelope,
        test_poller_independence_stored_path_wins,
        test_flag_off_is_text_only,
        test_fail_open_text_always_delivered,
        test_worker_want_voice_reachable_and_fail_open,
        test_worker_maybe_voice_attaches_voice_path,
        test_task_handler_sets_want_voice_when_flag_on,
        test_task_handler_want_voice_false_when_flag_off,
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
