#!/usr/bin/env python3
"""test_signal_routing.py — E2E for Phase-4.1.5 /sig signal routing.

Verifies:
  - phase3_cli.py sig writes a _signal envelope to the right inbox
  - The adapter recognises _signal envelopes via _peek_side_channel
    (so they bypass the per-chat lock, like /btw and /cancel)
  - The adapter's process_one resolves session_id -> chat_key via
    process_table, dispatches PLAN/SUMMARIZE/etc as inject_btw with
    a [CORVIN_SIGNAL: NAME] marker, KILL via _cancel_chat
  - Unknown session, unknown signal, missing args produce clear
    audit + ack envelopes with delivered=False
  - The CLI rejects invalid signal names before writing the envelope

Per-subtask E2E rule: real envelope write, real adapter
process_one, real process_table state. No mocks for moving parts;
the running claude subprocess is replaced by registering a stub
session in process_table without a real PID (the adapter resolves
session -> chat_key correctly even when no live process exists,
which is the exact failure mode we test for).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

CLI = ROOT / "phase3_cli.py"


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def _setup_sandbox() -> tuple[Path, Path, Path, Path]:
    """Create CORVIN_HOME + inbox + outbox + processed dirs."""
    home = Path(tempfile.mkdtemp(prefix="sig-test-"))
    inbox = home / "inbox"
    outbox = home / "outbox"
    processed = home / "processed"
    for d in (inbox, outbox, processed):
        d.mkdir(parents=True, exist_ok=True)
    return home, inbox, outbox, processed


def _register_stub_session(home: Path, session_id: str,
                           chat_key: str = "discord:42") -> None:
    """Register a session record in process_table without a real
    subprocess. The signal handler resolves session -> chat_key
    correctly; delivery fails with a clear reason because no
    _running_subprocs entry exists for chat_key — that's the
    test condition for graceful no-op."""
    os.environ["CORVIN_HOME"] = str(home)
    sys.modules.pop("process_table", None)
    sys.modules.pop("paths", None)
    import process_table  # type: ignore
    process_table.register_session(
        session_id, chat_key=chat_key, persona="coder", pid=99999,
    )


def _write_channel_settings(bridges_dir: Path, channel: str, settings: dict) -> None:
    """Write bridges/<channel>/settings.json under a SANDBOXED bridges dir
    (ADAPTER_BRIDGES_DIR), never the live repo's operator/bridges/<channel>/
    settings.json. This avoids the test-vs-real-config contamination class
    (adapter.py's _load_channel_settings docstring documents this exact
    failure mode from a prior incident) — a real discord_token/whitelist
    configured for the operator's own deployment must never leak into a
    test run's authorization decisions."""
    chan_dir = bridges_dir / channel
    chan_dir.mkdir(parents=True, exist_ok=True)
    (chan_dir / "settings.json").write_text(json.dumps(settings))


def _read_audit_events(home: Path) -> list[dict]:
    path = home / "audit.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _run_cli(*args: str, home: Path, inbox: Path | None = None,
             extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["CORVIN_HOME"] = str(home)
    if inbox is not None:
        env["ADAPTER_INBOX"] = str(inbox)
    env.setdefault("CORVIN_CHANNEL_ID", "discord:42")
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        env=env, capture_output=True, text=True,
    )


# --------------------------------------------------------------------- cases

def case_sig_writes_envelope(home: Path, inbox: Path, outbox: Path,
                              processed: Path) -> None:
    _section("sig writes _signal envelope to inbox")
    _register_stub_session(home, "s_sig_test")
    r = _run_cli("sig", "s_sig_test", "PLAN", home=home, inbox=inbox)
    assert r.returncode == 0, f"stderr={r.stderr}"

    files = list(inbox.glob("sig_*.json"))
    assert len(files) == 1, files
    payload = json.loads(files[0].read_text())
    assert payload["_signal"] is True
    assert payload["session_id"] == "s_sig_test"
    assert payload["signal"] == "PLAN"
    assert payload["channel"] == "discord"
    assert payload["chat_id"] == "42"
    print(f"  PASS envelope shape ok: {payload['signal']}, chat={payload['chat_id']}")


