"""Unit tests for ADR-0092 — operator/license/ package (M1 + M2.5).

Tests cover:
- FREE_TIER defaults when no key is loaded
- assert_limit() semantics for bool/list/int limits
- LicenseLimitError payload
- load_license_from_env() with mock token

Does NOT require network, the private signing key, or the cryptography package
(crypto tests use the public key validation path).
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

# Ensure operator/ is on the path regardless of where pytest is invoked.
_HERE = Path(__file__).resolve().parent
_OPERATOR = _HERE.parents[1]
if str(_OPERATOR) not in sys.path:
    sys.path.insert(0, str(_OPERATOR))

from license.limits import FREE_TIER, LicenseLimitError  # noqa: E402
import license.validator as _v  # noqa: E402


def _b64url(raw: bytes) -> str:
    """Unpadded URL-safe base64 — matches validator._b64_decode's tolerant input."""
    import base64
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _reload_validator():
    """Reset module-level state between tests."""
    _v._set_active_license(None)   # also resets _ACTIVE_LICENSE_CANARY
    _v._LICENSE_LOADED_AT = 0.0
    _v._LAST_RELOAD_AT = 0.0


class TestFreeTierDefaults(unittest.TestCase):
    def setUp(self):
        _reload_validator()

    def test_get_limit_returns_free_tier_when_no_key(self):
        self.assertEqual(_v.get_limit("a2a_peers_max"), FREE_TIER["a2a_peers_max"])
        self.assertEqual(_v.get_limit("compute_units_per_day"), FREE_TIER["compute_units_per_day"])
        self.assertEqual(_v.get_limit("engines_allowed"), FREE_TIER["engines_allowed"])

    def test_get_limit_unknown_feature_returns_zero(self):
        # Unknown features must return 0 (denied / fail-closed), not None (unlimited).
        # FREE_TIER.get(feature, 0) distinguishes "explicitly None = unlimited" from
        # "not in FREE_TIER at all = unknown = 0".
        self.assertEqual(_v.get_limit("nonexistent_feature_xyz"), 0)

    def test_active_tier_is_free_by_default(self):
        self.assertEqual(_v.active_tier(), "free")

    def test_is_loaded_false_by_default(self):
        self.assertFalse(_v.is_loaded())


class TestAssertLimitNumeric(unittest.TestCase):
    def setUp(self):
        _reload_validator()

    def test_numeric_within_limit_passes(self):
        # Free tier: a2a_peers_max = 1; requesting 1 is fine
        _v.assert_limit("a2a_peers_max", 1)  # no raise

    def test_numeric_exceeds_limit_raises(self):
        with self.assertRaises(LicenseLimitError) as ctx:
            _v.assert_limit("a2a_peers_max", 2)
        e = ctx.exception
        self.assertEqual(e.feature, "a2a_peers_max")
        self.assertEqual(e.requested, 2)
        self.assertEqual(e.limit, FREE_TIER["a2a_peers_max"])
        self.assertEqual(e.tier, "free")

    def test_default_requested_is_1(self):
        # compute_units_per_day free tier = 1; default requested=1 → ok
        _v.assert_limit("compute_units_per_day")  # no raise

    def test_zero_requested_always_passes(self):
        _v.assert_limit("a2a_peers_max", 0)  # 0 ≤ 1 → ok


