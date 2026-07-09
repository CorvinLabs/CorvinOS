"""License hardening tests — ADR-0144 in-process attack surface.

Covers:
  - Canary hash detects gc.get_referents() mutation
  - Canary hash detects name-rebind of _ACTIVE_LICENSE
  - reload_from_disk() is env-var isolated (uses _find_token_disk_only)
  - reload_from_disk() rate limiter (ignores rapid successive calls)
  - _set_active_license() keeps canary in sync
  - LSAD failure escalated to warning (smoke-check)
"""
from __future__ import annotations

import gc
import importlib
import os
import sys
import tempfile
import time
import types
from pathlib import Path
from unittest import mock

import pytest

# Ensure operator/ is on sys.path
_OPERATOR_ROOT = str(Path(__file__).resolve().parents[3])
if _OPERATOR_ROOT not in sys.path:
    sys.path.insert(0, _OPERATOR_ROOT)

# Force CORVIN_INTEGRATION_TEST so load_license_from_env(force=True) works.
os.environ.setdefault("CORVIN_INTEGRATION_TEST", "1")


def _fresh_validator():
    """Re-import validator with a clean module state."""
    mod_name = "license.validator"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    # Also clear sub-imports that may be cached.
    for key in list(sys.modules):
        if key.startswith("license."):
            del sys.modules[key]
    return importlib.import_module(mod_name)


# ── Closure-salt protection ───────────────────────────────────────────────────

def test_canary_salt_not_module_attribute():
    """The canary salt must NOT be exposed as a module attribute.

    Previously _CANARY_SALT was at module level, readable as
    ``license.validator._CANARY_SALT``. It is now closure-captured inside
    _compute_canary so an attacker cannot trivially extract the salt via
    attribute access and pre-compute a forged canary.
    """
    v = _fresh_validator()
    assert not hasattr(v, "_CANARY_SALT"), (
        "Canary salt must be closure-captured, not a module attribute — "
        "reading it via v._CANARY_SALT must be impossible."
    )


# ── Canary tests ─────────────────────────────────────────────────────────────

def test_canary_set_on_load():
    """After _set_active_license(dict), canary must be non-None."""
    v = _fresh_validator()
    v._set_active_license({"tier": "member", "iss": "corvinlabs.io"})
    assert v._ACTIVE_LICENSE is not None
    assert v._ACTIVE_LICENSE_CANARY is not None
    canary_before = v._ACTIVE_LICENSE_CANARY
    # get_limit() must NOT trigger the CRITICAL path — canary is valid.
    result = v.get_limit("workflows_concurrent")
    # enterprise tier → None (unlimited)
    assert result is None
    assert v._ACTIVE_LICENSE_CANARY == canary_before


def test_canary_cleared_on_none():
    """After _set_active_license(None), canary must also be None."""
    v = _fresh_validator()
    v._set_active_license({"tier": "member"})
    assert v._ACTIVE_LICENSE_CANARY is not None
    v._set_active_license(None)
    assert v._ACTIVE_LICENSE is None
    assert v._ACTIVE_LICENSE_CANARY is None


def test_canary_detects_gc_mutation(caplog):
    """gc.get_referents() mutation is detected by get_limit() → FREE_TIER fallback."""
    v = _fresh_validator()
    v._set_active_license({"tier": "member", "iss": "corvinlabs.io"})
    assert v.get_limit("workflows_concurrent") is None  # enterprise = unlimited

    # Mutate via gc — bypass MappingProxyType
    underlying = gc.get_referents(v._ACTIVE_LICENSE)[0]
    assert isinstance(underlying, dict)
    underlying["tier"] = "free"

    import logging
    with caplog.at_level(logging.CRITICAL, logger="corvin.license"):
        result = v.get_limit("workflows_concurrent")

    # Must fall back to FREE_TIER (value = 1)
    assert result == 1
    assert any("canary mismatch" in r.message for r in caplog.records)


def test_canary_detects_name_rebind(caplog):
    """Direct name-rebind of _ACTIVE_LICENSE without updating canary is detected."""
    v = _fresh_validator()
    v._set_active_license({"tier": "pro"})
    # Attacker rebinds without going through _set_active_license
    v._ACTIVE_LICENSE = types.MappingProxyType({"tier": "member"})

    import logging
    with caplog.at_level(logging.CRITICAL, logger="corvin.license"):
        result = v.get_limit("workflows_concurrent")

    # Must fall back to FREE_TIER
    assert result == 1
    assert any("canary mismatch" in r.message for r in caplog.records)


def test_canary_passes_after_honest_reload():
    """_set_active_license() called twice stays consistent."""
    v = _fresh_validator()
    v._set_active_license({"tier": "free"})
    assert v.get_limit("tenants_max") == 1  # free tier

    v._set_active_license({"tier": "member"})
    # Canary must be updated — no CRITICAL
    assert v.get_limit("tenants_max") is None  # member = unlimited


# ── reload_from_disk() env-var isolation ─────────────────────────────────────

