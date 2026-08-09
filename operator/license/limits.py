"""Free-tier defaults, tier defaults, and LicenseLimitError.

All licence-gated features default to FREE_TIER values.
Fail-closed: removing or skipping the licence check leaves everything locked
at the Free tier rather than silently granting elevated access.

Resolution order for get_limit() in validator.py:
  1. Active SesT's "limits" dict (per-customer override)
  2. TIER_RESOURCE_LIMITS[tier] (tier-level default)
  3. FREE_TIER (absolute fallback)
"""
from __future__ import annotations

import types as _types
from typing import Any


def _deep_freeze(obj: Any) -> Any:
    """Recursively wrap dicts in MappingProxyType so callers can read but not mutate.

    Lists are NOT converted here (that responsibility belongs to validator._freeze_license
    which handles license payloads). Lists in FREE_TIER/TIER_RESOURCE_LIMITS stay as
    tuples after this call so `in` checks and iteration still work correctly.
    """
    if isinstance(obj, dict):
        return _types.MappingProxyType({k: _deep_freeze(v) for k, v in obj.items()})
    if isinstance(obj, list):
        return tuple(_deep_freeze(item) for item in obj)
    return obj


class LicenseLimitError(Exception):
    """Raised by assert_limit() when a requested value exceeds the licence limit.

    Callers MUST surface this to the operator — treat it as a hard refusal,
    not a warning.
    """

    def __init__(self, feature: str, requested: Any = None, limit: Any = None, tier: str = "free"):
        self.feature = feature
        self.requested = requested
        self.limit = limit
        self.tier = tier
        if requested is not None and limit is not None:
            msg = (
                f"License limit exceeded: {feature} — "
                f"requested {requested!r}, limit {limit!r} (tier: {tier}). "
                f"Upgrade at corvin-labs.com/pricing."
            )
        else:
            msg = (
                f"License limit exceeded: {feature} not available on tier '{tier}'. "
                f"Upgrade at corvin-labs.com/pricing."
            )
        super().__init__(msg)


# ── Free-tier defaults ────────────────────────────────────────────────────────
# These are the limits that apply when NO valid licence key is present.
# Every field must have a value here — there is no "unknown" state.

FREE_TIER: dict[str, Any] = {
    # Compute — ONE shared daily pool for ALL agentic compute engines: ACS
    # workflows, TDE runs (tiered delegation, ADR-0214), compute/grid-search
    # runs (L25), forge compute_run and A2A compute. Every engine chokepoint
    # charges the SAME counter file (global/license/compute_quota.json), so
    # this limit is the SUM across engines, not per-engine (maintainer
    # decision 2026-07-24: 10 agentic turns/day on the free tier, was 1).
    "compute_units_per_day":  10,

    # Context Engineering (Vibe Engineering, ADR-0276). Its OWN daily pool,
    # SEPARATE from compute_units_per_day: a context-engineered turn runs extra
    # memory/graph/skill work (build_brief). 10/day on free; over budget a turn
    # runs on plain context (no CE) — a quiet degrade, never a block (I2). Own
    # counter_file (context_engineering_quota.json) so CE and delegation never
    # starve each other. Paid tiers → unlimited (member below).
    "context_engineering_units_per_day": 10,

    # Chat / design-assistant interactive turns. UNLIMITED on every tier
    # (operator decision 2026-06-23): the conversational assistant must always be
    # fully usable, even on the free tier with the local Hermes fallback. What IS
    # gated is the heavier machinery — compute workloads, workflows/pipelines,
    # A2A peers, custom layers — via their own axes below, NOT the chat axis.
    "chat_turns_per_day":     None,   # unlimited — chat is always free

    # Voice summaries (ADR-0194). Its OWN axis, deliberately not chat's: the
    # console's /voice/tts spawns the same paid summarizer as /voice/summarize
    # but runs automatically once per turn, so metering it on chat_turns_per_day
    # would charge every chat turn twice. Unlimited by default — this exists so
    # the two endpoints are on the same meter (previously /voice/summarize was
    # gated and /voice/tts was not, i.e. the gate was one endpoint away from
    # being routed around) and so a finite value can be shipped later without
    # touching the chat axis. NOTE: an axis MUST be declared here — an unknown
    # feature resolves to FREE_TIER.get(feature, 0), i.e. ZERO, which would
    # 402 every voice call rather than allow it.
    "voice_summaries_per_day": None,  # unlimited — voice follows chat

    # A2A (Layer 38)
    "a2a_peers_max":          1,

    # Workflows (web-UI concurrent + total cap)
    "workflows_concurrent":   1,
    "workflows_max":          1,   # free tier: only 1 workflow may exist

    # Multi-tenancy
    "tenants_max":            1,

    # RAG providers
    "rag_providers_max":      1,

    # Space — public publishing domains (Layer 40)
    "space_domains_max":      1,   # free tier: one public domain only

    # Bridges — list of allowed channel names; None = all allowed
    "bridges_allowed":        None,   # all bridges allowed on free tier

    # Engines — list of allowed engine ids; None = all allowed
    "engines_allowed":        None,   # all engines allowed on free tier

    # Data Sources — list of allowed DSI adapter ids; None = all allowed
    # Free tier: only local files (no remote DB credentials needed, no egress)
    "datasource_adapters_allowed": ["local_file"],

    # Custom Layers (ADR-0156 M2)
    # Maximum number of simultaneously active Tier-B/C custom layers.
    # Tier-A (prompt/skill) layers are always free; only B and C are gated.
    "active_custom_layers_bc": 1,

    # Compliance
    "data_residency":         False,  # zone enforcement not available on free tier
    # RESERVED — NOT YET ENFORCED (cloud-phase features). The three keys below
    # (audit_export, sso_enabled, enterprise_portal) are declared as tier
    # differentiators but have NO enforcement chokepoint in this repo; the
    # underlying features are deferred to the cloud phase. They are surfaced as
    # "(roadmap)" in the console license page so the UI does not advertise a
    # paid differentiator that ships no enforced feature. Do not gate any
    # feature on these keys until a real enforcement point lands.
    "audit_export":           False,  # reserved — not yet enforced (cloud-phase)
    "sso_enabled":            False,  # reserved — not yet enforced (cloud-phase)
    "enterprise_portal":      False,  # reserved — not yet enforced (cloud-phase)
}