class TestAssertLimitList(unittest.TestCase):
    def setUp(self):
        _reload_validator()

    def test_free_tier_all_engines_allowed(self):
        # FREE_TIER["engines_allowed"] = None → no restriction on free tier
        _v.assert_limit("engines_allowed", "claude")
        _v.assert_limit("engines_allowed", "hermes")
        _v.assert_limit("engines_allowed", "any_engine")

    def test_free_tier_all_bridges_allowed(self):
        # FREE_TIER["bridges_allowed"] = None → no restriction on free tier
        _v.assert_limit("bridges_allowed", "discord")
        _v.assert_limit("bridges_allowed", "slack")
        _v.assert_limit("bridges_allowed", "any_bridge")

    def test_sest_list_blocks_disallowed_engine(self):
        # Per-customer SesT override can still restrict engines to a list
        _v._set_active_license({
            "tier": "pro",
            "limits": {"engines_allowed": ["claude", "hermes"]},
        })
        _v.assert_limit("engines_allowed", "claude")     # in list → ok
        with self.assertRaises(LicenseLimitError) as ctx:
            _v.assert_limit("engines_allowed", "codex")  # not in list
        self.assertEqual(ctx.exception.feature, "engines_allowed")
        self.assertIn("codex", str(ctx.exception))

    def test_sest_list_blocks_disallowed_bridge(self):
        # Per-customer SesT override can still restrict bridges to a list
        _v._set_active_license({
            "tier": "pro",
            "limits": {"bridges_allowed": ["discord", "slack"]},
        })
        _v.assert_limit("bridges_allowed", "discord")      # in list → ok
        with self.assertRaises(LicenseLimitError):
            _v.assert_limit("bridges_allowed", "whatsapp") # not in list


class TestAssertLimitBool(unittest.TestCase):
    def setUp(self):
        _reload_validator()

    def test_disabled_bool_feature_raises(self):
        # audit_export = False on free tier
        with self.assertRaises(LicenseLimitError) as ctx:
            _v.assert_limit("audit_export")
        self.assertEqual(ctx.exception.feature, "audit_export")

    def test_enabled_bool_feature_passes_with_paid_licence(self):
        _v._set_active_license({
            "tier": "professional",
            "limits": {"audit_export": True},
        })
        _v.assert_limit("audit_export")  # no raise

    def test_none_limit_never_raises(self):
        # Enterprise unlimited — None = no constraint
        _v._set_active_license({
            "tier": "enterprise",
            "limits": {"a2a_peers_max": None},
        })
        _v.assert_limit("a2a_peers_max", 999)  # no raise


class TestLoadLicenseEnv(unittest.TestCase):
    def setUp(self):
        _reload_validator()

    def test_no_token_activates_free_tier(self):
        with patch.dict(os.environ, {}, clear=True):
            # Remove any CORVIN_LICENSE_KEY if set
            env = {k: v for k, v in os.environ.items() if k != "CORVIN_LICENSE_KEY"}
            with patch.dict(os.environ, env, clear=True):
                with patch("license.validator._find_token", return_value=None):
                    _v.load_license_from_env()
        self.assertIsNone(_v._ACTIVE_LICENSE)

    def test_invalid_token_stays_free_tier(self):
        with patch("license.validator._find_token", return_value="CORVIN-bad.token.here"):
            _v.load_license_from_env()
        self.assertIsNone(_v._ACTIVE_LICENSE)

    def test_malformed_token_stays_free_tier(self):
        with patch("license.validator._find_token", return_value="not-a-corvin-token"):
            _v.load_license_from_env()
        self.assertIsNone(_v._ACTIVE_LICENSE)