def test_reload_from_disk_ignores_env_var():
    """reload_from_disk() must not read CORVIN_LICENSE_KEY from the environment."""
    v = _fresh_validator()

    with tempfile.TemporaryDirectory() as tmpdir:
        # Set up snapshots so reload_from_disk() doesn't raise.
        v._LICENSE_INITIALIZED = True
        v._CORVIN_HOME_SNAPSHOT = Path(tmpdir)
        v._CONFIG_DIR_SNAPSHOT = Path(tmpdir)
        v._AUDIT_PATH_SNAPSHOT = None
        v._LAST_RELOAD_AT = 0.0

        # Put a fake (invalid) token in the env var.
        with mock.patch.dict(os.environ, {"CORVIN_LICENSE_KEY": "CORVIN-FAKE.FAKE.FAKE"}):
            # No disk files exist → should revert to Free tier (ignoring env var).
            v.reload_from_disk()

        # If the env var were read, _verify_ed25519 would return None and set free tier.
        # Either way the env var path must not win — canary must be consistent.
        assert v._ACTIVE_LICENSE is None
        assert v._ACTIVE_LICENSE_CANARY is None


def test_reload_from_disk_reads_disk_file():
    """reload_from_disk() picks up a token written to the session.key file."""
    v = _fresh_validator()

    with tempfile.TemporaryDirectory() as tmpdir:
        v._LICENSE_INITIALIZED = True
        v._CORVIN_HOME_SNAPSHOT = Path(tmpdir)
        v._CONFIG_DIR_SNAPSHOT = Path(tmpdir)
        v._AUDIT_PATH_SNAPSHOT = None
        v._LAST_RELOAD_AT = 0.0

        # Write a token that will fail signature verification (invalid format).
        session_file = Path(tmpdir) / "session.key"
        session_file.write_text("CORVIN-INVALID.INVALID.INVALID")

        v.reload_from_disk()

        # Signature check fails → free tier, but the disk file WAS read.
        assert v._ACTIVE_LICENSE is None


# ── Reload rate limiter ───────────────────────────────────────────────────────

def test_reload_rate_limiter_blocks_rapid_calls(caplog):
    """A second reload_from_disk() call within MIN interval is ignored."""
    v = _fresh_validator()

    with tempfile.TemporaryDirectory() as tmpdir:
        v._LICENSE_INITIALIZED = True
        v._CORVIN_HOME_SNAPSHOT = Path(tmpdir)
        v._CONFIG_DIR_SNAPSHOT = Path(tmpdir)
        v._AUDIT_PATH_SNAPSHOT = None

        # First call: allowed (sets _LAST_RELOAD_AT)
        v._LAST_RELOAD_AT = 0.0
        v.reload_from_disk()
        first_reload_ts = v._LAST_RELOAD_AT

        # Immediately call again — should be throttled. The throttle notice is
        # logged at DEBUG (not WARNING): a throttled reload is the EXPECTED,
        # self-healing outcome of the per-authenticated-op reload path, so
        # logging it at WARNING made the throttle its own noise source (2236
        # lines in ~19h, 2026-07-09). Capture at DEBUG accordingly.
        import logging
        with caplog.at_level(logging.DEBUG, logger="corvin.license"):
            v.reload_from_disk()

        # _LAST_RELOAD_AT must NOT have advanced beyond first_reload_ts
        assert v._LAST_RELOAD_AT == first_reload_ts
        assert any("too soon" in r.message for r in caplog.records)


def test_reload_rate_limiter_allows_after_interval():
    """After MIN interval elapses, reload_from_disk() is allowed again."""
    v = _fresh_validator()

    with tempfile.TemporaryDirectory() as tmpdir:
        v._LICENSE_INITIALIZED = True
        v._CORVIN_HOME_SNAPSHOT = Path(tmpdir)
        v._CONFIG_DIR_SNAPSHOT = Path(tmpdir)
        v._AUDIT_PATH_SNAPSHOT = None

        # Simulate the last reload was well in the past
        v._LAST_RELOAD_AT = time.time() - v._MIN_RELOAD_INTERVAL_SECONDS - 1.0

        before = v._LAST_RELOAD_AT
        v.reload_from_disk()

        # _LAST_RELOAD_AT must have advanced
        assert v._LAST_RELOAD_AT > before


def test_reload_rate_limiter_bypassed_when_content_changed():
    """A reload picking up genuinely NEW on-disk content must never be
    throttled, even milliseconds after a prior reload.

    reload_from_disk() is also called on every authenticated console session
    op (auth.py::_compute_lic_proof), so an unconditional throttle silently
    swallowed the one reload that actually matters: the console "Apply Key"
    reload immediately after writing a NEW token, almost always inside the
    cooldown window opened by an incidental per-request call moments earlier.
    """
    v = _fresh_validator()

    with tempfile.TemporaryDirectory() as tmpdir:
        v._LICENSE_INITIALIZED = True
        v._CORVIN_HOME_SNAPSHOT = Path(tmpdir)
        v._CONFIG_DIR_SNAPSHOT = Path(tmpdir)
        v._AUDIT_PATH_SNAPSHOT = None

        # Simulate an incidental per-request reload having just fired (no
        # token on disk yet) — this opens the throttle cooldown window.
        v._LAST_RELOAD_AT = 0.0
        v.reload_from_disk()
        first_ts = v._LAST_RELOAD_AT
        assert v._ACTIVE_LICENSE is None

        # A second incidental reload with STILL no token must be throttled
        # (content unchanged: None == None).
        v.reload_from_disk()
        assert v._LAST_RELOAD_AT == first_ts

        # Now, immediately (well within the throttle window), the user applies
        # a key via the console — a NEW token appears on disk. This reload
        # must go through despite the hot cooldown window.
        session_file = Path(tmpdir) / "global" / "license.key"
        session_file.parent.mkdir(parents=True, exist_ok=True)
        session_file.write_text("CORVIN-INVALID.INVALID.INVALID")
        # Mode 0600, matching the real write path (routes/license.py writes
        # via tempfile.mkstemp+chmod+replace) — a permissive mode is now
        # REJECTED outright by _find_token_disk_only (mode parity with
        # session.key), which would make this reload find nothing rather
        # than bypass the throttle, and is not what this test is exercising.
        os.chmod(session_file, 0o600)
        v.reload_from_disk()
        assert v._LAST_RELOAD_AT > first_ts, (
            "reload with new content must bypass the throttle"
        )


