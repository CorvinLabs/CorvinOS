"""Federated Agent Coordination — local-agent registry (CONCEPT-0097 Phase 1).

Scope note: this module covers the LOCAL half only — which worker-engine
deployments exist on THIS installation, so they can be advertised and
selected. It intentionally does not touch the A2A wire protocol
(``a2a_friendship.py``/``a2a_connectivity.py``): that subsystem carries its
own multi-round adversarial-review history and NBAC/attestation invariants,
and extending its payload for cross-peer agent discovery needs a dedicated
review pass, not a one-shot addition. See CONCEPT-0097 and the Phase 1 ADR
for the full federation design and the explicit scope cut.
"""
