"""Tests for HIGH-001: Device Binding Asymmetry — PARTIALLY REVERTED 2026-09-14.

HIGH-001 read the two token types' different device_fp handling as an asymmetry
hole and made a MISSING device_fp fail closed for type:license too. That half was
wrong and is reverted: the asymmetry is the token LIFECYCLE. Corvin-Features mints
the emailed entitlement (license_issuance._make_license_jwt) without device_fp
because the device is unknown at purchase time; the device identity first exists at
/v1/subscriptions/activate, which consumes that license JWT plus a machine_fp and
mints the device-bound session_permit. Requiring the claim a step earlier rejected
every paying member at "Apply Key" and, since the console stores license.key only
after the check passes, removed the only path that could ever produce a device_fp.
Seat enforcement belongs at activation (SubscriptionStore.activate →
DeviceAlreadyBound), not in this local check.

What HIGH-001 got right and what these tests still pin:
1. type:license tokens WITH device_fp are accepted/rejected on device match —
   the mismatch branch is symmetric across both token types (kept).
2. type:license tokens WITHOUT device_fp are accepted — issuer contract (reverted).
3. type:session_permit WITHOUT device_fp stays fail-closed — there it is a real
   issuance bug.
4. Grace-period recheck applies to both token types (kept).

See also tests/test_device_fp_token_type.py (review R1 #14), which asserted the
issuer contract all along and was silently red while HIGH-001 stood.
"""
from __future__ import annotations

import json
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from limits import FREE_TIER, LicenseLimitError
from validator import _check_device_fp, _verify_ed25519, canonical_tier


class TestDeviceBindingSymmetryFix(unittest.TestCase):
    """Device binding: mismatch is enforced for both token types; a MISSING
    device_fp is fail-closed only for the session_permit that can carry one."""

    def test_member_tier_license_token_without_device_fp_accepted(self):
        """type:license on member tier WITHOUT device_fp → accepted (issuer contract).

        Reverts the HIGH-001 half that rejected this. Corvin-Features never mints
        device_fp into type:"license"; rejecting it locked out every paying member
        and blocked the activation path that mints the bound session_permit.
        """
        claims = {
            "type": "license",
            "tier": "member",
            "device_fp": None,  # never minted into the emailed entitlement
            "jti": "token-123456",
        }
        result = _check_device_fp(claims)
        self.assertTrue(
            result, "Emailed member entitlement carries no device_fp by design — must be accepted"
        )

    def test_member_tier_session_permit_without_device_fp_rejected(self):
        """type:session_permit token on member tier WITHOUT device_fp → fail-closed (FREE tier)."""
        claims = {
            "type": "session_permit",
            "tier": "member",
            "device_fp": None,  # Missing device binding
            "jti": "token-123456",
        }
        # Existing behavior (regression test)
        result = _check_device_fp(claims)
        self.assertFalse(result, "Member-tier session_permit token without device_fp must be rejected")

    def test_free_tier_license_token_without_device_fp_accepted(self):
        """type:license token on free tier WITHOUT device_fp → allowed (no binding required)."""
        claims = {
            "type": "license",
            "tier": "free",
            "device_fp": None,  # Free tier doesn't require binding
            "jti": "token-123456",
        }
        result = _check_device_fp(claims)
        self.assertTrue(result, "Free-tier license token without device_fp is allowed")

    @patch("validator._local_device_fp")
    def test_member_tier_license_token_matching_device_accepted(self, mock_local_fp):
        """type:license token on member tier WITH matching device_fp → accepted."""
        mock_local_fp.return_value = "device-fp-abc123"
        claims = {
            "type": "license",
            "tier": "member",
            "device_fp": "device-fp-abc123",  # Matches local device
            "jti": "token-123456",
        }
        result = _check_device_fp(claims)
        self.assertTrue(result, "Member-tier license token with matching device_fp must be accepted")

    @patch("validator._local_device_fp")
    def test_member_tier_license_token_mismatched_device_rejected(self, mock_local_fp):
        """type:license token on member tier with mismatched device_fp → rejected."""
        mock_local_fp.return_value = "device-fp-abc123"
        claims = {
            "type": "license",
            "tier": "member",
            "device_fp": "device-fp-different",  # Different device
            "jti": "token-123456",
        }
        result = _check_device_fp(claims)
        self.assertFalse(result, "Member-tier license token with mismatched device_fp must be rejected")

    def test_canonical_tier_universal_treated_as_member(self):
        """Legacy tier='universal' canonicalizes to member (must enforce device binding).

        Asserted on the session_permit — the token type the missing-claim gate
        applies to — so the canonicalization check survives the HIGH-001 revert
        (review R1 #12: a literal "member" comparison let "universal" through).
        """
        claims = {
            "type": "session_permit",
            "tier": "universal",  # Legacy tier
            "device_fp": None,
            "jti": "token-123456",
        }
        result = _check_device_fp(claims)
        self.assertFalse(
            result, "Legacy tier='universal' must be treated as member and require device_fp"
        )

    def test_enterprise_tier_mapped_to_member(self):
        """Enterprise tier is canonicalized to member and requires device binding."""
        claims = {
            "type": "session_permit",
            "tier": "enterprise",  # Maps to member via canonical_tier
            "device_fp": None,
            "jti": "token-123456",
        }
        result = _check_device_fp(claims)
        # Enterprise maps to member, so the missing-device_fp gate applies
        self.assertFalse(
            result, "Enterprise tier (mapped to member) requires device binding"
        )

    def test_unknown_tier_fails_closed_to_free(self):
        """Unknown tier name fails closed to free and doesn't require device binding."""
        claims = {
            "type": "session_permit",
            "tier": "unknown-future-tier",  # Unknown tier fails-closed to free
            "device_fp": None,
            "jti": "token-123456",
        }
        result = _check_device_fp(claims)
        # Unknown tier fails-closed to free, so no device binding required
        self.assertTrue(result, "Unknown tier fails-closed to free, no device binding required")

    def test_audit_event_includes_token_type(self):
        """Device binding failure audit event includes token type for both token types."""
        with patch("validator._audit") as mock_audit:
            claims = {
                "type": "session_permit",
                "tier": "member",
                "device_fp": None,
                "jti": "token-abc123",
            }
            _check_device_fp(claims)
            # Verify audit event was called with token_type
            mock_audit.assert_called()
            call_kwargs = mock_audit.call_args[1]
            self.assertIn(
                "token_type", call_kwargs, "Audit event must include token_type field (HIGH-001 fix)"
            )
            self.assertEqual(call_kwargs["token_type"], "session_permit")