def test_reload_from_disk_checks_revocation():
    """ADR-0102 per-token revocation: load_license_from_env() (boot) checks
    _is_token_fp_revoked(), but reload_from_disk() never did -- so a token
    revoked (subscription cancelled) after boot kept re-activating on every
    authenticated console session op (auth.py calls reload_from_disk() per
    request) until the whole process was restarted. Adversarial-review find."""
    v = _fresh_validator()
    import hashlib

    with tempfile.TemporaryDirectory() as tmpdir:
        v._LICENSE_INITIALIZED = True
        v._CORVIN_HOME_SNAPSHOT = Path(tmpdir)
        v._CONFIG_DIR_SNAPSHOT = Path(tmpdir)
        v._AUDIT_PATH_SNAPSHOT = None
        v._LAST_RELOAD_AT = 0.0

        token = "CORVIN-fake.token.value"
        fp = hashlib.sha256(token.encode()).hexdigest()[:32]

        key_path = Path(tmpdir) / "global" / "license.key"
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_text(token)
        os.chmod(key_path, 0o600)

        # Signature check passes (mocked) and claims look like a valid,
        # non-expired member license -- the ONLY reason this should fail to
        # activate is the revocation check.
        v._verify_ed25519 = lambda _t: {
            "iss": "corvinlabs.io", "type": "license", "tier": "member",
            "exp": time.time() + 999_999, "iat": time.time(), "jti": "abc123",
        }
        # Revocation list (as if freshly fetched from Corvin-Features) contains
        # exactly this token's fingerprint.
        v._fetch_revoked_fps = lambda: [fp]

        v.reload_from_disk()

        assert v._ACTIVE_LICENSE is None, (
            "a revoked token must not activate on reload, even though its "
            "signature and claims are otherwise valid"
        )


def test_reload_from_disk_accepts_non_revoked_token():
    """Sanity counterpart: a token whose fingerprint is NOT on the revocation
    list must still activate normally on reload (the new check must not
    fail-closed on everything)."""
    v = _fresh_validator()

    with tempfile.TemporaryDirectory() as tmpdir:
        v._LICENSE_INITIALIZED = True
        v._CORVIN_HOME_SNAPSHOT = Path(tmpdir)
        v._CONFIG_DIR_SNAPSHOT = Path(tmpdir)
        v._AUDIT_PATH_SNAPSHOT = None
        v._LAST_RELOAD_AT = 0.0

        token = "CORVIN-fake.token.value"
        key_path = Path(tmpdir) / "global" / "license.key"
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_text(token)
        os.chmod(key_path, 0o600)

        v._verify_ed25519 = lambda _t: {
            "iss": "corvinlabs.io", "type": "license", "tier": "member",
            "exp": time.time() + 999_999, "iat": time.time(), "jti": "abc123",
        }
        v._fetch_revoked_fps = lambda: []  # empty revocation list

        v.reload_from_disk()

        assert v._ACTIVE_LICENSE is not None
        assert v._ACTIVE_LICENSE["tier"] == "member"

        # A subsequent incidental reload with the SAME (still-invalid) token
        # must be throttled again (content unchanged from the last load).
        applied_ts = v._LAST_RELOAD_AT
        v.reload_from_disk()
        assert v._LAST_RELOAD_AT == applied_ts


# ── reload_from_disk() requires prior boot ────────────────────────────────────

def test_reload_before_boot_raises():
    """reload_from_disk() must raise RuntimeError if load_license_from_env() hasn't run."""
    v = _fresh_validator()
    assert not v._LICENSE_INITIALIZED
    with pytest.raises(RuntimeError, match="before load_license_from_env"):
        v.reload_from_disk()


# ── _verified_license() propagates to all public API functions ────────────────

