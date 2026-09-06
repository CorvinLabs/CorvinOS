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

from .. import audit as console_audit
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

    # PluginDisableRefused inherits from PermissionError, NOT from PluginError —
    # so without importing it here every compliance-layer refusal fell through
    # _mutation_error()'s final clause and answered 500 "plugin operation failed"
    # with no audit event, while the admin plane answered 403 for the same
    # request. One mechanism, two doors, two different answers.
    from corvin_plugins.protocol import (  # type: ignore[import-not-found]
        PluginDisableRefused,
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
    #: Whether the plugin is registered in THIS process right now. It can diverge
    #: from `enabled`: self-healing may contain or unload a plugin without
    #: rewriting the operator's registry (an autonomous action must not edit
    #: configuration). Showing only `enabled` would repeat exactly the silent
    #: false display that hot-reload was introduced to remove.
    runtime_loaded: bool = False
    #: Set when the runtime state was changed by healing rather than by an operator.
    contained_by: str | None = None
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


class ScaffoldOut(BaseModel):
    """One Plugin-Builder scaffold record (ADR-0253) — NOT an installed
    plugin. Deliberately a much smaller shape than ``PluginOut``: a scaffold
    has no ``enabled``/``runtime_loaded``/``settings`` because it was never
    registered (ADR-0244's "the Builder emits, never loads" invariant)."""

    plugin_id: str
    display_name: str
    kind: str
    tier: str
    plugin_type: str | None
    path: str
    created_at: float


class ScaffoldListOut(BaseModel):
    scaffolds: list[ScaffoldOut]
    total: int


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


async def require_builder_surface(rec: Annotated[Any, Depends(require_session)]) -> Any:
    """Gate for the Plugin-Builder scaffold listing — independent of
    ``plugin_console_surface``: a scaffold is not an installed plugin, so
    whether the operator can browse the registry has no bearing on whether
    they can see what the Builder has generated for them (ADR-0253)."""
    if not _feature_flags.is_enabled("plugin_builder_enabled", rec.tenant_id):
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND)
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


def _runtime_state(plugin_id: str) -> tuple[bool, str | None]:
    """(is_registered_now, why_not) for one plugin.  Never raises.

    The reason is DERIVED, never assumed. Reporting "healing_unloaded" for anything
    that merely is not loaded would be a false statement in an operator-facing
    surface: a record with no class_path was never loadable, and a boot that has not
    reached the plugin yet has not healed anything either. Only an actual healing
    action in the orchestrator's history justifies that label.
    """
    try:
        from corvin_plugins import circuit_breaker as _cb
        from corvin_plugins.registry import get_registry

        loaded = plugin_id in get_registry().discover()
        breaker = _cb.snapshot().get(plugin_id) or {}
        state = breaker.get("state", "closed")

        if loaded:
            return True, (f"breaker_{state}" if state != "closed" else None)

        # Not loaded — ask the healer whether it did that, rather than guessing.
        collector = _collector()
        healer = getattr(collector, "_healer", None) if collector else None
        if healer is not None:
            try:
                from corvin_plugins.healing import HealingAction

                acted = {
                    rec.action
                    for rec in healer.history(plugin_id)
                    if rec.succeeded
                }
                if HealingAction.DISABLE in acted:
                    return False, "healing_unloaded"
                if HealingAction.ESCALATE in acted:
                    return False, "healing_escalated"
            except Exception:  # noqa: BLE001
                pass
        return False, "not_loaded"
    except Exception:  # noqa: BLE001 - a status read must not fail the request
        return False, None


def _to_out(record: Any) -> PluginOut:
    runtime_loaded, contained_by = _runtime_state(record.plugin_id)
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
        runtime_loaded=runtime_loaded,
        contained_by=contained_by if record.enabled else None,
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


#: Verbatim from routes/admin.py — the same refusal must read the same on both
#: doors, or an operator who hits one and then the other learns two different
#: things about one mechanism.
_COMPLIANCE_REFUSAL = (
    "{plugin_id} is on the compliance layer and cannot be disabled "
    "(GDPR Art. 30/32, EU AI Act Art. 50)"
)


def _audit_denied(rec: Any, action: str, plugin_id: str, reason: str) -> None:
    """Mirror of routes/admin.py::_audit_denied — same event, same detail shape.

    Only the plugin id goes into the chain, never ``str(exc)``: a plugin error
    message routinely carries a path or a host and the chain is append-only.
    """
    console_audit.action_denied(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action=action,
        target_kind="plugin",
        target_id=plugin_id,
        reason=reason,
    )


def _mutation_error(exc: Exception) -> HTTPException:
    """Map a lifecycle exception to a status code without leaking internals."""
    # FIRST, and by class rather than by route: PluginDisableRefused derives from
    # PermissionError, so it matches none of the PluginError branches below and
    # used to reach the 500 fallback.
    if isinstance(exc, PluginDisableRefused):
        return HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN, detail=str(exc)
        )
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


