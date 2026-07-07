"""E2E tests for ADR-0073 compliance gaps G-006, G-008, G-009, G-010.

G-006: identity_verification_mode boot warning
G-008: SBOM generation (CycloneDX 1.5)
G-009: Engine Trust Manifests for all 5 engines
G-010: Decision Registry — record, list, review, audit events
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SHARED = REPO / "operator" / "bridges" / "shared"
sys.path.insert(0, str(SHARED))
sys.path.insert(0, str(REPO / "operator" / "forge"))

PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    mark = "PASS" if ok else "FAIL"
    suffix = f" — {detail}" if detail else ""
    print(f"  {mark}  {label}{suffix}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


# ─────────────────────────────────────────────────────────────────────────────
# G-006 — identity_verification_mode
# ─────────────────────────────────────────────────────────────────────────────

def test_g006_identity_mode() -> None:
    print("\n── G-006: identity_verification_mode ──")

    # Import only the relevant function (does not need full adapter bootstrap)
    import importlib, types

    # We need to isolate _G006_WARNED_CHANNELS between calls
    # Import adapter module partially for the check function
    import adapter as _adapter
    _adapter._G006_WARNED_CHANNELS.clear()

    import logging
    warnings_seen: list[str] = []

    class _Handler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            warnings_seen.append(record.getMessage())

    h = _Handler()
    logger = logging.getLogger("corvin.adapter")
    logger.addHandler(h)
    logger.setLevel(logging.DEBUG)

    try:
        # platform_trust mode → should warn
        _adapter._check_identity_verification_mode("discord", {"identity_verification_mode": "platform_trust"})
        t("platform_trust emits WARNING",
          any("platform_trust" in w and "G-006" in w for w in warnings_seen))

        warnings_seen.clear()
        _adapter._G006_WARNED_CHANNELS.clear()

        # missing mode → defaults to platform_trust → should warn
        _adapter._check_identity_verification_mode("telegram", {})
        t("missing mode defaults to platform_trust warning",
          any("platform_trust" in w for w in warnings_seen))

        warnings_seen.clear()
        _adapter._G006_WARNED_CHANNELS.clear()

        # operator_verified → no warning
        _adapter._check_identity_verification_mode("whatsapp", {"identity_verification_mode": "operator_verified"})
        t("operator_verified mode: no warning",
          not any("platform_trust" in w for w in warnings_seen))

        warnings_seen.clear()
        _adapter._G006_WARNED_CHANNELS.clear()

        # unknown mode → error
        _adapter._check_identity_verification_mode("slack", {"identity_verification_mode": "magic_beans"})
        t("unknown mode emits ERROR",
          any("unknown identity_verification_mode" in w or "magic_beans" in w for w in warnings_seen))

        # Flood-protection: same channel called TWICE in a row → only warns once.
        # Set up: clear set, call discord once (warns), then clear warnings_seen only.
        _adapter._G006_WARNED_CHANNELS.clear()
        _adapter._check_identity_verification_mode("discord", {"identity_verification_mode": "platform_trust"})
        warnings_seen.clear()  # reset AFTER first call
        _adapter._check_identity_verification_mode("discord", {"identity_verification_mode": "platform_trust"})
        t("second call for same channel: no duplicate warning (flood protection)",
          not any("discord" in w for w in warnings_seen))

    finally:
        logger.removeHandler(h)


# ─────────────────────────────────────────────────────────────────────────────
# G-008 — SBOM generation
# ─────────────────────────────────────────────────────────────────────────────

def test_g008_sbom() -> None:
    print("\n── G-008: SBOM generation (CycloneDX 1.5) ──")
    from sbom_generator import build_sbom, check_freshness, _parse_requirements

    # The project dropped requirements.txt for pyproject.toml; sbom_generator
    # parses the [project] dependencies array directly from it (see
    # _parse_pyproject_dependencies).
    req_path = REPO / "pyproject.toml"
    t("pyproject.toml exists", req_path.exists())

    packages = _parse_requirements(req_path)
    t("parses ≥1 packages", len(packages) >= 1,
      detail=f"{len(packages)} found")

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "sbom.cdx.json"
        sbom = build_sbom(req_path)

        t("bomFormat = CycloneDX", sbom.get("bomFormat") == "CycloneDX")
        t("specVersion = 1.5", sbom.get("specVersion") == "1.5")
        t("has metadata.timestamp", bool(sbom.get("metadata", {}).get("timestamp")))
        t("has components list", isinstance(sbom.get("components"), list))
        t("component count matches packages",
          len(sbom["components"]) == len(packages),
          detail=f"{len(sbom['components'])} components, {len(packages)} packages")

        comp = sbom["components"][0] if sbom["components"] else {}
        t("component has purl", "purl" in comp)
        t("component has name", "name" in comp)
        t("component purl starts with pkg:pypi/",
          comp.get("purl", "").startswith("pkg:pypi/"))

        out.write_text(json.dumps(sbom, indent=2))
        ok, msg = check_freshness(out, max_age_days=1)
        t("freshness check passes for new SBOM", ok, detail=msg)

        # Old SBOM check
        old_sbom = {**sbom, "metadata": {**sbom["metadata"], "timestamp": "2020-01-01T00:00:00Z"}}
        old_out = Path(tmp) / "sbom_old.cdx.json"
        old_out.write_text(json.dumps(old_sbom))
        ok2, msg2 = check_freshness(old_out, max_age_days=90)
        t("freshness check fails for stale SBOM", not ok2, detail=msg2)

        # Missing SBOM
        ok3, _ = check_freshness(Path(tmp) / "nonexistent.cdx.json", max_age_days=90)
        t("freshness check fails for missing SBOM", not ok3)


# ─────────────────────────────────────────────────────────────────────────────
# G-009 — Engine Trust Manifests
# ─────────────────────────────────────────────────────────────────────────────

def test_g009_engine_manifests() -> None:
    print("\n── G-009: Engine Trust Manifests (Phase 30.1) ──")
    from engine_trust import load_manifest, evaluate_trust, EngineTrustManifestMissing

    engines = {
        "claude_code": "high",
        "hermes": "low",
        "opencode": "low",
        "codex_cli": "medium",
        "copilot": "medium",
    }

    for eid, expected_tier in engines.items():
        try:
            m = load_manifest(eid)
            t(f"{eid}: manifest loads", True)
            t(f"{eid}: trust_tier={expected_tier}",
              m.metadata.trust_tier == expected_tier,
              detail=f"got {m.metadata.trust_tier}")
            t(f"{eid}: has valid_until", bool(m.metadata.valid_until))
            t(f"{eid}: jailbreak_resistance in [0,1]",
              0.0 <= m.spec.jailbreak_resistance <= 1.0,
              detail=f"{m.spec.jailbreak_resistance}")
            t(f"{eid}: has tested_refusal_classes",
              len(m.spec.tested_refusal_classes) >= 1)
        except EngineTrustManifestMissing as e:
            t(f"{eid}: manifest loads", False, detail=str(e))
            t(f"{eid}: trust_tier={expected_tier}", False)
            t(f"{eid}: has valid_until", False)
            t(f"{eid}: jailbreak_resistance in [0,1]", False)
            t(f"{eid}: has tested_refusal_classes", False)

    # evaluate_trust returns passed=True for min_tier=low
    for eid in engines:
        try:
            v = evaluate_trust(eid, min_tier="low")
            t(f"{eid}: evaluate_trust(min_tier=low) passes",
              v.passed or (not v.passed and "expired" in (v.reason or "")),
              detail=f"passed={v.passed} reason={v.reason}")
        except Exception as e:
            t(f"{eid}: evaluate_trust(min_tier=low)", False, detail=str(e))

    # Hermes must fail at min_tier=high
    v = evaluate_trust("hermes", min_tier="high")
    t("hermes fails at min_tier=high (tier is low)", not v.passed,
      detail=f"reason={v.reason}")

    # claude_code must pass at min_tier=high
    v2 = evaluate_trust("claude_code", min_tier="high")
    t("claude_code passes at min_tier=high", v2.passed,
      detail=f"reason={v2.reason}")


# ─────────────────────────────────────────────────────────────────────────────
# G-010 — Decision Registry
# ─────────────────────────────────────────────────────────────────────────────

def test_g010_decision_registry() -> None:
    print("\n── G-010: Decision Registry ──")
    from decision_registry import (
        detect_significant_decision,
        extract_decision_class,
        generate_decision_id,
        record_decision,
        get_decision,
        list_pending_decisions,
        mark_reviewed,
        build_decision_prefix,
        DecisionRecord,
    )

    # Detection
    t("detects [decision:significant]",
      detect_significant_decision("Please help me decide [decision:significant] about X"))
    t("case-insensitive detection",
      detect_significant_decision("[DECISION:SIGNIFICANT] do this"))
    t("no false positive on plain text",
      not detect_significant_decision("This is a normal message"))
    t("extract_decision_class returns 'significant'",
      extract_decision_class("[decision:significant] X") == "significant")
    t("extract_decision_class returns custom class",
      extract_decision_class("[decision:credit_score] X") == "credit_score")

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)

        # Record a limited_risk decision
        did = generate_decision_id()
        rec = record_decision(
            decision_id=did,
            channel="discord",
            chat_key="test:123",
            risk_tier="limited_risk",
            engine_id="claude_code",
            persona="assistant",
            decision_class="significant",
            audit_path=tmpdir,
        )
        t("record_decision returns DecisionRecord", isinstance(rec, DecisionRecord))
        t("limited_risk: review_status=none", rec.review_status == "none")

        # Registry file created
        reg = tmpdir / "decision_registry.jsonl"
        t("decision_registry.jsonl created", reg.exists())

        # get_decision
        fetched = get_decision(did, registry_path=tmpdir)
        t("get_decision returns record", fetched is not None)
        t("get_decision: correct decision_id",
          fetched is not None and fetched["decision_id"] == did)

        # Record a high_risk decision
        did2 = generate_decision_id()
        rec2 = record_decision(
            decision_id=did2,
            channel="discord",
            chat_key="test:123",
            risk_tier="high_risk",
            engine_id="claude_code",
            persona="assistant",
            decision_class="significant",
            audit_path=tmpdir,
        )
        t("high_risk: review_status=pending", rec2.review_status == "pending")

        # list_pending_decisions
        pending = list_pending_decisions(registry_path=tmpdir)
        t("list_pending returns 1 pending (high_risk)", len(pending) == 1)
        t("pending decision is the high_risk one",
          pending[0]["decision_id"] == did2)

        # build_decision_prefix
        prefix_lr = build_decision_prefix(rec)
        prefix_hr = build_decision_prefix(rec2)
        t("limited_risk prefix contains decision ID", did[:8] in prefix_lr)
        t("high_risk prefix contains AWAITING HUMAN REVIEW",
          "AWAITING HUMAN REVIEW" in prefix_hr)
        t("high_risk prefix contains /decision-review",
          "/decision-review" in prefix_hr)

        # mark_reviewed
        ok = mark_reviewed(
            did2,
            reviewer_hash="abc123",
            outcome="approved",
            registry_path=tmpdir,
        )
        t("mark_reviewed returns True", ok)

        # Check no longer pending
        pending2 = list_pending_decisions(registry_path=tmpdir)
        t("after review: no pending decisions", len(pending2) == 0)

        # Reject invalid outcome
        try:
            mark_reviewed(did, reviewer_hash="x", outcome="maybe", registry_path=tmpdir)
            t("invalid outcome raises ValueError", False)
        except ValueError:
            t("invalid outcome raises ValueError", True)

        # get_decision on non-existent
        none_rec = get_decision("nonexistent-id", registry_path=tmpdir)
        t("get_decision returns None for unknown ID", none_rec is None)

        # Empty registry
        pending3 = list_pending_decisions(registry_path=Path(tmp) / "nonexistent")
        t("list_pending on missing registry returns []", pending3 == [])


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    os.environ.setdefault("CORVIN_HOME", tempfile.mkdtemp())
    os.environ.setdefault("CORVIN_TENANT_ID", "_default")

    test_g006_identity_mode()
    test_g008_sbom()
    test_g009_engine_manifests()
    test_g010_decision_registry()

    print(f"\n{'─' * 48}")
    print(f"TOTAL: {PASS} passed, {FAIL} failed")
    sys.exit(0 if FAIL == 0 else 1)
