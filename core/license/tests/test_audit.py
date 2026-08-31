"""Per-subtask E2E for license audit emitters.

Verifies the metadata-only-rule: customer_id (full), token, JWT body,
or signing material MUST NEVER appear in the audit chain. All five
event types ship with allow-lists enforced at the emit boundary.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from corvin_license import audit as license_audit


def _read_chain(sandbox_home) -> list[dict]:
    chain = sandbox_home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    if not chain.exists():
        return []
    return [json.loads(line) for line in chain.read_text().splitlines() if line.strip()]


def test_license_activated_happy_path(sandbox_home):
    license_audit.license_activated(
        tier="pro",
        customer_fp="abc123def456",
        valid_until=2_000_000_000,
        issued_at=1_700_000_000,
        employee_count_max=250,
        seats=50,
        feature_flags=["compliance_reports_premium"],
    )
    events = _read_chain(sandbox_home)
    assert len(events) == 1
    ev = events[0]
    assert ev["event_type"] == "license.activated"
    assert ev["severity"] == "INFO"
    assert ev["details"]["tier"] == "pro"
    assert ev["details"]["customer_fp"] == "abc123def456"


def test_audit_rejects_full_customer_id(sandbox_home):
    """customer_id (raw) is on the forbidden field set."""
    with pytest.raises(license_audit.LicenseAuditFieldNotAllowed):
        license_audit._emit(
            "license.activated",
            customer_id="full-uuid-here",  # forbidden
        )


def test_audit_rejects_token_field(sandbox_home):
    for forbidden in ("token", "jwt", "license_jwt", "signing_key",
                      "privkey", "secret"):
        with pytest.raises(license_audit.LicenseAuditFieldNotAllowed):
            license_audit._emit("license.activated", **{forbidden: "x"})


def test_audit_rejects_off_allowlist_field(sandbox_home):
    """A spurious 'extra_data' field on license.activated is rejected."""
    with pytest.raises(license_audit.LicenseAuditFieldNotAllowed):
        license_audit._emit(
            "license.activated",
            tier="pro",
            customer_fp="abc",
            extra_data="leak this please",  # off allowlist
        )


def test_license_violated_with_curated_reasons(sandbox_home):
    for reason in ("signature-invalid", "claim-missing", "grace-exhausted"):
        license_audit.license_violated(reason=reason)
    events = _read_chain(sandbox_home)
    assert len(events) == 3
    for ev in events:
        assert ev["event_type"] == "license.violated"
        assert ev["severity"] == "WARNING"


def test_license_violated_rejects_uncurated_reason(sandbox_home):
    with pytest.raises(license_audit.LicenseAuditFieldNotAllowed):
        license_audit.license_violated(reason="just-because")


def test_license_grace_started_metadata_only(sandbox_home):
    license_audit.license_grace_started(
        tier="business",
        customer_fp="abc123def456",
        grace_ends_at=2_000_000_000,
    )
    events = _read_chain(sandbox_home)
    assert len(events) == 1
    assert events[0]["event_type"] == "license.grace_started"


def test_license_revoked(sandbox_home):
    license_audit.license_revoked(customer_fp="abc", reason="operator-revoke")
    events = _read_chain(sandbox_home)
    assert events[-1]["event_type"] == "license.revoked"
    assert events[-1]["details"]["reason"] == "operator-revoke"


def test_hash_chain_integrity_holds(sandbox_home):
    """Five emit calls should produce a verifiable chain."""
    license_audit.license_activated(
        tier="pro", customer_fp="a", valid_until=2_000_000_000,
        issued_at=1_700_000_000, employee_count_max=10, seats=5,
        feature_flags=[],
    )
    license_audit.license_expired(tier="pro", customer_fp="a",
                                   expired_at=1_999_999_000)
    license_audit.license_grace_started(tier="pro", customer_fp="a",
                                         grace_ends_at=2_001_000_000)
    license_audit.license_violated(reason="grace-exhausted")
    license_audit.license_revoked(customer_fp="a", reason="renewal")

    from forge import security_events
    chain_path = sandbox_home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    ok, problems = security_events.verify_chain(chain_path)
    assert ok, f"chain broken: {problems}"