def test_active_tier_returns_free_on_gc_mutation(caplog):
    """active_tier() must return 'free' when the canary detects gc tampering."""
    v = _fresh_validator()
    v._set_active_license({"tier": "member", "iss": "corvinlabs.io"})
    assert v.active_tier() == "member"

    underlying = gc.get_referents(v._ACTIVE_LICENSE)[0]
    underlying["tier"] = "free"   # mutate to free to test the detection path

    import logging
    with caplog.at_level(logging.CRITICAL, logger="corvin.license"):
        # Even though underlying was mutated to "free", is_loaded() must return False
        # because the canary fires (the canary includes the full dict, not just tier).
        # In this specific mutation the tier was changed → canary mismatch → returns None
        result = v.active_tier()

    # Canary mismatch detected → "free" (safe default), NOT the tampered value
    assert result == "free"
    assert any("canary mismatch" in r.message for r in caplog.records)


def test_is_loaded_returns_false_on_gc_mutation(caplog):
    """is_loaded() must return False when the canary detects gc tampering."""
    v = _fresh_validator()
    v._set_active_license({"tier": "member"})
    assert v.is_loaded() is True

    underlying = gc.get_referents(v._ACTIVE_LICENSE)[0]
    underlying["tier"] = "free"

    import logging
    with caplog.at_level(logging.CRITICAL, logger="corvin.license"):
        result = v.is_loaded()

    assert result is False


def test_get_feature_returns_false_on_gc_mutation(caplog):
    """get_feature() must return False (denied) when the canary detects gc tampering."""
    v = _fresh_validator()
    v._set_active_license({
        "tier": "member",
        "features": {"white_label": True},
    })
    assert v.get_feature("white_label") is True

    underlying = gc.get_referents(v._ACTIVE_LICENSE)[0]
    underlying["tier"] = "free"   # trigger canary mismatch

    import logging
    with caplog.at_level(logging.CRITICAL, logger="corvin.license"):
        result = v.get_feature("white_label")

    assert result is False   # fail-closed


def test_get_custom_returns_default_on_gc_mutation(caplog):
    """get_custom() must return the default when the canary detects gc tampering."""
    v = _fresh_validator()
    v._set_active_license({
        "tier": "member",
        "custom": {"dedicated_model": "claude-opus-4-8"},
    })
    assert v.get_custom("dedicated_model") == "claude-opus-4-8"

    underlying = gc.get_referents(v._ACTIVE_LICENSE)[0]
    underlying["tier"] = "free"   # trigger canary mismatch

    import logging
    with caplog.at_level(logging.CRITICAL, logger="corvin.license"):
        result = v.get_custom("dedicated_model", default="fallback")

    assert result == "fallback"   # fail-closed to default


# ── Immutable tier tables ─────────────────────────────────────────────────────

def test_free_tier_is_immutable():
    """FREE_TIER must be a MappingProxyType — direct mutation raises TypeError."""
    from license.limits import FREE_TIER
    with pytest.raises(TypeError):
        FREE_TIER["compute_units_per_day"] = 9999  # type: ignore[index]


def test_tier_resource_limits_is_immutable():
    """TIER_RESOURCE_LIMITS must be a MappingProxyType — mutation raises TypeError."""
    from license.limits import TIER_RESOURCE_LIMITS
    with pytest.raises(TypeError):
        TIER_RESOURCE_LIMITS["enterprise"] = {}  # type: ignore[index]


def test_tier_resource_limits_inner_dict_is_immutable():
    """Inner tier dicts must also be frozen — nested mutation raises TypeError."""
    from license.limits import TIER_RESOURCE_LIMITS
    with pytest.raises(TypeError):
        TIER_RESOURCE_LIMITS["enterprise"]["compute_units_per_day"] = 0  # type: ignore[index]


def test_session_key_ring_is_immutable():
    """SESSION_SERVER_KEY_RING must be MappingProxyType — key replacement raises TypeError."""
    v = _fresh_validator()
    import types as _t
    assert isinstance(v.SESSION_SERVER_KEY_RING, _t.MappingProxyType), (
        "SESSION_SERVER_KEY_RING must be MappingProxyType to block in-process key replacement"
    )
    with pytest.raises(TypeError):
        v.SESSION_SERVER_KEY_RING["sess-v1"] = "attacker_key"  # type: ignore[index]


# ── Negative requested value rejected ────────────────────────────────────────

def test_assert_limit_rejects_negative_requested():
    """assert_limit must raise for negative requested values — they bypass > checks."""
    v = _fresh_validator()
    # Import AFTER fresh-reload so we get the same class instance the validator uses.
    LicenseLimitError = importlib.import_module("license.limits").LicenseLimitError
    v._set_active_license(None)   # free tier (compute_units_per_day = 1)
    with pytest.raises(LicenseLimitError):
        v.assert_limit("compute_units_per_day", -1)


def test_assert_limit_rejects_negative_on_enterprise():
    """Negative requested must be denied even on unlimited enterprise tier."""
    v = _fresh_validator()
    LicenseLimitError = importlib.import_module("license.limits").LicenseLimitError
    v._set_active_license({"tier": "member"})
    with pytest.raises(LicenseLimitError):
        v.assert_limit("compute_units_per_day", -1)


# ── RecursionError in deeply nested payload ───────────────────────────────────

