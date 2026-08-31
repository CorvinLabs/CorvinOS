"""Layer 42 CorvinOrg — OrgResolver implementation for L41 GrantChecker.

``CorvinOrgResolver`` implements the ``OrgResolver`` Protocol defined in
``grant_checker.py``. It maps an agent actor_id to its parent org actor_id
by checking active, valid endorsements across all local orgs for the tenant.

The resolver is fail-safe: any exception during lookup returns ``None`` (treat
as no affiliation), which causes the GrantChecker to fall through to direct
actor-level grant checks.

CI AST lint: MUST NOT import anthropic.
"""
from __future__ import annotations

from pathlib import Path

try:
    from .org_store import OrgStore, list_org_handles
    from .org_actor import is_endorsement_valid
except ImportError:
    import sys

    _here = Path(__file__).resolve().parent
    if str(_here) not in sys.path:
        sys.path.insert(0, str(_here))
    from org_store import OrgStore, list_org_handles
    from org_actor import is_endorsement_valid


class CorvinOrgResolver:
    """L42 implementation of the L41 ``OrgResolver`` Protocol.

    Scans all local org directories in the tenant's orgs root, looking for
    an active endorsement whose ``agent_actor_id`` matches the presenting actor.

    Parameters
    ----------
    tenant_id:
        The tenant whose orgs directory to scan.

    Usage
    -----
    Pass an instance to ``GrantChecker`` to enable org-level grant expansion::

        resolver = CorvinOrgResolver(tenant_id="_default")
        checker = GrantChecker(
            store=grant_store,
            local_actor_id=local_actor_id,
            local_public_key_hex=pub_hex,
            org_resolver=resolver,
        )
    """

    def __init__(self, tenant_id: str | None = "_default") -> None:
        self._tenant_id = tenant_id

    def resolve_to_org(self, agent_actor_id: str) -> str | None:
        """Return the org actor_id if agent_actor_id has a valid org endorsement.

        Checks all local orgs' endorsement dirs. Returns the first match.
        Returns ``None`` on no match or any lookup error (fail-safe).
        """
        try:
            for handle in list_org_handles(self._tenant_id):
                result = self._check_org(handle, agent_actor_id)
                if result is not None:
                    return result
        except Exception:
            pass
        return None

    def _check_org(self, org_handle: str, agent_actor_id: str) -> str | None:
        """Check a single org for a valid endorsement of the agent."""
        try:
            store = OrgStore(org_handle, self._tenant_id)
            endorsement = store.find_endorsement_for_agent(agent_actor_id)
            if endorsement is None:
                return None

            # Verify endorsement signature and validity
            pub_hex = store.get_public_key_hex()
            if not is_endorsement_valid(endorsement, pub_hex):
                return None

            actor_doc = store.get_actor()
            return actor_doc.get("id")
        except Exception:
            return None
