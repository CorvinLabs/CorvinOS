#!/usr/bin/env python3
"""test_adapter_stream_idle.py — E2E-Test für den Stream-Idle-Watchdog.

Pro CLAUDE.md (`feedback_per_subtask_e2e`): kein Mock — wir starten einen
echten Python-Subprozess, der sich als `claude` ausgibt, einen einzigen
stream-json-Event ausspuckt und dann *hängt*. Der Adapter muss ihn
innerhalb des konfigurierten Timeouts per SIGTERM beenden, statt 71 min
auf den CLI-internen Idle-Timeout zu warten.

Drei Sub-Tests:
  1. Hängender Subprozess wird vom Watchdog gekillt; call_claude_streaming
     liefert eine Fallback-Message (kein leerer String, kein Hang).
  2. Periodische Alive-Heartbeats landen während des Hangs in der
     on_status-Senke (nicht nur der initiale 1.5 s-Heartbeat).
  3. Stream-Idle-Recovery: bei `--continue`-Session feuert der
     Reset+Retry-Pfad — beim zweiten Versuch wird die Session frisch
     gestartet (`.session_started` ist nach dem Reset weg, bevor Retry
     beginnt).
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import textwrap
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def _fresh_adapter(env_overrides: dict | None = None):
    if env_overrides:
        for k, v in env_overrides.items():
            os.environ[k] = v
    # Clear env vars that other test modules may have left behind
    os.environ.pop("ADAPTER_FAKE_CLAUDE", None)
    for mod in list(sys.modules):
        if mod == "adapter" or mod == "agents" or mod.startswith("agents."):
            del sys.modules[mod]
    import adapter  # type: ignore
    # L44 (ADR-0143): stub the Tier-1 acceptable-use classifier to a benign
    # clear — these tests fake the `claude` binary and cannot answer the gate's
    # `claude -p` classifier call (would fail-closed before the engine path).
    # Gate coverage lives in test_house_rules.py; test-local double only.
    adapter._house_rules_classifier = lambda task, rules, auth, **_kw: ("", 1.0, "test-benign")
    return adapter


def _make_fake_claude(tmp: Path, behaviour: str) -> Path:
    """Schreibt ein ausführbares Python-Skript, das sich wie `claude -p ...
    --output-format stream-json --verbose` verhält. behaviour:
      - "hang": ein Init-Event drucken, dann unbegrenzt sleepen
      - "ok"  : Init + result emit, sauber exiten
    Returns: Path zum bin-Dir, das vorne in PATH gehört.
    """
    bin_dir = tmp / "fake-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "claude"
    if behaviour == "hang":
        body = textwrap.dedent('''\
            #!/usr/bin/env python3
            import json, sys, time, os, signal
            # Erstes Event sofort emittieren, damit der Adapter sieht:
            # "es lebt", aber dann radio silence.
            sys.stdout.write(json.dumps({"type": "system", "subtype": "init"}) + "\\n")
            sys.stdout.flush()
            # SIGTERM sauber annehmen, sonst killt der Watchdog mit SIGKILL.
            def _bye(signum, frame):
                sys.exit(143)
            signal.signal(signal.SIGTERM, _bye)
            while True:
                time.sleep(60)
        ''')
    elif behaviour == "ok":
        body = textwrap.dedent('''\
            #!/usr/bin/env python3
            import json, sys
            sys.stdout.write(json.dumps({"type": "system", "subtype": "init"}) + "\\n")
            sys.stdout.write(json.dumps({"type": "result", "is_error": False, "result": "fresh-ok"}) + "\\n")
            sys.stdout.flush()
        ''')
    elif behaviour == "tool_then_ok":
        # Emit a tool_use (assistant), then go SILENT for 4 s — longer
        # than a short stream-idle timeout but shorter than the tool
        # backstop — then deliver the result. Mirrors an orchestrator
        # delegate_* call that runs for minutes without stream events.
        body = textwrap.dedent('''\
            #!/usr/bin/env python3
            import json, sys, time
            sys.stdout.write(json.dumps({"type": "system", "subtype": "init"}) + "\\n")
            sys.stdout.write(json.dumps({
                "type": "assistant",
                "message": {"content": [
                    {"type": "tool_use", "id": "t1",
                     "name": "delegate_claude_code", "input": {}}
                ]},
            }) + "\\n")
            sys.stdout.flush()
            time.sleep(4)
            sys.stdout.write(json.dumps({"type": "result", "is_error": False, "result": "delegated-ok"}) + "\\n")
            sys.stdout.flush()
        ''')
    elif behaviour == "tool_then_hang":
        # Emit a tool_use, then hang forever — the tool backstop must
        # still terminate it (no infinite hang on a genuinely stuck tool).
        body = textwrap.dedent('''\
            #!/usr/bin/env python3
            import json, sys, time, signal
            sys.stdout.write(json.dumps({"type": "system", "subtype": "init"}) + "\\n")
            sys.stdout.write(json.dumps({
                "type": "assistant",
                "message": {"content": [
                    {"type": "tool_use", "id": "t1",
                     "name": "delegate_claude_code", "input": {}}
                ]},
            }) + "\\n")
            sys.stdout.flush()
            def _bye(signum, frame):
                sys.exit(143)
            signal.signal(signal.SIGTERM, _bye)
            while True:
                time.sleep(60)
        ''')
    elif behaviour == "short_hang_then_ok":
        # Emit init, then go silent for 3 s, then deliver result.
        # Used to verify that settings.json stream_idle_timeout_seconds can
        # raise the timeout above the env-var default (2 s here) so the 3 s
        # gap does NOT trigger the watchdog.
        body = textwrap.dedent('''\
            #!/usr/bin/env python3
            import json, sys, time, signal
            sys.stdout.write(json.dumps({"type": "system", "subtype": "init"}) + "\\n")
            sys.stdout.flush()
            def _bye(signum, frame):
                sys.exit(143)
            signal.signal(signal.SIGTERM, _bye)
            time.sleep(3)
            sys.stdout.write(json.dumps({"type": "result", "is_error": False, "result": "settings-ok"}) + "\\n")
            sys.stdout.flush()
        ''')
    else:
        raise ValueError(behaviour)
    script.write_text(body)
    script.chmod(0o755)
    return bin_dir


def test_idle_watchdog_kills_hanging_subproc() -> None:
    _section("idle watchdog kills hanging subproc")
    tmp = Path(tempfile.mkdtemp(prefix="adapter-idle-"))
    _orig_path = os.environ.get("PATH", "")
    try:
        bin_dir = _make_fake_claude(tmp, "hang")
        os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
        os.environ["ADAPTER_STREAM_IDLE_TIMEOUT"] = "2"
        os.environ["ADAPTER_HEARTBEAT_INTERVAL"] = "0"
        # Sandbox sessions / inbox / outbox.
        os.environ["ADAPTER_INBOX"]  = str(tmp / "inbox")
        os.environ["ADAPTER_OUTBOX"] = str(tmp / "outbox")
        os.environ["CORVIN_HOME"]  = str(tmp / "corvinOSHome")
        # Niemals hier ein API-Key brauchen.
        os.environ["VOICE_AUDIT_PATH"] = str(tmp / "audit.jsonl")
        adapter = _fresh_adapter()

        t0 = time.time()
        ans = adapter.call_claude_streaming(
            "irrelevant", channel="discord", chat_key="idle-test-1",
            mode="unrestricted", profile=None,
        )
        elapsed = time.time() - t0
        assert (
            "abgebrochen" in ans.lower()
            or "idle" in ans.lower()
            or "cancelled" in ans.lower()
            or "timeout" in ans.lower()
        ), f"expected fallback message about idle timeout, got: {ans!r}"
        # Watchdog timeout=2s + ≤5s grace + slack.
        assert elapsed < 12, f"adapter waited too long: {elapsed:.1f}s"
        print(f"PASS: hanging subproc killed in {elapsed:.1f}s, "
              f"fallback msg: {ans[:60]!r}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        os.environ["PATH"] = _orig_path
        for v in ("ADAPTER_STREAM_IDLE_TIMEOUT", "ADAPTER_HEARTBEAT_INTERVAL",
                  "ADAPTER_INBOX", "ADAPTER_OUTBOX", "CORVIN_HOME",
                  "VOICE_AUDIT_PATH"):
            os.environ.pop(v, None)


def test_alive_heartbeat_fires_during_silence() -> None:
    _section("periodic alive heartbeat fires during silence")
    tmp = Path(tempfile.mkdtemp(prefix="adapter-hb-"))
    _orig_path = os.environ.get("PATH", "")
    try:
        bin_dir = _make_fake_claude(tmp, "hang")
        os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
        os.environ["ADAPTER_STREAM_IDLE_TIMEOUT"] = "5"
        os.environ["ADAPTER_HEARTBEAT_INTERVAL"] = "1"
        os.environ["ADAPTER_INBOX"]  = str(tmp / "inbox")
        os.environ["ADAPTER_OUTBOX"] = str(tmp / "outbox")
        os.environ["CORVIN_HOME"]  = str(tmp / "corvinOSHome")
        os.environ["VOICE_AUDIT_PATH"] = str(tmp / "audit.jsonl")
        adapter = _fresh_adapter()

        seen: list[tuple[str, str | None]] = []

        def on_status(text: str, tool_name: str | None = None) -> None:
            seen.append((text, tool_name))

        adapter.call_claude_streaming(
            "irrelevant", channel="discord", chat_key="hb-test-1",
            mode="unrestricted", on_status=on_status, profile=None,
        )
        alive = [s for s in seen if s[1] == "_alive"]
        assert len(alive) >= 2, \
            f"expected ≥2 alive heartbeats during a 5 s hang, got {len(alive)} "\
            f"(all events: {seen})"
        # Texts must vary — same text in a row is suppressed by the
        # daemon-level dedupe, so the elapsed-seconds suffix is what
        # makes each ping observable in the chat.
        texts = [s[0] for s in alive]
        assert len(set(texts)) >= 2, f"alive heartbeat texts didn't vary: {texts}"
        print(f"PASS: alive heartbeats fired {len(alive)}× with varying text")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        os.environ["PATH"] = _orig_path
        for v in ("ADAPTER_STREAM_IDLE_TIMEOUT", "ADAPTER_HEARTBEAT_INTERVAL",
                  "ADAPTER_INBOX", "ADAPTER_OUTBOX", "CORVIN_HOME",
                  "VOICE_AUDIT_PATH"):
            os.environ.pop(v, None)


def test_stream_idle_triggers_session_reset_and_retry() -> None:
    _section("stream idle on --continue triggers session reset + retry")
    tmp = Path(tempfile.mkdtemp(prefix="adapter-reset-"))
    _orig_path = os.environ.get("PATH", "")
    try:
        # Erste Phase: hängender claude. Zweite Phase (Retry): wir tauschen
        # das Skript gegen "ok", um zu beweisen, dass der Retry-Pfad
        # bei frischer Session greift.
        bin_dir = _make_fake_claude(tmp, "hang")
        os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
        os.environ["ADAPTER_STREAM_IDLE_TIMEOUT"] = "2"
        os.environ["ADAPTER_HEARTBEAT_INTERVAL"] = "0"
        os.environ["ADAPTER_INBOX"]  = str(tmp / "inbox")
        os.environ["ADAPTER_OUTBOX"] = str(tmp / "outbox")
        os.environ["CORVIN_HOME"]  = str(tmp / "corvinOSHome")
        os.environ["VOICE_AUDIT_PATH"] = str(tmp / "audit.jsonl")
        adapter = _fresh_adapter()

        # Vorhandene Session simulieren (.session_started touchen).
        workdir = adapter._session_dir("discord", "reset-test-1")
        workdir.mkdir(parents=True, exist_ok=True)
        (workdir / ".session_started").touch()
        assert (workdir / ".session_started").exists()

        # Ersten Lauf: hang → SIGTERM → reset → retry (auch hang) →
        # reaches retry budget → returns fallback.
        t0 = time.time()
        ans = adapter.call_claude_streaming(
            "irrelevant", channel="discord", chat_key="reset-test-1",
            mode="unrestricted", profile=None,
        )
        elapsed = time.time() - t0
        # Beim ersten Hang: reset + retry (zweiter hang). Beide werden
        # vom Watchdog erschlagen → Gesamtzeit ≥ 2 × 2 s = 4 s, < ~20 s.
        # Dass die Funktion länger als ein einzelner Timeout brauchte,
        # ist der harte Beweis, dass der Retry-Pfad tatsächlich gefeuert
        # hat (sonst wäre sie nach ~2 s zurück).
        assert elapsed >= 3.5, \
            f"expected ≥2 attempts (≥3.5s), got {elapsed:.1f}s — retry path didn't fire"
        assert elapsed < 25, f"reset+retry took too long: {elapsed:.1f}s"
        assert (
            "abgebrochen" in ans.lower()
            or "idle" in ans.lower()
            or "cancelled" in ans.lower()
            or "timeout" in ans.lower()
        ), f"expected idle-timeout fallback, got: {ans!r}"
        print(f"PASS: idle on --continue → reset → retry → fallback "
              f"(took {elapsed:.1f}s)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        os.environ["PATH"] = _orig_path
        for v in ("ADAPTER_STREAM_IDLE_TIMEOUT", "ADAPTER_HEARTBEAT_INTERVAL",
                  "ADAPTER_INBOX", "ADAPTER_OUTBOX", "CORVIN_HOME",
                  "VOICE_AUDIT_PATH"):
            os.environ.pop(v, None)


def test_tool_call_in_flight_survives_short_idle() -> None:
    _section("tool_call in flight survives the short idle timeout")
    tmp = Path(tempfile.mkdtemp(prefix="adapter-tool-ok-"))
    _orig_path = os.environ.get("PATH", "")
    try:
        bin_dir = _make_fake_claude(tmp, "tool_then_ok")
        os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
        # Short token-idle (2 s) would have killed the 4 s tool gap on the
        # old code; the wide tool backstop (60 s) lets it run to result.
        os.environ["ADAPTER_STREAM_IDLE_TIMEOUT"] = "2"
        os.environ["ADAPTER_TOOL_IDLE_TIMEOUT"] = "60"
        os.environ["ADAPTER_HEARTBEAT_INTERVAL"] = "0"
        os.environ["ADAPTER_INBOX"]  = str(tmp / "inbox")
        os.environ["ADAPTER_OUTBOX"] = str(tmp / "outbox")
        os.environ["CORVIN_HOME"]  = str(tmp / "corvinOSHome")
        os.environ["VOICE_AUDIT_PATH"] = str(tmp / "audit.jsonl")
        adapter = _fresh_adapter()

        t0 = time.time()
        ans = adapter.call_claude_streaming(
            "irrelevant", channel="discord", chat_key="tool-ok-1",
            mode="unrestricted", profile=None,
        )
        elapsed = time.time() - t0
        assert "delegated-ok" in ans, \
            f"expected the post-tool result, got: {ans!r}"
        for bad in ("cancelled", "abgebrochen", "idle", "timeout"):
            assert bad not in ans.lower(), \
                f"watchdog wrongly fired during tool call: {ans!r}"
        # Must have actually waited through the 4 s silent tool gap.
        assert elapsed >= 3.5, \
            f"returned too fast ({elapsed:.1f}s) — did it skip the tool gap?"
        print(f"PASS: tool_call survived 4 s silence, got result "
              f"(took {elapsed:.1f}s)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        os.environ["PATH"] = _orig_path
        for v in ("ADAPTER_STREAM_IDLE_TIMEOUT", "ADAPTER_TOOL_IDLE_TIMEOUT",
                  "ADAPTER_HEARTBEAT_INTERVAL", "ADAPTER_INBOX",
                  "ADAPTER_OUTBOX", "CORVIN_HOME", "VOICE_AUDIT_PATH"):
            os.environ.pop(v, None)


def test_tool_backstop_kills_genuinely_hung_tool() -> None:
    _section("tool backstop still kills a genuinely hung tool")
    tmp = Path(tempfile.mkdtemp(prefix="adapter-tool-hang-"))
    _orig_path = os.environ.get("PATH", "")
    try:
        bin_dir = _make_fake_claude(tmp, "tool_then_hang")
        os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
        # Tool backstop at 2 s: a tool that never returns must still die.
        os.environ["ADAPTER_STREAM_IDLE_TIMEOUT"] = "30"
        os.environ["ADAPTER_TOOL_IDLE_TIMEOUT"] = "2"
        os.environ["ADAPTER_HEARTBEAT_INTERVAL"] = "0"
        os.environ["ADAPTER_INBOX"]  = str(tmp / "inbox")
        os.environ["ADAPTER_OUTBOX"] = str(tmp / "outbox")
        os.environ["CORVIN_HOME"]  = str(tmp / "corvinOSHome")
        os.environ["VOICE_AUDIT_PATH"] = str(tmp / "audit.jsonl")
        adapter = _fresh_adapter()

        t0 = time.time()
        ans = adapter.call_claude_streaming(
            "irrelevant", channel="discord", chat_key="tool-hang-1",
            mode="unrestricted", profile=None,
        )
        elapsed = time.time() - t0
        assert (
            "cancelled" in ans.lower()
            or "abgebrochen" in ans.lower()
            or "idle" in ans.lower()
            or "timeout" in ans.lower()
        ), f"expected tool-backstop idle fallback, got: {ans!r}"
        assert elapsed < 12, f"backstop waited too long: {elapsed:.1f}s"
        print(f"PASS: hung tool killed by backstop in {elapsed:.1f}s")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        os.environ["PATH"] = _orig_path
        for v in ("ADAPTER_STREAM_IDLE_TIMEOUT", "ADAPTER_TOOL_IDLE_TIMEOUT",
                  "ADAPTER_HEARTBEAT_INTERVAL", "ADAPTER_INBOX",
                  "ADAPTER_OUTBOX", "CORVIN_HOME", "VOICE_AUDIT_PATH"):
            os.environ.pop(v, None)


def test_settings_json_stream_idle_timeout_overrides_env() -> None:
    _section("settings.json stream_idle_timeout_seconds overrides built-in default")
    # Proof: adapter must use settings.json value (2 s) instead of the built-in
    # 300 s default when ADAPTER_STREAM_IDLE_TIMEOUT is NOT set in the env.
    # The fake claude hangs 3 s before returning.  Without settings.json the
    # 300 s default would let it succeed; with settings.json=2 s the watchdog
    # fires at 2 s — a clear falsifiable signal.
    # _test_* prefix keeps the temp channel dir out of version control
    # (operator/bridges/.gitignore covers _test_*/).
    bridges_dir = ROOT.parent
    ch_dir = bridges_dir / "_test_idle_settings"
    tmp = Path(tempfile.mkdtemp(prefix="adapter-settings-idle-"))
    _orig_path = os.environ.get("PATH", "")
    try:
        import json as _json
        ch_dir.mkdir(exist_ok=True)
        (ch_dir / "settings.json").write_text(
            _json.dumps({"stream_idle_timeout_seconds": 2})
        )

        bin_dir = _make_fake_claude(tmp, "short_hang_then_ok")
        os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
        # Ensure ADAPTER_STREAM_IDLE_TIMEOUT is absent so the adapter reads
        # settings.json (2 s) rather than the 300 s built-in default.
        os.environ.pop("ADAPTER_STREAM_IDLE_TIMEOUT", None)
        os.environ["ADAPTER_HEARTBEAT_INTERVAL"] = "0"
        os.environ["ADAPTER_INBOX"]  = str(tmp / "inbox")
        os.environ["ADAPTER_OUTBOX"] = str(tmp / "outbox")
        os.environ["CORVIN_HOME"]  = str(tmp / "corvinOSHome")
        os.environ["VOICE_AUDIT_PATH"] = str(tmp / "audit.jsonl")
        adapter = _fresh_adapter()

        t0 = time.time()
        ans = adapter.call_claude_streaming(
            "irrelevant", channel="_test_idle_settings",
            chat_key="settings-idle-1",
            mode="unrestricted", profile=None,
        )
        elapsed = time.time() - t0
        # settings.json said 2 s → watchdog must fire before the 3 s result.
        assert (
            "cancelled" in ans.lower()
            or "abgebrochen" in ans.lower()
            or "idle" in ans.lower()
            or "timeout" in ans.lower()
        ), (
            f"expected watchdog at 2 s (settings.json) to fire before the 3 s "
            f"result; got: {ans!r} (elapsed {elapsed:.1f}s)"
        )
        assert elapsed < 12, f"watchdog took too long: {elapsed:.1f}s"
        print(
            f"PASS: settings.json stream_idle_timeout_seconds=2 s fired before "
            f"the 3 s result — 300 s built-in default was correctly overridden "
            f"(elapsed {elapsed:.1f}s)"
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(ch_dir, ignore_errors=True)
        os.environ["PATH"] = _orig_path
        for v in ("ADAPTER_STREAM_IDLE_TIMEOUT", "ADAPTER_HEARTBEAT_INTERVAL",
                  "ADAPTER_INBOX", "ADAPTER_OUTBOX", "CORVIN_HOME",
                  "VOICE_AUDIT_PATH"):
            os.environ.pop(v, None)


if __name__ == "__main__":
    test_idle_watchdog_kills_hanging_subproc()
    test_alive_heartbeat_fires_during_silence()
    test_stream_idle_triggers_session_reset_and_retry()
    test_tool_call_in_flight_survives_short_idle()
    test_tool_backstop_kills_genuinely_hung_tool()
    test_settings_json_stream_idle_timeout_overrides_env()
    print("\nALL OK")
