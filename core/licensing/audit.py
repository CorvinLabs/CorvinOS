"""Audit emission for the A2A licensing gate.

Every licensing event is a hash-chained record in THE tenant audit chain
(``core.paths.tenant_audit_chain(tenant_id)``) — the file the boot tripwire
verifies and every compliance report reads. Until 2026-11-19 the verifier and
the authority appended to two private, unchained side files
(``licensing/verification_audit.jsonl`` and ``licensing/issuance_ledger.jsonl``),
which were not part of the audit trail at all. Those files are left untouched
on disk (append-only history is never rewritten); nothing writes them anymore.

Payloads are metadata only: ids, codes, tiers and counts. The free-text
revocation reason is recorded as a length, never as text.

Registration note: the positive field allowlists are folded into the core
writer below. The severities are passed explicitly per call; an
``EVENT_SEVERITY`` entry for each name in ``security_events.py`` is still to
be added by that file's owner (the names are listed in ``EVENTS``).

License: Apache-2.0
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_FORGE_DIR = Path(__file__).resolve().parents[2] / "corvin_operator" / "forge"

#: The writer's denylist refuses any key containing "credential" (it guards
#: secrets), so the credential's ID travels as ``cred_ref`` - an identifier,
#: never key material.
_COMMON = frozenset({"tenant_id", "cred_ref", "member_id", "license_tier"})

_RENAMES = {"credential_id": "cred_ref", "previous_credential_id": "previous_cred_ref"}

#: event name -> (severity, allowed detail fields)
EVENTS: Dict[str, tuple] = {
    "a2a.license_task_verified": (
        "INFO", _COMMON | {"task_id", "operation", "latency_ms"}),
    "a2a.license_task_denied": (
        "WARNING", _COMMON | {"task_id", "operation", "latency_ms", "reason_code"}),
    "a2a.license_credential_issued": (
        "INFO", _COMMON | {"issue_id", "status", "key_origin", "validity_days"}),
    "a2a.license_credential_renewed": (
        "INFO", _COMMON | {"previous_cred_ref"}),
    "a2a.license_credential_downgraded": (
        "WARNING", _COMMON | {"old_tier", "new_tier", "previous_cred_ref"}),
    "a2a.license_credential_revoked": (
        "WARNING", _COMMON | {"reason_len", "source"}),
    "a2a.license_delegation_recorded": (
        "INFO", _COMMON | {"task_id", "operation", "status"}),
}

VERIFICATION_EVENTS = ("a2a.license_task_verified", "a2a.license_task_denied")


class LicensingAuditError(RuntimeError):
    """An audit-first licensing record did not commit to the chain."""


def _security_events():
    try:
        from forge import security_events  # type: ignore[import-not-found]  # noqa: PLC0415
        return security_events
    except ImportError:
        pass
    if _FORGE_DIR.is_dir() and str(_FORGE_DIR) not in sys.path:
        sys.path.insert(0, str(_FORGE_DIR))
    from forge import security_events  # type: ignore[import-not-found]  # noqa: PLC0415
    return security_events


_registered = False


def _ensure_registered(se) -> None:
    global _registered
    if _registered:
        return
    for name, (_severity, fields) in EVENTS.items():
        se.register_event_allowlist(name, set(fields))
    _registered = True


def chain_path(tenant_id: str) -> Path:
    """The tenant's audit chain — never composed by hand."""
    from core.paths.tenant import tenant_audit_chain  # noqa: PLC0415
    return tenant_audit_chain(tenant_id)


def emit(event_type: str, *, tenant_id: str, details: Dict[str, Any], strict: bool = False) -> bool:
    """Append one hash-chained licensing record for *tenant_id*.

    ``strict=True`` is for audit-FIRST callers (issue / downgrade / revoke): a
    record that does not commit raises :class:`LicensingAuditError` so the
    guarded action is refused instead of happening unrecorded. Verification
    emits best-effort: a denial stays a denial either way, and a PASS is only
    reported after its record committed (see ``A2ADelegationVerifier``).
    """
    severity, allowed = EVENTS[event_type]
    renamed = {_RENAMES.get(k, k): v for k, v in details.items()}
    payload = {k: v for k, v in renamed.items() if k in allowed and v is not None}
    payload["tenant_id"] = tenant_id
    try:
        se = _security_events()
        _ensure_registered(se)
        path = chain_path(tenant_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        se.write_event(path, event_type, severity=severity, details=payload)
        return True
    except Exception as exc:  # noqa: BLE001 - chain unreachable / refused
        logger.error("licensing audit write failed for %s: %s", event_type, type(exc).__name__)
        if strict:
            raise LicensingAuditError(
                f"{event_type}: audit chain write did not commit ({type(exc).__name__})"
            ) from exc
        return False


def _iso(ts: Any) -> str:
    try:
        from datetime import datetime, timezone  # noqa: PLC0415
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def read_verification_events(
    tenant_id: str,
    *,
    limit: int = 100,
    member_id: Optional[str] = None,
    max_scan_bytes: int = 4 * 1024 * 1024,
) -> List[Dict[str, Any]]:
    """Most recent verification records of *tenant_id*, newest first.

    Bounded: only the last ``max_scan_bytes`` of the chain are scanned (the
    chain can be hundreds of MB; no request may walk all of it).
    """
    import json  # noqa: PLC0415

    path = chain_path(tenant_id)
    events: List[Dict[str, Any]] = []
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            if size > max_scan_bytes:
                f.seek(size - max_scan_bytes)
                f.readline()  # drop the partial first line
            raw_lines = f.read().splitlines()
    except FileNotFoundError:
        return events
    except OSError as exc:
        logger.error("licensing audit read failed: %s", type(exc).__name__)
        return events
    for raw in reversed(raw_lines):
        if len(events) >= limit:
            break
        if b"a2a.license_task_" not in raw:
            continue
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        etype = rec.get("event_type")
        if etype not in VERIFICATION_EVENTS:
            continue
        det = rec.get("details") or {}
        if det.get("tenant_id") not in (None, tenant_id):
            continue
        if member_id and det.get("member_id") != member_id:
            continue
        events.append({
            "timestamp": _iso(rec.get("ts")),
            "event_type": etype,
            "task_id": det.get("task_id"),
            "member_id": det.get("member_id"),
            "credential_id": det.get("cred_ref"),
            "is_valid": etype == "a2a.license_task_verified",
            "license_tier": det.get("license_tier"),
            "reason_code": det.get("reason_code"),
        })
    return events