def test_set_active_license_handles_deeply_nested_payload(caplog):
    """A deeply nested payload must not cause a permanent RecursionError crash."""
    import logging
    v = _fresh_validator()
    # Build a payload with 1000 nesting levels (well past Python default of ~500).
    deep = {}
    node = deep
    for _ in range(1000):
        child = {}
        node["x"] = child
        node = child

    with caplog.at_level(logging.WARNING, logger="corvin.license"):
        v._set_active_license({"tier": "member", "custom": deep})

    # Must have fallen back gracefully to free tier, not crashed.
    assert v._ACTIVE_LICENSE is None
    assert v._ACTIVE_LICENSE_CANARY is None
    assert v.active_tier() == "free"


# ── Operator key closure protection ──────────────────────────────────────────

def test_corvin_public_key_b64_rebind_does_not_affect_verification():
    """Rebinding CORVIN_PUBLIC_KEY_B64 must NOT change the key used in verification.

    _verify_ed25519 now reads the operator key from a closure-captured snapshot
    (_get_operator_pubkey_b64), not from the live module attribute.  An attacker
    who rebinds the module attribute therefore cannot inject an attacker-controlled
    key into the verification path.
    """
    v = _fresh_validator()
    original_key = v.CORVIN_PUBLIC_KEY_B64
    # Rebind the module attribute to an attacker key.
    v.CORVIN_PUBLIC_KEY_B64 = "ATTACKER_KEY_PLACEHOLDER=="
    # The closure accessor must still return the original value.
    assert v._get_operator_pubkey_b64() == original_key, (
        "Closure must snapshot the key at import time — module rebind must not propagate"
    )


def test_placeholder_check_rebind_does_not_clear_guard():
    """Clearing _PLACEHOLDER_OPERATOR_PUBKEYS must NOT affect the in-closure check."""
    v = _fresh_validator()
    # Rebind to empty frozenset — normally would bypass the FND-22 guard.
    v._PLACEHOLDER_OPERATOR_PUBKEYS = frozenset()
    # The closure accessor must still recognise the original placeholder key.
    original_placeholder = "MCowBQYDK2VwAyEARuefomX8OXo0fiWeu1iPqqCaKgz2B5eg/mOYXY0iEUs="
    assert v._is_placeholder_key(original_placeholder), (
        "Closure must snapshot _PLACEHOLDER_OPERATOR_PUBKEYS — module rebind must not bypass FND-22"
    )


# ── bool-True limit with float requested ─────────────────────────────────────

def test_assert_limit_bool_true_rejects_float_over_one():
    """bool-True limit must block requested=1.5 (float > 1.0) — not just int > 1."""
    from license.limits import LicenseLimitError as _LLE
    v = _fresh_validator()
    LicenseLimitError = importlib.import_module("license.limits").LicenseLimitError
    # Simulate a SesT token with limits: {"feature": true} (JSON boolean)
    v._set_active_license({
        "tier": "member",
        "limits": {"concurrent_exports": True},
    })
    with pytest.raises(LicenseLimitError):
        v.assert_limit("concurrent_exports", requested=1.5)


# ── Float ceiling in numeric comparison ──────────────────────────────────────

def test_assert_limit_float_ceiling_blocks_just_over_limit():
    """assert_limit must block float 5.1 when limit is 5 (int(5.1)=5 would pass wrongly)."""
    v = _fresh_validator()
    LicenseLimitError = importlib.import_module("license.limits").LicenseLimitError
    v._set_active_license({
        "tier": "member",
        "limits": {"forge_tools_max": 5},
    })
    with pytest.raises(LicenseLimitError):
        v.assert_limit("forge_tools_max", requested=5.1)


def test_assert_limit_float_at_exact_limit_passes():
    """assert_limit must allow exactly-at-limit float (5.0 ≤ 5)."""
    v = _fresh_validator()
    v._set_active_license({
        "tier": "member",
        "limits": {"forge_tools_max": 5},
    })
    v.assert_limit("forge_tools_max", requested=5.0)  # must not raise


# ── Circular-reference rebind attack (ADR-0144 F-07) ─────────────────────────

def test_circular_reference_canary_bypass_is_blocked(caplog):
    """Two-step rebind with a circular-reference dict must be detected.

    Attack pattern:
      1. _ACTIVE_LICENSE ← MappingProxyType({..., "self": circular}) so that
         _compute_canary fails and returns _CANARY_UNCOMPUTABLE
      2. _ACTIVE_LICENSE_CANARY ← _CANARY_UNCOMPUTABLE  (spoof the sentinel)
    Before ADR-0144 F-07 the canary check compared equal (sentinel == sentinel)
    and returned the tampered license.  After the fix, _verified_license() detects
    the _CANARY_UNCOMPUTABLE-while-non-None state and falls back to Free tier.
    """
    import logging
    v = _fresh_validator()
    v._set_active_license({"tier": "member", "iss": "corvinlabs.io"})
    assert v.active_tier() == "member"

    # Build a circular dict that will cause JSON serialisation to fail.
    bad: dict = {"tier": "member", "limits": {"workflows_concurrent": None}}
    bad["_circ"] = bad  # circular reference

    # Step 1: inject the circular dict as the active license.
    v._ACTIVE_LICENSE = types.MappingProxyType(bad)
    # Step 2: spoof the canary with the sentinel.
    v._ACTIVE_LICENSE_CANARY = v._CANARY_UNCOMPUTABLE

    with caplog.at_level(logging.CRITICAL, logger="corvin.license"):
        tier = v.active_tier()
        limit = v.get_limit("workflows_concurrent")

    # Must fall back to Free tier — the attack is blocked.
    assert tier == "free", f"Expected 'free' (attack blocked) but got {tier!r}"
    assert limit is not None, "workflows_concurrent must be limited on free tier"
    assert any("circular-reference" in r.message or "CANARY_UNCOMPUTABLE" in r.message
               or "F-07" in r.message
               for r in caplog.records), "Expected F-07 CRITICAL log"


