"""Per-subtask E2E for the license verifier — RS256 happy path + every
failure mode. Uses real cryptography library, real PyJWT, real disk."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from corvin_license import verifier


def test_fingerprint_is_short_and_deterministic():
    a = verifier.fingerprint_customer_id("cust-01HG-XYZ")
    b = verifier.fingerprint_customer_id("cust-01HG-XYZ")
    c = verifier.fingerprint_customer_id("cust-other")
    assert a == b
    assert a != c
    assert len(a) == 12
    assert all(ch in "0123456789abcdef" for ch in a)


def test_verify_valid_token_round_trip(pinned_pubkey, make_jwt, rs256_keypair):
    _priv, pub_pem, _, _ = rs256_keypair
    # ADR-0019: business tier's canonical flag set is the only valid input.
    token = make_jwt(customer_id="acme-01", tier="business",
                     employee_count_max=500, seats=100,
                     feature_flags=["compliance_reports_premium",
                                    "sso_wizard", "support_integration",
                                    "compute"])
    lic = verifier.verify_token(token, pubkey_pem=pub_pem)
    assert lic.customer_id == "acme-01"
    assert lic.tier == "business"
    assert lic.employee_count_max == 500
    assert lic.seats == 100
    assert lic.has_flag("compliance_reports_premium")
    assert lic.has_flag("sso_wizard")
    assert lic.has_flag("support_integration")
    # worm_archive is enterprise-only; not in business.
    assert not lic.has_flag("worm_archive")
    assert not lic.is_expired()


def test_verify_rejects_bad_signature(rs256_keypair, make_jwt):
    """Token signed with key A must not verify against key B's pubkey."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_pub = other.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    token = make_jwt()
    with pytest.raises(verifier.LicenseSignatureError):
        verifier.verify_token(token, pubkey_pem=other_pub)


def test_verify_rejects_hs256_algorithm(rs256_keypair):
    """A HS256-signed token must be rejected even with a 'valid' pubkey."""
    import jwt as _pyjwt
    token = _pyjwt.encode(
        {"iss": "corvin-maintainer", "iat": int(time.time()),
         "exp": int(time.time()) + 1000, "customer_id": "x",
         "tier": "pro", "employee_count_max": 10, "seats": 5},
        "shared-secret-only", algorithm="HS256")
    _priv, pub_pem, _, _ = rs256_keypair
    with pytest.raises(verifier.LicenseSignatureError) as exc:
        verifier.verify_token(token, pubkey_pem=pub_pem)
    assert "algorithm" in str(exc.value).lower()


def test_verify_rejects_expired_token(rs256_keypair, make_jwt):
    """Token whose exp is in the past must raise LicenseExpired."""
    _priv, pub_pem, _, _ = rs256_keypair
    # issued 100h ago, valid 1h → already expired
    token = make_jwt(valid_seconds=3600, issued_offset_s=-100 * 3600)
    with pytest.raises(verifier.LicenseExpired):
        verifier.verify_token(token, pubkey_pem=pub_pem)


def test_verify_rejects_wrong_issuer(rs256_keypair, make_jwt):
    _priv, pub_pem, _, _ = rs256_keypair
    token = make_jwt(issuer="other-maintainer")
    with pytest.raises(verifier.LicenseClaimError) as exc:
        verifier.verify_token(token, pubkey_pem=pub_pem)
    assert "issuer" in str(exc.value).lower()


def test_verify_rejects_invalid_tier(rs256_keypair, make_jwt):
    _priv, pub_pem, _, _ = rs256_keypair
    token = make_jwt(tier="god-mode")
    with pytest.raises(verifier.LicenseClaimError) as exc:
        verifier.verify_token(token, pubkey_pem=pub_pem)
    assert "tier" in str(exc.value).lower()


def test_verify_rejects_signed_free_tier(rs256_keypair, make_jwt):
    """A signed free-tier token is structurally meaningless — refuse."""
    _priv, pub_pem, _, _ = rs256_keypair
    token = make_jwt(tier="free")
    with pytest.raises(verifier.LicenseClaimError) as exc:
        verifier.verify_token(token, pubkey_pem=pub_pem)
    assert "free" in str(exc.value).lower()


def test_verify_rejects_employee_count_out_of_range(rs256_keypair, make_jwt):
    _priv, pub_pem, _, _ = rs256_keypair
    token = make_jwt(employee_count_max=0)
    with pytest.raises(verifier.LicenseClaimError):
        verifier.verify_token(token, pubkey_pem=pub_pem)