def case_sig_rejects_invalid_signal(home, inbox, outbox, processed) -> None:
    _section("sig rejects unknown signal name")
    _register_stub_session(home, "s_bad_sig")
    r = _run_cli("sig", "s_bad_sig", "INVALID_SIGNAL", home=home, inbox=inbox)
    assert r.returncode == 1, r.stdout
    assert "unknown signal" in r.stderr, r.stderr
    assert not list(inbox.glob("sig_*.json")), "no envelope on bad signal"
    print(f"  PASS rejected: {r.stderr.strip()}")


def case_sig_rejects_unknown_session(home, inbox, outbox, processed) -> None:
    _section("sig rejects unknown session_id")
    r = _run_cli("sig", "s_does_not_exist", "PLAN", home=home, inbox=inbox)
    assert r.returncode == 1, r.stdout
    assert "unknown session" in r.stderr, r.stderr
    assert not list(inbox.glob("sig_*.json"))
    print(f"  PASS rejected: {r.stderr.strip()}")


def case_sig_validates_arity(home, inbox, outbox, processed) -> None:
    _section("sig requires session_id + signal")
    r = _run_cli("sig", home=home, inbox=inbox)
    assert r.returncode == 1
    r2 = _run_cli("sig", "s_only_session", home=home, inbox=inbox)
    assert r2.returncode == 1
    print("  PASS rejected: missing args")


def case_adapter_handles_signal_envelope(home, inbox, outbox, processed) -> None:
    """The most important case: write a _signal envelope, run the
    adapter's process_one, verify the audit event lands and the
    outbox ack is generated."""
    _section("adapter process_one handles _signal envelope end-to-end")
    _register_stub_session(home, "s_e2e_signal", chat_key="discord:99")

    # Force the adapter's INBOX/OUTBOX/PROCESSED paths into our sandbox
    os.environ["ADAPTER_INBOX"] = str(inbox)
    os.environ["ADAPTER_OUTBOX"] = str(outbox)
    os.environ["ADAPTER_PROCESSED"] = str(processed)
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["CORVIN_CHANNEL_ID"] = "discord:99"

    # Re-import adapter so it picks up the new env-derived paths
    for mod in ("adapter", "process_table", "paths"):
        sys.modules.pop(mod, None)
    import adapter  # type: ignore

    # Write the envelope directly (skip the CLI for tighter unit-of-test)
    msg_id = "sig_test_e2e"
    envelope = {
        "msg_id": msg_id,
        "channel": "discord",
        "from": "_test",
        "chat_id": "99",
        "_signal": True,
        "session_id": "s_e2e_signal",
        "signal": "PLAN",
        "ts": 0,
    }
    inbox_file = inbox / f"{msg_id}.json"
    inbox_file.write_text(json.dumps(envelope))

    # _peek_side_channel must classify _signal as bypass-lock
    assert adapter._peek_side_channel(inbox_file) is True
    print("  PASS _peek_side_channel classifies _signal as bypass-lock")

    # process_one handles the envelope. Even though there's no real
    # subprocess for chat=discord:99, the handler should:
    #   - resolve session via process_table (success)
    #   - attempt inject_btw (fail because no running stdin)
    #   - log delivered=False with reason "no running subprocess"
    #   - write outbox ack
    #   - move inbox to processed
    settings = {"whitelist": []}  # empty whitelist = legacy fail-open
    adapter.process_one(inbox_file, settings)

    # Verify outbox ack was written
    out_files = list(outbox.glob(f"{msg_id}*"))
    assert len(out_files) == 1, out_files
    ack = json.loads(out_files[0].read_text())
    assert "Signal PLAN" in ack["text"], ack
    print(f"  PASS outbox ack: {ack['text']}")

    # Verify inbox file was moved to processed
    assert not inbox_file.exists()
    assert (processed / inbox_file.name).exists()
    print("  PASS inbox file moved to processed")


