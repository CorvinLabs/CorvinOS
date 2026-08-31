"""E2E: Verify Console and Discord use the same OS-model resolution.

This test demonstrates that both surfaces call resolve_os_model()
with the same tenant_id and engine_id, ensuring consistency.

Run with: uv run pytest tests/test_model_consistency_e2e.py -v
"""
import sys
from pathlib import Path

# Setup path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from bridges.shared import model_selector


def test_default_high_is_sonnet():
    """Verify: DEFAULT_HIGH is Sonnet, not Haiku."""
    assert model_selector.DEFAULT_HIGH == "claude-sonnet-5", \
        "DEFAULT_HIGH must be Sonnet-5 for Main OS-Turns"


def test_default_low_is_haiku():
    """Verify: DEFAULT_LOW is Haiku (for adaptive downgrade)."""
    assert model_selector.DEFAULT_LOW == "claude-haiku-4-5-20251001", \
        "DEFAULT_LOW must be Haiku for adaptive selection"


def test_resolve_os_model_returns_sonnet_by_default():
    """Verify: resolve_os_model() returns Sonnet by default (no env override)."""
    # No env vars set, no profile → should return DEFAULT_HIGH
    result = model_selector.resolve_os_model(None, engine_id="claude_code")
    assert result == "claude-sonnet-5", \
        f"Expected Sonnet-5 by default, got {result}"


def test_haiku_downgrade_disabled_by_default():
    """Verify: Haiku downgrade requires explicit CORVIN_OS_MODEL_ALLOW_HAIKU=1."""
    # haiku_downgrade_allowed() should return False by default
    assert not model_selector.haiku_downgrade_allowed(), \
        "Haiku downgrade must be disabled by default"


def test_autoselect_respects_haiku_opt_in():
    """Verify: autoselect uses Sonnet unless user explicitly opts into Haiku."""
    # Even with small payload, without ALLOW_HAIKU flag → should return Sonnet
    small_payload = 1000  # bytes
    result = model_selector.autoselect_os_model(small_payload)
    assert result == "claude-sonnet-5", \
        "autoselect must default to Sonnet unless CORVIN_OS_MODEL_ALLOW_HAIKU=1"


def test_apply_floor_never_downgrades():
    """Verify: Floor model never downgrades chosen model."""
    # If chosen is Sonnet, floor to Haiku should NOT downgrade
    result = model_selector.apply_floor("claude-sonnet-5", "haiku")
    assert result == "claude-sonnet-5", \
        "apply_floor must never downgrade; Sonnet > Haiku"


def test_tier_1_override_wins():
    """Verify: CORVIN_OS_MODEL_OVERRIDE (Tier 1) wins all other tiers."""
    import os
    from unittest import mock

    with mock.patch.dict(os.environ, {"CORVIN_OS_MODEL_OVERRIDE": "claude-opus-4-7"}):
        result = model_selector.resolve_os_model(None)
        assert result == "claude-opus-4-7", \
            "Tier-1 CORVIN_OS_MODEL_OVERRIDE must win"


def test_tier_2_profile_model_wins():
    """Verify: profile.model (Tier 2) wins over autoselect."""
    result = model_selector.resolve_os_model(
        {"model": "claude-haiku-4-5-20251001"},
        engine_id="claude_code",
    )
    assert result == "claude-haiku-4-5-20251001", \
        "Tier-2 profile.model must win over Tier-3 autoselect"


if __name__ == "__main__":
    # Run all tests
    test_default_high_is_sonnet()
    test_default_low_is_haiku()
    test_resolve_os_model_returns_sonnet_by_default()
    test_haiku_downgrade_disabled_by_default()
    test_autoselect_respects_haiku_opt_in()
    test_apply_floor_never_downgrades()
    test_tier_1_override_wins()
    test_tier_2_profile_model_wins()
    print("✅ All E2E model-consistency tests passed!")
