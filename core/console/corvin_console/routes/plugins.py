"""Console routes for the plugin registry (ADR-0233 Phase 4).

Behind the ``plugin_console_surface`` feature flag: with the flag off every route
here returns 404, so the surface is genuinely absent rather than merely hidden in
the UI.  Mutations additionally require ``plugin_runtime_lifecycle`` — the read
side can be inspected without allowing runtime changes.

Tenant resolution: ALWAYS ``rec.tenant_id`` from the authenticated
``SessionRecord``, never an env var and never a request field (CLAUDE.md
§ Multi-tenant axis — console routing must not read CORVIN_TENANT_ID).
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi import status as http_status
from pydantic import BaseModel, Field

from .. import feature_flags as _feature_flags
from ..deps import require_csrf, require_session

log = logging.getLogger(__name__)

router = APIRouter()

# The plugin package lives outside the console package; import it the same way
# adapter.py does (optional, path-extended, guarded).
_PLUGINS_AVAILABLE = False
try:
    # routes → corvin_console → console → core, so parents[3] is `core/`.
    _core_plugins = Path(__file__).resolve().parents[3] / "plugins"
    if (_core_plugins / "corvin_plugins").is_dir() and str(_core_plugins) not in sys.path:
        # append, NOT insert(0): this directory also contains generic top-level
        # names (tests/, templates/) with no __init__.py, so putting it FIRST on
        # sys.path lets them shadow another package's `tests` — the same class as
        # the operator/ stdlib-shadow trap. Appending means existing paths win.
        sys.path.append(str(_core_plugins))
    from corvin_plugins.manifest import (  # type: ignore[import-not-found]
        InvalidPluginID,
        Locality,
        NetworkEgress,
        PIIRisk,
        PluginError,
        PluginNotFound,
        PluginOrigin,
        PluginRecord,
        SettingsValidator,
        UnknownPluginType,
        ValidationError,
    )
    from corvin_plugins.state import (  # type: ignore[import-not-found]
        ConsentRequired,
        EgressNotDeclared,
        LifecycleDisabled,
        PluginLifecycle,
        RegistryCorrupt,
        TenantRegistry,
    )

    _PLUGINS_AVAILABLE = True
except ImportError:  # pragma: no cover - stripped install without core/plugins
    log.warning("corvin_plugins not importable — /plugins routes will report 503")


# ── Models ────────────────────────────────────────────────────────────────────


class PluginOut(BaseModel):
    """One registry record as the Console sees it."""

    plugin_id: str
    version: str
    display_name: str
    plugin_type: str
    origin: str
    pii_risk: str
    #: ADR-0124 Inv. 3 declarations, so the Console can show WHERE a plugin runs
    #: and WHAT it talks to before an operator enables it.
    locality: str
    network_egress: str
    egress_hosts: list[str]
    enabled: bool
    requires_consent: bool
    settings: dict[str, Any]
    settings_schema: dict[str, Any]
    dependencies: list[str]
    installed_at: str | None = None
    last_error_type: str | None = None


class PluginListOut(BaseModel):
    plugins: list[PluginOut]
    total: int
    lifecycle_enabled: bool


class SettingsIn(BaseModel):
    settings: dict[str, Any] = Field(default_factory=dict)


class EnableIn(BaseModel):
    #: Present and true means the operator has seen the consent notice for a
    #: community / high-PII plugin. Absent means the enable is refused for those.
    consent_granted: bool = False


class InstallIn(BaseModel):
    plugin_id: str
    version: str
    display_name: str
    plugin_type: str
    class_path: str | None = None
    origin: str = "community"
    pii_risk: str = "low"
    # Least-trusted defaults, matching PluginRecord: an installer that says nothing
    # gets "unclassified, talks to the internet", never "safe".
    locality: str = "unknown"
    network_egress: str = "external"
    egress_hosts: list[str] = Field(default_factory=list)
    settings_schema: dict[str, Any] = Field(default_factory=dict)
    settings: dict[str, Any] = Field(default_factory=dict)
    dependencies: list[str] = Field(default_factory=list)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _require_surface(tenant_id: str) -> None:
    """404 when the console surface is off — the route does not exist for you."""
    if not _PLUGINS_AVAILABLE:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="plugin subsystem unavailable in this installation",
        )
    if not _feature_flags.is_enabled("plugin_console_surface", tenant_id):
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND)


async def require_surface(rec: Annotated[Any, Depends(require_session)]) -> Any:
    """Session + surface gate as a DEPENDENCY, not an in-body check.

    This must be a dependency: FastAPI validates the request body only after
    dependencies resolve, so an in-function check would let a malformed POST
    answer 422 while the flag is off — telling the caller the route exists. A
    dark feature must be indistinguishable from an absent one.
    """
    _require_surface(rec.tenant_id)
    return rec


async def require_surface_csrf(rec: Annotated[Any, Depends(require_csrf)]) -> Any:
    """Same gate for mutations: CSRF first (401/403), then the surface (404)."""
    _require_surface(rec.tenant_id)
    return rec


#: The gateway lifespan owns the collector instance (it owns the event loop); the
#: route only reads its latest snapshot. Set via set_collector() at boot.
_COLLECTOR: Any | None = None


def set_collector(collector: Any | None) -> None:
    """Register the process-wide health collector (called from the boot path)."""
    global _COLLECTOR
    _COLLECTOR = collector


def _collector() -> Any | None:
    return _COLLECTOR


def _lifecycle(tenant_id: str) -> Any:
    return PluginLifecycle(
        tenant_id=tenant_id,
        lifecycle_enabled=lambda: _feature_flags.is_enabled(
            "plugin_runtime_lifecycle", tenant_id
        ),
    )


def _to_out(record: Any) -> PluginOut:
    return PluginOut(
        plugin_id=record.plugin_id,
        version=record.version,
        display_name=record.display_name,
        plugin_type=record.plugin_type,
        origin=record.origin.value,
        pii_risk=record.pii_risk.value,
        locality=record.locality.value,
        network_egress=record.network_egress.value,
        egress_hosts=list(record.egress_hosts),
        enabled=record.enabled,
        requires_consent=record.consent_required(),
        settings=record.settings,
        settings_schema=record.settings_schema,
        dependencies=list(record.dependencies),
        installed_at=record.installed_at.isoformat() if record.installed_at else None,
        last_error_type=record.last_error_type,
    )


def _load(tenant_id: str) -> Any:
    try:
        return TenantRegistry.load(tenant_id=tenant_id)
    except RegistryCorrupt as exc:
        # Fail closed and say so: the operator must repair the file. Returning an
        # empty list here would invite a save that overwrites it.
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"plugin registry unreadable: {type(exc).__name__}",
        ) from exc
    except PluginError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"plugin registry rejected: {exc}",
        ) from exc


def _mutation_error(exc: Exception) -> HTTPException:
    """Map a lifecycle exception to a status code without leaking internals."""
    if isinstance(exc, LifecycleDisabled):
        return HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail=("runtime plugin changes are switched off — enable "
                    "plugin_runtime_lifecycle in Settings → Features"),
        )
    if isinstance(exc, (ConsentRequired, EgressNotDeclared)):
        return HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, PluginNotFound):
        return HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail="plugin not installed"
        )
    # A malformed plugin_id / plugin_type / settings payload is unprocessable
    # INPUT (422), not a state conflict (409) — InvalidPluginID inherits from
    # PluginError, so without this line it would have fallen through to 409.
    if isinstance(exc, (ValidationError, UnknownPluginType, InvalidPluginID)):
        return HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )
    if isinstance(exc, PluginError):
        return HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=str(exc))
    return HTTPException(
        status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=f"plugin operation failed: {type(exc).__name__}",
    )


# ── Read ──────────────────────────────────────────────────────────────────────


@router.get("/plugins")
async def list_plugins(
    rec: Annotated[Any, Depends(require_surface)],
) -> PluginListOut:
    registry = _load(rec.tenant_id)
    records = [_to_out(r) for r in sorted(registry.records.values(), key=lambda r: r.plugin_id)]
    return PluginListOut(
        plugins=records,
        total=len(records),
        lifecycle_enabled=_feature_flags.is_enabled(
            "plugin_runtime_lifecycle", rec.tenant_id
        ),
    )


@router.get("/plugins/health")
async def plugin_health(
    rec: Annotated[Any, Depends(require_surface)],
) -> dict[str, Any]:
    """Live health + circuit-breaker state of the plugins loaded in this process."""
    from corvin_plugins import circuit_breaker
    from corvin_plugins import registry as runtime_registry

    monitoring = _feature_flags.is_enabled("plugin_health_monitoring", rec.tenant_id)
    collector = _collector()
    if monitoring and collector is not None and collector.running:
        # A collector is polling: serve its snapshot instead of calling every
        # plugin again on each request.
        return {
            "monitoring_enabled": True,
            "collector_running": True,
            **collector.snapshot().to_dict(),
            "breakers": __import__(
                "corvin_plugins.circuit_breaker", fromlist=["snapshot"]
            ).snapshot(),
        }
    if not monitoring:
        # Flag off: report breaker state (already maintained, free to read) but do
        # not call into plugins.
        return {"monitoring_enabled": False, "breakers": circuit_breaker.snapshot()}

    statuses = runtime_registry.health_check_all()
    return {
        "monitoring_enabled": True,
        "plugins": {
            pid: {"ok": st.ok, "message": st.message, "details": st.details}
            for pid, st in statuses.items()
        },
        "breakers": circuit_breaker.snapshot(),
    }


@router.get("/plugins/metrics")
async def plugin_metrics(
    rec: Annotated[Any, Depends(require_surface)],
) -> Response:
    """Plugin health + breaker state in Prometheus 0.0.4 text format.

    Breaker numbers are always real (breakers run regardless of the monitoring
    flag); the health gauges are zero until a collector has polled at least once.
    """
    from corvin_plugins import health as _health

    collector = _collector()
    snapshot = collector.snapshot() if collector is not None else None
    body = _health.render_prometheus(snapshot)
    return Response(content=body, media_type="text/plain; version=0.0.4")


@router.get("/plugins/{plugin_id}")
async def get_plugin(
    plugin_id: str,
    rec: Annotated[Any, Depends(require_surface)],
) -> PluginOut:
    registry = _load(rec.tenant_id)
    try:
        return _to_out(registry.get(plugin_id))
    except PluginNotFound as exc:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail="plugin not installed"
        ) from exc


# ── Mutations (CSRF-protected) ────────────────────────────────────────────────


@router.post("/plugins")
async def install_plugin(
    body: InstallIn,
    rec: Annotated[Any, Depends(require_surface_csrf)],
) -> PluginOut:
    try:
        record = PluginRecord(
            plugin_id=body.plugin_id,
            version=body.version,
            display_name=body.display_name or body.plugin_id,
            plugin_type=body.plugin_type,
            origin=PluginOrigin(body.origin),
            pii_risk=PIIRisk(body.pii_risk),
            locality=Locality(body.locality),
            network_egress=NetworkEgress(body.network_egress),
            egress_hosts=body.egress_hosts,
            settings_schema=body.settings_schema,
            settings=body.settings,
            dependencies=body.dependencies,
            class_path=body.class_path,
        )
    except (UnknownPluginType, InvalidPluginID, PluginError) as exc:
        raise _mutation_error(exc) from exc
    except ValueError as exc:  # bad origin / pii_risk enum value
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    try:
        # The operator identity in the audit trail is the session's role, not a
        # display name — no PII in audit details.
        stored = _lifecycle(rec.tenant_id).install(record, installed_by="console")
    except Exception as exc:  # noqa: BLE001 - mapped to a status below
        raise _mutation_error(exc) from exc
    return _to_out(stored)


@router.post("/plugins/{plugin_id}/enable")
async def enable_plugin(
    plugin_id: str,
    rec: Annotated[Any, Depends(require_surface_csrf)],
    body: EnableIn | None = None,
) -> PluginOut:
    granted = "console" if (body and body.consent_granted) else None
    try:
        return _to_out(_lifecycle(rec.tenant_id).enable(plugin_id, consent_granted_by=granted))
    except Exception as exc:  # noqa: BLE001
        raise _mutation_error(exc) from exc


@router.post("/plugins/{plugin_id}/disable")
async def disable_plugin(
    plugin_id: str,
    rec: Annotated[Any, Depends(require_surface_csrf)],
) -> PluginOut:
    try:
        return _to_out(_lifecycle(rec.tenant_id).disable(plugin_id))
    except Exception as exc:  # noqa: BLE001
        raise _mutation_error(exc) from exc


@router.post("/plugins/{plugin_id}/settings")
async def update_settings(
    plugin_id: str,
    body: SettingsIn,
    rec: Annotated[Any, Depends(require_surface_csrf)],
) -> PluginOut:
    try:
        return _to_out(_lifecycle(rec.tenant_id).set_settings(plugin_id, body.settings))
    except Exception as exc:  # noqa: BLE001
        raise _mutation_error(exc) from exc


@router.delete("/plugins/{plugin_id}")
async def uninstall_plugin(
    plugin_id: str,
    rec: Annotated[Any, Depends(require_surface_csrf)],
) -> dict[str, Any]:
    try:
        _lifecycle(rec.tenant_id).uninstall(plugin_id)
    except Exception as exc:  # noqa: BLE001
        raise _mutation_error(exc) from exc
    # The audit trail outlives the plugin (GDPR Art. 30) — say so explicitly, so
    # nobody expects an uninstall to erase history.
    return {"uninstalled": plugin_id, "audit_retained": True}


@router.get("/plugins/{plugin_id}/schema-defaults")
async def schema_defaults(
    plugin_id: str,
    rec: Annotated[Any, Depends(require_surface)],
) -> dict[str, Any]:
    """Declared defaults, for pre-filling the settings form."""
    registry = _load(rec.tenant_id)
    try:
        record = registry.get(plugin_id)
    except PluginNotFound as exc:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail="plugin not installed"
        ) from exc
    return {"defaults": SettingsValidator(record.settings_schema).defaults()}
