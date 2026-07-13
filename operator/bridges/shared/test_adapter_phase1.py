#!/usr/bin/env python3
"""test_adapter_phase1.py — Tests for die Phase-1 Härtungen:

1. Poison-Quarantäne: wenn process_one mitten im Durchlauf weft, landet die
   Inbox-file in processed/poison/ + sidecar .err mit Traceback.
2. .env-Parser: tolerant gegen Kommentare, Quotes, export-Prefix, Whitespace.
3. system_prompt_for: nennt den richtigen Channel-Namen im System-Prompt;
   Backwards-Compat-Alias WA_SYSTEM_PROMPT zeigt weiter auf den WhatsApp-value.
4. Streaming-Recursion-Counter: max 1 Retry bei dauerhaftem Session-Error.

Runs complete in /tmp/, without externe Abhängigkeiten.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def _set_sandbox(tmp: Path) -> None:
    """Setzt INBOX/OUTBOX/PROCESSED-Env so dass adapter im /tmp arbeitet."""
    os.environ["ADAPTER_INBOX"] = str(tmp / "inbox")
    os.environ["ADAPTER_OUTBOX"] = str(tmp / "outbox")
    os.environ["ADAPTER_PROCESSED"] = str(tmp / "processed")
    for d in ("inbox", "outbox", "processed"):
        (tmp / d).mkdir(parents=True, exist_ok=True)


def test_poison_quarantine() -> None:
    _section("poison-quarantine")
    tmp = Path(tempfile.mkdtemp(prefix="adapter-phase1-"))
    try:
        _set_sandbox(tmp)
        os.environ["ADAPTER_FAKE_CLAUDE"] = "1"
        os.environ["ADAPTER_DISABLE_VOICE"] = "1"

        # Force fresh import so module picks up the env vars.
        for mod in list(sys.modules):
            if mod == "adapter":
                del sys.modules[mod]
        import adapter  # type: ignore

        # Dispatcher braucht den Executor; main() startet ihn — we bauen ihn
        # direkt for den Test.
        from concurrent.futures import ThreadPoolExecutor
        adapter._executor = ThreadPoolExecutor(max_workers=2)

        # process_one durch eine Version erset, die explodiert.
        original_process_one = adapter.process_one

        def boom(inbox_file: Path, settings: dict) -> None:
            raise RuntimeError("simulated explosion mid-processing")

        adapter.process_one = boom  # type: ignore

        msg = {"id": "poisontest_01", "channel": "telegram",
               "from": "user-1", "chat_id": 42, "text": "hi"}
        inbox_path = Path(adapter.INBOX) / "poisontest_01.json"
        inbox_path.write_text(json.dumps(msg))

        adapter.submit_inbox_item(inbox_path, {})
        adapter._executor.shutdown(wait=True)

        # Restore for any later tests in same process.
        adapter.process_one = original_process_one  # type: ignore

        # Inbox-file darf NICHT mehr im inbox liegen.
        assert not inbox_path.exists(), \
            f"inbox file still present — would loop forever: {inbox_path}"
        # Muss in processed/poison/ liegen, mit sidecar .err.
        poison_dir = Path(adapter.PROCESSED) / "poison"
        moved = poison_dir / "poisontest_01.json"
        err = poison_dir / "poisontest_01.json.err"
        assert moved.exists(), f"poison file not at expected path: {moved}"
        assert err.exists(), f"err sidecar not written: {err}"
        err_text = err.read_text()
        assert "simulated explosion" in err_text, \
            f"traceback didn't capture the original exception: {err_text[:200]}"
        print(f"PASS: poison file moved to {moved.relative_to(tmp)}")
        print(f"PASS: traceback sidecar written ({len(err_text)} chars)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        for k in ("ADAPTER_INBOX", "ADAPTER_OUTBOX", "ADAPTER_PROCESSED",
                  "ADAPTER_FAKE_CLAUDE", "ADAPTER_DISABLE_VOICE"):
            os.environ.pop(k, None)


def test_process_one_non_dict_top_level_json() -> None:
    """Blind spot: process_one()'s `json.loads(inbox_file.read_text())` is
    only guarded against `json.JSONDecodeError`. A syntactically valid but
    non-object top-level JSON payload — an int, a bare string, or a list —
    parses fine, and the very next line, `msg.get("id")`, raises an
    unguarded AttributeError. There is a second internal call site for
    process_one() besides submit_inbox_item(); this test calls it directly
    to prove process_one() itself has no defense of its own against the
    non-dict shape (today's safety net, if any, lives strictly in the
    caller — see test_submit_inbox_item_non_dict_json_is_not_quarantined
    below for why that caller-side net is weaker than it looks)."""
    _section("process_one-non-dict-top-level-json")
    tmp = Path(tempfile.mkdtemp(prefix="adapter-nondict-"))
    try:
        _set_sandbox(tmp)
        os.environ["ADAPTER_FAKE_CLAUDE"] = "1"
        os.environ["ADAPTER_DISABLE_VOICE"] = "1"
        for mod in list(sys.modules):
            if mod == "adapter":
                del sys.modules[mod]
        import adapter  # type: ignore

        for label, payload in (
            ("int", "42"),
            ("string", '"just a string"'),
            ("list", "[1, 2, 3]"),
        ):
            inbox_path = Path(adapter.INBOX) / f"nondict_{label}.json"
            inbox_path.write_text(payload)
            try:
                adapter.process_one(inbox_path, {})
            except AttributeError as e:
                print(f"PASS(documents bug): process_one({label}) raised "
                      f"unguarded AttributeError: {e}")
            else:
                raise AssertionError(
                    f"process_one({label!r}) did NOT raise — has an "
                    f"isinstance(msg, dict) guard been added to "
                    f"process_one()? If so, update this test to assert "
                    f"graceful quarantine/skip instead of documenting the "
                    f"crash."
                )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        for k in ("ADAPTER_INBOX", "ADAPTER_OUTBOX", "ADAPTER_PROCESSED",
                  "ADAPTER_FAKE_CLAUDE", "ADAPTER_DISABLE_VOICE"):
            os.environ.pop(k, None)


def test_submit_inbox_item_non_dict_json_is_not_quarantined() -> None:
    """Blind spot, worse than the plain AttributeError above: the real
    production entry point, submit_inbox_item(), calls _route_key() and
    _peek_side_channel() on the raw inbox file BEFORE _runner()'s
    try/except is even constructed. Both helpers only catch
    (OSError, json.JSONDecodeError) around their own json.loads() — the
    following msg.get(...) call raises AttributeError for a non-dict
    top-level JSON payload, and that exception propagates all the way out
    of submit_inbox_item() itself, never touching process_one()'s poison-
    quarantine machinery at all. Only main()'s generic per-tick
    `except Exception as e: log(f"loop error: {e}")` — two frames further
    out — would catch it, and that handler neither quarantines nor deletes
    the file. Worse, submit_inbox_item() had already written a pre-submit
    `_in_flight[msg_id] = (time.time(), None)` bookkeeping entry just
    before the crash; that entry is only ever popped in _runner()'s
    `finally`, which never runs here. So the poison file survives on disk,
    is never quarantined, and every subsequent poll tick silently no-ops
    it (`if msg_id in _in_flight: return`) until IN_FLIGHT_TTL (default 1h)
    reaps the stale entry — at which point the identical crash recurs.
    Net effect: a single malformed inbox message becomes a permanently
    stuck, periodically recrashing poison pill that is never quarantined."""
    _section("submit_inbox_item-non-dict-json-poison-pill")
    tmp = Path(tempfile.mkdtemp(prefix="adapter-poisonpill-"))
    try:
        _set_sandbox(tmp)
        os.environ["ADAPTER_FAKE_CLAUDE"] = "1"
        os.environ["ADAPTER_DISABLE_VOICE"] = "1"
        for mod in list(sys.modules):
            if mod == "adapter":
                del sys.modules[mod]
        import adapter  # type: ignore
        from concurrent.futures import ThreadPoolExecutor
        adapter._executor = ThreadPoolExecutor(max_workers=2)
        adapter._sidechannel_executor = ThreadPoolExecutor(max_workers=2)

        inbox_path = Path(adapter.INBOX) / "poisonpill_01.json"
        inbox_path.write_text(json.dumps([1, 2, 3]))

        raised = None
        try:
            adapter.submit_inbox_item(inbox_path, {})
        except AttributeError as e:
            raised = e
        else:
            raise AssertionError(
                "submit_inbox_item() did NOT raise for a non-dict "
                "top-level JSON payload — has _route_key()/"
                "_peek_side_channel() grown an isinstance(msg, dict) "
                "guard? If so, update this test to assert the file is "
                "quarantined instead of documenting the crash."
            )
        print(f"PASS(documents bug): submit_inbox_item raised unguarded "
              f"{type(raised).__name__}: {raised}")

        # Worse than a caught-and-quarantined poison file: the file is
        # still sitting in inbox/, unmoved and undeleted.
        assert inbox_path.exists(), (
            "inbox file vanished — was it (silently) quarantined after "
            "all? If a fix landed, this documents SAFE behavior now; "
            "update this test to assert that positively."
        )
        poison_dir = Path(adapter.PROCESSED) / "poison"
        assert not (poison_dir / inbox_path.name).exists(), (
            "poison file unexpectedly present in processed/poison/ — the "
            "crash path must have grown a quarantine step; good news, but "
            "update this test to assert that positively instead of "
            "documenting its absence."
        )
        print("PASS(documents bug): poison file left in place in inbox/, "
              "never quarantined")

        # And the pre-submit _in_flight bookkeeping entry is never popped
        # (that only happens in _runner's finally, which never ran) — so
        # it silently no-ops every following poll tick.
        assert "poisonpill_01" in adapter._in_flight, (
            "the pre-submit _in_flight entry was already cleared — either "
            "the crash path changed, or unrelated cleanup ran; verify "
            "before trusting this assertion"
        )
        _ts, fut = adapter._in_flight["poisonpill_01"]
        assert fut is None, (
            "a Future got attached despite the crash happening before "
            "pool.submit() ran — investigate before trusting this test"
        )
        print("PASS(documents bug): stale _in_flight entry left dangling "
              "with fut=None — every future poll tick will silently "
              "no-op this msg_id until IN_FLIGHT_TTL reaps it, at which "
              "point the identical crash recurs")

        # Cleanup so this doesn't leak into later tests in the same
        # process (fresh adapter import gives a fresh _in_flight dict, but
        # be defensive anyway).
        adapter._in_flight.pop("poisonpill_01", None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        for k in ("ADAPTER_INBOX", "ADAPTER_OUTBOX", "ADAPTER_PROCESSED",
                  "ADAPTER_FAKE_CLAUDE", "ADAPTER_DISABLE_VOICE"):
            os.environ.pop(k, None)


def test_env_parser() -> None:
    _section("env-parser")
    # Re-import auch hier, damit we die function sehen.
    for mod in list(sys.modules):
        if mod == "adapter":
            del sys.modules[mod]
    import adapter  # type: ignore

    tmp = Path(tempfile.mkdtemp(prefix="env-test-"))
    try:
        env_file = tmp / ".env"
        env_file.write_text(
            "# this is a comment\n"
            "  # indented comment\n"
            "\n"
            "OTHER_KEY=should-not-match\n"
            "OPENAI_API_KEY=plain-value\n"
            "QUOTED_DOUBLE=\"double-quoted\"\n"
            "QUOTED_SINGLE='single-quoted'\n"
            "  WITH_LEADING_WS=padded\n"
            "WITH_SPACES_AROUND_EQ = spaced\n"
            "export EXPORTED_KEY=exported-val\n"
        )
        cases = [
            ("OPENAI_API_KEY",       "plain-value"),
            ("QUOTED_DOUBLE",        "double-quoted"),
            ("QUOTED_SINGLE",        "single-quoted"),
            ("WITH_LEADING_WS",      "padded"),
            ("WITH_SPACES_AROUND_EQ", "spaced"),
            ("EXPORTED_KEY",         "exported-val"),
            ("MISSING_KEY",          None),
        ]
        for key, expected in cases:
            got = adapter._load_env_value(key, env_file)
            assert got == expected, \
                f"{key}: got {got!r}, expected {expected!r}"
            print(f"PASS: {key!r} → {got!r}")

        # file fehlt → None.
        assert adapter._load_env_value("ANY", tmp / "nope.env") is None
        print("PASS: missing file → None")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_system_prompt_per_channel() -> None:
    _section("system-prompt-per-channel")
    for mod in list(sys.modules):
        if mod == "adapter":
            del sys.modules[mod]
    import adapter  # type: ignore

    expected = {
        "whatsapp": "WhatsApp",
        "telegram": "Telegram",
        "discord":  "Discord",
        "slack":    "Slack",
    }
    for channel, label in expected.items():
        prompt = adapter.system_prompt_for(channel)
        assert label in prompt, \
            f"{channel}: '{label}' not in prompt: {prompt[:120]}"
        # No cross-contamination — the OTHER labels must NOT show up.
        for other_ch, other_label in expected.items():
            if other_ch == channel:
                continue
            assert other_label not in prompt, \
                f"{channel}: foreign label {other_label!r} leaked into prompt"
        print(f"PASS: channel={channel} → prompt mentions {label!r} and nothing else")

    # Backwards-compat alias bleibt der WhatsApp-value.
    assert adapter.WA_SYSTEM_PROMPT == adapter.system_prompt_for("whatsapp")
    print("PASS: WA_SYSTEM_PROMPT alias still resolves to WhatsApp prompt")

    # Clarification clause must be present in every channel's prompt — it
    # is what stops Claude from guessing missing details and starting a
    # task that "goes wrong" because of unstated assumptions. The prompt
    # has been translated between DE and EN over time, so check for either
    # language's marker rather than tying the test to a specific wording.
    for channel in expected:
        prompt = adapter.system_prompt_for(channel)
        assert ("Bei Unklarheiten" in prompt
                or "When information is missing" in prompt), \
            f"{channel}: clarification clause missing"
        assert ("frag aktiv back" in prompt
                or "ask back" in prompt), \
            f"{channel}: ask-back wording missing"
        assert ("irreversibl" in prompt
                or "irreversible" in prompt), \
            f"{channel}: irreversible-action guard missing"
    print("PASS: clarification + irreversible-guard clauses present in every channel")


def test_streaming_recursion_counter() -> None:
    _section("streaming-recursion-counter")
    for mod in list(sys.modules):
        if mod == "adapter":
            del sys.modules[mod]
    import adapter  # type: ignore

    # L44 (ADR-0143): stub the Tier-1 acceptable-use classifier to a benign
    # clear. This test monkeypatches adapter.subprocess.Popen and counts calls;
    # the gate's `claude -p` classifier goes through subprocess.run (→ Popen),
    # which would both inflate the count and fail-closed (escalate) before the
    # engine retry path runs. Gate coverage lives in test_house_rules.py.
    adapter._house_rules_classifier = lambda task, rules, auth, **_kw: ("", 1.0, "test-benign")

    tmp = Path(tempfile.mkdtemp(prefix="recursion-"))
    try:
        # This test mocks adapter.subprocess.Popen and counts calls to verify
        # the ClaudeCode retry-bounding logic. Without a real `claude` CLI on
        # PATH (any CI runner), ADR-0159 engine auto-detect silently falls
        # back to hermes instead — a different code path that never touches
        # subprocess.Popen the same way, so the mock sees 0 calls instead of
        # the expected 2. Pin the engine so auto-detect is bypassed.
        os.environ["CORVIN_OS_ENGINE"] = "claude_code"
        # _session_dir braucht XDG_CACHE_HOME, otherwise fasst es ~/.cache an.
        os.environ["XDG_CACHE_HOME"] = str(tmp)
        # Wichtig: modulee-constant muss nachgezogen werden.
        adapter.SESSIONS_ROOT = Path(tmp) / "corvin-voice" / "sessions"

        # We erset subprocess.Popen so, dass der Streaming-Loop sofort
        # einen result-Event mit is_error=True und "session" im Text erzeugt.
        # Damit greift die Retry-Logik.
        call_count = {"n": 0}

        class FakeProc:
            returncode = 1
            pid = 12345

            def __init__(self):
                # Eine result-Zeile mit error.
                self._lines = iter([
                    json.dumps({"type": "result",
                                "is_error": True,
                                "result": "fatal: invalid session token"}) + "\n"
                ])
                self.stdout = self
                self.stderr = None
                # Layer 13: adapter writes the initial prompt as stream-json
                # to stdin. A StringIO is a sufficient stand-in for the test —
                # the FakeProc's stdout still drives the result-event flow.
                import io as _io
                self.stdin = _io.StringIO()

            def __iter__(self):
                return self._lines

            def wait(self):
                return self.returncode

            def kill(self):
                pass

            def read(self):
                return ""

        original_popen = adapter.subprocess.Popen

        def fake_popen(*args, **kwargs):
            call_count["n"] += 1
            return FakeProc()

        adapter.subprocess.Popen = fake_popen  # type: ignore

        # Workdir mit existing session-Marker so dass has_session=True.
        wd = adapter._session_dir("telegram", "user42")
        (wd / ".session_started").touch()

        try:
            result = adapter.call_claude_streaming(
                "hello", channel="telegram", chat_key="user42",
            )
        finally:
            adapter.subprocess.Popen = original_popen  # type: ignore

        # Erwartet: 2 Popen-Aufrufe (Initial + 1 Retry) — NICHT mehr.
        assert call_count["n"] == 2, \
            f"recursion not bounded — got {call_count['n']} popen calls"
        # Result must be a non-empty user-visible error string (no silent loop).
        # The adapter returns English messages — accept both "failed" and the
        # original German "fehlgeschlagen" so the test survives future i18n changes.
        assert result and (
            "failed" in result.lower()
            or "fehlgeschlagen" in result.lower()
            or "fatal" in result.lower()
        ), f"unexpected return: {result!r}"
        print(f"PASS: bounded to 2 popen calls (1 initial + 1 retry), got {call_count['n']}")
        print(f"PASS: returns user-visible error string after retry exhausted")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        os.environ.pop("XDG_CACHE_HOME", None)
        os.environ.pop("CORVIN_OS_ENGINE", None)


def main() -> int:
    test_poison_quarantine()
    test_process_one_non_dict_top_level_json()
    test_submit_inbox_item_non_dict_json_is_not_quarantined()
    test_env_parser()
    test_system_prompt_per_channel()
    test_streaming_recursion_counter()
    print("\nALL CHECKS PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
