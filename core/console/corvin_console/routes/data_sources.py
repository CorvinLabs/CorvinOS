"""Data Sources console routes (ADR-0106 DSI v1).

Seven endpoints:
  GET  /v1/console/data-sources            → list DSI v1 connections
  POST /v1/console/data-sources            → register a new connection
  GET  /v1/console/data-sources/adapters   → list available adapters
  GET  /v1/console/data-sources/{name}     → single connection details + last ping
  POST /v1/console/data-sources/{name}/test  → connectivity test
  DELETE /v1/console/data-sources/{name}   → unregister
  GET  /v1/console/data-sources/{name}/audit → last N audit events for this connection

All routes:
  - require_session (GET) or require_csrf (mutations)
  - use rec.tenant_id from session (NEVER env var)
  - emit console.action_performed / action_failed audit events
  - delegate datasource audit events to the forge hash chain via _make_ds_audit_writer
"""
from __future__ import annotations

import json
import sys
from collections import deque
from pathlib import Path
from typing import Annotated, Any

# Scan at most this many tail lines when searching the audit chain (avoids
# loading the entire file for large chains; L37 rotates at 100 MB).
_AUDIT_SCAN_MAX_LINES = 50_000

try:
    from forge import paths as _forge_paths  # noqa: E402
    _FORGE_AVAILABLE = True
except ImportError:
    _FORGE_AVAILABLE = False

# License gate — same PYTHONPATH setup as other console routes (e.g. space.py)
_OPERATOR = Path(__file__).resolve().parents[4] / "operator"
if str(_OPERATOR) not in sys.path:
    sys.path.insert(0, str(_OPERATOR))

# Fail-closed FREE_TIER fallback for the limits this route reads. A bare
# ``{}.get`` would return None for every feature, and None is the "unlimited"
# sentinel — so an unimportable license package would FAIL OPEN (every adapter
# allowed). Hard-code the FREE_TIER cap inline so the gate stays fail-closed:
# free tier allows only local-file connections.
_DS_FREE_TIER_FALLBACK: dict = {"datasource_adapters_allowed": ["local_file"]}

try:
    from license.validator import get_limit as _lic_get_limit  # type: ignore[import]
    from license.limits import LicenseLimitError as _LicLimitError  # type: ignore[import]
except ImportError:
    try:
        from license.limits import FREE_TIER as _FREE_TIER, LicenseLimitError as _LicLimitError  # type: ignore[import]
        _lic_get_limit = _FREE_TIER.get  # type: ignore[assignment]
    except ImportError:
        class _LicLimitError(Exception): pass  # type: ignore[assignment,misc]
        # Innermost fallback: license package entirely absent. Resolve via the
        # hard-coded FREE_TIER caps (fail-closed), never to None=unlimited.
        _lic_get_limit = _DS_FREE_TIER_FALLBACK.get  # type: ignore[assignment]

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from fastapi.responses import Response
from pydantic import BaseModel

from .. import audit as console_audit
from .. import auth as session_auth
from ..deps import require_csrf, require_session

import logging
_log = logging.getLogger(__name__)

