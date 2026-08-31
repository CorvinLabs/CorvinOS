"""Per-subtask E2E — ADR-0020 Phase 30.1 (Engine-Trust-Härtung).

Covers the contract from docs/decisions/0020-engine-trust-hardening.md
and the must-NOT rules in the ADR's enforcement section. The dispatcher
wiring (Phase 30.1b) is NOT in scope; this suite only exercises the
data layer + verdict API + audit emission.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "operator" / "bridges" / "shared"))
sys.path.insert(0, str(REPO / "operator" / "forge"))

PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    suffix = f" — {detail}" if detail else ""
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{suffix}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


def _fresh_module():
    """Re-import engine_trust so module-level constants pick up env changes."""
    sys.modules.pop("engine_trust", None)
    return importlib.import_module("engine_trust")


def _iso(offset_days: int = 0) -> str:
    dt = datetime.now(timezone.utc) + timedelta(days=offset_days)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_yaml(path: Path, body: dict[str, Any]) -> None:
    import yaml as _y
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_y.safe_dump(body, sort_keys=False))


def _good_manifest(engine_id: str, *, tier: str = "high",
                   binary_sha256: str | None = None,
                   valid_until: str | None = None) -> dict[str, Any]:
    return {
        "apiVersion": "corvin/v1",
        "kind": "EngineTrust",
        "metadata": {
            "engine_id": engine_id,
            "trust_tier": tier,
            "evaluated_at": _iso(-10),
            "evaluated_against": "test-fixture",
            "valid_until": valid_until or _iso(180),
        },
        "spec": {
            "binary_sha256": binary_sha256,
            "jailbreak_resistance": 0.9,
            "system_prompt_respect": 0.9,
            "tool_call_fidelity": 0.95,
            "tested_refusal_classes": ["harmful_content"],
            "notes": "test fixture",
        },
    }


# ---------------------------------------------------------------------------
# Section 1 — Schema validation
# ---------------------------------------------------------------------------


def section_schema_validation() -> None:
    print("\n[1/7] Schema-Validation")
    et = _fresh_module()

    # Manifest aus Bundle laden (drei live shipped trust files)
    for engine_id in ("claude_code", "codex_cli", "opencode"):
        try:
            m = et.load_manifest(engine_id)
            t(f"bundle manifest loads for {engine_id}",
              m.metadata.engine_id == engine_id)
        except Exception as e:
            t(f"bundle manifest loads for {engine_id}", False, detail=repr(e))

    # extra=forbid — unknown top-level field
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CORVIN_HOME"] = tmp
        try:
            et2 = _fresh_module()
            override = Path(tmp) / "global" / "engine_trust" / "claude_code.yaml"
            body = _good_manifest("claude_code")
            body["unknown_top_level"] = "should-reject"
            _write_yaml(override, body)
            try:
                et2.load_manifest("claude_code")
                t("extra=forbid rejects unknown top-level", False)
            except et2.EngineTrustManifestMalformed:
                t("extra=forbid rejects unknown top-level", True)
        finally:
            os.environ.pop("CORVIN_HOME", None)

    # cross-field: metadata.engine_id must match requested id
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CORVIN_HOME"] = tmp
        try:
            et2 = _fresh_module()
            override = Path(tmp) / "global" / "engine_trust" / "claude_code.yaml"
            body = _good_manifest("not_claude_code")
            _write_yaml(override, body)
            try:
                et2.load_manifest("claude_code")
                t("cross-field engine_id mismatch raises", False)
            except et2.EngineTrustManifestMalformed:
                t("cross-field engine_id mismatch raises", True)
        finally:
            os.environ.pop("CORVIN_HOME", None)

    # binary_sha256 charset enforced
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CORVIN_HOME"] = tmp
        try:
            et2 = _fresh_module()
            override = Path(tmp) / "global" / "engine_trust" / "claude_code.yaml"
            body = _good_manifest("claude_code", binary_sha256="not-hex-too-short")
            _write_yaml(override, body)
            try:
                et2.load_manifest("claude_code")
                t("invalid binary_sha256 charset rejected", False)
            except et2.EngineTrustManifestMalformed:
                t("invalid binary_sha256 charset rejected", True)
        finally:
            os.environ.pop("CORVIN_HOME", None)

    # engine_id charset
    try:
        et.load_manifest("../../../etc/passwd")
        t("path-traversal engine_id rejected", False)
    except et.EngineTrustError:
        t("path-traversal engine_id rejected", True)


# ---------------------------------------------------------------------------
# Section 2 — Operator override beats bundle
# ---------------------------------------------------------------------------


def section_override_resolution() -> None:
    print("\n[2/7] Override-Resolution (operator > bundle)")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CORVIN_HOME"] = tmp
        try:
            et = _fresh_module()
            # Bundle says "high" for claude_code; override flips to "low"
            override = Path(tmp) / "global" / "engine_trust" / "claude_code.yaml"
            body = _good_manifest("claude_code", tier="low")
            _write_yaml(override, body)
            m = et.load_manifest("claude_code")
            t("override beats bundle (tier=low wins)",
              m.metadata.trust_tier == "low",
              detail=f"got {m.metadata.trust_tier}")
        finally:
            os.environ.pop("CORVIN_HOME", None)


# ---------------------------------------------------------------------------
# Section 3 — Tier-Gate verdict
# ---------------------------------------------------------------------------


def section_tier_gate() -> None:
    print("\n[3/7] Tier-Gate (verdict)")
    et = _fresh_module()

    # claude_code is high → passes min_tier=medium
    v = et.evaluate_trust("claude_code", min_tier="medium")
    t("high tier passes min=medium", v.passed and v.reason == "ok")

    # claude_code is high → passes min_tier=high
    v = et.evaluate_trust("claude_code", min_tier="high")
    t("high tier passes min=high", v.passed)

    # opencode is low → fails min_tier=medium
    v = et.evaluate_trust("opencode", min_tier="medium")
    t("low tier fails min=medium",
      (not v.passed) and v.reason == "trust-tier-too-low",
      detail=v.reason)

    # codex_cli is medium → fails min_tier=high
    v = et.evaluate_trust("codex_cli", min_tier="high")
    t("medium tier fails min=high",
      (not v.passed) and v.reason == "trust-tier-too-low")

    # invalid min_tier raises
    try:
        et.evaluate_trust("claude_code", min_tier="bogus")
        t("invalid min_tier raises", False)
    except et.EngineTrustError:
        t("invalid min_tier raises", True)


# ---------------------------------------------------------------------------
# Section 4 — Manifest expiry downgrades to low
# ---------------------------------------------------------------------------


def section_expiry_downgrade() -> None:
    print("\n[4/7] Manifest-Expiry → low downgrade")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CORVIN_HOME"] = tmp
        try:
            et = _fresh_module()
            override = Path(tmp) / "global" / "engine_trust" / "claude_code.yaml"
            # Expired 30 days ago, but declared as high
            body = _good_manifest("claude_code", tier="high",
                                  valid_until=_iso(-30))
            _write_yaml(override, body)

            # Without expiry: would pass min=medium. With expiry: downgrades to low → fails.
            v = et.evaluate_trust("claude_code", min_tier="medium")
            t("expired manifest with declared=high fails min=medium",
              (not v.passed) and v.expired and v.reason == "manifest-expired",
              detail=v.reason)
            t("expired effective_tier is 'low'",
              v.effective_tier == "low",
              detail=v.effective_tier)
            t("declared_tier preserved",
              v.declared_tier == "high")

            # An expired manifest still passes min=low (low ≥ low)
            v = et.evaluate_trust("claude_code", min_tier="low")
            t("expired manifest still passes min=low",
              v.passed and v.expired,
              detail=f"passed={v.passed} expired={v.expired}")
        finally:
            os.environ.pop("CORVIN_HOME", None)


# ---------------------------------------------------------------------------
# Section 5 — Binary hash check
# ---------------------------------------------------------------------------


def section_binary_hash() -> None:
    print("\n[5/7] Binary-SHA256 Pin")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CORVIN_HOME"] = tmp
        try:
            et = _fresh_module()
            # write a fake binary
            fake_bin = Path(tmp) / "claude"
            fake_bin.write_bytes(b"#!/bin/sh\necho fake\n")
            real_hash = hashlib.sha256(fake_bin.read_bytes()).hexdigest()

            # 5a — matching hash
            override = Path(tmp) / "global" / "engine_trust" / "claude_code.yaml"
            body = _good_manifest("claude_code", binary_sha256=real_hash)
            _write_yaml(override, body)
            v = et.evaluate_trust("claude_code", min_tier="low",
                                  current_binary_path=fake_bin)
            t("matching binary_sha256 passes",
              v.passed and v.binary_check == "matched")

            # 5b — mismatching hash
            wrong_hash = "0" * 64
            body2 = _good_manifest("claude_code", binary_sha256=wrong_hash)
            _write_yaml(override, body2)
            v = et.evaluate_trust("claude_code", min_tier="low",
                                  current_binary_path=fake_bin)
            t("mismatching binary_sha256 fails",
              (not v.passed) and v.reason == "binary-hash-mismatch",
              detail=v.reason)
            t("verdict carries expected + observed",
              v.expected_sha256 == wrong_hash and v.observed_sha256 == real_hash)

            # 5c — binary path missing
            body3 = _good_manifest("claude_code", binary_sha256=real_hash)
            _write_yaml(override, body3)
            v = et.evaluate_trust("claude_code", min_tier="low",
                                  current_binary_path=Path(tmp) / "nonexistent")
            t("missing binary path → binary-missing",
              (not v.passed) and v.reason == "binary-missing")

            # 5d — null binary_sha256 in manifest → check skipped
            body4 = _good_manifest("claude_code", binary_sha256=None)
            _write_yaml(override, body4)
            v = et.evaluate_trust("claude_code", min_tier="low",
                                  current_binary_path=fake_bin)
            t("null binary_sha256 → check skipped",
              v.passed and v.binary_check == "skipped")
        finally:
            os.environ.pop("CORVIN_HOME", None)


# ---------------------------------------------------------------------------
# Section 6 — Audit emission (allow-list + forbidden fields)
# ---------------------------------------------------------------------------


def section_audit_emission() -> None:
    print("\n[6/7] Audit-Emission + Allow-List")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CORVIN_HOME"] = tmp
        try:
            et = _fresh_module()
            audit = Path(tmp) / "audit.jsonl"

            # 6a — passed verdict emits nothing
            v = et.evaluate_trust("claude_code", min_tier="low")
            assert v.passed
            ev = et.emit_violation_event(v, audit_path=audit)
            t("passed verdict → no event", ev is None)
            t("audit file not created on pass", not audit.exists())

            # 6b — tier-violation emits trust_tier_violated
            v = et.evaluate_trust("opencode", min_tier="high")
            assert not v.passed and v.reason == "trust-tier-too-low"
            ev = et.emit_violation_event(v, audit_path=audit)
            t("tier-violation emits trust_tier_violated",
              ev == "engine.trust_tier_violated", detail=str(ev))
            chain = [json.loads(line) for line in audit.read_text().splitlines() if line]
            t("audit chain has one entry", len(chain) == 1)
            ev_rec = chain[0]
            t("event details have engine_id", "engine_id" in ev_rec["details"])
            t("event details have actual_tier", "actual_tier" in ev_rec["details"])
            t("event details have min_tier", "min_tier" in ev_rec["details"])

            # 6c — manifest-missing emits trust_manifest_missing
            v = et.evaluate_trust("nonexistent_engine", min_tier="low")
            assert not v.passed and v.reason == "manifest-missing"
            ev = et.emit_violation_event(v, audit_path=audit)
            t("manifest-missing emits trust_manifest_missing",
              ev == "engine.trust_manifest_missing")

            # 6d — expired manifest emits trust_manifest_expired
            override = Path(tmp) / "global" / "engine_trust" / "claude_code.yaml"
            body = _good_manifest("claude_code", tier="high",
                                  valid_until=_iso(-30))
            _write_yaml(override, body)
            et2 = _fresh_module()
            v = et2.evaluate_trust("claude_code", min_tier="medium")
            assert not v.passed
            ev = et2.emit_violation_event(v, audit_path=audit)
            t("expired manifest emits trust_manifest_expired",
              ev == "engine.trust_manifest_expired", detail=str(ev))

            # 6e — forbidden field rejected at boundary
            try:
                et2._validate_audit_details(
                    "engine.trust_tier_violated",
                    {"engine_id": "x", "actual_tier": "low",
                     "min_tier": "high", "manifest_body": "leaked!"},
                )
                t("forbidden field rejected", False, detail="no exception")
            except et2.EngineTrustAuditFieldNotAllowed:
                t("forbidden field rejected", True)

            # 6f — off-allowlist field rejected
            try:
                et2._validate_audit_details(
                    "engine.trust_tier_violated",
                    {"engine_id": "x", "actual_tier": "low",
                     "min_tier": "high", "uninvited": "x"},
                )
                t("off-allowlist field rejected", False)
            except et2.EngineTrustAuditFieldNotAllowed:
                t("off-allowlist field rejected", True)
        finally:
            os.environ.pop("CORVIN_HOME", None)


# ---------------------------------------------------------------------------
# Section 7 — Audit-event registered in EVENT_SEVERITY
# ---------------------------------------------------------------------------


def section_event_registry() -> None:
    print("\n[7/7] Audit-Event-Registry (security_events.EVENT_SEVERITY)")
    sys.modules.pop("forge.security_events", None)
    from forge import security_events as se
    for ev in (
        "engine.trust_tier_violated",
        "engine.trust_manifest_expired",
        "engine.binary_hash_mismatch",
        "engine.trust_manifest_missing",
    ):
        t(f"{ev} registered",
          ev in se.EVENT_SEVERITY,
          detail=se.EVENT_SEVERITY.get(ev, "<missing>"))
        if ev in se.EVENT_SEVERITY:
            t(f"{ev} severity is WARNING",
              se.EVENT_SEVERITY[ev] == "WARNING")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    print("=" * 60)
    print("test_engine_trust.py — ADR-0020 Phase 30.1")
    print("=" * 60)

    section_schema_validation()
    section_override_resolution()
    section_tier_gate()
    section_expiry_downgrade()
    section_binary_hash()
    section_audit_emission()
    section_event_registry()

    print()
    print(f"PASS={PASS}  FAIL={FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