# ── Per-tier defaults (ADR-0094) ──────────────────────────────────────────────
# Applied when the active SesT has no "limits" key or a field is absent from it.
# None = unlimited (no constraint); overrides FREE_TIER for that field.

TIER_RESOURCE_LIMITS: dict[str, dict[str, Any]] = {
    # Member — the single paid tier, EVERYTHING unlocked, single-device licence
    # (€10/month, instance-ID-bound). Only two tiers exist (operator decision
    # 2026-06-23): free (FREE_TIER) + member. All legacy paid-tier names + the
    # "universal" alias collapse to member (aliases added below).
    "member": {
        "compute_units_per_day":        None,   # unlimited
        "context_engineering_units_per_day": None,  # unlimited (Vibe Engineering)
        "chat_turns_per_day":           None,   # unlimited
        "voice_summaries_per_day":      None,   # unlimited
        "a2a_peers_max":                None,
        "workflows_concurrent":         None,
        "workflows_max":                None,   # unlimited
        "tenants_max":                  None,
        "rag_providers_max":            None,
        "space_domains_max":            None,   # unlimited
        "bridges_allowed":              None,
        "engines_allowed":              None,
        "datasource_adapters_allowed":  None,   # all adapters
        "active_custom_layers_bc":      None,   # unlimited (ADR-0156 M2)
        "data_residency":               True,
        "audit_export":                 True,
        "sso_enabled":                  True,
        "enterprise_portal":            True,
    },
}

# Only two tiers exist (operator decision 2026-06-23): free + member. Every
# legacy paid-tier name (+ "universal") collapses to member. Aliases must be
# added BEFORE _deep_freeze() locks the dict.
for _legacy in ("universal", "starter", "personal", "professional",
                "pro", "business", "enterprise"):
    TIER_RESOURCE_LIMITS[_legacy] = dict(TIER_RESOURCE_LIMITS["member"])

# Freeze both tables so in-process mutation raises TypeError instead of silently
# downgrading all users.  _deep_freeze() wraps dicts recursively in MappingProxyType
# and converts lists to tuples.  Any code that read these values via dict literals
# or isinstance(x, dict) will need to also accept MappingProxyType — the same
# pattern already in place for _ACTIVE_LICENSE after _freeze_license().
FREE_TIER = _deep_freeze(FREE_TIER)
TIER_RESOURCE_LIMITS = _deep_freeze(TIER_RESOURCE_LIMITS)
