"""One audit sink for the deployment / marketplace / discovery modules.

Every record goes through ``forge.security_events.write_event`` onto
``forge.paths.tenant_audit_chain(tenant_id)`` — THE per-tenant chain the
ADR-0232 boot tripwire verifies. No hand-composed path, no second chain format,
no in-memory "audit trail" standing in for the chain.

Fail-closed: :func:`emit` raises :class:`AuditWriteFailed` when the writer is
unavailable or the write does not commit, so a caller that is "audit-first" can
refuse the action it was about to take.

Field allowlists are registered at runtime through
``security_events.register_event_allowlist`` (the same mechanism
``core/learning/event_persistence.py`` uses). A module declares its events once
with :func:`register_events`; :func:`emit` refuses an event type that was never
declared, so a typo cannot write a record whose fields the floor then drops.
Declared field names must be content-free (ids, enums, counts, hash prefixes) —
the writer refuses a denylisted name at registration.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any, Iterable, Mapping

__all__ = ["AuditWriteFailed", "register_events", "emit", "registered_events"]

# Always admitted next to a module's own fields.
_BASE_FIELDS = frozenset({"tenant_id", "lom", "component"})

_lock = threading.Lock()
_declared: dict[str, frozenset[str]] = {}
_registered_for: dict[int, set[str]] = {}


class AuditWriteFailed(RuntimeError):
    """The record did not reach the tenant audit chain (fail-closed)."""


def _forge():
    """Return ``(security_events, paths)`` of the ONE forge writer.

    Imported as the top-level ``forge`` package — the same module object the
    core writer (``corvin_operator/bridges/shared/audit.py``) uses, so the
    runtime allowlist registration lands in the writer that actually filters.
    """
    try:
        from forge import paths as fp  # type: ignore[import-not-found]
        from forge import security_events as se  # type: ignore[import-not-found]
    except ImportError:
        forge_root = Path(__file__).resolve().parents[2] / "corvin_operator" / "forge"
        if (forge_root / "forge").is_dir() and str(forge_root) not in sys.path:
            sys.path.insert(0, str(forge_root))
        # With ``corvin_operator`` on sys.path, ``import forge`` first resolves
        # the outer ``corvin_operator/forge/`` directory as an empty namespace
        # package and caches it; drop that stale entry so the retry finds the
        # real package.
        cached = sys.modules.get("forge")
        if cached is not None and getattr(cached, "__file__", None) is None:
            del sys.modules["forge"]
        try:
            from forge import paths as fp  # type: ignore[import-not-found]
            from forge import security_events as se  # type: ignore[import-not-found]
        except ImportError as exc:
            raise AuditWriteFailed("forge audit writer unavailable") from exc
    return se, fp


def register_events(events: Mapping[str, Iterable[str]]) -> None:
    """Declare event types and their content-free detail fields (idempotent)."""
    with _lock:
        for name, fields in events.items():
            _declared[str(name)] = frozenset(fields) | _BASE_FIELDS | _declared.get(str(name), frozenset())


def registered_events() -> dict[str, frozenset[str]]:
    """Snapshot of every declared event type → allowed fields (for reports/tests)."""
    with _lock:
        return dict(_declared)


def _ensure_registered(se, event_type: str) -> None:
    with _lock:
        done = _registered_for.setdefault(id(se), set())
        if event_type in done:
            return
        fields = _declared[event_type]
        se.register_event_allowlist(event_type, fields)
        done.add(event_type)


def emit(
    event_type: str,
    details: Mapping[str, Any] | None = None,
    *,
    tenant_id: str = "_default",
    severity: str = "INFO",
) -> dict:
    """Append one hash-chained record to ``tenant_audit_chain(tenant_id)``.

    Returns the written record. Raises :class:`AuditWriteFailed` on any failure
    (undeclared event, writer unavailable, tenant mismatch, I/O error).
    """
    if event_type not in _declared:
        raise AuditWriteFailed(f"audit event {event_type!r} was never declared via register_events()")
    se, fp = _forge()
    try:
        _ensure_registered(se, event_type)
        chain = fp.tenant_audit_chain(tenant_id)
        chain.parent.mkdir(parents=True, exist_ok=True)
        return se.write_event(
            chain,
            event_type,
            severity=str(severity).upper(),
            details={**dict(details or {}), "tenant_id": tenant_id},
            hash_chain=True,
        )
    except AuditWriteFailed:
        raise
    except Exception as exc:  # noqa: BLE001 — every failure is a non-commit
        raise AuditWriteFailed(f"audit write for {event_type!r} did not commit: {type(exc).__name__}") from exc