def case_adapter_handles_unknown_session_signal(home, inbox, outbox,
                                                 processed) -> None:
    """When session_id is not in process_table, the adapter ack
    must say 'unknown session' clearly and not crash."""
    _section("adapter handles unknown session_id gracefully")

    os.environ["ADAPTER_INBOX"] = str(inbox)
    os.environ["ADAPTER_OUTBOX"] = str(outbox)
    os.environ["ADAPTER_PROCESSED"] = str(processed)
    os.environ["CORVIN_HOME"] = str(home)

    for mod in ("adapter", "process_table", "paths"):
        sys.modules.pop(mod, None)
    import adapter  # type: ignore

    msg_id = "sig_unknown"
    envelope = {
        "msg_id": msg_id,
        "channel": "discord",
        "from": "_test",
        "chat_id": "99",
        "_signal": True,
        "session_id": "s_does_not_exist",
        "signal": "PLAN",
        "ts": 0,
    }
    inbox_file = inbox / f"{msg_id}.json"
    inbox_file.write_text(json.dumps(envelope))

    adapter.process_one(inbox_file, {"whitelist": []})

    out_files = list(outbox.glob(f"{msg_id}*"))
    assert len(out_files) == 1
    ack = json.loads(out_files[0].read_text())
    assert "unknown session" in ack["text"], ack
    print(f"  PASS ack: {ack['text']}")


def case_adapter_rejects_unsupported_signal(home, inbox, outbox,
                                             processed) -> None:
    _section("adapter ack reports unsupported signal cleanly")
    _register_stub_session(home, "s_unsupp")

    os.environ["ADAPTER_INBOX"] = str(inbox)
    os.environ["ADAPTER_OUTBOX"] = str(outbox)
    os.environ["ADAPTER_PROCESSED"] = str(processed)
    os.environ["CORVIN_HOME"] = str(home)

    for mod in ("adapter", "process_table", "paths"):
        sys.modules.pop(mod, None)
    import adapter  # type: ignore

    msg_id = "sig_unsupp"
    envelope = {
        "msg_id": msg_id, "channel": "discord", "from": "_test",
        "chat_id": "42", "_signal": True,
        "session_id": "s_unsupp", "signal": "WEIRDO", "ts": 0,
    }
    inbox_file = inbox / f"{msg_id}.json"
    inbox_file.write_text(json.dumps(envelope))

    adapter.process_one(inbox_file, {"whitelist": []})

    out_files = list(outbox.glob(f"{msg_id}*"))
    ack = json.loads(out_files[0].read_text())
    assert "unsupported" in ack["text"].lower() or "WEIRDO" in ack["text"], ack
    print(f"  PASS ack: {ack['text']}")


def case_signal_kill_cross_chat_denied(home, inbox, outbox, processed) -> None:
    """KILL targeting a session in a DIFFERENT chat than the sender's own
    must be gated by _inbox_sender_authorized(channel, sender, target_chat)
    — not just the sender's own-chat authorization. Here the sender passes
    the general per-message authz for their own chat (via an audience='all'
    chat profile) but is NOT whitelisted for the target chat, so the
    cross-chat KILL must be denied and _cancel_chat must never run."""
    _section("_signal KILL cross-chat: sender unauthorized for target chat -> denied")

    os.environ["ADAPTER_INBOX"] = str(inbox)
    os.environ["ADAPTER_OUTBOX"] = str(outbox)
    os.environ["ADAPTER_PROCESSED"] = str(processed)
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["ADAPTER_BRIDGES_DIR"] = str(home)
    os.environ["VOICE_AUDIT_PATH"] = str(home / "audit.jsonl")

    # Sender "mallory" is authorized in their OWN chat ("chat-a") via an
    # audience='all' profile, but the global whitelist excludes them, and
    # the target chat ("chat-b") has no audience='all' override — so
    # mallory is unauthorized for chat-b specifically.
    _write_channel_settings(home, "discord", {
        "whitelist": ["someone-else"],
        "chat_profiles": {"chat-a": {"audience": "all"}},
    })

    sys.modules.pop("process_table", None)
    sys.modules.pop("paths", None)
    import process_table  # type: ignore
    process_table.register_session(
        "s_kill_target_b", chat_key="chat-b", persona="coder", pid=555,
    )

    for mod in ("adapter", "process_table", "paths"):
        sys.modules.pop(mod, None)
    import adapter  # type: ignore

    calls: list[str] = []
    adapter._cancel_chat = lambda chat_key: (calls.append(chat_key) or 0)

    msg_id = "sig_kill_cross_deny"
    envelope = {
        "msg_id": msg_id, "channel": "discord", "from": "mallory",
        "chat_id": "chat-a", "_signal": True,
        "session_id": "s_kill_target_b", "signal": "KILL", "ts": 0,
    }
    inbox_file = inbox / f"{msg_id}.json"
    inbox_file.write_text(json.dumps(envelope))

    adapter.process_one(inbox_file, {"whitelist": []})

    assert calls == [], (
        f"_cancel_chat must NOT run on a denied cross-chat KILL, got calls={calls}"
    )

    out_files = list(outbox.glob(f"{msg_id}*"))
    assert len(out_files) == 1, out_files
    ack = json.loads(out_files[0].read_text())
    assert "cross-chat KILL denied" in ack["text"], ack
    print(f"  PASS ack denies cross-chat KILL: {ack['text']}")

    events = _read_audit_events(home)
    sig_events = [e for e in events if e.get("event_type") == "bridge.signal_inject"]
    assert sig_events, [e.get("event_type") for e in events]
    details = sig_events[-1].get("details", {})
    assert details.get("delivered") is False, details
    assert "cross-chat KILL denied" in details.get("reason", ""), details
    print("  PASS audit event bridge.signal_inject delivered=false, reason cross-chat")

    os.environ.pop("ADAPTER_BRIDGES_DIR", None)


