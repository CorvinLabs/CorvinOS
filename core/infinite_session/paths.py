"""Shared identifier validation, tenant roots and core-audit access (ADR-0540).

Every module of the infinite-session engine builds its on-disk paths through
this file so that the three invariants below hold in ONE place:

1. Identifiers (task / phase / snapshot / bridge / transaction / alert ids)
   match ``^[A-Za-z0-9_.-]{1,128}$`` and never contain ``..`` — validated
   before they are joined into a path (fail-closed, GDPR Art. 32).
2. The tenant root is ``<corvin_home>/tenants/<tenant_id>/infinite_session/``
   with ``tenant_id`` validated by :func:`core.tenants.validate_tenant_id` and
   ``corvin_home`` resolved via :func:`core.paths.tenant.corvin_home` — never
   ``Path.home() / ".corvin"``.
3. Every path is re-checked with ``resolve().is_relative_to(root)`` before
   the file is opened (defence in depth against a validator regression).
4. Audit records go to the CORE hash-chained writer (audit-first, fail-closed)
   and never carry snapshot content — only ids, hashes and sizes.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable, Optional

from core.paths.tenant import corvin_home as _corvin_home
from core.tenants import validate_tenant_id

ID_PATTERN = r"^[A-Za-z0-9_.-]{1,128}$"
_ID_RE = re.compile(ID_PATTERN)

AuditFn = Callable[..., str]
"""``audit(event_type, *, tenant_id, details) -> audit_ref`` — raises on failure."""


class InvalidIdentifier(ValueError):
    """An identifier failed the strict allowlist (fail-closed)."""


class PathEscape(ValueError):
    """A resolved path left its tenant root (fail-closed)."""


def validate_id(value: Any, name: str = "id") -> str:
    """Return ``value`` if it is a safe identifier, else raise InvalidIdentifier."""
    if not isinstance(value, str) or not _ID_RE.match(value) or ".." in value:
        raise InvalidIdentifier(
            f"{name} must match {ID_PATTERN} without '..' (fail-closed)"
        )
    return value


def is_valid_id(value: Any) -> bool:
    try:
        validate_id(value)
        return True
    except InvalidIdentifier:
        return False


def tenant_root(tenant_id: str, corvin_home: Optional[str | Path] = None) -> Path:
    """``<corvin_home>/tenants/<tenant_id>/infinite_session`` (tenant validated)."""
    validate_tenant_id(tenant_id)
    base = Path(corvin_home).expanduser() if corvin_home else _corvin_home()
    return base / "tenants" / tenant_id / "infinite_session"


def safe_child(root: Path, *parts: str) -> Path:
    """Join validated ``parts`` under ``root`` and prove the result stays inside.

    Each part is validated with :func:`validate_id`; the joined path is then
    resolved (following symlinks) and must be relative to the resolved root.
    """
    for part in parts:
        validate_id(part)
    candidate = root.joinpath(*parts)
    root_resolved = root.resolve()
    if not candidate.resolve().is_relative_to(root_resolved):
        raise PathEscape(f"path escapes tenant root (fail-closed)")
    return candidate


# Positive allowlists for the engine's core-chain events (ADR-0129 M2 / F-A4:
# the floor is default-deny; every key must be registered). Metadata only —
# ids, hashes, counts — never content.
_SNAPSHOT_EVENT_FIELDS = frozenset({
    "task_id", "phase_id", "snapshot_id", "snapshot_type", "content_hash",
    "prev_snapshot_hash", "seq", "size_bytes", "audit_ref",
})
AUDIT_EVENT_ALLOWLISTS: dict[str, frozenset[str]] = {
    "infinite_session.snapshot_created": _SNAPSHOT_EVENT_FIELDS,
    "infinite_session.snapshot_archived": _SNAPSHOT_EVENT_FIELDS,
}
_allowlists_registered = False


def _register_allowlists() -> None:
    """Fold the engine's allowlists into the core writer's registry (idempotent)."""
    global _allowlists_registered
    if _allowlists_registered:
        return
    import sys  # noqa: PLC0415

    forge_dir = Path(__file__).resolve().parents[2] / "operator" / "forge"
    if forge_dir.is_dir() and str(forge_dir) not in sys.path:
        sys.path.insert(0, str(forge_dir))
    from forge import security_events  # noqa: PLC0415  # type: ignore[import-not-found]

    for event_type, fields in AUDIT_EVENT_ALLOWLISTS.items():
        security_events.register_event_allowlist(event_type, fields)
    _allowlists_registered = True


def core_audit(event_type: str, *, tenant_id: str, details: dict[str, Any]) -> str:
    """Write one CONTENT-FREE record to the core hash chain, return its audit_ref.

    Delegates to :func:`core.learning.event_persistence.core_audit_event` — the
    shared, verified-commit wrapper over the core writer
    (``operator/bridges/shared/audit.py``). Raises ``RuntimeError`` when the
    writer is unavailable or the record did not commit (ADR-0232/0233) — which
    includes the metadata floor dropping a field, so every key written here
    is registered in :data:`AUDIT_EVENT_ALLOWLISTS` first.
    """
    from core.learning.event_persistence import core_audit_event  # noqa: PLC0415

    validate_tenant_id(tenant_id)
    if event_type not in AUDIT_EVENT_ALLOWLISTS:
        raise RuntimeError(f"unregistered infinite-session audit event {event_type!r} (fail-closed)")
    unknown = set(details) - AUDIT_EVENT_ALLOWLISTS[event_type]
    if unknown:
        raise RuntimeError(f"{event_type}: fields not on the allowlist {sorted(unknown)} (fail-closed)")
    _register_allowlists()
    return core_audit_event(event_type, tenant_id=tenant_id, details=dict(details))


def content_free(details: dict[str, Any]) -> dict[str, Any]:
    """Drop any state payload keys so an audit record can never carry content."""
    forbidden = {"state_dict", "old_state", "new_state", "state", "reason", "payload"}
    return {k: v for k, v in details.items() if k not in forbidden}