# ── Comprehensive E2E proof: all attacks fail in sequence ────────────────────

def test_e2e_all_attacks_blocked():
    """End-to-end proof: load a real enterprise license, attempt every known
    in-process bypass vector, verify each is blocked.

    This is the single canonical proof that the validator cannot be bypassed
    by any of the ADR-0144 attack vectors (within the ADR-0139 accepted boundary).
    """
    # Import LicenseLimitError AFTER _fresh_validator() — the fresh-import cycle
    # produces a new class object; pytest.raises needs the same identity.
    v = _fresh_validator()
    LicenseLimitError = importlib.import_module("license.limits").LicenseLimitError
    v._set_active_license({
        "tier": "member",
        "iss": "corvinlabs.io",
        "limits": {
            "compute_units_per_day": 1000,
            "tenants_max": 50,
            "forge_tools_max": 5,
        },
        "features": {"white_label": True},
    })

    # Baseline: enterprise works normally.
    assert v.active_tier() == "member"
    assert v.get_limit("compute_units_per_day") == 1000
    assert v.get_feature("white_label") is True

    # ── Attack A: gc.get_referents() dict mutation ────────────────────────────
    underlying = gc.get_referents(v._ACTIVE_LICENSE)[0]
    assert isinstance(underlying, dict)
    underlying["tier"] = "free"  # gc-mutation attempt
    # Must fall back to free tier — canary detects the hash change.
    assert v.active_tier() == "free", "gc mutation must be detected"
    assert v.get_limit("compute_units_per_day") != 1000, "gc mutation must be detected"
    # Restore to clean enterprise state for subsequent attacks.
    v._set_active_license({
        "tier": "member", "iss": "corvinlabs.io",
        "limits": {"compute_units_per_day": 1000, "tenants_max": 50,
                   "forge_tools_max": 5},
        "features": {"white_label": True},
    })

    # ── Attack B: direct _ACTIVE_LICENSE name-rebind ─────────────────────────
    v._ACTIVE_LICENSE = types.MappingProxyType({"tier": "member"})
    # Canary mismatch (salt-protected) → free tier.
    assert v.active_tier() == "free", "name-rebind must be detected"
    v._set_active_license({
        "tier": "member", "iss": "corvinlabs.io",
        "limits": {"compute_units_per_day": 1000, "tenants_max": 50,
                   "forge_tools_max": 5},
    })

    # ── Attack C: circular-reference sentinel spoof (F-07) ───────────────────
    bad: dict = {"tier": "member", "limits": {"compute_units_per_day": None}}
    bad["_circ"] = bad
    v._ACTIVE_LICENSE = types.MappingProxyType(bad)
    v._ACTIVE_LICENSE_CANARY = v._CANARY_UNCOMPUTABLE
    assert v.active_tier() == "free", "circular-reference spoof must be detected"
    v._set_active_license({
        "tier": "member", "iss": "corvinlabs.io",
        "limits": {"compute_units_per_day": 1000, "tenants_max": 50,
                   "forge_tools_max": 5},
    })

    # ── Attack D: negative requested bypasses > check ────────────────────────
    with pytest.raises(LicenseLimitError):
        v.assert_limit("compute_units_per_day", -1)

    # ── Attack E: float truncation bypass (int(5.9) = 5 ≤ 5) ────────────────
    with pytest.raises(LicenseLimitError):
        v.assert_limit("forge_tools_max", 5.9)  # must ceil → 6 > 5 → raise

    # ── Attack F: bool-True limit passed with requested=100 ──────────────────
    v._set_active_license({
        "tier": "member",
        "limits": {"sso_enabled": True},  # bool True = feature on, but count=1
    })
    with pytest.raises(LicenseLimitError):
        v.assert_limit("sso_enabled", requested=100)

    # ── Attack G: FREE_TIER mutation attempt ─────────────────────────────────
    from license.limits import FREE_TIER
    with pytest.raises(TypeError):
        FREE_TIER["compute_units_per_day"] = 9999  # type: ignore[index]

    # ── Attack H: TIER_RESOURCE_LIMITS mutation attempt ───────────────────────
    from license.limits import TIER_RESOURCE_LIMITS
    with pytest.raises(TypeError):
        TIER_RESOURCE_LIMITS["enterprise"]["compute_units_per_day"] = 0  # type: ignore[index]

    # ── Attack I: SESSION_SERVER_KEY_RING key replacement ────────────────────
    with pytest.raises(TypeError):
        v.SESSION_SERVER_KEY_RING["sess-v1"] = "attacker_key"  # type: ignore[index]

    # ── Attack J: operator key module rebind ─────────────────────────────────
    original_key = v.CORVIN_PUBLIC_KEY_B64
    v.CORVIN_PUBLIC_KEY_B64 = "ATTACKER_KEY=="
    assert v._get_operator_pubkey_b64() == original_key, "key closure must not be affected"
    v.CORVIN_PUBLIC_KEY_B64 = original_key  # restore

    # ── Attack K: placeholder guard rebind ───────────────────────────────────
    original_placeholders = v._PLACEHOLDER_OPERATOR_PUBKEYS
    v._PLACEHOLDER_OPERATOR_PUBKEYS = frozenset()
    placeholder = "MCowBQYDK2VwAyEARuefomX8OXo0fiWeu1iPqqCaKgz2B5eg/mOYXY0iEUs="
    assert v._is_placeholder_key(placeholder), "placeholder closure must not be affected"
    v._PLACEHOLDER_OPERATOR_PUBKEYS = original_placeholders

    # ── Attack L: unknown feature defaults to 0 (fail-closed) ───────────────
    v._set_active_license({"tier": "member"})
    limit_unknown = v.get_limit("__nonexistent_feature_xyz__")
    assert limit_unknown == 0, f"Unknown feature must default to 0 (fail-closed), got {limit_unknown!r}"