def _running_builtins(exclude_ids: set[str]) -> list[PluginOut]:
    """Builtin plugins LOADED in this process, as ``installed/active`` entries.

    G3/G4: a builtin discovered + loaded at boot by ``bootstrap_builtin`` (from
    the Corvin-Marketplace checkout, operator rule) must appear under "installed"
    even without an explicit install click — otherwise the Console shows an empty
    list while the plugin is demonstrably running. Source of the metadata is the
    on-disk manifest (``_builtin_plugin_dirs(_marketplace_root())`` + the in-wheel
    root); the entry is only emitted when the manifest's ``plugin_id`` is actually
    in the process registry, so this reports RUNNING builtins, never merely
    on-disk ones (which would be a false "active"). An id already in
    ``registry.yaml`` (``exclude_ids``) is skipped — the explicit record wins.

    Never raises into the request: a discovery failure degrades to "no builtins".
    """
    try:
        from corvin_plugins import bootstrap as _bootstrap
        from corvin_plugins.registry import get_registry

        loaded = set(get_registry().discover())
        if not loaded:
            return []
        dirs = list(_bootstrap._builtin_plugin_dirs(_bootstrap._BUILTIN_ROOT))
        for d in _bootstrap._builtin_plugin_dirs(_bootstrap._marketplace_root()):
            if d not in dirs:
                dirs.append(d)
    except Exception:  # noqa: BLE001
        return []

    out: list[PluginOut] = []
    seen: set[str] = set(exclude_ids)
    for plugin_dir in dirs:
        try:
            manifest = _bootstrap.load_from_manifest_safe(plugin_dir / "plugin.yaml")
        except Exception:  # noqa: BLE001
            continue
        pid = str(manifest.get("plugin_id") or "").strip()
        if not pid or pid in seen or pid not in loaded:
            continue
        seen.add(pid)
        runtime_loaded, contained = _runtime_state(pid)
        out.append(
            PluginOut(
                plugin_id=pid,
                version=str(manifest.get("version", "0.0.0")),
                display_name=str(manifest.get("display_name") or pid),
                plugin_type=str(manifest.get("plugin_type") or "generic"),
                origin="builtin",  # location-derived: these ship in a buildin/ tree
                pii_risk=str(manifest.get("pii_risk", "none")),
                locality=str(manifest.get("locality", "local")),
                network_egress=str(manifest.get("network_egress", "none")),
                egress_hosts=list(manifest.get("egress_hosts") or []),
                enabled=True,
                runtime_loaded=runtime_loaded,
                contained_by=contained,
                requires_consent=False,
                settings={},
                settings_schema={},
                dependencies=list(manifest.get("dependencies") or []),
                installed_at=None,
                last_error_type=None,
            )
        )
    return out


@router.get("/plugins")
async def list_plugins(
    rec: Annotated[Any, Depends(require_surface)],
) -> PluginListOut:
    registry = _load(rec.tenant_id)
    records = [_to_out(r) for r in sorted(registry.records.values(), key=lambda r: r.plugin_id)]
    # Merge in builtins running in THIS process that are not already an explicit
    # registry.yaml record (dedup by plugin_id; the registry record wins).
    records.extend(_running_builtins({r.plugin_id for r in records}))
    records.sort(key=lambda r: r.plugin_id)
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


@router.get("/plugins/scaffolded")
async def list_scaffolded_plugins(
    rec: Annotated[Any, Depends(require_builder_surface)],
) -> ScaffoldListOut:
    """Plugin-Builder scaffolds recorded for this tenant (ADR-0253).

    Registered BEFORE ``/plugins/{plugin_id}`` — a path-parameter route
    declared first would swallow this one, treating "scaffolded" as a
    ``plugin_id`` (same reason ``/plugins/health`` and ``/plugins/metrics``
    precede it above).
    """
    try:
        from plugin_builder import index_store  # noqa: PLC0415
    except ImportError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="plugin-builder subsystem unavailable in this installation",
        ) from exc
    records = [ScaffoldOut(**r) for r in index_store.list_scaffolds(rec.tenant_id)]
    return ScaffoldListOut(scaffolds=records, total=len(records))


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
    """Disable a record.  Refuses the compliance layer with 403 + an audit event.

    This is the OLDER of the two doors onto the same mechanism (the other is
    ``POST /api/admin/plugins/{id}/disable``, ADR-0239).  It does not pre-check
    the boot layer — the refusal comes from ``PluginLifecycle.disable()``, which
    routes the hot-unload through ``registry.disable()`` with
    ``operator_initiated=True``.  What this route owes the operator is the same
    ANSWER the admin plane gives: 403, not 500, and a ``console.action_denied``
    event carrying the same ``reason``.
    """
    try:
        return _to_out(_lifecycle(rec.tenant_id).disable(plugin_id))
    except PluginDisableRefused as exc:
        _audit_denied(rec, "plugin.disable", plugin_id, "compliance-layer")
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail=_COMPLIANCE_REFUSAL.format(plugin_id=plugin_id),
        ) from exc
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
