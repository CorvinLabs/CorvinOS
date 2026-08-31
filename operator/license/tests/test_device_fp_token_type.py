"""Regression (project review R1 #14/#12): the member single-device fail-closed
must apply ONLY to the device-bound session_permit, not to the emailed
type:"license" entitlement token (which by design never carries device_fp), and
must compare the CANONICAL tier so a legacy tier="universal" permit cannot bypass
the binding."""
import importlib

validator = importlib.import_module("license.validator")


def test_emailed_member_license_without_device_fp_is_accepted():
    # type:"license" entitlement — no device_fp by design → must NOT fail closed.
    claims = {"tier": "member", "type": "license", "jti": "abc"}
    assert validator._check_device_fp(claims) is True


def test_member_session_permit_without_device_fp_is_rejected():
    # device-bound permit missing its device_fp → genuine issuance bug → closed.
    claims = {"tier": "member", "type": "session_permit", "jti": "abc"}
    assert validator._check_device_fp(claims) is False


def test_universal_session_permit_without_device_fp_is_rejected_canonically():
    # legacy "universal" canonicalizes to member → must NOT bypass the binding.
    claims = {"tier": "universal", "type": "session_permit", "jti": "abc"}
    assert validator._check_device_fp(claims) is False


def test_free_tier_token_without_device_fp_is_accepted():
    claims = {"tier": "free", "type": "license", "jti": "abc"}
    assert validator._check_device_fp(claims) is True
