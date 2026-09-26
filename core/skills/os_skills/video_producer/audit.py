"""Blender render-path audit event emitters (video_producer).

Emits five `blender.*` events into the unified per-tenant hash chain
(``core.paths.tenant_audit_chain``), covering one full BlenderHeadlessOrchestrator
render: start, script generation, completion, validation failure, and error.

All events carry metadata only -- blend/output file paths, resolution, codec,
frame counts, durations, exception class names. NO subprocess stdout/stderr,
NO exception messages (those may embed file contents or other unfiltered text).

Mirrors the ADR-2043 plugin-audit pattern (core/plugins/corvin_plugins/audit.py):
same allow-list-first, fail-closed emit() shape.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)


# Allow-list (mirrored in corvin_operator/forge/forge/security_events.py::_EVENT_ALLOWLIST)
_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "blender.render_start": frozenset({
        "blend_file", "input_video", "resolution", "fps", "frame_count",
        "output_codec", "bitrate_kbps", "timeout_sec", "tenant_id",
    }),
    "blender.bpy_script_generated": frozenset({
        "script_lines", "tenant_id",
    }),
    "blender.render_complete": frozenset({
        "output_file", "frame_count", "duration_sec", "tenant_id",
    }),
    "blender.render_validation_failed": frozenset({
        "output_file", "tenant_id",
    }),
    "blender.render_error": frozenset({
        "error_class", "tenant_id",
    }),
}


class AuditFieldNotAllowed(ValueError):
    """Raised when an event details dict carries a non-allow-listed key."""


def _check_allow_list(event: str, details: dict[str, Any]) -> None:
    """Validate event details against the allow-list (fail-closed)."""
    allowed = _ALLOWED_FIELDS.get(event)
    if allowed is None:
        raise AuditFieldNotAllowed(
            f"unknown blender event type: {event!r}; "
            f"register it in core/skills/os_skills/video_producer/audit.py::_ALLOWED_FIELDS first",
        )
    extras = set(details.keys()) - allowed
    if extras:
        raise AuditFieldNotAllowed(
            f"{event}: forbidden detail keys {sorted(extras)}; allowed: {sorted(allowed)}",
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
    """Emit one blender.* event into the unified hash chain.

    Args:
        event: event type (blender.*)
        path: audit chain file path (``core.paths.tenant_audit_chain(tenant_id)``)
        tenant_id: tenant scope
        severity: override severity (defaults from EVENT_SEVERITY)
        write_event_fn: injected write function (testing); production uses forge.security_events
        **details: event-specific metadata (validated against allow-list)
    """
    payload: dict[str, Any] = dict(details)
    if tenant_id is not None:
        payload["tenant_id"] = tenant_id

    _check_allow_list(event, payload)

    if write_event_fn is None:
        from forge.security_events import write_event as _we  # type: ignore[import]
        write_event_fn = _we

    try:
        write_event_fn(path, event, details=payload, severity=severity)
    except Exception as e:  # noqa: BLE001
        # Audit-first (ADR-0232): a render whose audit trail can't be written
        # must not be reported as a silently-successful, unaudited render.
        log.exception("blender audit emit FAILED for %s: %s", event, type(e).__name__)
        raise RuntimeError(
            f"Blender audit event {event!r} failed to write to chain (audit-first violation): {type(e).__name__}"
        ) from e


__all__ = ["AuditFieldNotAllowed", "emit"]