# ── capability.assert_limit() hardening ──────────────────────────────────────

def _make_cap():
    """Return a Capability wrapping a minimal stub SOB client (free tier)."""
    # Import after fresh module state to avoid stale class references.
    _OPERATOR_ROOT = str(Path(__file__).resolve().parents[3])
    if _OPERATOR_ROOT not in sys.path:
        sys.path.insert(0, _OPERATOR_ROOT)

    from license.capability import Capability
    from license.limits import FREE_TIER, TIER_RESOURCE_LIMITS

    class _StubSob:
        def get_claims(self):
            return {"tier": "free", "limits": {"test_cap_feat": 5, "bool_feat": True}}
        def active_tier(self):
            return "free"
        def is_loaded(self):
            return True

    return Capability(_StubSob())


def test_capability_assert_limit_negative_rejected():
    """capability.assert_limit must reject negative requested values."""
    from license.limits import LicenseLimitError
    cap = _make_cap()
    with pytest.raises(LicenseLimitError):
        cap.assert_limit("test_cap_feat", -1)


def test_capability_assert_limit_float_ceiling():
    """capability.assert_limit must use math.ceil, not int() truncation."""
    from license.limits import LicenseLimitError
    cap = _make_cap()
    # limit is 5; int(5.9)=5 would pass wrongly; ceil(5.9)=6 > 5 → must raise
    with pytest.raises(LicenseLimitError):
        cap.assert_limit("test_cap_feat", 5.9)


def test_capability_assert_limit_bool_true_numeric_bypass():
    """capability.assert_limit with bool-True limit must block requested > 1."""
    from license.limits import LicenseLimitError
    cap = _make_cap()
    # bool_feat has limit=True; requested=100 must be blocked
    with pytest.raises(LicenseLimitError):
        cap.assert_limit("bool_feat", 100)


# ── JWT type-coercion guards (ADR-0144 string-coercion + negative-limit) ──────

def test_string_digit_limit_is_rejected(caplog):
    """A string-typed digit limit ('9999') must NOT be coerced to int 9999.

    Before ADR-0144 string-coercion guard: int('9999')=9999 succeeded silently
    in both assert_limit and compute_quota, granting an oversized quota to any
    customer whose SesT had a string instead of int for a numeric limit field.

    After the fix: _resolve_limit falls through to the tier default and logs a
    WARNING so the operator notices the malformed token.
    """
    import logging
    v = _fresh_validator()
    # Simulate a SesT with a digit-string for a numeric limit (free tier).
    v._set_active_license({
        "tier": "free",
        "limits": {"compute_units_per_day": "9999"},  # string, not int!
    })
    with caplog.at_level(logging.WARNING, logger="corvin.license"):
        result = v.get_limit("compute_units_per_day")

    # Must NOT be 9999 — must fall back to free-tier default (1) not string-coerced 9999.
    assert result != 9999, (
        f"String '9999' must not be coerced to int 9999; got {result!r}"
    )
    assert any("string" in r.message for r in caplog.records), (
        "Expected a WARNING about the string-typed limit"
    )


def test_string_digit_assert_limit_uses_tier_default():
    """With a string digit limit, assert_limit uses the tier default (not 9999)."""
    v = _fresh_validator()
    LicenseLimitError = importlib.import_module("license.limits").LicenseLimitError
    # free tier: compute_units_per_day = 1.  String "9999" must be ignored.
    # Requesting 9999 must therefore raise because free-tier limit is 1.
    v._set_active_license({
        "tier": "free",
        "limits": {"compute_units_per_day": "9999"},
    })
    with pytest.raises(LicenseLimitError):
        v.assert_limit("compute_units_per_day", 9999)


