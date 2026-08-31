"""Mandatory-MAC enforcement tests (ADR-0137 M2 / R2-04 / R3-03).

Locks in verify_chain's keyed-MAC behaviour, which shipped untested:
  * an all-mac chain verifies clean,
  * a forged/wrong mac is caught (mac_tampered),
  * a mac stripped from a record after the MAC-epoch is caught (mac_missing),
  * a fully mac-stripped LIVE chain is caught (mac_stripped_chain).

The anchor key is pointed at a throwaway FILE (CORVIN_AUDIT_ANCHOR_KEY is a
*path*, not a hex value) BEFORE importing security_events, so every write_event
attaches a verifiable mac and the sentinel lands in the tempdir.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="mandatory-mac-"))
_KEY = _TMP / "audit_anchor.key"
_KEY.write_bytes(b"\x5a" * 32)
os.environ["CORVIN_AUDIT_ANCHOR_KEY"] = str(_KEY)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from forge.security_events import write_event, verify_chain  # noqa: E402

PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


def _issues(problems) -> set[str]:
    return {p.get("issue") for p in problems}


def test_all_mac_chain_verifies():
    print("\n[all-mac chain verifies]")
    p = _TMP / "audit.jsonl"  # live-chain name so the strip detector is active
    p.write_text("")
    macs = 0
    for i in range(4):
        r = write_event(p, "tool.created", tool=f"x{i}")
        macs += 1 if "mac" in r else 0
    t("write_event attaches a mac to every record", macs == 4, detail=f"{macs}/4")
    ok, problems = verify_chain(p)
    t("clean all-mac chain verifies", ok and not problems, detail=str(_issues(problems)))


def test_tampered_mac_caught():
    print("\n[forged mac → mac_tampered]")
    p = _TMP / "audit_tamper.jsonl"
    p.write_text("")
    for i in range(3):
        write_event(p, "tool.created", tool=f"y{i}")
    lines = p.read_text().splitlines()
    rec = json.loads(lines[-1])
    rec["mac"] = "0" * 16  # forge a wrong mac (attacker can't compute the real one)
    lines[-1] = json.dumps(rec)
    p.write_text("\n".join(lines) + "\n")
    ok, problems = verify_chain(p)
    t("forged mac flagged mac_tampered",
      (not ok) and "mac_tampered" in _issues(problems), detail=str(_issues(problems)))


def test_missing_mac_after_epoch_caught():
    print("\n[mac stripped after epoch → mac_missing]")
    p = _TMP / "audit_missing.jsonl"
    p.write_text("")
    write_event(p, "tool.created", tool="a")  # mac'd → MAC-epoch starts here
    write_event(p, "tool.created", tool="b")
    write_event(p, "tool.created", tool="c")
    lines = p.read_text().splitlines()
    rec = json.loads(lines[1])  # strip the mac off the middle record
    rec.pop("mac", None)
    lines[1] = json.dumps(rec)
    p.write_text("\n".join(lines) + "\n")
    ok, problems = verify_chain(p)
    t("post-epoch missing mac flagged mac_missing",
      (not ok) and "mac_missing" in _issues(problems), detail=str(_issues(problems)))


def test_full_strip_caught():
    print("\n[every mac stripped on live chain → mac_stripped_chain]")
    p = _TMP / "audit.jsonl"  # MUST be the live-chain name for the strip detector
    p.write_text("")
    for i in range(4):
        write_event(p, "tool.created", tool=f"z{i}")
    lines = p.read_text().splitlines()
    stripped = []
    for ln in lines:
        rec = json.loads(ln)
        rec.pop("mac", None)  # silent downgrade to hash-only
        stripped.append(json.dumps(rec))
    p.write_text("\n".join(stripped) + "\n")
    ok, problems = verify_chain(p)
    t("fully-stripped live chain flagged mac_stripped_chain",
      (not ok) and "mac_stripped_chain" in _issues(problems), detail=str(_issues(problems)))


def test_legacy_zero_mac_chain_is_exempt():
    print("\n[legacy zero-mac chain (no per-chain marker) is NOT flagged]")
    # A chain that NEVER wrote a mac (e.g. a session that ran no mac-writing
    # tool) has no per-chain marker, so the full-strip detector must exempt it —
    # even though the host sentinel exists (another chain wrote a mac above).
    p = _TMP / "legacy_session" / "audit.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("")
    # Write hash-chained records WITHOUT going through write_event's mac path by
    # disabling the chain mac: use hash_chain but the key is present, so to get a
    # genuinely mac-free legacy record we write via write_event then strip — but
    # crucially NEVER stamp this chain's marker. Simulate by writing to a path
    # whose marker we ensure is absent.
    for i in range(3):
        write_event(p, "tool.created", tool=f"n{i}")
    # Strip any macs so the chain carries zero — a legacy/never-mac'd shape.
    lines = [json.loads(x) for x in p.read_text().splitlines()]
    for rec in lines:
        rec.pop("mac", None)
    p.write_text("\n".join(json.dumps(r) for r in lines) + "\n")
    # Remove this chain's per-chain marker to model a chain that never had one.
    from forge.security_events import _mac_chain_marker_path
    mk = _mac_chain_marker_path(p)
    if mk.exists():
        mk.unlink()
    ok, problems = verify_chain(p)
    t("legacy zero-mac chain not flagged mac_stripped_chain",
      "mac_stripped_chain" not in _issues(problems), detail=str(_issues(problems)))


def main() -> int:
    test_all_mac_chain_verifies()
    test_tampered_mac_caught()
    test_missing_mac_after_epoch_caught()
    test_full_strip_caught()
    test_legacy_zero_mac_chain_is_exempt()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