class TestGracePeriodRecheckBothTokenTypes(unittest.TestCase):
    """Verify grace-period device binding checks apply to both token types."""

    @patch("validator._check_session_grace_period")
    @patch("validator._local_device_fp")
    @patch("validator._check_instance_id_bound")
    @patch("validator._audit")
    def test_grace_period_device_fp_check_for_license_token(
        self, mock_audit, mock_instance_id, mock_local_fp, mock_grace
    ):
        """Grace-period recheck must apply device_fp check for type:license (HIGH-001)."""
        mock_grace.return_value = True
        mock_instance_id.return_value = True
        mock_local_fp.return_value = "device-abc"

        claims = {
            "type": "license",  # type:license must also get device_fp recheck
            "tier": "member",
            "device_fp": "device-xyz",  # Mismatch
            "jti": "token-123",
            "iss": "corvinlabs.io",
            "exp": int(time.time()) - 100,  # Expired
        }

        result = _check_device_fp(claims)
        # Device mismatch during grace period should be rejected
        self.assertFalse(result)

    @patch("validator._check_session_grace_period")
    @patch("validator._local_device_fp")
    @patch("validator._check_instance_id_bound")
    def test_grace_period_device_fp_check_for_session_permit(
        self, mock_instance_id, mock_local_fp, mock_grace
    ):
        """Grace-period recheck must apply device_fp check for type:session_permit (existing)."""
        mock_grace.return_value = True
        mock_instance_id.return_value = True
        mock_local_fp.return_value = "device-abc"

        claims = {
            "type": "session_permit",
            "tier": "member",
            "device_fp": "device-xyz",  # Mismatch
            "jti": "token-123",
            "iss": "corvinlabs.io",
            "exp": int(time.time()) - 100,  # Expired
        }

        result = _check_device_fp(claims)
        # Device mismatch during grace period should be rejected
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