# Lazy import of compute package (optional dependency)
def _get_registry():
    """Return a DataSourceRegistry instance scoped to the current Corvin home."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "core/compute"))
        from corvin_compute.fabric.datasources.registry import DataSourceRegistry
        return DataSourceRegistry()
    except ImportError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="compute plugin unavailable",
        )


def _get_registry_optional():
    """Like _get_registry() but returns None when the compute plugin is absent.

    Read-only endpoints use this to degrade to an empty result on a BASE install
    (no [compute] extra → corvin_compute not importable) instead of returning
    503. Without this the dashboard's data-sources widget surfaced a 503 in the
    browser console on every fresh `pip install corvinos` — the console must open
    error-free regardless of which optional extras are installed.
    """
    try:
        return _get_registry()
    except HTTPException:
        return None


def _make_ds_audit_writer(tenant_id: str):
    """Build an AuditWriter that writes datasource events to the forge audit chain."""
    if not _FORGE_AVAILABLE:
        return None
    try:
        audit_path = _forge_paths.tenant_home(tenant_id) / "audit.jsonl"

        from forge.security_events import write_event
        def _writer(event_type: str, severity: str, details: dict) -> None:
            write_event(audit_path, event_type, severity=severity, details=details)
        return _writer
    except Exception:
        # Best-effort — never block operations for audit failures
        return None


router = APIRouter()


# ---------------------------------------------------------------------------
# GET /data-sources/adapters  (placed before /{name} to avoid route conflict)
# ---------------------------------------------------------------------------

@router.get("/data-sources/adapters")
def list_adapters(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> list[dict[str, Any]]:
    """Return DSI v1 class-level metadata for all available adapters."""
    reg = _get_registry_optional()
    if reg is None:
        return []  # compute extra not installed — feature simply unavailable
    tid = rec.tenant_id
    adapters = reg.discover_adapters(tid)
    results: list[dict[str, Any]] = []
    for am in adapters:
        info = reg.describe_adapter(am.name, tid)
        if info is not None:
            results.append({**info, "tier": am.tier})
    return results


# ---------------------------------------------------------------------------
# GET /data-sources
# ---------------------------------------------------------------------------

@router.get("/data-sources")
def list_connections(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> list[dict[str, Any]]:
    """List all DSI v1 connections for the tenant."""
    reg = _get_registry_optional()
    if reg is None:
        return []  # compute extra not installed — no connections to list
    return reg.list_connections_v1(rec.tenant_id)


# ---------------------------------------------------------------------------
# POST /data-sources
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    manifest: dict[str, Any]


@router.post("/data-sources", status_code=201)
def register_connection(
    body: RegisterRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Register a new DSI v1 data source connection."""
    reg = _get_registry()
    tid = rec.tenant_id

    # ── License gate: adapter allowlist ──────────────────────────────────────
    # Free tier: only local_file. Member and above: all adapters (None = unlimited).
    _adapter = body.manifest.get("adapter", "")
    _allowed_adapters = _lic_get_limit("datasource_adapters_allowed")
    if _allowed_adapters is not None and _adapter not in _allowed_adapters:
        console_audit.action_failed(
            tenant_id=tid,
            sid_fingerprint=rec.sid_fingerprint[:8],
            action="datasource.register",
            target_kind="datasource_connection",
            target_id=body.manifest.get("name", ""),
            reason="license_limit_exceeded",
        )
        raise HTTPException(
            status_code=http_status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "error": "license_limit",
                "feature": "datasource_adapters_allowed",
                "adapter": _adapter,
                "msg": (
                    f"Adapter '{_adapter}' requires a Member licence or higher. "
                    "Only 'local_file' connections are available on the Free tier."
                ),
                "upgrade_url": "https://corvin-labs.com/pricing",
            },
        )

    audit_writer = _make_ds_audit_writer(tid)
    if audit_writer is None:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Forge audit chain unavailable — cannot satisfy audit-first invariant for register.",
        )

    try:
        manifest = reg.register(
            body.manifest,
            tid,
            audit_writer=audit_writer,
        )
    except (KeyError, ValueError) as exc:
        console_audit.action_failed(
            tenant_id=tid,
            sid_fingerprint=rec.sid_fingerprint[:8],
            action="datasource.register",
            target_kind="datasource_connection",
            target_id=body.manifest.get("name", ""),
            reason="validation_failed",
        )
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="invalid request",
        )
    except PermissionError as exc:
        console_audit.action_failed(
            tenant_id=tid,
            sid_fingerprint=rec.sid_fingerprint[:8],
            action="datasource.register",
            target_kind="datasource_connection",
            target_id=body.manifest.get("name", ""),
            reason="permission_denied",
        )
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="not permitted",
        )

    console_audit.action_performed(
        tenant_id=tid,
        sid_fingerprint=rec.sid_fingerprint[:8],
        action="datasource.register",
        target_kind="datasource_connection",
        target_id=manifest.name,
    )

    return {
        "name": manifest.name,
        "adapter": manifest.adapter,
        "data_classification": manifest.data_classification,
        "data_residency": manifest.data_residency,
        "tags": manifest.tags,
        "description": manifest.description,
    }


# ---------------------------------------------------------------------------
# GET /data-sources/{name}
# ---------------------------------------------------------------------------