class TestValidatorWithMockClaims(unittest.TestCase):
    """Tests that inject a pre-validated claims dict to check limit behaviour."""

    def setUp(self):
        _reload_validator()

    def _activate(self, claims: dict):
        _v._set_active_license(claims)

    def test_professional_tier_limits(self):
        self._activate({
            "tier": "professional",
            "limits": {
                "a2a_peers_max": 10,
                "engines_allowed": ["claude", "hermes", "codex", "opencode"],
                "workflows_concurrent": 15,
                "audit_export": True,
            },
        })
        _v.assert_limit("a2a_peers_max", 10)   # at limit — ok
        _v.assert_limit("engines_allowed", "claude")  # in list — ok
        _v.assert_limit("audit_export")               # True — ok
        with self.assertRaises(LicenseLimitError):
            _v.assert_limit("a2a_peers_max", 11)  # over limit

    def test_enterprise_unlimited_none(self):
        self._activate({
            "tier": "enterprise",
            "limits": {
                "a2a_peers_max": None,
                "engines_allowed": None,
                "workflows_concurrent": None,
            },
        })
        _v.assert_limit("a2a_peers_max", 10000)  # None = unlimited
        _v.assert_limit("engines_allowed", "anything")  # None = all allowed
        _v.assert_limit("workflows_concurrent", 999)

    def test_is_feature_allowed_false_on_free(self):
        self.assertFalse(_v.is_feature_allowed("sso_enabled"))
        self.assertFalse(_v.is_feature_allowed("audit_export"))

    def test_is_feature_allowed_true_with_key(self):
        self._activate({"tier": "professional", "limits": {"sso_enabled": True}})
        self.assertTrue(_v.is_feature_allowed("sso_enabled"))

    def test_subscription_lapsed_guard(self):
        """validate_claims rejects tokens with expired subscription_active_until."""
        past = int(time.time()) - 3600
        claims = {
            "iss": "corvinlabs.io",
            "type": "session",
            "exp": int(time.time()) + 86400,  # token itself not expired
            "subscription_active_until": past,  # but subscription lapsed
            "tier": "professional",
            "jti": "ses_test001",
        }
        result = _v._validate_claims(claims)
        self.assertIsNone(result, "expired subscription should be rejected")

    def test_expired_token_guard(self):
        past = int(time.time()) - 3600
        claims = {
            "iss": "corvinlabs.io",
            "type": "session",
            "exp": past,
            "tier": "starter",
            "jti": "ses_expired",
        }
        result = _v._validate_claims(claims)
        self.assertIsNone(result)

    def test_wrong_issuer_rejected(self):
        claims = {
            "iss": "evil.example.com",
            "type": "session",
            "exp": int(time.time()) + 86400,
        }
        result = _v._validate_claims(claims)
        self.assertIsNone(result)

    def test_wrong_type_rejected(self):
        claims = {
            "iss": "corvinlabs.io",
            "type": "access_token",  # not "session" or "license"
            "exp": int(time.time()) + 86400,
        }
        result = _v._validate_claims(claims)
        self.assertIsNone(result)


class TestLicenseLimitError(unittest.TestCase):
    def test_error_message_contains_feature(self):
        e = LicenseLimitError("a2a_peers_max", requested=5, limit=1, tier="free")
        self.assertIn("a2a_peers_max", str(e))
        self.assertIn("5", str(e))
        self.assertIn("1", str(e))
        self.assertIn("free", str(e))

    def test_error_without_values(self):
        e = LicenseLimitError("sso_enabled", tier="starter")
        self.assertIn("sso_enabled", str(e))
        self.assertIn("starter", str(e))

    def test_error_is_exception(self):
        e = LicenseLimitError("x")
        self.assertIsInstance(e, Exception)

    def test_attributes(self):
        e = LicenseLimitError("bridges_allowed", requested="slack", limit=["discord"])
        self.assertEqual(e.feature, "bridges_allowed")
        self.assertEqual(e.requested, "slack")
        self.assertEqual(e.limit, ["discord"])