def test_verify_drops_unknown_feature_flags_silently(rs256_keypair, make_jwt):
    """Forward-compatible: unknown flags are dropped, not raised.

    ADR-0019: the kept-flags MUST still match the tier's canonical set,
    so we pass the full enterprise set PLUS one unknown future flag.
    The unknown flag drops before the Tier→Flag check; the canonical
    enterprise set survives → token verifies cleanly.
    """
    _priv, pub_pem, _, _ = rs256_keypair
    token = make_jwt(
        tier="enterprise",
        feature_flags=[
            "compliance_reports_premium", "cross_tenant_search",
            "sso_wizard", "worm_archive", "sla_dashboard",
            "support_integration", "white_label_ui",
            "compute", "compute_fabric",
            "future_v2_feature",  # unknown → dropped
        ],
    )
    lic = verifier.verify_token(token, pubkey_pem=pub_pem)
    assert "sso_wizard" in lic.feature_flags
    assert "white_label_ui" in lic.feature_flags
    assert "future_v2_feature" not in lic.feature_flags


def test_verify_rejects_tier_flag_drift(rs256_keypair, make_jwt):
    """ADR-0019 §Security #6: signed-but-off-tier flag set is rejected.

    Defence-in-depth: even if a signer compromise produced a
    cryptographically-valid JWT with a pro tier and worm_archive
    (enterprise-only), the verifier refuses to install it.
    """
    _priv, pub_pem, _, _ = rs256_keypair
    token = make_jwt(
        tier="pro",
        feature_flags=["compliance_reports_premium", "worm_archive"],
    )
    with pytest.raises(verifier.LicenseClaimError) as exc:
        verifier.verify_token(token, pubkey_pem=pub_pem)
    assert "tier-flag-drift" in str(exc.value)


def test_load_from_disk_missing_file(sandbox_home, pinned_pubkey):
    """No license.jwt → LicenseFileMissing (free tier signal, NOT error)."""
    with pytest.raises(verifier.LicenseFileMissing):
        verifier.load_license_from_disk()


def test_load_from_disk_world_readable_rejected(
    sandbox_home, pinned_pubkey, make_jwt, write_license_file,
):
    token = make_jwt()
    write_license_file(token, mode=0o644)
    with pytest.raises(verifier.LicenseFileMalformed) as exc:
        verifier.load_license_from_disk()
    assert "mode" in str(exc.value).lower()


def test_load_from_disk_empty_file_rejected(
    sandbox_home, pinned_pubkey, write_license_file,
):
    write_license_file("")
    with pytest.raises(verifier.LicenseFileMalformed):
        verifier.load_license_from_disk()


def test_load_from_disk_happy_path(
    sandbox_home, pinned_pubkey, make_jwt, write_license_file,
):
    token = make_jwt(customer_id="happy-01", tier="enterprise",
                     employee_count_max=10_000, seats=5000)
    write_license_file(token)
    lic = verifier.load_license_from_disk()
    assert lic.customer_id == "happy-01"
    assert lic.tier == "enterprise"


def test_public_dict_redacts_customer_id(rs256_keypair, make_jwt):
    _priv, pub_pem, _, _ = rs256_keypair
    token = make_jwt(customer_id="should-not-leak")
    lic = verifier.verify_token(token, pubkey_pem=pub_pem)
    pub = lic.to_public_dict()
    assert "customer_id" not in pub
    assert "customer_id_fingerprint" in pub
    assert pub["customer_id_fingerprint"] == verifier.fingerprint_customer_id("should-not-leak")
    assert "should-not-leak" not in str(pub)


# ── Trial-claim validation (community single-machine binding) ─────────
# _validate_claims lines ~427-473: trial_type/trial_expires_at/trial_id
# shape checks + machine_fp binding enforced ONLY for trial_type=="community".

def test_verify_rejects_community_trial_machine_fp_mismatch(
    rs256_keypair, make_jwt, monkeypatch,
):
    """A community trial JWT bound to machine A must not verify on machine B."""
    from corvin_license import trial as _trial
    _priv, pub_pem, _, _ = rs256_keypair
    monkeypatch.setattr(_trial, "machine_fingerprint", lambda: "a" * 32)
    token = make_jwt(
        trial_type="community",
        machine_fp="b" * 32,  # deliberately mismatched, valid shape
    )
    with pytest.raises(verifier.LicenseClaimError) as exc:
        verifier.verify_token(token, pubkey_pem=pub_pem)
    assert "machine_fp-mismatch" in str(exc.value)


