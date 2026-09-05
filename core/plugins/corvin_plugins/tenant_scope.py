"""Provider slots are process-wide, so they may not be filled by a stranger (ADR-0250).

Each of the eight ADR-0033 provider registries holds **one active provider per
process**, not per tenant.  ``set_active()`` takes no tenant argument and there is
nowhere to put one without changing the provider contract.  The consequences are
mechanical rather than hypothetical:

* an ``audit_backend`` installed by tenant A receives a copy of *every* tenant's
  audit events;
* a ``user_backend`` installed by tenant A authenticates for *every* tenant;
* a ``recall_backend`` installed by tenant A writes *every* tenant's turns to the
  path A configured.

ADR-0233's addendum recorded this and answered it with a sentence in
``docs/claude-ref/layer-plugins.md``: *"do not install a third-party provider
plugin on a multi-tenant install."*  That is an instruction to a human, enforced by
nothing, while the admin API happily accepts the operation.

This module is the enforcement.  It is the immediate half of ADR-0250 — the
keying migration (D2) is the real fix and is a separate change; until it lands,
the slot may only be filled by code that shipped in the wheel.

Two properties are load-bearing and neither is a preference:

**Fail-closed on an unanswerable question.**  If the tenant set cannot be
enumerated the answer is *refuse*, not "probably one tenant".  Same reasoning as
ADR-0238's supervisor: "could not check" is not "nothing is running".  A refusal
is one command to recover from; a wrong permit is silent and crosses a data
boundary.

**No feature flag.**  A ``false`` here would be an operator switch that re-enables
a cross-tenant data path, which CLAUDE.md forbids on a compliance mechanism.  The
ship-dark *effect* is preserved without one: on the default single-tenant install
the check finds one tenant and changes nothing.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


#: Plugin types that occupy a process-wide provider slot (ADR-0033 + ADR-0233).
#:
#: Deliberately NOT derived from ``KNOWN_PLUGIN_TYPES``: three of those types —
#: ``compute_engine``, ``worker_engine``, ``bridge_channel`` — register into their
#: own subsystems' registries rather than into ``providers/``, and those registries
#: are keyed differently.  Listing the provider types explicitly means adding a new
#: provider module forces a deliberate edit here; deriving would have silently
#: included the three that do not belong and silently excluded a new one that does.
#: ``test_provider_types_match_the_provider_modules`` pins this set to the
#: ``providers/`` directory, so a new ``providers/<type>.py`` must be added here.
PROVIDER_PLUGIN_TYPES: frozenset[str] = frozenset(
    {
        "audit_backend",
        "user_backend",
        "recall_backend",
        "router_backend",
        "summary_provider",
        "notification_backend",
        "stt_provider",
        "data_connector",
        "context_retriever",  # ADR-0599 CEL/TDE context-selection slot
    }
)


@dataclass(frozen=True)
class ScopeDecision:
    """The outcome of the tenant-scope check for one plugin."""

    allowed: bool
    #: Short, stable slug for the audit detail — never free-form text.
    reason: str
    #: Number of tenants found, or None when enumeration failed.
    tenant_count: int | None = None

    def __bool__(self) -> bool:  # pragma: no cover - convenience only
        return self.allowed


def count_tenants(corvin_home: Path) -> int | None:
    """Count tenants on this install.  ``None`` means the question is unanswerable.

    A tenant is a directory under ``<corvin_home>/tenants/``.  Symlinks are counted
    as what they point at rather than skipped: the backward-compat layout puts
    symlinks at ``<corvin_home>/{global,sessions,...}``, but those live one level
    up and are not inside ``tenants/``, so a symlink *here* is a real tenant that
    someone relocated.

    Returns ``None`` — not ``0``, and not ``1`` — when the directory cannot be
    read.  Zero and one are permissive answers, and an unreadable directory must
    never produce a permissive answer.
    """
    try:
        tenants_dir = Path(corvin_home) / "tenants"
        if not tenants_dir.is_dir():
            # No tenants directory at all is a fresh or non-standard install, and
            # a fresh install has exactly one tenant by construction. This is the
            # one absent-means-one case, and it is safe because the alternative
            # would refuse every provider plugin on a first boot.
            return 1
        return sum(1 for child in tenants_dir.iterdir() if child.is_dir())
    except OSError as exc:
        log.warning(
            "tenant enumeration failed (%s) — the provider-slot check will refuse",
            type(exc).__name__,
        )
        return None


def evaluate(
    *,
    plugin_type: str,
    origin: str | None,
    corvin_home: Path,
) -> ScopeDecision:
    """May this plugin occupy a process-wide provider slot?

    ``origin`` is the manifest's provenance value.  ``None`` means the load path
    could not supply one — the declarative ``spec.plugins.installed`` path carries
    no ``origin`` field — and is treated as **not builtin**.  That is deliberate:
    an operator writing a class path into a tenant config is an explicit opt-in
    for *that tenant*, and says nothing about the other tenants whose data the
    slot would reach.
    """
    if plugin_type not in PROVIDER_PLUGIN_TYPES:
        return ScopeDecision(True, "not_a_provider_type")

    # Only the wheel is exempt. `vetted` is deliberately NOT: a maintainer
    # signature attests who wrote the code (ADR-0249), not that its storage path,
    # cache key or auth decision is tenant-aware. Those are different claims and
    # conflating them is how the exemption would quietly widen.
    if origin == "builtin":
        return ScopeDecision(True, "origin_builtin")

    count = count_tenants(corvin_home)
    if count is None:
        return ScopeDecision(False, "tenant_enumeration_failed", None)
    if count <= 1:
        return ScopeDecision(True, "single_tenant", count)
    return ScopeDecision(False, "multi_tenant_provider_slot", count)


__all__ = [
    "PROVIDER_PLUGIN_TYPES",
    "ScopeDecision",
    "count_tenants",
    "evaluate",
]
