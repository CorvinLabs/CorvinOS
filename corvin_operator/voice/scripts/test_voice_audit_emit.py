"""Phase H1 E2E: voice-audit emit writes hash-chained events.

The CLI gains an `emit` subcommand so non-Python callers (Node.js
daemons especially) can append structured events without re-implementing
the chain logic. The subcommand is a thin shell over
forge.security_events.write_event with the same severity table the
voice/audit.py module uses, so events line up with what the adapter
emits.

Three scenarios:
  1. Single emit → one record on disk, prev_hash="" + hash set
  2. Two emits in a row → second prev_hash == first hash (chain stays linked)
  3. Tampering after emit → voice-audit verify catches it
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CLI = REPO_ROOT / "operator" / "voice" / "scripts" / "voice_audit.py"


PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


def _run(*args, audit_path):
    return subprocess.run(
        [sys.executable, str(CLI), "--path", str(audit_path), *args],
        capture_output=True, text=True,
    )


def test_emit_writes_one_record():
    print("\n[emit one event → one record with prev_hash='' + hash set]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        audit = td / "audit.jsonl"
        proc = _run(
            "emit", "bridge.whitelist_deny",
            "--channel", "discord",
            "--user", "u-hostile",
            "--details", json.dumps({"reason": "not in whitelist"}),
            audit_path=audit,
        )
        t("emit rc=0", proc.returncode == 0,
          detail=f"stderr={proc.stderr!r}")
        t("audit file exists", audit.exists())
        lines = audit.read_text().splitlines()
        t("exactly one line written", len(lines) == 1)
        rec = json.loads(lines[0])
        t("event_type matches",
          rec["event_type"] == "bridge.whitelist_deny")
        t("severity = WARNING (per voice severity table)",
          rec["severity"] == "WARNING")
        t("prev_hash empty (chain start)", rec.get("prev_hash") == "")
        t("hash set",
          isinstance(rec.get("hash"), str) and len(rec["hash"]) > 0)
        det = rec.get("details") or {}
        t("details.channel propagated",
          det.get("channel") == "discord")
        t("details.user propagated",
          det.get("user") == "u-hostile")
        t("details.reason propagated",
          det.get("reason") == "not in whitelist")


def test_two_emits_link():
    print("\n[two emits → second prev_hash == first hash]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        audit = td / "audit.jsonl"
        _run("emit", "bridge.login",
             "--channel", "telegram", "--user", "u1",
             audit_path=audit)
        _run("emit", "bridge.message_received",
             "--channel", "telegram", "--user", "u1",
             "--chat-key", "chat-42",
             audit_path=audit)
        lines = audit.read_text().splitlines()
        t("two records", len(lines) == 2)
        r1, r2 = (json.loads(l) for l in lines)
        t("second prev_hash == first hash",
          r2["prev_hash"] == r1["hash"])
        # verify rc=0
        v = _run("verify", audit_path=audit)
        t("voice-audit verify rc=0", v.returncode == 0)


def test_tamper_caught():
    print("\n[emit + tamper → verify exits 1, names line]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        audit = td / "audit.jsonl"
        _run("emit", "bridge.cancel",
             "--channel", "discord", "--user", "u",
             audit_path=audit)
        _run("emit", "bridge.cancel",
             "--channel", "discord", "--user", "u",
             audit_path=audit)
        # tamper line 1: change channel
        lines = audit.read_text().splitlines()
        rec = json.loads(lines[0])
        rec["details"]["channel"] = "evil"
        lines[0] = json.dumps(rec)
        audit.write_text("\n".join(lines) + "\n")
        v = _run("verify", audit_path=audit)
        t("verify rc=1", v.returncode == 1)
        t("stderr names line 1",
          "line 1" in v.stderr)
        t("stderr says tampered",
          "tampered" in v.stderr)


def test_missing_event_type_errors():
    print("\n[emit without event_type → usage error]")
    with tempfile.TemporaryDirectory() as td:
        audit = Path(td) / "audit.jsonl"
        proc = _run("emit", audit_path=audit)
        t("rc != 0", proc.returncode != 0)


def test_invalid_details_json_errors():
    print("\n[emit --details with malformed JSON → exit != 0]")
    with tempfile.TemporaryDirectory() as td:
        audit = Path(td) / "audit.jsonl"
        proc = _run("emit", "bridge.login", "--details", "{not json",
                     audit_path=audit)
        t("rc != 0", proc.returncode != 0)
        t("stderr mentions details / json",
          "json" in proc.stderr.lower() or "details" in proc.stderr.lower())


def main() -> int:
    test_emit_writes_one_record()
    test_two_emits_link()
    test_tamper_caught()
    test_missing_event_type_errors()
    test_invalid_details_json_errors()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
