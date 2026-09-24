"""A2A audit event emitters (Layer 38 — Nonce Block & Offline Pairing).

Emits three critical Layer 38 security events into the unified hash chain:
- a2a.genesis_block_created: NBAC (Nonce-Based Auth Chain) initialization
- a2a.offline_pair_initiated: offline pairing protocol start
- a2a.nonce_collision_detected: nonce reuse detection

All events carry metadata only — nonce prefixes (first 8 hex chars, never full),
peer identifiers, and counters. NO payload content, NO private keys, NO full nonce values.

Integration: wire these emitters into the A2A call sites in:
- genesis.py (nonce block creation)
- pairing.py (offline pairing protocol)
- nonce_validation.py (collision detection)
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)


# Allow-list (mirrored in security_events.py::_EVENT_ALLOWLIST)
# CRITICAL (GDPR Art. 5, 6, 32): tenant_id is REQUIRED on every event.
_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "a2a.genesis_block_created": frozenset({
        "tenant_id", "instance_id", "network_id", "nonce_prefix", "epoch",
    }),
    "a2a.offline_pair_initiated": frozenset({
        "tenant_id", "task_id", "peer_id", "pairing_id", "ttl_s",
    }),
    "a2a.nonce_collision_detected": frozenset({
        "tenant_id", "nonce_prefix", "epoch", "collision_count",
    }),
}

# REQUIRED fields per event type (fail-closed if missing)
_REQUIRED_FIELDS: dict[str, frozenset[str]] = {
    "a2a.genesis_block_created": frozenset({"tenant_id"}),
    "a2a.offline_pair_initiated": frozenset({"tenant_id"}),
    "a2a.nonce_collision_detected": frozenset({"tenant_id"}),
}


class AuditFieldNotAllowed(ValueError):
    """Raised when an event details dict carries a non-allow-listed key."""


def _check_allow_list(event: str, details: dict[str, Any]) -> None:
    """Validate event details against the allow-list (fail-closed).

    Enforces:
    1. All required fields must be present (tenant_id is always required for GDPR compliance)
    2. No extra fields (unknown fields are forbidden)
    """
    allowed = _ALLOWED_FIELDS.get(event)
    if allowed is None:
        raise AuditFieldNotAllowed(
            f"unknown a2a event type: {event!r}; "
            f"register it in a2a_audit.py::_ALLOWED_FIELDS first",
        )

    # Check required fields (GDPR compliance: tenant_id always required)
    required = _REQUIRED_FIELDS.get(event, frozenset())
    missing = required - set(details.keys())
    if missing:
        raise AuditFieldNotAllowed(
            f"{event}: missing required fields {sorted(missing)}; "
            f"all events must include tenant_id for GDPR compliance",
        )

    # Check for extra fields
    extras = set(details.keys()) - allowed
    if extras:
        raise AuditFieldNotAllowed(
            f"{event}: forbidden detail keys {sorted(extras)}; "
            f"allowed: {sorted(allowed)}",
        )


def emit(
    event: str,
    *,
    path: Path,
    tenant_id: str | None = None,
    severity: str | None = None,
    write_event_fn: Callable[..., Any] | None = None,
    **details: Any,
) -> None:
    """Emit one a2a.* event into the unified hash chain.

    Args:
        event: event type (a2a.*)
        path: audit chain file path
        tenant_id: tenant scope
        severity: override severity (defaults from EVENT_SEVERITY)
        write_event_fn: injected write function (testing); production uses forge.security_events
        **details: event-specific metadata (validated against allow-list)
    """
    # Compose details dict
    payload: dict[str, Any] = dict(details)
    if tenant_id is not None:
        payload["tenant_id"] = tenant_id

    _check_allow_list(event, payload)

    if write_event_fn is None:
        from forge.security_events import write_event as _we  # type: ignore[import]
        write_event_fn = _we

    try:
        write_event_fn(path, event, details=payload, severity=severity)
    except Exception:  # noqa: BLE001
        log.exception("a2a audit emit failed for %s", event)


def emit_genesis_block_created(
    path: Path,
    instance_id: str,
    network_id: str,
    nonce_prefix: str,
    epoch: int,
    tenant_id: str | None = None,
) -> None:
    """Emit NBAC genesis block creation event (Layer 38 nonce block).

    Args:
        path: audit chain file path
        instance_id: originating instance identifier
        network_id: A2A network identifier
        nonce_prefix: first 8 hex chars of genesis nonce (never full value)
        epoch: nonce epoch number
        tenant_id: tenant scope
    """
    emit(
        "a2a.genesis_block_created",
        path=path,
        tenant_id=tenant_id,
        instance_id=instance_id,
        network_id=network_id,
        nonce_prefix=nonce_prefix[:8],  # Enforce prefix limit
        epoch=int(epoch),
    )


def emit_offline_pair_initiated(
    path: Path,
    task_id: str,
    peer_id: str,
    pairing_id: str,
    ttl_s: int,
    tenant_id: str | None = None,
) -> None:
    """Emit offline pairing protocol initiation event (Layer 38 pairing).

    Args:
        path: audit chain file path
        task_id: A2A task identifier
        peer_id: peer instance identifier
        pairing_id: unique pairing session ID
        ttl_s: pairing invitation TTL in seconds
        tenant_id: tenant scope
    """
    emit(
        "a2a.offline_pair_initiated",
        path=path,
        tenant_id=tenant_id,
        task_id=task_id,
        peer_id=peer_id,
        pairing_id=pairing_id,
        ttl_s=int(ttl_s),
    )


def emit_nonce_collision_detected(
    path: Path,
    nonce_prefix: str,
    epoch: int,
    collision_count: int,
    tenant_id: str | None = None,
) -> None:
    """Emit nonce collision detection event (Layer 38 security).

    Fires when nonce validation detects reuse (collision) in the same epoch.
    This is a security event and should be escalated for investigation.

    Args:
        path: audit chain file path
        nonce_prefix: first 8 hex chars of colliding nonce
        epoch: nonce epoch where collision detected
        collision_count: total collisions detected in this epoch
        tenant_id: tenant scope
    """
    emit(
        "a2a.nonce_collision_detected",
        path=path,
        tenant_id=tenant_id,
        nonce_prefix=nonce_prefix[:8],  # Enforce prefix limit
        epoch=int(epoch),
        collision_count=int(collision_count),
    )


__all__ = [
    "AuditFieldNotAllowed",
    "emit",
    "emit_genesis_block_created",
    "emit_offline_pair_initiated",
    "emit_nonce_collision_detected",
]
