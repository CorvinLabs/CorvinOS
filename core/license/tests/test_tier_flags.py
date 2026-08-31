"""ADR-0019 §Tier→Flag Map — single-source-of-truth tests.

Validates:
* TIER_FLAGS covers all VALID_TIERS minus 'free'.
* Every flag in every tier is a member of VALID_FEATURE_FLAGS.
* No flag appears in a higher tier without appearing in every
  higher-still tier (subset monotonicity — a business-flag must
  also be in enterprise; otherwise an enterprise customer would
  pay more for less).
* flags_for_tier + validate_flags_for_tier round-trip cleanly.
* Mismatch diagnostics distinguish extra / missing / both.
"""
from __future__ import annotations

import pytest

from corvin_license import tier_flags, verifier


def test_every_tier_is_known():
    """Every entry in TIER_FLAGS must be a valid tier name."""
    for tier in tier_flags.TIER_FLAGS:
        assert tier in verifier.VALID_TIERS, f"unknown tier {tier!r}"


def test_every_flag_is_known():
    """Every flag in every tier must be in VALID_FEATURE_FLAGS."""
    for tier, flags in tier_flags.TIER_FLAGS.items():
        for f in flags:
            assert f in verifier.VALID_FEATURE_FLAGS, (
                f"tier {tier!r} grants unknown flag {f!r}"
            )


def test_free_tier_grants_nothing():
    assert tier_flags.TIER_FLAGS["free"] == frozenset()


def test_tier_hierarchy_is_monotonic():
    """A flag granted to a lower tier must also be granted to every
    higher tier — otherwise a customer upgrading would lose a feature.

    Order: free ⊂ pro ⊂ business ⊂ enterprise.
    """
    order = ["free", "pro", "business", "enterprise"]
    for i in range(len(order) - 1):
        lower, higher = order[i], order[i + 1]
        assert tier_flags.TIER_FLAGS[lower].issubset(
            tier_flags.TIER_FLAGS[higher]
        ), f"non-monotonic: {lower}={sorted(tier_flags.TIER_FLAGS[lower])} " \
           f"⊄ {higher}={sorted(tier_flags.TIER_FLAGS[higher])}"


def test_flags_for_tier_returns_canonical_set():
    assert tier_flags.flags_for_tier("pro") == frozenset({
        "compliance_reports_premium",
        "compute",
    })
    # Case + whitespace tolerated on tier name.
    assert tier_flags.flags_for_tier(" Pro ") == frozenset({
        "compliance_reports_premium",
        "compute",
    })


def test_flags_for_tier_unknown_raises():
    with pytest.raises(tier_flags.TierFlagMismatch) as exc:
        tier_flags.flags_for_tier("god-mode")
    assert "unknown-tier" in str(exc.value)


def test_validate_flags_for_tier_happy_path():
    expected = tier_flags.validate_flags_for_tier(
        "business",
        ["compliance_reports_premium", "sso_wizard", "support_integration", "compute"],
    )
    assert expected == tier_flags.TIER_FLAGS["business"]


def test_validate_flags_for_tier_extra_flag_rejected():
    """A flag the tier doesn't grant is rejected."""
    with pytest.raises(tier_flags.TierFlagMismatch) as exc:
        tier_flags.validate_flags_for_tier(
            "pro",
            ["compliance_reports_premium", "compute", "worm_archive"],
        )
    msg = str(exc.value)
    assert "tier-flag-extra" in msg
    assert "worm_archive" in msg


def test_validate_flags_for_tier_missing_flag_rejected():
    """A flag set with fewer flags than canonical is rejected."""
    with pytest.raises(tier_flags.TierFlagMismatch) as exc:
        tier_flags.validate_flags_for_tier(
            "business",
            ["compliance_reports_premium"],  # missing sso_wizard, support_integration
        )
    msg = str(exc.value)
    assert "tier-flag-missing" in msg


def test_validate_flags_for_tier_both_extra_and_missing():
    """Both extra and missing flags produce the combined diagnostic."""
    with pytest.raises(tier_flags.TierFlagMismatch) as exc:
        tier_flags.validate_flags_for_tier(
            "business",
            ["sso_wizard", "worm_archive"],
        )
    msg = str(exc.value)
    assert "tier-flag-mismatch" in msg
    assert "extra" in msg.lower()
    assert "missing" in msg.lower()


def test_validate_flags_for_tier_idempotent_order():
    """Flag-list ordering does not affect validation."""
    e1 = tier_flags.validate_flags_for_tier(
        "enterprise",
        sorted(tier_flags.TIER_FLAGS["enterprise"]),
    )
    e2 = tier_flags.validate_flags_for_tier(
        "enterprise",
        list(reversed(sorted(tier_flags.TIER_FLAGS["enterprise"]))),
    )
    assert e1 == e2 == tier_flags.TIER_FLAGS["enterprise"]