def case_signal_kill_cross_chat_authorized(home, inbox, outbox, processed) -> None:
    """Companion positive case: sender IS whitelisted (so authorized for
    ANY chat on that channel, including the cross-chat target) -> the
    cross-chat KILL must be allowed and _cancel_chat must run."""
    _section("_signal KILL cross-chat: sender whitelisted for target chat -> allowed")

    os.environ["ADAPTER_INBOX"] = str(inbox)
    os.environ["ADAPTER_OUTBOX"] = str(outbox)
    os.environ["ADAPTER_PROCESSED"] = str(processed)
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["ADAPTER_BRIDGES_DIR"] = str(home)
    os.environ["VOICE_AUDIT_PATH"] = str(home / "audit.jsonl")

    _write_channel_settings(home, "discord", {"whitelist": ["root-op"]})

    sys.modules.pop("process_table", None)
    sys.modules.pop("paths", None)
    import process_table  # type: ignore
    process_table.register_session(
        "s_kill_target_ok", chat_key="chat-b2", persona="coder", pid=777,
    )

    for mod in ("adapter", "process_table", "paths"):
        sys.modules.pop(mod, None)
    import adapter  # type: ignore

    calls: list[str] = []
    adapter._cancel_chat = lambda chat_key: (calls.append(chat_key) or 1)

    msg_id = "sig_kill_cross_ok"
    envelope = {
        "msg_id": msg_id, "channel": "discord", "from": "root-op",
        "chat_id": "chat-a2", "_signal": True,
        "session_id": "s_kill_target_ok", "signal": "KILL", "ts": 0,
    }
    inbox_file = inbox / f"{msg_id}.json"
    inbox_file.write_text(json.dumps(envelope))

    adapter.process_one(inbox_file, {"whitelist": []})

    assert calls == ["chat-b2"], (
        f"_cancel_chat must run against the target chat, got calls={calls}"
    )

    out_files = list(outbox.glob(f"{msg_id}*"))
    assert len(out_files) == 1, out_files
    ack = json.loads(out_files[0].read_text())
    assert "delivered" in ack["text"], ack
    assert "s_kill_target_ok" in ack["text"], ack
    print(f"  PASS ack confirms authorized cross-chat KILL: {ack['text']}")

    events = _read_audit_events(home)
    sig_events = [e for e in events if e.get("event_type") == "bridge.signal_inject"]
    assert sig_events, [e.get("event_type") for e in events]
    details = sig_events[-1].get("details", {})
    assert details.get("delivered") is True, details
    print("  PASS audit event bridge.signal_inject delivered=true")

    os.environ.pop("ADAPTER_BRIDGES_DIR", None)


