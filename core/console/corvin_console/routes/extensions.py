"""Layer Extensions console routes (ADR-0142 M5).

Six endpoints:
  GET    /v1/console/extensions            → list all layers (core + ext)
  GET    /v1/console/extensions/validate    → validate a manifest YAML (body/query)
  GET    /v1/console/extensions/{name}      → detail + manifest (404 if unknown)
  POST   /v1/console/extensions            → install from a directory path (disabled by default)
  PUT    /v1/console/extensions/{name}      → enable/disable (core → 403)
  DELETE /v1/console/extensions/{name}      → remove (core → 403)

All routes:
  - require_session (GET) or require_csrf (mutations)
  - use rec.tenant_id from the session (NEVER an env var)
  - emit console.action_performed / action_failed (metadata-only)

The ext.* lifecycle audit events (ext.installed/removed/enabled/disabled) are
emitted by the shared ExtensionRegistry / layer_cli wrappers onto the L16 hash
chain. The console route ALSO emits a metadata-only console.* event so the
owner-console audit explorer reflects who performed the action. We NEVER leak
manifest hook content, file paths, or secrets into either chain.

Core layers (``corvin.*``) are immutable per ADR-0141 — they are listed but
enable/disable/remove is rejected with the ADR-0142 wording.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from fastapi.responses import Response
from pydantic import BaseModel

from .. import audit as console_audit
from .. import auth as session_auth
from ..deps import require_csrf, require_session

_THIS_DIR = Path(__file__).resolve().parent
_REPO = _THIS_DIR.parents[3]
_SHARED = _REPO / "operator" / "bridges" / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

import extension_registry as _reg  # noqa: E402
import layer_cli as _cli  # noqa: E402

import logging
_log = logging.getLogger(__name__)

ExtensionRegistry = _reg.ExtensionRegistry
ExtensionError = _reg.ExtensionError
ExtensionNamespaceError = _reg.ExtensionNamespaceError
ExtensionManifestError = _reg.ExtensionManifestError
ExtensionDependencyError = _reg.ExtensionDependencyError

router = APIRouter()


def _make_registry(tenant_id: str) -> ExtensionRegistry:
    """Build a registry scoped to the authenticated session's tenant.

    The console is single-tenant by construction; the project/session scopes
    are not addressable from the web UI, so only tenant + user scopes surface.
    """
    return ExtensionRegistry(tenant_id=tenant_id)


def _is_core_name(name: str) -> bool:
    return name.startswith(_reg._CORE_PREFIX)


# ---------------------------------------------------------------------------
# GET /extensions
# ---------------------------------------------------------------------------

@router.get("/extensions")
def list_extensions(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, list[dict[str, Any]]]:
    """List all layers: immutable core layers + user-managed extensions."""
    reg = _make_registry(rec.tenant_id)
    core = reg.list_core()
    exts = [m.to_public_dict() for m in reg.list_extensions()]
    return {"core": core, "extensions": exts}


# ---------------------------------------------------------------------------
# GET /extensions/validate  (placed before /{name} to avoid route capture)
# ---------------------------------------------------------------------------

class ValidateRequest(BaseModel):
    manifest_yaml: str = ""


@router.get("/extensions/validate")
def validate_manifest(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    manifest_yaml: str = "",
) -> dict[str, Any]:
    """Lint a layer.yaml passed as the ``manifest_yaml`` query parameter.

    Returns ``{ok, name?, version?, scope?, hooks?, requires?, error?}``.
    Read-only — never writes to disk, never emits a mutation audit event.
    """
    return _validate_yaml(manifest_yaml, rec.tenant_id)


@router.post("/extensions/validate")
def validate_manifest_post(
    body: ValidateRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """POST variant of the validator (body carries the YAML). Read-only."""
    return _validate_yaml(body.manifest_yaml, rec.tenant_id)


def _validate_yaml(manifest_yaml: str, tenant_id: str) -> dict[str, Any]:
    if not manifest_yaml.strip():
        return {"ok": False, "error": "empty manifest"}
    import yaml
    try:
        raw = yaml.safe_load(manifest_yaml)
    except Exception as exc:  # noqa: BLE001 — surface a clean parse error
        return {"ok": False, "error": f"YAML parse error: {exc}"}
    try:
        manifest = _reg.parse_manifest(raw)
        _reg.validate_name(manifest.name)
        reg = _make_registry(tenant_id)
        _reg.check_requires(manifest, reg.core_capabilities)
    except ExtensionNamespaceError as exc:
        return {"ok": False, "error": f"namespace: {exc}"}
    except (ExtensionManifestError, ExtensionDependencyError, ExtensionError) as exc:
        return {"ok": False, "error": "internal error"}
    return {
        "ok": True,
        "name": manifest.name,
        "version": manifest.version,
        "scope": manifest.scope,
        "hooks": len(manifest.hooks),
        "requires": len(manifest.requires),
    }


# ---------------------------------------------------------------------------
# GET /extensions/{name}
# ---------------------------------------------------------------------------

@router.get("/extensions/{name}")
def get_extension(
    name: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Return detail + manifest for a single layer (core or extension)."""
    reg = _make_registry(rec.tenant_id)
    if _is_core_name(name):
        for c in reg.list_core():
            if c["name"] == name:
                return {**c, "removable": False}
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"No core layer '{name}' found",
        )
    manifest = reg.get(name)
    if manifest is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"No extension '{name}' found",
        )
    return {**manifest.to_public_dict(), "core": False, "removable": True}


