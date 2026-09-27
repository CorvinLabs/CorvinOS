"""ADR-0232 "Phase 1A" skeleton — defused (adversarial review 2026-09-27).

The skeleton shipped a second audit chain (``AuditTrail``) and a second boot
tripwire checking that second chain. Both are now refused / delegated: there is
ONE chain per tenant (``forge.paths.tenant_audit_chain``) and ONE tripwire
(``corvin_compliance_reports.tripwire``). These tests pin that.
"""

from pathlib import Path
from unittest import mock

import pytest

from core.compliance import AuditRecord, AuditTrail, BootTripwire, ComplianceError
from core.compliance.audit_trail import new_audit_record, pseudonymise_actor
from core.compliance.boot_tripwire import assert_boot_compliance
from core.compliance.exceptions import TripwireError
from corvin_compliance_reports import tripwire as canonical_tripwire
from forge import paths as forge_paths


class TestAuditRecord:
    """AuditRecord stays an inert, immutable value type."""

    def _record(self, **kw):
        base = dict(
            timestamp="2026-09-26T20:32:00Z", event_type="consent_granted",
            tenant_id="default", actor="operator", action="approve",
            resource="skill:foo", result="allowed", details={"version": "1.0"},
        )
        base.update(kw)
        return AuditRecord(**base)

    def test_audit_record_frozen(self):
        with pytest.raises(AttributeError):
            self._record().timestamp = "2026-09-26T21:00:00Z"

    def test_audit_record_hash_deterministic(self):
        r = self._record()
        assert r.hash() == r.hash()

    def test_new_audit_record_never_carries_a_raw_user_id(self):
        rec = new_audit_record("consent_granted", "default", "alice@example.com",
                               "grant", "analytics", "allowed")
        assert rec.actor != "alice@example.com"
        assert rec.actor == pseudonymise_actor("alice@example.com")
        assert len(rec.actor) == 8

    def test_system_actor_is_kept(self):
        rec = new_audit_record("x", "default", "system", "a", "r", "allowed")
        assert rec.actor == "system"


class TestNoSecondChain:
    """AuditTrail must not be able to create a second chain."""

    def test_audit_trail_refuses(self, tmp_path):
        with pytest.raises(NotImplementedError, match="tenant_audit_chain"):
            AuditTrail(tmp_path / "audit.jsonl")
        assert not (tmp_path / "audit.jsonl").exists()


class TestBootTripwireDelegates:
    """BootTripwire is a facade over the canonical tripwire."""

    def test_refuses_to_vouch_for_another_root(self, tmp_path):
        other = tmp_path / "not-the-active-home"
        tw = BootTripwire(other)
        assert tw.run() is False
        assert tw.status()["all_pass"] is False
        with pytest.raises(TripwireError):
            tw.assert_all()
        # it created nothing there (the old skeleton wrote/checked <home>/audit.jsonl)
        assert not other.exists()

    def test_run_reports_canonical_results(self):
        home = forge_paths.corvin_home()
        fake = [canonical_tripwire.TripwireResult("audit_chain_intact", True, "ok"),
                canonical_tripwire.TripwireResult("consent_gate_denies_by_default", False, "x")]
        with mock.patch.object(canonical_tripwire, "check_all", return_value=fake):
            tw = BootTripwire(home)
            assert tw.run() is False
        assert [c["component"] for c in tw.status()["checks"]] == [
            "audit_chain_intact", "consent_gate_denies_by_default"]

    def test_run_passes_only_when_every_canonical_check_passes(self):
        fake = [canonical_tripwire.TripwireResult("audit_chain_intact", True, "ok")]
        with mock.patch.object(canonical_tripwire, "check_all", return_value=fake):
            assert BootTripwire(forge_paths.corvin_home()).run() is True

    def test_assert_all_wraps_canonical_failure(self):
        with mock.patch.object(canonical_tripwire, "assert_all",
                               side_effect=canonical_tripwire.TripwireError("chain broken")):
            with pytest.raises(TripwireError, match="chain broken"):
                assert_boot_compliance(forge_paths.corvin_home())

    def test_unrunnable_tripwire_fails_closed(self):
        with mock.patch.object(canonical_tripwire, "check_all", side_effect=RuntimeError("boom")):
            assert BootTripwire(forge_paths.corvin_home()).run() is False

    def test_tripwire_error_is_a_compliance_error(self):
        assert issubclass(TripwireError, ComplianceError)
