#!/usr/bin/env python3
"""Roadmap L — daily audit-verify timer + bridge-notify on chain break.

E2E covering the `voice_audit.py verify --notify-bridge` path:

  1. Build a sandboxed audit chain (3 valid events, one corrupted).
  2. Run voice_audit.py with --notify-bridge --relay-config + --outbox-dir.
  3. Assert exit code 1 (chain broken) + at least one outbox envelope
     with `_audit_chain_break: True` and the configured target.
  4. Negative-control: a clean chain → exit 0 + no envelope.
  5. Negative-control: relay disabled / missing → exit 1 + no envelope.

Run as: python3 operator/voice/scripts/test_audit_verify_notify.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "operator" / "voice" / "scripts" / "voice_audit.py"
sys.path.insert(0, str(REPO / "operator" / "forge"))

from forge.security_events import write_event  # noqa: E402


PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


def _build_chain(audit: Path) -> None:
    audit.parent.mkdir(parents=True, exist_ok=True)
    write_event(audit, "tool.created", tool="t1", run_id="r1",
                details={"x": 1})
    write_event(audit, "skill.create", tool="s1", run_id="r2",
                details={"y": 2})
    write_event(audit, "tool.created", tool="t2", run_id="r3",
                details={"x": 3})


def _corrupt(audit: Path, line_index: int) -> None:
    """Tamper with the `details` field of one record so the chain breaks."""
    lines = audit.read_text().splitlines()
    rec = json.loads(lines[line_index])
    rec["details"] = {"tampered": True}
    lines[line_index] = json.dumps(rec)
    audit.write_text("\n".join(lines) + "\n")


def _run_verify(audit: Path, relay: Path | None, outbox: Path) -> tuple[int, str, str]:
    cmd = [sys.executable, str(SCRIPT), "--path", str(audit),
           "verify", "--notify-bridge", "--outbox-dir", str(outbox)]
    if relay is not None:
        cmd += ["--relay-config", str(relay)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    return proc.returncode, proc.stdout, proc.stderr


def case_chain_break_with_relay():
    print("\n[case] chain break + relay configured → outbox envelope written")
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        audit = td_path / "audit.jsonl"
        outbox = td_path / "outbox"
        outbox.mkdir()
        relay = td_path / "relay.json"
        relay.write_text(json.dumps({
            "enabled": True,
            "channel": "telegram",
            "to":      "12345",
            "prefix":  "🚨 audit",
        }))
        _build_chain(audit)
        _corrupt(audit, line_index=1)
        rc, stdout, stderr = _run_verify(audit, relay, outbox)
        t("verify exit code = 1 (chain broken)", rc == 1,
          detail=f"got {rc}; stderr={stderr[:200]!r}")
        envelopes = list(outbox.glob("*.json"))
        t("exactly one envelope written to outbox",
          len(envelopes) == 1, detail=f"got {len(envelopes)}")
        if envelopes:
            env = json.loads(envelopes[0].read_text())
            t("envelope channel = telegram",
              env.get("channel") == "telegram",
              detail=f"got {env.get('channel')!r}")
            t("envelope to = 12345",
              str(env.get("to")) == "12345",
              detail=f"got {env.get('to')!r}")
            t("envelope chat_id = 12345 (numeric for telegram)",
              env.get("chat_id") == 12345,
              detail=f"got {env.get('chat_id')!r}")
            t("envelope _audit_chain_break flag",
              env.get("_audit_chain_break") is True)
            t("envelope text mentions AUDIT CHAIN BROKEN",
              "AUDIT CHAIN BROKEN" in (env.get("text") or ""),
              detail=f"text={env.get('text', '')[:100]!r}")
            t("envelope text carries the configured prefix",
              "🚨 audit" in (env.get("text") or ""),
              detail=f"text={env.get('text', '')[:100]!r}")


def case_clean_chain_no_envelope():
    print("\n[case] clean chain + relay configured → no envelope, exit 0")
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        audit = td_path / "audit.jsonl"
        outbox = td_path / "outbox"
        outbox.mkdir()
        relay = td_path / "relay.json"
        relay.write_text(json.dumps({
            "enabled": True,
            "channel": "telegram",
            "to":      "12345",
        }))
        _build_chain(audit)
        rc, stdout, _ = _run_verify(audit, relay, outbox)
        t("verify exit code = 0 (chain ok)", rc == 0,
          detail=f"got {rc}; stdout={stdout[:120]!r}")
        envelopes = list(outbox.glob("*.json"))
        t("no envelope written to outbox on clean chain",
          len(envelopes) == 0, detail=f"got {len(envelopes)}")


def case_chain_break_no_relay():
    print("\n[case] chain break + no relay → exit 1, no envelope")
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        audit = td_path / "audit.jsonl"
        outbox = td_path / "outbox"
        outbox.mkdir()
        relay = td_path / "relay.json"  # does not exist
        _build_chain(audit)
        _corrupt(audit, line_index=2)
        rc, _, stderr = _run_verify(audit, relay, outbox)
        t("verify exit code = 1 (chain broken)", rc == 1)
        envelopes = list(outbox.glob("*.json"))
        t("no envelope written when relay.json is missing",
          len(envelopes) == 0, detail=f"got {len(envelopes)}")
        t("stderr explains relay not configured",
          "relay not configured" in stderr or "relay" in stderr,
          detail=f"stderr={stderr[:200]!r}")


def case_chain_break_relay_disabled():
    print("\n[case] chain break + relay disabled → exit 1, no envelope")
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        audit = td_path / "audit.jsonl"
        outbox = td_path / "outbox"
        outbox.mkdir()
        relay = td_path / "relay.json"
        relay.write_text(json.dumps({
            "enabled": False,
            "channel": "telegram",
            "to":      "12345",
        }))
        _build_chain(audit)
        _corrupt(audit, line_index=0)
        rc, _, _ = _run_verify(audit, relay, outbox)
        t("verify exit code = 1 (chain broken)", rc == 1)
        envelopes = list(outbox.glob("*.json"))
        t("no envelope written when relay disabled",
          len(envelopes) == 0, detail=f"got {len(envelopes)}")


def case_systemd_units_exist():
    print("\n[case] systemd unit templates ship with the plugin")
    sd = REPO / "operator" / "voice" / "scripts" / "systemd"
    svc = sd / "corvin-audit-verify.service"
    timer = sd / "corvin-audit-verify.timer"
    t("audit-verify.service exists", svc.exists(), detail=str(svc))
    t("audit-verify.timer exists", timer.exists(), detail=str(timer))
    if svc.exists():
        body = svc.read_text()
        t("service ExecStart references voice_audit.py verify --notify-bridge",
          "voice_audit.py verify --notify-bridge" in body,
          detail=body[:200])
    if timer.exists():
        body = timer.read_text()
        t("timer fires daily 04:30",
          "OnCalendar=*-*-* 04:30:00" in body,
          detail=body[:200])
    bsh = (REPO / "operator" / "bridges" / "bridge.sh").read_text()
    t("bridge.sh registers the audit-verify timer in ALL_UNITS",
      "UNIT_CORVIN_AUDIT_VERIFY_TIMER" in bsh)
    t("bridge.sh enables the timer in cmd_up()",
      "audit-verify timer enabled" in bsh)


def main() -> int:
    case_chain_break_with_relay()
    case_clean_chain_no_envelope()
    case_chain_break_no_relay()
    case_chain_break_relay_disabled()
    case_systemd_units_exist()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