# ---------------------------------------------------------------------------
# POST /extensions
# ---------------------------------------------------------------------------

class InstallRequest(BaseModel):
    # Directory-path install is the must-have. Tarball/GitHub-URL download is a
    # documented TODO (ADR-0142 M4 note); the same install path applies once a
    # downloader unpacks into a directory.
    source: str
    scope: str | None = None
    enable: bool = False


@router.post("/extensions", status_code=201)
def install_extension(
    body: InstallRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Install an extension from a local directory path. Installed DISABLED by
    default (ADR-0142: no auto-enable without operator confirmation)."""
    tid = rec.tenant_id
    src = body.source.strip()

    # Reject remote sources for now — directory-path install is the must-have.
    if src.startswith(("http://", "https://", "github:")):
        console_audit.action_failed(
            tenant_id=tid,
            sid_fingerprint=rec.sid_fingerprint[:8],
            action="extension.install",
            target_kind="extension",
            target_id="",
            reason="source_unsupported",
        )
        raise HTTPException(
            status_code=http_status.HTTP_501_NOT_IMPLEMENTED,
            detail="Tarball/GitHub-URL install is not yet implemented; "
                   "use a local directory path.",
        )

    try:
        result = _cli.install_dir(
            src,
            scope_override=body.scope,
            tenant_id=tid,
            enable=body.enable,
        )
    except ExtensionNamespaceError as exc:
        console_audit.action_failed(
            tenant_id=tid,
            sid_fingerprint=rec.sid_fingerprint[:8],
            action="extension.install",
            target_kind="extension",
            target_id="",
            reason="namespace_rejected",
        )
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="conflict",
        )
    except ValueError as exc:
        console_audit.action_failed(
            tenant_id=tid,
            sid_fingerprint=rec.sid_fingerprint[:8],
            action="extension.install",
            target_kind="extension",
            target_id="",
            reason="validation_failed",
        )
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="invalid request",
        )
    except ExtensionError as exc:
        console_audit.action_failed(
            tenant_id=tid,
            sid_fingerprint=rec.sid_fingerprint[:8],
            action="extension.install",
            target_kind="extension",
            target_id="",
            reason="install_failed",
        )
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="invalid request",
        )

    console_audit.action_performed(
        tenant_id=tid,
        sid_fingerprint=rec.sid_fingerprint[:8],
        action="extension.install",
        target_kind="extension",
        target_id=result["name"],
    )
    return result


# ---------------------------------------------------------------------------
# PUT /extensions/{name}
# ---------------------------------------------------------------------------

class EnabledRequest(BaseModel):
    enabled: bool


@router.put("/extensions/{name}")
def set_extension_enabled(
    name: str,
    body: EnabledRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Enable or disable an extension. Core layers are immutable (403)."""
    tid = rec.tenant_id
    action = "extension.enable" if body.enabled else "extension.disable"

    if _is_core_name(name):
        console_audit.action_failed(
            tenant_id=tid,
            sid_fingerprint=rec.sid_fingerprint[:8],
            action=action,
            target_kind="extension",
            target_id=name,
            reason="core_layer_immutable",
        )
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail=_cli._CORE_REMOVE_MSG.format(name=name),
        )

    try:
        result = _cli.set_enabled(name, enable=body.enabled, tenant_id=tid)
    except PermissionError as exc:
        console_audit.action_failed(
            tenant_id=tid,
            sid_fingerprint=rec.sid_fingerprint[:8],
            action=action,
            target_kind="extension",
            target_id=name,
            reason="core_layer_immutable",
        )
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="not permitted",
        )
    except KeyError:
        console_audit.action_failed(
            tenant_id=tid,
            sid_fingerprint=rec.sid_fingerprint[:8],
            action=action,
            target_kind="extension",
            target_id=name,
            reason="not_found",
        )
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"No extension '{name}' found",
        )

    console_audit.action_performed(
        tenant_id=tid,
        sid_fingerprint=rec.sid_fingerprint[:8],
        action=action,
        target_kind="extension",
        target_id=name,
    )
    return result


# ---------------------------------------------------------------------------
# DELETE /extensions/{name}
# ---------------------------------------------------------------------------

@router.delete("/extensions/{name}", status_code=204)
def remove_extension(
    name: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> Response:
    """Remove a non-core extension. Core layers are immutable (403)."""
    tid = rec.tenant_id

    if _is_core_name(name):
        console_audit.action_failed(
            tenant_id=tid,
            sid_fingerprint=rec.sid_fingerprint[:8],
            action="extension.remove",
            target_kind="extension",
            target_id=name,
            reason="core_layer_immutable",
        )
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail=_cli._CORE_REMOVE_MSG.format(name=name),
        )

    try:
        _cli.remove(name, tenant_id=tid)
    except PermissionError as exc:
        console_audit.action_failed(
            tenant_id=tid,
            sid_fingerprint=rec.sid_fingerprint[:8],
            action="extension.remove",
            target_kind="extension",
            target_id=name,
            reason="core_layer_immutable",
        )
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="not permitted",
        )
    except KeyError:
        console_audit.action_failed(
            tenant_id=tid,
            sid_fingerprint=rec.sid_fingerprint[:8],
            action="extension.remove",
            target_kind="extension",
            target_id=name,
            reason="not_found",
        )
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"No extension '{name}' found",
        )

    console_audit.action_performed(
        tenant_id=tid,
        sid_fingerprint=rec.sid_fingerprint[:8],
        action="extension.remove",
        target_kind="extension",
        target_id=name,
    )
    return Response(status_code=204)
