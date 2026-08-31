#!/usr/bin/env python3
"""test_auth_elevation.py — per-subtask E2E for layer-16 v2 PIN-Elevation.

Each test exercises the gate from a different angle:

  case 1  in-process grant / revoke / TTL semantics
  case 2  pre-tool hook returns exit-2 on a destructive MCP tool when
          the caller is a bridge chat and not elevated
  case 3  pre-tool hook returns exit-0 when the chat is elevated
  case 4  pre-tool hook fail-OPEN when CORVIN_CHANNEL_ID is missing
          (bare CLI use, no bridge context)
  case 5  pre-tool hook fail-OPEN on non-destructive tool names
  case 6  audit chain carries auth.elevation_grant / .required / .revoke
          events; verify_chain stays clean

The hook lives at operator/voice/hooks/auth_elevation_gate.py. The library
that owns the elevation-store is operator/bridges/shared/auth_elevation.py.
The two are wired with the same on-disk store
(``<corvin_home>/global/auth/elevation.json``).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent  # bridges/shared/
HOOK = HERE.parent.parent / "voice" / "hooks" / "auth_elevation_gate.py"
sys.path.insert(0, str(HERE))

PASS, FAIL = 0, 0


def _ok(msg: str) -> None:
    global PASS
    print(f"  PASS  {msg}")
    PASS += 1


def _bad(msg: str) -> None:
    global FAIL
    print(f"  FAIL  {msg}")
    FAIL += 1


def _eq(a, b, msg: str) -> None:
    if a == b:
        _ok(msg)
    else:
        _bad(f"{msg} — expected {b!r}, got {a!r}")


def _fresh_sandbox() -> Path:
    return Path(tempfile.mkdtemp(prefix="auth-elev-test-"))


def _import_fresh_lib():
    for m in ("auth_elevation",):
        sys.modules.pop(m, None)
    import auth_elevation  # type: ignore
    return auth_elevation


# ─── case 1: in-process grant / revoke / TTL ────────────────────────────────

def case_1_grant_revoke_ttl() -> None:
    print("\n=== case 1: grant / revoke / TTL ===")
    sb = _fresh_sandbox()
    os.environ["CORVIN_HOME"] = str(sb)
    try:
        ae = _import_fresh_lib()
        # Wrong PIN refused
        ok, why = ae.grant(chat_key="discord:c1", pin="bad",
                           settings_pin="good", channel="discord")
        _eq(ok, False, "wrong PIN refused")
        _eq(why, "wrong-pin", "wrong-pin reason")

        # No-pin-configured refused
        ok2, why2 = ae.grant(chat_key="discord:c1", pin="x",
                             settings_pin=None, channel="discord")
        _eq(ok2, False, "no-pin-configured refused")
        _eq(why2, "no-pin-configured", "no-pin-configured reason")

        # Correct PIN grants, default TTL is 600s
        ok3, why3 = ae.grant(chat_key="discord:c1", pin="good",
                             settings_pin="good", channel="discord")
        _eq(ok3, True, "correct PIN grants")
        _eq(why3, "ok", "grant ok reason")
        _eq(ae.is_elevated("discord:c1"), True, "is_elevated after grant")
        ttl = ae.remaining_ttl("discord:c1")
        _ok(f"TTL is in (0, 600] — got {ttl}") if 0 < ttl <= 600 \
            else _bad(f"TTL out of range: {ttl}")

        # Manual revoke
        existed = ae.revoke(chat_key="discord:c1", channel="discord")
        _eq(existed, True, "revoke returns existed=True")
        _eq(ae.is_elevated("discord:c1"), False, "no longer elevated after revoke")

        # Re-revoke is a no-op
        again = ae.revoke(chat_key="discord:c1", channel="discord")
        _eq(again, False, "re-revoke returns False")
    finally:
        os.environ.pop("CORVIN_HOME", None)
        shutil.rmtree(sb, ignore_errors=True)


# ─── case 2: hook denies on destructive tool, no elevation ─────────────────

def case_2_hook_denies_when_not_elevated() -> None:
    print("\n=== case 2: hook DENIES forge_promote when not elevated ===")
    sb = _fresh_sandbox()
    payload = {
        "tool_name": "mcp__forge__forge_promote",
        "tool_input": {"name": "x"},
    }
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True, text=True,
        env={**os.environ,
             "CORVIN_HOME": str(sb),
             "CORVIN_HOME": str(sb),
             "CORVIN_CHANNEL_ID": "discord:c-deny",
             "CORVIN_CHANNEL_ID": "discord:c-deny"},
    )
    _eq(proc.returncode, 2, "hook exits with 2 (deny)")
    if proc.returncode == 2:
        _ok(f"deny stderr starts with auth_elevation: {proc.stderr[:80]!r}") \
            if proc.stderr.startswith("auth_elevation") \
            else _bad(f"deny message wrong: {proc.stderr[:120]!r}")
    shutil.rmtree(sb, ignore_errors=True)


# ─── case 3: hook ALLOWS when chat is elevated ─────────────────────────────

def case_3_hook_allows_when_elevated() -> None:
    print("\n=== case 3: hook ALLOWS forge_promote when chat is elevated ===")
    sb = _fresh_sandbox()
    os.environ["CORVIN_HOME"] = str(sb)
    try:
        ae = _import_fresh_lib()
        ae.grant(chat_key="discord:c-ok", pin="p", settings_pin="p",
                 channel="discord")
        payload = {
            "tool_name": "mcp__skill_forge__skill_promote",
            "tool_input": {"name": "y"},
        }
        proc = subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps(payload),
            capture_output=True, text=True,
            env={**os.environ,
                 "CORVIN_HOME": str(sb),
                 "CORVIN_HOME": str(sb),
                 "CORVIN_CHANNEL_ID": "discord:c-ok",
                 "CORVIN_CHANNEL_ID": "discord:c-ok"},
        )
        _eq(proc.returncode, 0, "hook exits with 0 (allow) when elevated")
        _eq(proc.stderr, "", "no deny message on allow")
    finally:
        os.environ.pop("CORVIN_HOME", None)
        shutil.rmtree(sb, ignore_errors=True)


# ─── case 4: fail-OPEN without bridge context (bare CLI) ───────────────────

def case_4_fail_open_no_channel_id() -> None:
    print("\n=== case 4: hook fail-OPEN when CORVIN_CHANNEL_ID is missing ===")
    sb = _fresh_sandbox()
    payload = {
        "tool_name": "mcp__forge__forge_promote",
        "tool_input": {"name": "z"},
    }
    env = {k: v for k, v in os.environ.items()
           if k not in ("CORVIN_CHANNEL_ID", "CORVIN_CHANNEL_ID")}
    env["CORVIN_HOME"] = str(sb)
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True, text=True, env=env,
    )
    _eq(proc.returncode, 0,
        "no CORVIN_CHANNEL_ID → fail-open (bare CLI use)")
    shutil.rmtree(sb, ignore_errors=True)


# ─── case 5: non-destructive tools always allowed ──────────────────────────

def case_5_non_destructive_tool_always_allowed() -> None:
    print("\n=== case 5: hook ALLOWS non-destructive tools ===")
    sb = _fresh_sandbox()
    payload = {
        "tool_name": "mcp__forge__forge_tool",  # create, not promote
        "tool_input": {"name": "x"},
    }
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True, text=True,
        env={**os.environ,
             "CORVIN_HOME": str(sb),
             "CORVIN_HOME": str(sb),
             "CORVIN_CHANNEL_ID": "discord:c-any",
             "CORVIN_CHANNEL_ID": "discord:c-any"},
    )
    _eq(proc.returncode, 0, "non-destructive tool not gated")
    shutil.rmtree(sb, ignore_errors=True)


# ─── case 6: audit chain carries the events ────────────────────────────────

def case_6_audit_chain_records_events() -> None:
    print("\n=== case 6: audit chain records auth.* events ===")
    sb = _fresh_sandbox()
    os.environ["CORVIN_HOME"] = str(sb)
    try:
        ae = _import_fresh_lib()
        # 1 wrong-PIN, 1 grant, 1 revoke
        ae.grant(chat_key="discord:c-aud", pin="bad", settings_pin="good",
                 channel="discord")
        ae.grant(chat_key="discord:c-aud", pin="good", settings_pin="good",
                 channel="discord")
        ae.revoke(chat_key="discord:c-aud", channel="discord")

        audit_path = sb / "global" / "forge" / "audit.jsonl"
        if not audit_path.exists():
            _bad(f"audit.jsonl not created at {audit_path}")
            return
        events = [json.loads(line) for line in audit_path.read_text().splitlines()
                  if line.strip()]
        types = [e.get("event_type") for e in events]
        _ok("audit.elevation_required emitted") \
            if "auth.elevation_required" in types \
            else _bad(f"missing elevation_required in {types}")
        _ok("audit.elevation_grant emitted") \
            if "auth.elevation_grant" in types \
            else _bad(f"missing elevation_grant in {types}")
        _ok("audit.elevation_revoke emitted") \
            if "auth.elevation_revoke" in types \
            else _bad(f"missing elevation_revoke in {types}")

        # verify_chain still clean
        try:
            sys.path.insert(0,
                str(Path(__file__).resolve().parents[2] / "forge"))
            from forge.security_events import verify_chain  # type: ignore
            ok, problems = verify_chain(audit_path)
            _eq(ok, True, "verify_chain reports ok")
            _eq(problems, [], "verify_chain reports no problems")
        except ImportError:
            _bad("forge.security_events not importable for verify_chain")
    finally:
        os.environ.pop("CORVIN_HOME", None)
        shutil.rmtree(sb, ignore_errors=True)


def main() -> int:
    case_1_grant_revoke_ttl()
    case_2_hook_denies_when_not_elevated()
    case_3_hook_allows_when_elevated()
    case_4_fail_open_no_channel_id()
    case_5_non_destructive_tool_always_allowed()
    case_6_audit_chain_records_events()
    print(f"\n{PASS} pass, {FAIL} fail")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())


# ─── pytest-compatible tests ────────────────────────────────────────────────

import unittest  # noqa: E402


class TestPinLockout(unittest.TestCase):
    """V-006: PIN brute-force protection — pytest-compatible unit tests."""

    def _fresh_ae(self, tmp: Path):
        """Import auth_elevation fresh against a sandbox CORVIN_HOME."""
        for m in list(sys.modules.keys()):
            if "auth_elevation" in m:
                sys.modules.pop(m, None)
        os.environ["CORVIN_HOME"] = str(tmp)
        os.environ["CORVIN_HOME"] = str(tmp)
        import auth_elevation as ae  # type: ignore
        # Reset in-memory failure state carried from previous test runs.
        with ae._pin_lock:
            ae._pin_failures.clear()
        return ae

    def test_pin_lockout_after_5_fails(self) -> None:
        """After 5 wrong-PIN attempts the 6th call returns pin-lockout
        without ever reaching the PIN comparison logic."""
        sb = Path(tempfile.mkdtemp(prefix="pin-lockout-"))
        try:
            ae = self._fresh_ae(sb)
            chat_key = "discord:lockout-test"
            settings_pin = "correct-pin"

            # Five consecutive wrong-PIN attempts — should all return wrong-pin
            for i in range(5):
                ok, why = ae.grant(
                    chat_key=chat_key, pin="wrong",
                    settings_pin=settings_pin, channel="discord",
                )
                self.assertFalse(ok, f"attempt {i + 1} should be refused")
                self.assertEqual(why, "wrong-pin",
                                 f"attempt {i + 1} reason should be wrong-pin, got {why!r}")

            # 6th attempt — even with the CORRECT pin — must be refused as locked-out
            ok, why = ae.grant(
                chat_key=chat_key, pin=settings_pin,
                settings_pin=settings_pin, channel="discord",
            )
            self.assertFalse(ok, "6th attempt should be locked out")
            self.assertEqual(why, "pin-lockout",
                             f"expected pin-lockout, got {why!r}")

            # Confirm the chat is NOT elevated (no elevation was granted)
            self.assertFalse(ae.is_elevated(chat_key),
                             "chat must not be elevated after lockout")
        finally:
            os.environ.pop("CORVIN_HOME", None)
            os.environ.pop("CORVIN_HOME", None)
            shutil.rmtree(sb, ignore_errors=True)

    def test_pin_lockout_emits_lockout_started_audit_event(self) -> None:
        """V-006 / ADR-0072: after 5 wrong-PIN attempts the
        ``auth.elevation_lockout_started`` event must appear in the
        unified audit chain at ``<CORVIN_HOME>/global/forge/audit.jsonl``."""
        sb = Path(tempfile.mkdtemp(prefix="pin-lockout-audit-"))
        try:
            ae = self._fresh_ae(sb)
            # Ensure forge package is importable so audit writes succeed.
            forge_pkg = Path(__file__).resolve().parents[2] / "forge"
            if str(forge_pkg) not in sys.path:
                sys.path.insert(0, str(forge_pkg))

            chat_key = "discord:lockout-audit"
            settings_pin = "correct"

            # Five wrong-PIN attempts to trigger the lockout threshold.
            for _ in range(5):
                ae.grant(
                    chat_key=chat_key, pin="wrong",
                    settings_pin=settings_pin, channel="discord",
                )

            audit_path = sb / "global" / "forge" / "audit.jsonl"
            self.assertTrue(
                audit_path.exists(),
                f"audit.jsonl not created at {audit_path}",
            )
            events = [
                json.loads(line)
                for line in audit_path.read_text().splitlines()
                if line.strip()
            ]
            event_types = [e.get("event_type") for e in events]
            self.assertIn(
                "auth.elevation_lockout_started",
                event_types,
                f"auth.elevation_lockout_started missing from chain: {event_types}",
            )
            # The lockout event must not carry raw uid — only chat_key is permitted.
            lockout_events = [
                e for e in events
                if e.get("event_type") == "auth.elevation_lockout_started"
            ]
            for ev in lockout_events:
                details = ev.get("details", {})
                self.assertNotIn(
                    "uid", details,
                    "uid must not appear in auth.elevation_lockout_started details",
                )
        finally:
            os.environ.pop("CORVIN_HOME", None)
            shutil.rmtree(sb, ignore_errors=True)

    def test_pin_failures_cleared_on_success(self) -> None:
        """A successful PIN entry clears the failure counter,
        so subsequent wrong attempts start the window from zero."""
        sb = Path(tempfile.mkdtemp(prefix="pin-clear-"))
        try:
            ae = self._fresh_ae(sb)
            chat_key = "discord:clear-test"
            settings_pin = "secret"

            # 4 wrong attempts (below threshold)
            for _ in range(4):
                ae.grant(chat_key=chat_key, pin="bad",
                         settings_pin=settings_pin, channel="discord")

            # Correct PIN clears the counter
            ok, why = ae.grant(chat_key=chat_key, pin=settings_pin,
                               settings_pin=settings_pin, channel="discord")
            self.assertTrue(ok, "correct PIN should grant elevation")
            self.assertEqual(why, "ok")

            # After clearing, failure counter is reset; one more wrong should
            # not immediately lock out
            ae.revoke(chat_key=chat_key, channel="discord")
            ok2, why2 = ae.grant(chat_key=chat_key, pin="bad",
                                 settings_pin=settings_pin, channel="discord")
            self.assertFalse(ok2)
            self.assertEqual(why2, "wrong-pin",
                             "single wrong attempt after clear should not lock out")
        finally:
            os.environ.pop("CORVIN_HOME", None)
            os.environ.pop("CORVIN_HOME", None)
            shutil.rmtree(sb, ignore_errors=True)