class TestGetFeature(unittest.TestCase):
    """ADR-0092 M2.5 — get_feature() for boolean capability flags."""

    def setUp(self):
        _reload_validator()

    def test_absent_feature_returns_false_by_default(self):
        # No licence key → always False
        self.assertFalse(_v.get_feature("white_label"))
        self.assertFalse(_v.get_feature("nonexistent_feature_xyz"))

    def test_absent_features_dict_returns_false(self):
        # Licence loaded but no 'features' key in claims
        _v._set_active_license({"tier": "professional", "limits": {}})
        self.assertFalse(_v.get_feature("white_label"))

    def test_feature_true_returns_true(self):
        _v._set_active_license({
            "tier": "professional",
            "features": {"white_label": True, "beta_workflow_editor": False},
        })
        self.assertTrue(_v.get_feature("white_label"))

    def test_feature_false_returns_false(self):
        _v._set_active_license({
            "tier": "professional",
            "features": {"beta_workflow_editor": False},
        })
        self.assertFalse(_v.get_feature("beta_workflow_editor"))

    def test_feature_missing_key_returns_false(self):
        _v._set_active_license({
            "tier": "professional",
            "features": {"white_label": True},
        })
        # experimental_voice_synthesis not in features dict → False (opt-in)
        self.assertFalse(_v.get_feature("experimental_voice_synthesis"))

    def test_feature_truthy_values_cast_to_bool(self):
        _v._set_active_license({
            "tier": "enterprise",
            "features": {"some_flag": 1},  # truthy int
        })
        self.assertTrue(_v.get_feature("some_flag"))

    def test_multiple_features_independent(self):
        _v._set_active_license({
            "tier": "starter",
            "features": {
                "feature_a": True,
                "feature_b": False,
                "feature_c": True,
            },
        })
        self.assertTrue(_v.get_feature("feature_a"))
        self.assertFalse(_v.get_feature("feature_b"))
        self.assertTrue(_v.get_feature("feature_c"))

    def test_enterprise_customer_can_have_feature_disabled(self):
        _v._set_active_license({
            "tier": "enterprise",
            "features": {"beta_workflow_editor": False},
        })
        self.assertFalse(_v.get_feature("beta_workflow_editor"))

    def test_starter_customer_can_have_beta_feature_enabled(self):
        _v._set_active_license({
            "tier": "starter",
            "features": {"experimental_voice_synthesis": True},
        })
        self.assertTrue(_v.get_feature("experimental_voice_synthesis"))


class TestGetCustom(unittest.TestCase):
    """ADR-0092 M2.5 — get_custom() for arbitrary per-customer metadata."""

    def setUp(self):
        _reload_validator()

    def test_absent_custom_returns_none_by_default(self):
        self.assertIsNone(_v.get_custom("dedicated_model"))
        self.assertIsNone(_v.get_custom("nonexistent_key_xyz"))

    def test_absent_custom_dict_returns_default(self):
        _v._set_active_license({"tier": "professional", "limits": {}})
        self.assertIsNone(_v.get_custom("dedicated_model"))
        self.assertEqual(_v.get_custom("dedicated_model", default="claude-sonnet-4-6"),
                         "claude-sonnet-4-6")

    def test_custom_string_value(self):
        _v._set_active_license({
            "tier": "enterprise",
            "custom": {"dedicated_model": "claude-opus-4-8"},
        })
        self.assertEqual(_v.get_custom("dedicated_model"), "claude-opus-4-8")

    def test_custom_integer_value(self):
        _v._set_active_license({
            "tier": "professional",
            "custom": {"max_file_upload_mb": 250},
        })
        self.assertEqual(_v.get_custom("max_file_upload_mb"), 250)

    def test_custom_list_value(self):
        regions = ["eu-west-1", "eu-central-1"]
        _v._set_active_license({
            "tier": "enterprise",
            "custom": {"allowed_data_regions": regions},
        })
        # _freeze_license converts list → tuple; compare as tuple
        result = _v.get_custom("allowed_data_regions")
        self.assertEqual(list(result) if isinstance(result, tuple) else result, regions)

    def test_custom_missing_key_returns_caller_default(self):
        _v._set_active_license({
            "tier": "enterprise",
            "custom": {"dedicated_model": "claude-opus-4-8"},
        })
        self.assertEqual(_v.get_custom("sla_tier", default="standard"), "standard")
        self.assertIsNone(_v.get_custom("sla_tier"))

    def test_custom_and_features_independent(self):
        _v._set_active_license({
            "tier": "professional",
            "features": {"white_label": True},
            "custom": {"brand_name": "AcmeAI"},
        })
        self.assertTrue(_v.get_feature("white_label"))
        self.assertEqual(_v.get_custom("brand_name"), "AcmeAI")
        self.assertIsNone(_v.get_custom("support_channel"))
        self.assertFalse(_v.get_feature("beta_workflow_editor"))

    def test_full_sest_payload_all_three_dicts(self):
        """Integration: all three dicts coexist correctly."""
        _v._set_active_license({
            "tier": "enterprise",
            "limits": {
                "a2a_peers_max": 50,
                "engines_allowed": None,  # unlimited
            },
            "features": {
                "white_label": True,
                "experimental_voice_synthesis": True,
            },
            "custom": {
                "dedicated_model": "claude-opus-4-8",
                "sla_tier": "gold",
                "max_file_upload_mb": 500,
            },
        })
        # limits
        self.assertEqual(_v.get_limit("a2a_peers_max"), 50)
        _v.assert_limit("a2a_peers_max", 50)        # at limit → ok
        _v.assert_limit("engines_allowed", "anything")  # None → unlimited

        # features
        self.assertTrue(_v.get_feature("white_label"))
        self.assertTrue(_v.get_feature("experimental_voice_synthesis"))
        self.assertFalse(_v.get_feature("beta_workflow_editor"))  # absent → False

        # custom
        self.assertEqual(_v.get_custom("dedicated_model"), "claude-opus-4-8")
        self.assertEqual(_v.get_custom("sla_tier"), "gold")
        self.assertEqual(_v.get_custom("max_file_upload_mb"), 500)
        self.assertIsNone(_v.get_custom("support_channel"))


