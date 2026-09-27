"""One audit writer for the core/skills modules that used to "audit" into a log line.

``chain_write`` appends a record to THE hash-chained audit chain the core audit
helper resolves (``audit.audit_path()`` → ``tenant_audit_chain()``, honouring
the ``VOICE_AUDIT_PATH`` / ``FORGE_ROOT`` redirects exactly like
``audit.audit_event``) through ``forge.security_events.write_event`` — the same
writer every other chained event uses. Unlike ``audit.audit_event`` it does NOT
swallow failures: it raises, so an audit-FIRST caller can refuse the audited
operation when its record cannot be committed.

Each event's field set is registered as its positive allowlist
(``register_event_allowlist``) before the write; without that the writer's
default-deny floor drops every field and the record says nothing.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable


class AuditWriteUnavailable(RuntimeError):
    """The core audit writer is not importable in this process."""


def _writer():
    try:
        from audit import audit_path  # type: ignore[import-not-found]  # noqa: PLC0415
    except ImportError:
        try:
            from corvin_operator.bridges.shared.audit import audit_path  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover — stripped install
            raise AuditWriteUnavailable("core audit helper not importable") from exc
    try:
        # audit.py puts corvin_operator/forge on sys.path when it imports.
        from forge.security_events import (  # type: ignore[import-not-found]  # noqa: PLC0415
            register_event_allowlist,
            write_event,
        )
    except ImportError as exc:
        raise AuditWriteUnavailable("forge.security_events not importable") from exc
    return audit_path, register_event_allowlist, write_event


def chain_write(
    event_type: str,
    details: Dict[str, Any],
    *,
    fields: Iterable[str],
    tenant_id: str = "_default",
) -> Dict[str, Any]:
    """Append one chained record; raise on ANY failure (never silent)."""
    audit_path, register_event_allowlist, write_event = _writer()
    register_event_allowlist(event_type, frozenset(fields) | {"tenant_id"})
    body = {k: v for k, v in details.items() if k != "tenant_id"}
    body["tenant_id"] = tenant_id
    return write_event(audit_path(), event_type, details=body, hash_chain=True)