def test_verify_accepts_community_trial_machine_fp_match(
    rs256_keypair, make_jwt, monkeypatch,
):
    """The same community trial JWT verifies fine on the machine it was issued for."""
    from corvin_license import trial as _trial
    _priv, pub_pem, _, _ = rs256_keypair
    monkeypatch.setattr(_trial, "machine_fingerprint", lambda: "a" * 32)
    token = make_jwt(trial_type="community", machine_fp="a" * 32)
    lic = verifier.verify_token(token, pubkey_pem=pub_pem)
    assert lic.trial_type == "community"
    assert lic.machine_fp == "a" * 32


def test_verify_accepts_business_trial_machine_fp_mismatch(
    rs256_keypair, make_jwt, monkeypatch,
):
    """Business trials are intentionally multi-machine — mismatch is NOT enforced.

    Locks in the asymmetry documented in verifier.py so a future 'fix'
    can't silently tighten business trials to single-machine too.
    """
    from corvin_license import trial as _trial
    _priv, pub_pem, _, _ = rs256_keypair
    monkeypatch.setattr(_trial, "machine_fingerprint", lambda: "a" * 32)
    token = make_jwt(trial_type="business", machine_fp="b" * 32)
    lic = verifier.verify_token(token, pubkey_pem=pub_pem)
    assert lic.trial_type == "business"
    # machine_fp is stored for operator inspection but never checked.
    assert lic.machine_fp == "b" * 32


def test_verify_rejects_trial_expires_at_beyond_exp(rs256_keypair, make_jwt):
    """trial_expires_at must not exceed the token's own exp claim."""
    _priv, pub_pem, _, _ = rs256_keypair
    now = int(time.time())
    token = make_jwt(
        trial_type="community",
        valid_seconds=3600,
        trial_expires_at=now + 3600 + 100,  # past exp
    )
    with pytest.raises(verifier.LicenseClaimError) as exc:
        verifier.verify_token(token, pubkey_pem=pub_pem)
    assert "trial_expires_at-exceeds-exp" in str(exc.value)


def test_verify_rejects_malformed_trial_id(rs256_keypair, make_jwt):
    _priv, pub_pem, _, _ = rs256_keypair
    token = make_jwt(trial_type="community", trial_id="not-a-valid-id!")
    with pytest.raises(verifier.LicenseClaimError) as exc:
        verifier.verify_token(token, pubkey_pem=pub_pem)
    assert "trial_id-shape" in str(exc.value)


# ── Clock-rollback guard (anti clock-manipulation / future-iat) ───────
# _validate_claims lines ~422-425: `now < issued_at - 300` → LicenseClaimError.

def test_verify_rejects_clock_before_issuance(rs256_keypair, make_jwt):
    """Verifying with `now` more than 300s before the token's iat is refused."""
    _priv, pub_pem, _, _ = rs256_keypair
    iat = int(time.time())
    token = make_jwt()
    with pytest.raises(verifier.LicenseClaimError) as exc:
        verifier.verify_token(token, pubkey_pem=pub_pem, now=iat - 301)
    assert "clock-before-issuance" in str(exc.value)


def test_verify_accepts_clock_within_ntp_drift_leeway(rs256_keypair, make_jwt):
    """A now inside the 300s NTP-drift leeway before iat still verifies."""
    _priv, pub_pem, _, _ = rs256_keypair
    iat = int(time.time())
    token = make_jwt()
    lic = verifier.verify_token(token, pubkey_pem=pub_pem, now=iat - 299)
    assert lic.customer_id == "cust-01HG-XYZ"


# ── seats bound (independent twin of employee_count_max-out-of-range) ─

@pytest.mark.parametrize("bad_seats", [0, 100_001])
def test_verify_rejects_seats_out_of_range(rs256_keypair, make_jwt, bad_seats):
    _priv, pub_pem, _, _ = rs256_keypair
    token = make_jwt(seats=bad_seats)
    with pytest.raises(verifier.LicenseClaimError) as exc:
        verifier.verify_token(token, pubkey_pem=pub_pem)
    assert "seats-out-of-range" in str(exc.value)
