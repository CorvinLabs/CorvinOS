"""Tier → Feature-Flag canonical map.

ADR-0019 §"Tier→Flag Map" — single source of truth for which
premium features each commercial tier enables. Used by:

* ``cli.py::_cmd_issue`` — refuses ``--flags`` that don't match the
  chosen ``--tier`` (closes the inconsistent-license risk where a
  Maintainer could accidentally hand out enterprise flags to a
  pro-tier customer).
* The future ``Corvin-Signer`` air-gapped signing host — rejects
  sign-requests whose flag set is not exactly ``TIER_FLAGS[tier]``.
* ``verifier._validate_claims`` — defence-in-depth: rejects an
  installed license whose flag set drifts from the tier mapping
  (catches a signing-host compromise that produced an off-tier JWT).

Adding a new tier or moving a flag between tiers is a commercial-
policy decision; it goes through an ADR amendment, not a per-PR
change. The CI gate ``test_tier_flag_map_consistency`` validates
that every value in this map is a subset of
``verifier.VALID_FEATURE_FLAGS``.
"""
from __future__ import annotations

from typing import Iterable


# Canonical mapping. The frozenset values protect against accidental
# mutation by importers; the order of iteration is undefined which is
# fine because every consumer compares as a set.
TIER_FLAGS: dict[str, frozenset[str]] = {
    "free": frozenset(),
    # personal: single-device instance-bound licence (€9/month).
    # Same feature flags as pro; limits differ (instance_id_bound in SesT).
    "personal": frozenset({
        "compliance_reports_premium",
        "compute",               # ADR-0013/ADR-0017 — out-of-LLM compute worker
    }),
    "pro": frozenset({
        "compliance_reports_premium",
        "compute",               # ADR-0013/ADR-0017 — out-of-LLM compute worker
    }),
    "business": frozenset({
        "compliance_reports_premium",
        "sso_wizard",
        "support_integration",
        "compute",               # ADR-0013/ADR-0017
    }),
    "enterprise": frozenset({
        "compliance_reports_premium",
        "cross_tenant_search",
        "sso_wizard",
        "worm_archive",
        "sla_dashboard",
        "support_integration",
        "white_label_ui",
        "compute",               # ADR-0013/ADR-0017
        "compute_fabric",        # ADR-0026 — Compute Fabric (parallel workers, sharding)
    }),
    # ADR-0097 / ADR-0098: flat-rate mass-market tier (€10/month per device).
    # "member" is the canonical CorvinOS name; "universal" is the legacy Corvin-Features name.
    "member": frozenset({
        "compliance_reports_premium",
        "worm_archive",
        "sla_dashboard",
        "compute",
        "compute_fabric",
    }),
}

# Backward-compat aliases — same flag sets, different tier names used in earlier tokens.
TIER_FLAGS["universal"] = TIER_FLAGS["member"]
TIER_FLAGS["starter"] = TIER_FLAGS["personal"]
TIER_FLAGS["professional"] = TIER_FLAGS["pro"]


class TierFlagMismatch(ValueError):
    """Raised when a flag set does not match the canonical tier flags.

    Two sub-cases the message disambiguates:

    * Extra flags — the caller asked for a flag the tier doesn't grant.
      Issuing the license anyway would let the customer use a feature
      they didn't pay for.
    * Missing flags — the caller asked for fewer flags than the tier
      grants. Issuing the license anyway would silently downgrade the
      customer below what they paid for.
    """


def flags_for_tier(tier: str) -> frozenset[str]:
    """Return the canonical flag set for a tier, or raise.

    Unknown tier names raise ``TierFlagMismatch`` (not ``KeyError``)
    so callers get a single exception class to catch.
    """
    tier_norm = tier.lower().strip()
    if tier_norm not in TIER_FLAGS:
        raise TierFlagMismatch(
            f"unknown-tier: {tier!r} (known: {sorted(TIER_FLAGS)})"
        )
    return TIER_FLAGS[tier_norm]


def validate_flags_for_tier(
    tier: str,
    flags: Iterable[str],
) -> frozenset[str]:
    """Verify ``flags`` matches the canonical tier set exactly.

    Returns the canonical frozenset (same as ``flags_for_tier(tier)``)
    so callers can use the return value as the source-of-truth flag
    set for the rest of the issuance.

    Raises ``TierFlagMismatch`` with a precise diagnostic when:
    * The flag set has entries not in the canonical tier set.
    * The flag set is missing entries the canonical tier set has.

    An empty input flag iterable is fine for ``tier="free"`` (which
    has no flags). For commercial tiers, empty input is rejected
    because the caller almost certainly meant "default to the tier's
    flags" — but that's an implicit guess we don't make here. The
    caller derives flags from the tier explicitly via
    ``flags_for_tier(tier)``.
    """
    expected = flags_for_tier(tier)
    actual = frozenset(flags)

    extra = actual - expected
    missing = expected - actual

    if extra and missing:
        raise TierFlagMismatch(
            f"tier-flag-mismatch: tier={tier!r} "
            f"extra={sorted(extra)} missing={sorted(missing)}"
        )
    if extra:
        raise TierFlagMismatch(
            f"tier-flag-extra: tier={tier!r} carries flags "
            f"{sorted(extra)} not granted to this tier "
            f"(canonical: {sorted(expected)})"
        )
    if missing:
        raise TierFlagMismatch(
            f"tier-flag-missing: tier={tier!r} is missing flags "
            f"{sorted(missing)} the canonical mapping grants "
            f"(canonical: {sorted(expected)})"
        )

    return expected


__all__ = [
    "TIER_FLAGS",
    "TierFlagMismatch",
    "flags_for_tier",
    "validate_flags_for_tier",
]