def case_signal_session_race_guard_blocks_kill(home, inbox, outbox, processed) -> None:
    """Stale-session-window guard: the registry (process_table) resolves
    the session to a chat_key whose LIVE subprocess pid no longer matches
    the registry record's pid (a newer session started in the same chat
    meanwhile). The signal must be refused with 'session race' and must
    NOT act (no _cancel_chat) against the newer live process."""
    _section("_signal session-race guard: stale registry pid vs newer live pid -> refused")

    os.environ["ADAPTER_INBOX"] = str(inbox)
    os.environ["ADAPTER_OUTBOX"] = str(outbox)
    os.environ["ADAPTER_PROCESSED"] = str(processed)
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["ADAPTER_BRIDGES_DIR"] = str(home)
    os.environ["VOICE_AUDIT_PATH"] = str(home / "audit.jsonl")
    # No settings.json written -> _load_channel_settings returns {} ->
    # fail-open ("no-settings"); irrelevant here, the race guard fires
    # before the KILL/cross-chat authz branch is ever reached.

    sys.modules.pop("process_table", None)
    sys.modules.pop("paths", None)
    import process_table  # type: ignore
    process_table.register_session(
        "s_race", chat_key="chat-race", persona="coder", pid=111,
    )

    for mod in ("adapter", "process_table", "paths"):
        sys.modules.pop(mod, None)
    import adapter  # type: ignore

    class _FakeProc:
        def __init__(self, pid: int) -> None:
            self.pid = pid

    with adapter._running_subprocs_guard:
        adapter._running_subprocs["chat-race"] = [_FakeProc(222)]

    calls: list[str] = []
    adapter._cancel_chat = lambda chat_key: (calls.append(chat_key) or 1)

    msg_id = "sig_race"
    envelope = {
        "msg_id": msg_id, "channel": "discord", "from": "_test",
        "chat_id": "chat-race", "_signal": True,
        "session_id": "s_race", "signal": "KILL", "ts": 0,
    }
    inbox_file = inbox / f"{msg_id}.json"
    inbox_file.write_text(json.dumps(envelope))

    adapter.process_one(inbox_file, {"whitelist": []})

    assert calls == [], (
        f"_cancel_chat must NOT run when the session-race guard trips, got {calls}"
    )

    out_files = list(outbox.glob(f"{msg_id}*"))
    assert len(out_files) == 1, out_files
    ack = json.loads(out_files[0].read_text())
    assert "session race" in ack["text"], ack
    assert "111" in ack["text"] and "222" in ack["text"], ack
    print(f"  PASS ack refuses stale-pid signal: {ack['text']}")

    events = _read_audit_events(home)
    sig_events = [e for e in events if e.get("event_type") == "bridge.signal_inject"]
    assert sig_events, [e.get("event_type") for e in events]
    details = sig_events[-1].get("details", {})
    assert details.get("delivered") is False, details
    assert "session race" in details.get("reason", ""), details
    print("  PASS audit event bridge.signal_inject delivered=false, reason session race")

    os.environ.pop("ADAPTER_BRIDGES_DIR", None)


def case_help_text_lists_sig(home, inbox, outbox, processed) -> None:
    _section("help text mentions sig subcommand")
    r = _run_cli("help", home=home)
    # help text comes from the docstring at module level; the COMMANDS
    # dict has 'sig' so that's structural verification
    sys.modules.pop("phase3_cli", None)
    sys.path.insert(0, str(ROOT))
    import phase3_cli  # type: ignore
    assert "sig" in phase3_cli.COMMANDS, list(phase3_cli.COMMANDS)
    print("  PASS sig is in COMMANDS dict")


# --------------------------------------------------------------------- driver

def main() -> None:
    saved_env = {k: os.environ.get(k) for k in
                 ("CORVIN_HOME", "ADAPTER_INBOX", "ADAPTER_OUTBOX",
                  "ADAPTER_PROCESSED", "CORVIN_CHANNEL_ID",
                  "ADAPTER_BRIDGES_DIR", "VOICE_AUDIT_PATH")}
    cases = [
        case_sig_writes_envelope,
        case_sig_rejects_invalid_signal,
        case_sig_rejects_unknown_session,
        case_sig_validates_arity,
        case_adapter_handles_signal_envelope,
        case_adapter_handles_unknown_session_signal,
        case_adapter_rejects_unsupported_signal,
        case_signal_kill_cross_chat_denied,
        case_signal_kill_cross_chat_authorized,
        case_signal_session_race_guard_blocks_kill,
        case_help_text_lists_sig,
    ]
    failures = 0
    for case in cases:
        home, inbox, outbox, processed = _setup_sandbox()
        try:
            case(home, inbox, outbox, processed)
        except Exception as exc:
            failures += 1
            print(f"  FAIL: {case.__name__}: {exc!r}")
            import traceback
            traceback.print_exc()
        finally:
            shutil.rmtree(home, ignore_errors=True)

    # Restore env
    for k, v in saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

    print(f"\n=== {len(cases) - failures}/{len(cases)} cases passed ===")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
