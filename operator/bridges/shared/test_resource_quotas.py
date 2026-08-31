"""Tests for ADR-0094 — license resource quota enforcement.

Covers:
- TIER_RESOURCE_LIMITS: correct values per tier
- get_limit() three-level resolution (SesT → tier → FREE_TIER)
- assert_limit() with tier defaults
- Compute daily counter: within quota, at quota, over quota
- Compute counter: None limit (enterprise unlimited)
- Compute counter: fail-open on I/O error
- Compute counter: date rollover
- Compute counter: concurrent access (flock)
- security_events.py: new events registered

Run from the operator/ directory:
    python -m pytest bridges/shared/test_resource_quotas.py -v
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

# ── Path bootstrap ────────────────────────────────────────────────────────────
# _HERE = operator/bridges/shared/
# _OPERATOR = operator/  (parents[1])

_HERE = Path(__file__).resolve().parent
_OPERATOR = _HERE.parents[1]  # operator/

for _p in [
    str(_OPERATOR),                       # enables  import license.xxx
    str(_OPERATOR / "forge"),             # enables  import forge.xxx
    str(_OPERATOR / "license"),           # enables  import compute_quota directly
]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import license.limits as _lim_mod  # noqa: E402
import license.validator as _v  # noqa: E402
from license.limits import (  # noqa: E402
    FREE_TIER,
    TIER_RESOURCE_LIMITS,
    LicenseLimitError,
)
import license.compute_quota as _cq  # noqa: E402


def _reload():
    """Reset module-level state between tests."""
    _v._set_active_license(None)   # also resets _ACTIVE_LICENSE_CANARY
    _v._LICENSE_LOADED_AT = 0.0
    _v._LAST_RELOAD_AT = 0.0
    # Reset path snapshots so patched CORVIN_HOME env var takes effect
    _v._CORVIN_HOME_SNAPSHOT = None
    _v._LICENSE_INITIALIZED = False


# ── TIER_RESOURCE_LIMITS ──────────────────────────────────────────────────────

class TestTierResourceLimits(unittest.TestCase):
    # 2-TIER MODEL (operator decision 2026-06-23): only `free` (FREE_TIER, bounded)
    # and `member` (everything unlimited) exist. Every legacy paid-tier name
    # (pro/business/personal/professional/starter/enterprise/universal) is an
    # ALIAS for member — there are no intermediate paid tiers.
    LEGACY_ALIASES = ("universal", "starter", "personal", "professional",
                      "pro", "business", "enterprise")

    def test_member_tier_present(self):
        self.assertIn("member", TIER_RESOURCE_LIMITS)

    def test_member_everything_unlimited(self):
        m = TIER_RESOURCE_LIMITS["member"]
        for key in ("compute_units_per_day", "chat_turns_per_day", "a2a_peers_max",
                    "workflows_concurrent", "workflows_max", "tenants_max",
                    "rag_providers_max", "space_domains_max", "bridges_allowed",
                    "engines_allowed", "datasource_adapters_allowed",
                    "active_custom_layers_bc"):
            self.assertIsNone(m[key], f"member {key} must be unlimited (None)")
        for flag in ("data_residency", "audit_export", "sso_enabled", "enterprise_portal"):
            self.assertTrue(m[flag], f"member {flag} must be enabled")

    def test_legacy_tier_names_alias_to_member(self):
        for name in self.LEGACY_ALIASES:
            self.assertIn(name, TIER_RESOURCE_LIMITS, f"legacy alias {name!r} missing")
            self.assertEqual(TIER_RESOURCE_LIMITS[name], TIER_RESOURCE_LIMITS["member"],
                             f"legacy tier {name!r} must alias to member (2-tier model)")

    def test_only_free_and_member_exist(self):
        # Every named entry in the table is the unlimited member set — no
        # intermediate paid tier survives the 2-tier collapse.
        for name, limits in TIER_RESOURCE_LIMITS.items():
            self.assertEqual(limits, TIER_RESOURCE_LIMITS["member"],
                             f"tier {name!r} is not the member set — only free+member exist")

    def test_quota_keys_present_in_member(self):
        quota_keys = [
            "compute_units_per_day", "a2a_peers_max", "workflows_concurrent",
            "tenants_max", "bridges_allowed", "engines_allowed",
            "audit_export", "sso_enabled", "enterprise_portal",
        ]
        for key in quota_keys:
            self.assertIn(key, TIER_RESOURCE_LIMITS["member"],
                          f"Quota key {key!r} missing from member tier defaults")

    def test_free_tier_is_bounded(self):
        # The free tier actually constrains the heavy machinery (compute=1, a2a=1,
        # 1 workflow, 1 custom layer) — but chat is always free (unlimited).
        self.assertEqual(FREE_TIER["compute_units_per_day"], 1)
        self.assertEqual(FREE_TIER["a2a_peers_max"], 1)
        self.assertEqual(FREE_TIER["workflows_max"], 1)
        self.assertEqual(FREE_TIER["active_custom_layers_bc"], 1)
        self.assertIsNone(FREE_TIER["chat_turns_per_day"], "chat is always free")


# ── get_limit() three-level resolution ───────────────────────────────────────

class TestGetLimitResolution(unittest.TestCase):

    def setUp(self):
        _reload()

    def test_no_license_falls_back_to_free_tier(self):
        self.assertEqual(_v.get_limit("a2a_peers_max"), FREE_TIER["a2a_peers_max"])
        self.assertEqual(_v.get_limit("compute_units_per_day"),
                         FREE_TIER["compute_units_per_day"])

    def test_paid_token_without_limits_key_uses_member_unlimited(self):
        # 2-tier model: a paid token (any legacy name aliases to member) with no
        # embedded 'limits' dict → member defaults = everything unlimited (None).
        for tier in ("member", "pro", "business", "enterprise"):
            _v._set_active_license({"tier": tier})
            self.assertIsNone(_v.get_limit("compute_units_per_day"),
                              f"{tier} → unlimited compute (member)")
            self.assertIsNone(_v.get_limit("a2a_peers_max"), f"{tier} → unlimited a2a")
            self.assertIsNone(_v.get_limit("bridges_allowed"))   # None = all allowed

    def test_enterprise_token_no_limits_key_unlimited(self):
        _v._set_active_license({"tier": "enterprise"})
        self.assertIsNone(_v.get_limit("compute_units_per_day"))
        self.assertIsNone(_v.get_limit("a2a_peers_max"))

    def test_sest_limits_override_tier_defaults(self):
        # Per-customer override wins over tier default
        _v._set_active_license({
            "tier": "pro",
            "limits": {"compute_units_per_day": 42, "a2a_peers_max": 7},
        })
        self.assertEqual(_v.get_limit("compute_units_per_day"), 42)
        self.assertEqual(_v.get_limit("a2a_peers_max"), 7)

    def test_sest_limits_partial_override_falls_through(self):
        # Only one field overridden; other fields fall back to tier defaults
        _v._set_active_license({
            "tier": "pro",
            "limits": {"a2a_peers_max": 3},
        })
        # a2a overridden
        self.assertEqual(_v.get_limit("a2a_peers_max"), 3)
        # compute_units falls through to tier default (not free tier)
        self.assertEqual(_v.get_limit("compute_units_per_day"),
                         TIER_RESOURCE_LIMITS["pro"]["compute_units_per_day"])

    def test_unknown_tier_falls_to_free_tier(self):
        _v._set_active_license({"tier": "unknown_future_tier"})
        # No tier default → FREE_TIER
        self.assertEqual(_v.get_limit("compute_units_per_day"),
                         FREE_TIER["compute_units_per_day"])

    def test_unknown_feature_returns_zero(self):
        _v._set_active_license({"tier": "pro"})
        self.assertEqual(_v.get_limit("nonexistent_feature_xyz"), 0)

    def test_none_in_limits_means_unlimited(self):
        _v._set_active_license({
            "tier": "pro",
            "limits": {"compute_units_per_day": None},
        })
        self.assertIsNone(_v.get_limit("compute_units_per_day"))


# ── assert_limit() with tier defaults ────────────────────────────────────────

class TestAssertLimitWithTierDefaults(unittest.TestCase):

    def setUp(self):
        _reload()

    def test_pro_a2a_allows_up_to_limit(self):
        _v._set_active_license({"tier": "pro"})
        _v.assert_limit("a2a_peers_max", 10)  # at limit → ok

    def test_free_a2a_over_limit_raises(self):
        # 2-tier: only the FREE tier bounds a2a (=1); member/paid is unlimited.
        _v._set_active_license(None)
        with self.assertRaises(LicenseLimitError) as ctx:
            _v.assert_limit("a2a_peers_max", 2)
        e = ctx.exception
        self.assertEqual(e.feature, "a2a_peers_max")
        self.assertEqual(e.requested, 2)
        self.assertEqual(e.limit, FREE_TIER["a2a_peers_max"])

    def test_enterprise_a2a_unlimited(self):
        _v._set_active_license({"tier": "enterprise"})
        _v.assert_limit("a2a_peers_max", 10000)  # None → unlimited

    def test_business_bridges_none_allows_any(self):
        _v._set_active_license({"tier": "business"})
        _v.assert_limit("bridges_allowed", "slack")    # None → unlimited
        _v.assert_limit("bridges_allowed", "whatsapp")

    def test_pro_engines_none_allows_any(self):
        _v._set_active_license({"tier": "pro"})
        _v.assert_limit("engines_allowed", "claude")

    def test_pro_compute_within_limit(self):
        _v._set_active_license({"tier": "pro"})
        _v.assert_limit("compute_units_per_day", 500)

    def test_free_compute_over_limit_raises(self):
        # 2-tier: only the FREE tier bounds compute (=1/day); member is unlimited.
        _v._set_active_license(None)
        with self.assertRaises(LicenseLimitError):
            _v.assert_limit("compute_units_per_day", 2)

    def test_sest_override_respected_in_assert(self):
        _v._set_active_license({
            "tier": "pro",
            "limits": {"compute_units_per_day": 5},
        })
        _v.assert_limit("compute_units_per_day", 5)  # at limit
        with self.assertRaises(LicenseLimitError):
            _v.assert_limit("compute_units_per_day", 6)


# ── Compute daily counter ─────────────────────────────────────────────────────

class TestComputeQuotaCounter(unittest.TestCase):

    def setUp(self):
        _reload()
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self._home = Path(self._tmp.name)

    def tearDown(self):
        _v._set_active_license(None)
        self._tmp.cleanup()

    def _set_limit(self, n):
        _v._set_active_license({"tier": "pro", "limits": {"compute_units_per_day": n}})

    def test_first_call_succeeds(self):
        self._set_limit(5)
        _cq.increment_and_check(self._home)
        self.assertEqual(_cq.get_today_count(self._home), 1)

    def test_calls_up_to_limit_succeed(self):
        self._set_limit(3)
        for _ in range(3):
            _cq.increment_and_check(self._home)
        self.assertEqual(_cq.get_today_count(self._home), 3)

    def test_over_limit_raises(self):
        self._set_limit(2)
        _cq.increment_and_check(self._home)
        _cq.increment_and_check(self._home)
        with self.assertRaises(LicenseLimitError) as ctx:
            _cq.increment_and_check(self._home)
        e = ctx.exception
        self.assertEqual(e.feature, "compute_units_per_day")
        self.assertEqual(e.requested, 3)
        self.assertEqual(e.limit, 2)

    def test_counter_not_incremented_after_rejection(self):
        self._set_limit(1)
        _cq.increment_and_check(self._home)
        with self.assertRaises(LicenseLimitError):
            _cq.increment_and_check(self._home)
        # Counter should still be 1, not 2
        self.assertEqual(_cq.get_today_count(self._home), 1)

    def test_none_limit_enterprise_never_blocks(self):
        _v._set_active_license({"tier": "enterprise"})
        for _ in range(1000):
            _cq.increment_and_check(self._home)
        self.assertEqual(_cq.get_today_count(self._home), 1000)

    def test_free_tier_limit_applied(self):
        # No license → FREE_TIER["compute_units_per_day"] = 1
        limit = FREE_TIER["compute_units_per_day"]
        for _ in range(limit):
            _cq.increment_and_check(self._home)
        with self.assertRaises(LicenseLimitError) as ctx:
            _cq.increment_and_check(self._home)
        self.assertEqual(ctx.exception.limit, FREE_TIER["compute_units_per_day"])

    def test_quota_file_mode_0600(self):
        self._set_limit(5)
        _cq.increment_and_check(self._home)
        quota_file = self._home / "global" / "license" / "compute_quota.json"
        self.assertTrue(quota_file.exists())
        mode = quota_file.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600, f"expected 0600, got 0o{mode:o}")

    def test_date_rollover_resets_counter(self):
        self._set_limit(1)
        today = _cq._today_utc()
        # Manually write yesterday's entry as today to simulate a full day
        path = _cq._quota_path(self._home)
        path.parent.mkdir(parents=True, exist_ok=True)
        import json as _json
        yesterday = "2000-01-01"  # far in the past, not today
        path.write_text(_json.dumps({yesterday: 999}))
        os.chmod(path, 0o600)
        # Today's counter is fresh (0) even though yesterday was at 999
        _cq.increment_and_check(self._home)   # should not raise
        self.assertEqual(_cq.get_today_count(self._home), 1)

    def test_get_today_count_on_missing_file(self):
        self.assertEqual(_cq.get_today_count(self._home), 0)

    def test_fail_closed_on_io_error_with_finite_limit(self):
        # Make the quota directory a file (causes mkdir to fail inside).
        # LIC-2: a finite limit (paid tier with a cap, or free tier) must
        # fail CLOSED on a persistent I/O error — the counter is load-bearing,
        # so an unwritable counter must not grant unmetered access.
        broken = self._home / "global" / "license"
        broken.parent.mkdir(parents=True, exist_ok=True)
        broken.touch()  # file where directory would go
        self._set_limit(1)
        with self.assertRaises(LicenseLimitError):
            _cq.increment_and_check(self._home)

    def test_fail_open_on_io_error_with_unlimited_tier(self):
        # LIC-2: None (unlimited tier) must fail OPEN on a persistent I/O
        # error — there is no quota to enforce, so an operational failure
        # must never block.
        broken = self._home / "global" / "license"
        broken.parent.mkdir(parents=True, exist_ok=True)
        broken.touch()  # file where directory would go
        _v._set_active_license({"tier": "enterprise"})
        # Should not raise — fail-open
        _cq.increment_and_check(self._home)

    def test_concurrent_calls_do_not_corrupt_counter(self):
        self._set_limit(100)
        results = []
        errors = []

        def _call():
            try:
                _cq.increment_and_check(self._home)
                results.append(1)
            except LicenseLimitError:
                errors.append(1)
            except Exception as exc:
                errors.append(str(exc))

        threads = [threading.Thread(target=_call) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        total = _cq.get_today_count(self._home)
        self.assertEqual(total, len(results), "counter must equal successful calls")
        self.assertEqual(len(errors), 0, f"unexpected errors: {errors}")


# ── Security event registry ───────────────────────────────────────────────────

class TestSecurityEventRegistry(unittest.TestCase):

    def _get_registry(self):
        try:
            from forge.security_events import EVENT_SEVERITY
            return EVENT_SEVERITY
        except ImportError:
            self.skipTest("forge not on path — skipping event registry check")

    def test_license_limit_exceeded_registered(self):
        reg = self._get_registry()
        self.assertIn("license.limit_exceeded", reg)
        self.assertEqual(reg["license.limit_exceeded"], "WARNING")

    def test_compute_quota_exceeded_registered(self):
        reg = self._get_registry()
        self.assertIn("compute.quota_exceeded", reg)
        self.assertEqual(reg["compute.quota_exceeded"], "WARNING")

    def test_engine_blocked_by_license_registered(self):
        reg = self._get_registry()
        self.assertIn("engine.blocked_by_license", reg)

    def test_bridge_blocked_by_license_registered(self):
        reg = self._get_registry()
        self.assertIn("bridge.blocked_by_license", reg)

    def test_tenant_blocked_by_license_registered(self):
        reg = self._get_registry()
        self.assertIn("tenant.blocked_by_license", reg)

    def test_instance_id_mismatch_registered(self):
        reg = self._get_registry()
        self.assertIn("license.instance_id_mismatch", reg)
        self.assertEqual(reg["license.instance_id_mismatch"], "WARNING")


# ── Instance-ID binding (Personal tier) ─────────────────────────────────────

class TestInstanceIdBinding(unittest.TestCase):
    """Personal-tier tokens carry limits.instance_id_bound.
    validator._check_instance_id_bound() enforces one-installation-per-licence.
    """

    def setUp(self):
        _reload()
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self._home = Path(self._tmp.name)
        self._env_patch = patch.dict(
            "os.environ", {"CORVIN_HOME": str(self._home)}
        )
        self._env_patch.start()

    def tearDown(self):
        _reload()
        self._env_patch.stop()
        self._tmp.cleanup()

    def _write_instance_id(self, iid: str) -> None:
        iid_dir = self._home / "global"
        iid_dir.mkdir(parents=True, exist_ok=True)
        p = iid_dir / "instance_id.json"
        p.write_text(json.dumps({"instance_id": iid}))
        p.chmod(0o600)

    def test_unbound_token_passes_any_instance(self):
        # Token without instance_id_bound is valid anywhere
        claims: dict = {"tier": "pro", "limits": {}}
        self.assertTrue(_v._check_instance_id_bound(claims))

    def test_bound_token_matches_local_instance(self):
        iid = "aaaabbbb-1111-2222-3333-444455556666"
        self._write_instance_id(iid)
        claims: dict = {"tier": "personal", "limits": {"instance_id_bound": iid}}
        self.assertTrue(_v._check_instance_id_bound(claims))

    def test_bound_token_mismatch_returns_false(self):
        self._write_instance_id("correct-uuid-here")
        claims: dict = {"tier": "personal", "limits": {"instance_id_bound": "wrong-uuid"}}
        self.assertFalse(_v._check_instance_id_bound(claims))

    def test_bound_token_missing_file_returns_false(self):
        # instance_id.json does not exist → fail-closed
        claims: dict = {"tier": "personal", "limits": {"instance_id_bound": "any-id"}}
        self.assertFalse(_v._check_instance_id_bound(claims))

    def test_personal_token_limits_applied_when_bound_matches(self):
        iid = "ccccdddd-5555-6666-7777-888899990000"
        self._write_instance_id(iid)
        _v._set_active_license({
            "tier": "personal",
            "limits": {"instance_id_bound": iid},
        })
        # Limits should come from TIER_RESOURCE_LIMITS["personal"]
        self.assertEqual(_v.get_limit("compute_units_per_day"),
                         TIER_RESOURCE_LIMITS["personal"]["compute_units_per_day"])
        self.assertEqual(_v.get_limit("a2a_peers_max"),
                         TIER_RESOURCE_LIMITS["personal"]["a2a_peers_max"])
        self.assertTrue(_v.get_limit("audit_export"))
        self.assertIsNone(_v.get_limit("bridges_allowed"))


# ── Corvin-Features limits module ────────────────────────────────────────────

class TestCorvinFeaturesLimits(unittest.TestCase):
    """Verify that Corvin-Features TIER_RESOURCE_LIMITS stays in sync."""

    def _import_cf_limits(self):
        cf_src = Path(__file__).resolve().parents[3] / "Corvin-Features" / "src"
        if str(cf_src) not in sys.path:
            sys.path.insert(0, str(cf_src))
        try:
            from corvin_features.limits import TIER_RESOURCE_LIMITS as CF_LIMITS
            return CF_LIMITS
        except ImportError:
            self.skipTest("Corvin-Features not available")

    def test_same_paid_tiers_as_corvinOS(self):
        # 2-tier model: compare the PAID-tier aliases. CorvinOS keeps `free` as a
        # separate FREE_TIER constant; Corvin-Features carries it inside
        # TIER_RESOURCE_LIMITS — so ignore the `free` key and require the paid
        # alias sets to match exactly (no tier drift across repos).
        cf = self._import_cf_limits()
        cf_paid = set(cf.keys()) - {"free"}
        os_paid = set(TIER_RESOURCE_LIMITS.keys()) - {"free"}
        self.assertEqual(cf_paid, os_paid,
                         "paid-tier alias sets diverge between CorvinOS and Corvin-Features")

    def test_all_paid_tiers_unlimited_both_repos(self):
        # Every paid alias in BOTH repos must be the unlimited member set.
        cf = self._import_cf_limits()
        for name in (set(TIER_RESOURCE_LIMITS.keys()) - {"free"}):
            self.assertIsNone(TIER_RESOURCE_LIMITS[name]["compute_units_per_day"],
                              f"CorvinOS tier {name!r} is not unlimited (2-tier model)")
            self.assertIsNone(cf[name]["compute_units_per_day"],
                              f"Corvin-Features tier {name!r} is not unlimited (2-tier model)")
            self.assertIsNone(cf[name]["a2a_peers_max"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