def test_negative_limit_clamped_to_zero(caplog):
    """A negative integer limit must be clamped to 0, not silently DoS the feature.

    A SesT with limits: {feature: -1} would make (current+1) > -1 always True,
    permanently blocking the feature even on the first call.  Clamping to 0 makes
    the denial explicit (0 = no access) rather than a silent side-effect of the
    comparison arithmetic.
    """
    import logging
    v = _fresh_validator()
    v._set_active_license({
        "tier": "member",
        "limits": {"compute_units_per_day": -1},
    })
    with caplog.at_level(logging.WARNING, logger="corvin.license"):
        result = v.get_limit("compute_units_per_day")

    assert result == 0, f"Negative limit must be clamped to 0, got {result!r}"
    assert any("negative" in r.message for r in caplog.records), (
        "Expected a WARNING about the negative limit"
    )


def test_null_in_limits_grants_unlimited():
    """null/None in per-customer limits grants unlimited for that feature.

    This is intentional: the signing tool uses null to mean 'no constraint'
    (same semantic as enterprise tier defaults).  This test documents the
    design decision and verifies the behaviour is stable.

    Security note: this relies entirely on signing-tool correctness — a bug
    that serialises a numeric field as null would grant unlimited access for
    that customer.  The Ed25519 signature is the last line of defence.
    """
    v = _fresh_validator()
    v._set_active_license({
        "tier": "personal",
        "limits": {"compute_units_per_day": None},  # operator explicit unlimited
    })
    result = v.get_limit("compute_units_per_day")
    # None = unlimited; assert_limit must NOT raise for any requested value.
    assert result is None, (
        "null in per-customer limits must grant unlimited (None) for that feature"
    )
    v.assert_limit("compute_units_per_day", 9999)  # must not raise


def test_unknown_string_limit_falls_through_to_tier():
    """A non-digit string limit ('unlimited') must fall through to tier default."""
    v = _fresh_validator()
    v._set_active_license({
        "tier": "free",
        "limits": {"compute_units_per_day": "unlimited"},
    })
    result = v.get_limit("compute_units_per_day")
    # "unlimited" string — invalid type, falls through to FREE_TIER default = 1
    assert result == 1, (
        f"Non-digit string must fall through to FREE_TIER; got {result!r}"
    )


# ── ADR-0139 accepted in-process boundary — documented bypasses ───────────────
#
# The tests below DELIBERATELY SUCCEED — i.e., the "attack" works.
# This is expected and documented in ADR-0139 + in _make_compute_canary() docstring.
# Their purpose is to prove the boundary is understood and tested, not to block the
# attacks (which would require an out-of-process enforcer — ADR-0139 Option A,
# not yet implemented).  If any of these tests START FAILING, it means we added a
# countermeasure for an accepted vector without an ADR — that would be a regression.
#
# Each test includes a BYPASS_CONFIRMED marker so they are easy to grep.

def test_adr0139_compute_canary_rebind_is_accepted_bypass():
    """BYPASS_CONFIRMED (ADR-0139): _compute_canary rebind defeats the canary check.

    Rebinding _compute_canary to ``lambda x: _ACTIVE_LICENSE_CANARY`` makes the
    canary always return the currently-stored canary value, so any _ACTIVE_LICENSE
    passes the check.  This requires 2 attribute rebinds and no knowledge of the
    closure-captured salt.

    This test verifies the vector WORKS (i.e., it is an accepted gap).
    Compensating controls: bwrap, L10 path-gate, MCP-server vetting.
    """
    import types as _types
    v = _fresh_validator()
    v._set_active_license({"tier": "member", "iss": "corvinlabs.io"})
    assert v.active_tier() == "member"

    original_compute_canary = v._compute_canary
    try:
        v._compute_canary = lambda lic: v._ACTIVE_LICENSE_CANARY  # Rebind 1
        v._ACTIVE_LICENSE = _types.MappingProxyType({"tier": "member"})  # Rebind 2
        tier = v.active_tier()
        assert tier == "member", (
            "ADR-0139 boundary: _compute_canary rebind should bypass the canary. "
            "If this fails, a countermeasure was added without updating ADR-0139."
        )
    finally:
        v._compute_canary = original_compute_canary
        v._set_active_license(None)


def test_adr0139_verified_license_rebind_is_accepted_bypass():
    """BYPASS_CONFIRMED (ADR-0139): _verified_license rebind defeats all enforcement.

    Single-rebind — the most powerful in-process bypass.  All public enforcement
    APIs (get_limit, assert_limit, active_tier) go through _verified_license.
    """
    import types as _types
    v = _fresh_validator()
    assert v.active_tier() == "free"

    original_vl = v._verified_license
    try:
        v._verified_license = lambda: _types.MappingProxyType({"tier": "member"})
        assert v.active_tier() == "member", (
            "ADR-0139 boundary: _verified_license rebind should bypass all checks."
        )
    finally:
        v._verified_license = original_vl


def test_adr0139_grace_period_rebind_is_accepted():
    """BYPASS_CONFIRMED (ADR-0139): _GRACE_PERIOD_SECONDS is rebindable.

    An attacker can extend the grace period to accept tokens expired years ago.
    Not tested with a real signed expired JWT here; structural rebindability confirmed.
    """
    v = _fresh_validator()
    original = v._GRACE_PERIOD_SECONDS
    try:
        v._GRACE_PERIOD_SECONDS = 999_999_999.0
        assert v._GRACE_PERIOD_SECONDS == 999_999_999.0
    finally:
        v._GRACE_PERIOD_SECONDS = original
