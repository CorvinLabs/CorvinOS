"""Audit-chain emitter (ADR-0013 Phase 13.6).

Routes the five canonical compute.* events into the unified hash chain
via :func:`forge.security_events.write_event`. Mirror of the L23
voice-transcribe rule: **parameter values never enter the chain.**

The audit-field allow-list per event is the structural defence — any
extra key raises :class:`AuditFieldNotAllowed`. A future event type
must register both its name (in ``forge.security_events.EVENT_SEVERITY``)
and its allow-list (here).

Tier-3 sensitivity (per-field ``x-sensitive: true``) is enforced by
:func:`redact_sensitive_fields` at the iter-log write boundary; the
audit chain only ever sees fingerprints.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any, Callable, Mapping

log = logging.getLogger(__name__)


class AuditFieldNotAllowed(ValueError):
    """Raised when an event details dict carries a non-allow-listed key."""


# Per-event allow-lists. Every compute.* event must appear here.
# ``run_id`` + ``tenant_id`` + ``ts`` are always implicit.
_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "compute.run_started": frozenset({
        "run_id", "tenant_id", "tool_name", "strategy", "budget",
    }),
    "compute.iteration_completed": frozenset({
        "run_id", "tenant_id", "iter", "loss", "wall_ms", "strategy",
        "param_fingerprint", "cache_hit",
    }),
    "compute.run_terminal": frozenset({
        "run_id", "tenant_id", "state", "total_iterations", "total_wall_s",
        "best_loss", "convergence_reason",
    }),
    "compute.run_failed": frozenset({
        "run_id", "tenant_id", "iter", "error_class", "error_message",
    }),
    "compute.worker_unreachable": frozenset({
        "tenant_id", "attempted_socket",
    }),
    "compute.run_recovering": frozenset({
        "run_id", "tenant_id", "resume_from_iter", "history_size",
    }),
    "compute.run_aborted": frozenset({
        "run_id", "tenant_id", "engine_id", "iterations_done",
    }),
    # ADR-0099 — Anthropic Batch API backend events.
    # batch_id_prefix: first 16 chars only (never full batch_id).
    "compute.batch_submitted": frozenset({
        "run_id", "tenant_id", "batch_id_prefix", "candidate_count",
    }),
    "compute.batch_completed": frozenset({
        "run_id", "tenant_id", "batch_id_prefix", "candidate_count", "duration_ms",
    }),
    "compute.batch_partial": frozenset({
        "run_id", "tenant_id", "batch_id_prefix",
        "candidate_count", "failed_candidate_count",
    }),
    "compute.batch_cancelled": frozenset({
        "run_id", "tenant_id", "batch_id_prefix", "reason",
    }),
    "compute.batch_gate_blocked": frozenset({
        "run_id", "tenant_id", "reason",
    }),
    "compute.batch_api_error": frozenset({
        "run_id", "tenant_id", "batch_id_prefix", "error_class",
    }),
    "compute.batch_fallback": frozenset({
        "run_id", "tenant_id", "reason", "candidate_count",
    }),
}


def _check_allow_list(event: str, details: Mapping[str, Any]) -> None:
    allowed = _ALLOWED_FIELDS.get(event)
    if allowed is None:
        # Fail-closed: unknown event types must be registered in _ALLOWED_FIELDS
        # before calling emit(). This matches the EVENT_SEVERITY registration
        # pattern in forge/security_events.py.
        raise AuditFieldNotAllowed(
            f"unknown compute event type: {event!r}; "
            f"register it in compute/audit.py::_ALLOWED_FIELDS first",
        )
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
    run_id: str | None = None,
    tenant_id: str | None = None,
    severity: str | None = None,
    write_event_fn: Callable[..., Any] | None = None,
    **details: Any,
) -> None:
    """Emit one compute.* event into the unified hash chain.

    ``write_event_fn`` is injected for testability — production code
    leaves it ``None`` and falls back to
    :func:`forge.security_events.write_event`.
    """
    # Compose the full details dict before allow-list validation.
    payload: dict[str, Any] = dict(details)
    if run_id is not None:
        payload["run_id"] = run_id
    if tenant_id is not None:
        payload["tenant_id"] = tenant_id

    _check_allow_list(event, payload)

    if write_event_fn is None:
        from forge.security_events import write_event as _we  # type: ignore[import]
        write_event_fn = _we
    try:
        write_event_fn(path, event, details=payload, severity=severity)
    except Exception:  # noqa: BLE001
        log.exception("audit emit failed for %s", event)


def redact_sensitive_fields(
    params: Mapping[str, Any], sensitive_fields: list[str],
) -> dict[str, Any]:
    """Replace each ``sensitive_fields`` value with ``<hash:<12-hex>>``.

    Non-sensitive fields pass through unchanged. The clear-text value
    stays out of the on-disk iteration log; only the value's 12-char
    SHA-256 prefix lands there.
    """
    if not sensitive_fields:
        return dict(params)
    redacted: dict[str, Any] = {}
    for k, v in params.items():
        if k in sensitive_fields:
            digest = hashlib.sha256(repr(v).encode("utf-8")).hexdigest()[:12]
            redacted[k] = f"<hash:{digest}>"
        else:
            redacted[k] = v
    return redacted


__all__ = [
    "AuditFieldNotAllowed",
    "emit",
    "redact_sensitive_fields",
]
