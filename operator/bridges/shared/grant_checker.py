"""Layer 41 Social Capability Grants — GrantChecker validation pipeline.

Implements the seven-step capability check from ADR-0054 §grant-lifecycle:

  1. Parse + verify Ed25519 signature against grantor's public key.
  2. Confirm grantee actor_id matches the presenting actor.
  3. Confirm grantor is the local tenant (cross-tenant grants not accepted).
  4. Confirm grantee is still a follower (unfollow = grant invalid).
  5. Evaluate conditions: TTL, rate_limit, data_class_ceiling.
  6. Confirm the requested action matches a granted capability.
  7. ALLOW → ``grant.allowed`` audit + rate-counter increment.
     DENY  → ``grant.denied`` audit only (fail-silent to caller).

L42 hook: ``OrgResolver`` is a typing.Protocol that L42 (CorvinOrg) implements
to extend grantee resolution to org-affiliated agents. When no resolver is
provided, org-actor expansion is skipped silently.

CI AST lint: MUST NOT import anthropic.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

try:
    from .grant_store import GrantStore, grant_db_path
    from .grant_issuer import verify_grant
    from .audit import audit_event
    from .social_actor import load_keypair, social_dir
    from .social_registry import SocialRegistry
except ImportError:
    import sys

    _here = Path(__file__).resolve().parent
    if str(_here) not in sys.path:
        sys.path.insert(0, str(_here))
    from grant_store import GrantStore, grant_db_path
    from grant_issuer import verify_grant
    from audit import audit_event
    from social_actor import load_keypair, social_dir
    from social_registry import SocialRegistry


# ── Data class ordering ───────────────────────────────────────────────────────

_DATA_CLASS_ORDER: dict[str, int] = {
    "PUBLIC": 0,
    "INTERNAL": 1,
    "CONFIDENTIAL": 2,
    "SECRET": 3,
}


# ── L42 OrgResolver hook ──────────────────────────────────────────────────────


@runtime_checkable
class OrgResolver(Protocol):
    """Protocol for L42 CorvinOrg — maps affiliated agent actors to their org.

    Implement this Protocol in L42 and pass an instance to ``GrantChecker``
    to enable org-level grant expansion (ADR-0055 §integration-l41).
    When no resolver is provided, all org-affiliated grant lookups are skipped.
    """

    def resolve_to_org(self, agent_actor_id: str) -> str | None:
        """Return the parent org actor_id if the agent has an active org affiliation.

        Returns ``None`` if the agent has no org affiliation, or if the
        affiliation endorsement has expired / been revoked.
        """
        ...


# ── Result ────────────────────────────────────────────────────────────────────


@dataclass
class GrantCheckResult:
    """Result of a capability check.

    ``deny_reason`` is populated on DENY but MUST NOT be forwarded to the
    remote caller — it is written to the L16 audit chain only (fail-silent).
    ``data_class_ceiling`` is the ceiling from the matched grant's conditions;
    the caller is responsible for enforcing it against L34 before responding.
    """

    allowed: bool
    grant_id: str | None = None
    data_class_ceiling: str | None = None
    deny_reason: str | None = field(default=None, repr=False)


# ── Internal helpers ──────────────────────────────────────────────────────────


def _capability_matches(granted: str, requested: str) -> bool:
    """Return True if the granted capability covers the requested one.

    Wildcard rule: ``*`` in a granted segment matches any single requested
    segment. Wildcards are never implicit — ``domain.research.read`` does NOT
    match a request for ``domain.*.read``; only the grantor can issue a wildcard.
    """
    g_parts = granted.split(".")
    r_parts = requested.split(".")
    if len(g_parts) != len(r_parts):
        return False
    return all(g == "*" or g == r for g, r in zip(g_parts, r_parts))


def _grant_covers_capability(capabilities: list[str], requested: str) -> bool:
    return any(_capability_matches(cap, requested) for cap in capabilities)


def _data_class_allowed(ceiling: str, requested_class: str) -> bool:
    ceiling_level = _DATA_CLASS_ORDER.get(ceiling, -1)
    requested_level = _DATA_CLASS_ORDER.get(requested_class, 999)
    return requested_level <= ceiling_level


def _is_follower(actor_id: str, tenant_id: str | None) -> bool:
    """Return True if actor_id is currently a follower of the local tenant.

    Fail-closed: returns False on any exception (missing registry, DB error).
    An actor that cannot be verified as a follower is treated as not a follower.
    """
    try:
        with SocialRegistry(tenant_id=tenant_id) as registry:
            followers = registry.list_actors(relationship="follower")
            return any(f.get("actor_id") == actor_id for f in followers)
    except Exception:
        return False


# ── GrantChecker ──────────────────────────────────────────────────────────────


class GrantChecker:
    """Seven-step capability grant validation pipeline (ADR-0054).

    Parameters
    ----------
    store:
        GrantStore to read active grants from.
    local_actor_id:
        The local tenant's ActivityPub actor ID (step 3 check).
    local_public_key_hex:
        Ed25519 public key of the local tenant in hex (step 1 verification).
        M1 only verifies grants signed by the local tenant. Remote-grantor
        verification (M4) will fetch keys from remote actor.json endpoints.
    org_resolver:
        Optional L42 OrgResolver for org-affiliated agent expansion.
        Pass ``None`` when L42 is not installed (safe default).
    tenant_id:
        Tenant identifier for social registry follower lookup.
    """

    def __init__(
        self,
        store: GrantStore,
        local_actor_id: str,
        local_public_key_hex: str,
        *,
        org_resolver: OrgResolver | None = None,
        tenant_id: str | None = "_default",
    ) -> None:
        self._store = store
        self._local_actor_id = local_actor_id
        self._local_public_key_hex = local_public_key_hex
        self._org_resolver = org_resolver
        self._tenant_id = tenant_id

    def check(
        self,
        presenting_actor: str,
        capability: str,
        *,
        data_class: str | None = None,
    ) -> GrantCheckResult:
        """Run the full seven-step check for (presenting_actor, capability).

        Parameters
        ----------
        presenting_actor:
            The actor_id of the entity requesting access.
        capability:
            The specific capability requested (e.g. ``'domain.research.read'``).
            Must not contain wildcards — the requestor always names a specific
            resource; wildcards live only in grants.
        data_class:
            Optional data classification of the resource to be accessed.
            When set, the grant's ``data_class_ceiling`` condition is enforced.
            When None, data_class_ceiling is reported in the result but not
            enforced here (the caller applies it via L34).

        Returns
        -------
        ``GrantCheckResult`` with ``allowed=True`` on success.
        On deny: ``allowed=False`` and ``deny_reason`` set (audit-only, never
        forward to the remote caller).
        """
        # Build the candidate grantee addresses to search:
        # 1. The exact presenting actor
        # 2. All-followers wildcard "*"
        # 3. The presenting actor's parent org (via L42 OrgResolver, if installed)
        grantee_candidates = [presenting_actor, "*"]
        if self._org_resolver is not None:
            org_id = self._org_resolver.resolve_to_org(presenting_actor)
            if org_id is not None:
                grantee_candidates.append(org_id)

        all_grants: list[dict] = []
        for candidate in grantee_candidates:
            all_grants.extend(
                self._store.list_grants(grantee_actor=candidate, include_revoked=False)
            )

        if not all_grants:
            return self._deny(presenting_actor, capability, "no_active_grants")

        # Step 4 (follower check) is done once outside the per-grant loop:
        # it is the same for every grant candidate and may involve a DB query.
        is_follower = _is_follower(presenting_actor, self._tenant_id)

        for grant in all_grants:
            result = self._evaluate_grant(
                grant, presenting_actor, capability, data_class, is_follower
            )
            if result is not None:
                return result

        return self._deny(presenting_actor, capability, "no_matching_grant")

    def _evaluate_grant(
        self,
        grant: dict,
        presenting_actor: str,
        capability: str,
        data_class: str | None,
        is_follower: bool,
    ) -> GrantCheckResult | None:
        """Evaluate a single grant through steps 1–6. Returns None on any step failure."""
        grant_id = grant.get("grant_id", "")
        conditions = grant.get("conditions") or {}

        # Step 1: Ed25519 signature verification
        # M1 scope: only local-tenant-signed grants (grantor == local actor).
        # M4 will add remote public-key fetch for remote grantors.
        if not verify_grant(grant, self._local_public_key_hex):
            return None

        # Step 2: grantee_actor must match the presenting actor (or "*" wildcard)
        grantee = grant.get("grantee_actor", "")
        if grantee != "*":
            if grantee == presenting_actor:
                pass  # exact match
            elif self._org_resolver is not None:
                # Allow if grantee is the presenting actor's parent org
                org_id = self._org_resolver.resolve_to_org(presenting_actor)
                if org_id != grantee:
                    return None
            else:
                return None

        # Step 3: Grantor must be the local tenant's actor
        if grant.get("grantor_actor") != self._local_actor_id:
            return None

        # Step 4: Grantee must still be a follower
        if not is_follower:
            return None

        # Step 5a: TTL check
        valid_until = conditions.get("valid_until")
        if valid_until is not None and int(time.time()) > int(valid_until):
            return None

        # Step 5b: Rate limit check (read-only — counter incremented only on ALLOW)
        rate_limit = conditions.get("rate_limit")
        if rate_limit is not None:
            if not self._store.check_rate_limit(grant_id, rate_limit):
                return None

        # Step 5c: Data class ceiling — enforced here only when data_class is known
        ceiling = conditions.get("data_class_ceiling")
        if ceiling is not None and data_class is not None:
            if not _data_class_allowed(ceiling, data_class):
                return None

        # Step 6: Capability match
        grant_caps = grant.get("capabilities") or []
        if not _grant_covers_capability(grant_caps, capability):
            return None

        # All steps passed → ALLOW
        if rate_limit is not None:
            self._store.increment_rate_counter(grant_id, rate_limit)

        audit_event(
            "grant.allowed",
            details={
                "grant_id": grant_id,
                "capability": capability,
                "grantee_prefix": presenting_actor[:16],
            },
        )
        return GrantCheckResult(
            allowed=True,
            grant_id=grant_id,
            data_class_ceiling=ceiling,
        )

    def _deny(
        self,
        presenting_actor: str,
        capability: str,
        reason: str,
    ) -> GrantCheckResult:
        audit_event(
            "grant.denied",
            details={
                "reason": reason,
                "capability": capability,
                "grantee_prefix": presenting_actor[:16],
            },
        )
        return GrantCheckResult(allowed=False, deny_reason=reason)


# ── Factory helper ────────────────────────────────────────────────────────────


def make_checker(
    tenant_id: str | None = "_default",
    org_resolver: OrgResolver | None = None,
) -> GrantChecker:
    """Build a GrantChecker for a tenant using its local keypair and grant store.

    Raises ``RuntimeError`` if the tenant has no social actor keypair (not yet
    joined CorvinFed). The caller should catch this and treat it as an implicit
    deny for all capability checks.
    """
    try:
        private_hex, public_hex = load_keypair(tenant_id=tenant_id)
    except Exception as exc:
        raise RuntimeError(
            f"no social actor keypair for tenant {tenant_id!r}: {exc}"
        ) from exc

    actor_doc_file = social_dir(tenant_id) / "actor.json"
    local_actor_id = ""
    try:
        actor_data = json.loads(actor_doc_file.read_text(encoding="utf-8"))
        local_actor_id = (
            actor_data.get("id")
            or actor_data.get("actor_id")
            or ""
        )
    except Exception:
        pass

    store = GrantStore(grant_db_path(tenant_id=tenant_id))

    return GrantChecker(
        store=store,
        local_actor_id=local_actor_id,
        local_public_key_hex=public_hex,
        org_resolver=org_resolver,
        tenant_id=tenant_id,
    )