@router.get("/data-sources/{name}")
def get_connection(
    name: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Return details for a single DSI v1 connection."""
    reg = _get_registry()
    tid = rec.tenant_id
    conns = {c["name"]: c for c in reg.list_connections_v1(tid)}
    if name not in conns:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"No DSI v1 connection '{name}' found",
        )
    raw = conns[name]

    # Attach adapter metadata
    adapter_info = reg.describe_adapter(raw.get("adapter", ""), tid)
    return {
        **raw,
        "adapter_meta": adapter_info,
    }


# ---------------------------------------------------------------------------
# POST /data-sources/{name}/test
# ---------------------------------------------------------------------------

@router.post("/data-sources/{name}/test")
def test_connection(
    name: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Test connectivity for a DSI v1 connection."""
    reg = _get_registry()
    tid = rec.tenant_id

    try:
        result = reg.test_connection(
            name,
            tid,
            audit_writer=_make_ds_audit_writer(tid),
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"No DSI v1 connection '{name}' found",
        )
    except (ValueError, KeyError, ImportError, AttributeError) as exc:
        console_audit.action_failed(
            tenant_id=tid,
            sid_fingerprint=rec.sid_fingerprint[:8],
            action="datasource.test",
            target_kind="datasource_connection",
            target_id=name,
            reason="connection_failed",
        )
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="invalid request",
        )

    console_audit.action_performed(
        tenant_id=tid,
        sid_fingerprint=rec.sid_fingerprint[:8],
        action="datasource.test",
        target_kind="datasource_connection",
        target_id=name,
    )

    return {
        "ok": result.ok,
        "latency_ms": round(result.latency_ms, 1),
        "detail": result.detail,
    }


# ---------------------------------------------------------------------------
# DELETE /data-sources/{name}
# ---------------------------------------------------------------------------

@router.delete("/data-sources/{name}", status_code=204)
def unregister_connection(
    name: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> Response:
    """Unregister a DSI v1 connection."""
    reg = _get_registry()
    tid = rec.tenant_id

    audit_writer = _make_ds_audit_writer(tid)
    if audit_writer is None:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Forge audit chain unavailable — cannot satisfy audit-first invariant for unregister.",
        )

    try:
        reg.unregister(name, tid, audit_writer=audit_writer)
    except FileNotFoundError:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"No DSI v1 connection '{name}' found",
        )
    except ValueError as exc:
        console_audit.action_failed(
            tenant_id=tid,
            sid_fingerprint=rec.sid_fingerprint[:8],
            action="datasource.unregister",
            target_kind="datasource_connection",
            target_id=name,
            reason="validation_failed",
        )
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="invalid request",
        )

    console_audit.action_performed(
        tenant_id=tid,
        sid_fingerprint=rec.sid_fingerprint[:8],
        action="datasource.unregister",
        target_kind="datasource_connection",
        target_id=name,
    )

    return Response(status_code=204)


# ---------------------------------------------------------------------------
# GET /data-sources/{name}/audit
# ---------------------------------------------------------------------------

@router.get("/data-sources/{name}/audit")
def get_audit(
    name: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Return the last N datasource audit events for a connection."""
    limit = min(max(limit, 1), 500)
    if not _FORGE_AVAILABLE:
        return []

    tid = rec.tenant_id
    audit_path = _forge_paths.tenant_home(tid) / "audit.jsonl"
    if not audit_path.exists():
        return []

    events: list[dict[str, Any]] = []
    try:
        with audit_path.open(encoding="utf-8") as fh:
            # Use a bounded deque so we never materialise the full file in RAM.
            # L37 rotates at 100 MB; scanning the last _AUDIT_SCAN_MAX_LINES
            # lines covers all recent datasource events without allocating more.
            tail = deque(fh, maxlen=_AUDIT_SCAN_MAX_LINES)
        for line in reversed(tail):
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            details = ev.get("details") or ev.get("d") or {}
            if details.get("name") == name or details.get("target_id") == name:
                etype = ev.get("event_type") or ev.get("t") or ""
                if etype.startswith("datasource.") or etype.startswith("console."):
                    events.append({
                        "event_type": etype,
                        "severity": ev.get("severity") or ev.get("s") or "INFO",
                        "ts": ev.get("ts") or ev.get("timestamp"),
                        "details": {
                            k: v for k, v in details.items()
                            if k not in ("tenant_id",)
                        },
                    })
            if len(events) >= limit:
                break
    except OSError:
        pass

    return events