class TestRevocationByResolvedKid(unittest.TestCase):
    """ADR-0139: revocation must fire for sess-* tokens.

    Regression guard for the `_extra_revocation_kid` UnboundLocalError. Before
    the fix the sess-* branch left that name unbound, the resulting error was
    swallowed by the revocation-check `except`, and revocation was silently
    skipped for server session permits. We sign a real sess-v1 token so that —
    absent revocation — it verifies successfully; the revocation list must
    still reject it.
    """

    def setUp(self):
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import (
                Ed25519PrivateKey,
            )
        except Exception:  # noqa: BLE001
            self.skipTest("cryptography package not available")
        from cryptography.hazmat.primitives import serialization

        self._priv = Ed25519PrivateKey.generate()
        pub_der = self._priv.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        self._pub_b64 = _b64url(pub_der)
        _reload_validator()

    def _make_sess_token(self, kid: str = "sess-v1") -> str:
        header_b64 = _b64url(json.dumps({"alg": "EdDSA", "kid": kid}).encode())
        payload_b64 = _b64url(json.dumps({"tier": "professional"}).encode())
        sig = self._priv.sign(f"{header_b64}.{payload_b64}".encode())
        return f"CORVIN-{header_b64}.{payload_b64}.{_b64url(sig)}"

    def test_sess_token_verifies_when_not_revoked(self):
        # Control: with our key in the ring and an empty revocation list, the
        # signed token must verify — proving the test harness is sound.
        # patch.object replaces the whole MappingProxyType attribute instead of
        # using patch.dict (which requires item assignment and fails on proxies).
        import types as _types
        token = self._make_sess_token()
        with patch.object(_v, "SESSION_SERVER_KEY_RING",
                          _types.MappingProxyType({"sess-v1": self._pub_b64})), \
                patch("license.manifest.load_cached_manifest",
                      return_value={"revoked_kids": []}):
            claims = _v._verify_ed25519(token)
        self.assertIsNotNone(claims)
        self.assertEqual(claims["tier"], "professional")

    def test_sess_token_rejected_when_kid_revoked(self):
        # The bug: revocation was bypassed for sess-* tokens, so this returned
        # the claims instead of None.
        import types as _types
        token = self._make_sess_token()
        with patch.object(_v, "SESSION_SERVER_KEY_RING",
                          _types.MappingProxyType({"sess-v1": self._pub_b64})), \
                patch("license.manifest.load_cached_manifest",
                      return_value={"revoked_kids": ["sess-v1"]}):
            claims = _v._verify_ed25519(token)
        self.assertIsNone(claims)


if __name__ == "__main__":
    unittest.main(verbosity=2)
