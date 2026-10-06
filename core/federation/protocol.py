"""Closed vocabularies of the A2A federation exchange (ADR-2232).

Shared by the receiver (``a2a_federation.py``) and the origin
(``peer_catalog.py``, ``delegation.py``) so a value that came off the wire is
only ever recorded, audited or shown if it is one of these tokens — never a
peer-chosen string.
"""
from __future__ import annotations

import re

FEDERATION_VERSION = 1
MAX_HOPS = 3

# Signed rejection reasons a receiver may send for a federation request.
PUBLIC_REASONS = frozenset({
    "federation_unsupported_version",
    "federation_bad_request",
    "federation_disabled",
    "federation_no_worker",
    "federation_unknown_agent",
    "federation_agent_not_federable",
    "federation_capability_mismatch",
    "federation_no_agent",
    "federation_agent_busy",
    "federation_duplicate_task",
    "federation_hop_limit",
    "federation_audit_unavailable",
})

# The receiver's existing closed A2A rejection reasons
# (remote_trigger_receiver.public_rejection_reason + "busy").
A2A_PUBLIC_REASONS = frozenset({
    "busy", "clock_skew", "disabled", "identity_required", "identity_revoked",
    "integrity_required", "peer_limit", "purpose_not_allowed", "rate_limited", "replay",
})

# ResponseEnvelope statuses the origin records verbatim; anything else is "error".
RESPONSE_STATUSES = frozenset({"ok", "filtered", "rejected", "timeout", "error"})


# Same shape the sender's TOFU pin accepts. An unpinned endpoint does not
# check the peer's instance_id at all, so the origin checks it here.
_INSTANCE_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{8,64}$")


def valid_instance_id(value: object) -> bool:
    return isinstance(value, str) and bool(_INSTANCE_ID_RE.match(value))


def closed_status(value: object) -> str:
    return value if isinstance(value, str) and value in RESPONSE_STATUSES else "error"


def closed_reason(value: object, *, fallback: str = "peer_refused") -> str:
    known = PUBLIC_REASONS | A2A_PUBLIC_REASONS
    return value if isinstance(value, str) and value in known else fallback
