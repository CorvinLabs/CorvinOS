"""ADR-0052 F1 — E2E tests for the Compliance Assertion Layer.

Tests cover:
  - Each predicate: pass + deny cases
  - assert_compliant() raises ComplianceViolation on deny
  - check_compliant() returns (False, reason) without raising
  - run_predicate_self_test() returns (True, [])
  - Audit event emission (mock forge audit path)
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

# Make shared/ importable without installation
_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

from cal_predicates import (
    ALL_PREDICATES,
    check_all,
    _p_forge_policy_write_always_deny,
    _p_consent_self_grant_only,
    _p_session_reset_requires_audit,
    _p_compliance_mode_never_off,
    _p_disclosure_bypass_never,
    _p_audit_chain_integrity_never_skip,
    _p_social_no_spawn,
)
from compliance_assertion import (
    ComplianceViolation,
    assert_compliant,
    check_compliant,
    run_predicate_self_test,
)


# ── Predicate unit tests ──────────────────────────────────────────────────

class TestForgePolicy:
    def test_deny_on_forge_policy_write(self):
        for at in ("forge.policy_write", "forge_policy_write", "policy_write"):
            ok, reason = _p_forge_policy_write_always_deny(at, {})
            assert not ok
            assert reason

    def test_allow_unrelated_action(self):
        ok, _ = _p_forge_policy_write_always_deny("tool.create", {})
        assert ok


class TestConsentSelfGrant:
    def test_deny_cross_user_grant(self):
        ok, reason = _p_consent_self_grant_only(
            "consent.grant", {"grantor": "uid-A", "grantee": "uid-B"}
        )
        assert not ok
        assert "CAL-P2" in reason

    def test_allow_self_grant(self):
        ok, _ = _p_consent_self_grant_only(
            "consent.grant", {"grantor": "uid-X", "grantee": "uid-X"}
        )
        assert ok

    def test_allow_unrelated_action(self):
        ok, _ = _p_consent_self_grant_only("tool.create", {})
        assert ok

    def test_deny_missing_grantor(self):
        """FIX-10: deny when grantor is missing."""
        ok, reason = _p_consent_self_grant_only(
            "consent.grant", {"grantee": "uid-X"}
        )
        assert not ok
        assert "CAL-P2" in reason

    def test_deny_missing_grantee(self):
        """FIX-10: deny when grantee is missing."""
        ok, reason = _p_consent_self_grant_only(
            "consent.grant", {"grantor": "uid-X"}
        )
        assert not ok
        assert "CAL-P2" in reason

    def test_deny_empty_details(self):
        """FIX-10: deny when both are missing."""
        ok, reason = _p_consent_self_grant_only("consent.grant", {})
        assert not ok
        assert "CAL-P2" in reason


class TestSessionReset:
    def test_deny_without_audit_confirmed(self):
        ok, reason = _p_session_reset_requires_audit(
            "session.reset", {"audit_write_confirmed": False}
        )
        assert not ok

    def test_deny_missing_flag(self):
        ok, _ = _p_session_reset_requires_audit("session.reset", {})
        assert not ok

    def test_allow_with_audit_confirmed(self):
        ok, _ = _p_session_reset_requires_audit(
            "session.reset", {"audit_write_confirmed": True}
        )
        assert ok

    def test_allow_unrelated_action(self):
        ok, _ = _p_session_reset_requires_audit("tool.run", {})
        assert ok


class TestComplianceModeOff:
    @pytest.mark.parametrize("at", [
        "compliance.disable", "compliance_disable",
        "compliance.off", "compliance_off",
        "audit.disable", "audit_disable",
        "path_gate.disable", "consent_gate.disable",
    ])
    def test_deny_compliance_off_actions(self, at):
        ok, reason = _p_compliance_mode_never_off(at, {})
        assert not ok
        assert reason

    def test_allow_normal_action(self):
        ok, _ = _p_compliance_mode_never_off("session.reset", {})
        assert ok


class TestDisclosureBypass:
    @pytest.mark.parametrize("at", [
        "disclosure.skip", "disclosure_skip",
        "disclosure.disable", "disclosure.bypass",
    ])
    def test_deny_bypass(self, at):
        ok, reason = _p_disclosure_bypass_never(at, {})
        assert not ok

    def test_allow_normal(self):
        ok, _ = _p_disclosure_bypass_never("disclosure.delivered", {})
        assert ok


class TestSocialNoSpawn:
    """FIX-7: CAL-P7 social no-spawn predicate."""

    @pytest.mark.parametrize("at", [
        "social.spawn_worker",
        "social.post_spawn",
        "social.trigger_worker",
        "social.incoming_post_spawn",
    ])
    def test_deny_social_spawn_actions(self, at):
        ok, reason = _p_social_no_spawn(at, {})
        assert not ok
        assert "CAL-P7" in reason

    def test_allow_social_post_received(self):
        ok, _ = _p_social_no_spawn("social.post_received", {})
        assert ok

    def test_allow_unrelated_action(self):
        ok, _ = _p_social_no_spawn("forge.tool_create", {})
        assert ok

    def test_deny_via_check_all(self):
        ok, reason = check_all("social.spawn_worker", {})
        assert not ok
        assert "CAL-P7" in reason


class TestAuditChainIntegrity:
    @pytest.mark.parametrize("at", [
        "audit.skip_event", "audit_skip",
        "audit.truncate", "audit.delete_record", "audit.rewrite",
    ])
    def test_deny_chain_manipulation(self, at):
        ok, reason = _p_audit_chain_integrity_never_skip(at, {})
        assert not ok

    def test_allow_normal(self):
        ok, _ = _p_audit_chain_integrity_never_skip("audit.rotation_link", {})
        assert ok


# ── check_all / assert_compliant / check_compliant ────────────────────────

class TestCheckAll:
    def test_pass_benign_action(self):
        ok, reason = check_all("tool.create", {"name": "my_tool"})
        assert ok
        assert reason == ""

    def test_deny_first_predicate_hit(self):
        ok, reason = check_all("forge.policy_write", {})
        assert not ok
        assert reason


class TestAssertCompliant:
    def test_raises_on_violation(self):
        with pytest.raises(ComplianceViolation) as exc_info:
            assert_compliant("compliance.disable")
        assert "CAL violation" in str(exc_info.value)

    def test_no_raise_on_benign(self):
        assert_compliant("tool.create")  # must not raise

    def test_violation_has_action_type(self):
        try:
            assert_compliant("audit.skip_event")
        except ComplianceViolation as exc:
            assert exc.action_type == "audit.skip_event"
        else:
            pytest.fail("Expected ComplianceViolation")


class TestCheckCompliant:
    def test_returns_false_on_violation(self):
        ok, reason = check_compliant("forge.policy_write")
        assert not ok
        assert reason

    def test_returns_true_on_benign(self):
        ok, reason = check_compliant("skill.create")
        assert ok
        assert reason == ""


# ── Self-test ─────────────────────────────────────────────────────────────

class TestSelfTest:
    def test_self_test_passes(self):
        ok, failures = run_predicate_self_test()
        assert ok, f"Predicate self-test failed: {failures}"
        assert failures == []

    def test_predicate_count_matches(self):
        ok, failures = run_predicate_self_test()
        assert len(ALL_PREDICATES) >= 7  # at least 7: 6 original + CAL-P7 social_no_spawn


# ── Audit event emission E2E ──────────────────────────────────────────────

class TestAuditEmission:
    def test_violation_emits_audit_event(self, tmp_path):
        """Verify that a violation writes compliance_assertion.violated to the
        audit chain under a temp CORVIN_HOME."""
        audit_dir = tmp_path / "global" / "forge"
        audit_dir.mkdir(parents=True)
        audit_file = audit_dir / "audit.jsonl"

        original = os.environ.get("CORVIN_HOME")
        try:
            os.environ["CORVIN_HOME"] = str(tmp_path)
            try:
                assert_compliant("compliance.off")
            except ComplianceViolation:
                pass

            assert audit_file.exists(), "Audit file not created"
            import json
            events = [json.loads(line) for line in audit_file.read_text().splitlines() if line]
            types = [e.get("event_type") for e in events]
            assert "compliance_assertion.violated" in types, (
                f"Expected 'compliance_assertion.violated' in {types}"
            )
        finally:
            if original is None:
                os.environ.pop("CORVIN_HOME", None)
            else:
                os.environ["CORVIN_HOME"] = original
