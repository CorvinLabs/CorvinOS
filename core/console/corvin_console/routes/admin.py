"""Admin control plane — the UI-independent administration API (ADR-0239/0243).

Six routes under ``/api/admin`` that expose plugin administration without going
through the Console SPA, so a CLI, a custom dashboard or a headless deployment
can drive the same operations:

    GET  /api/admin/plugins
    GET  /api/admin/plugins/{plugin_id}
    POST /api/admin/plugins/{plugin_id}/enable
    POST /api/admin/plugins/{plugin_id}/disable
    PUT  /api/admin/plugins/{plugin_id}/config
    GET  /api/admin/health

Load-bearing properties:

* **No new auth surface.**  The same authenticated ``SessionRecord`` the rest of
  the Console uses (``require_session`` / ``require_csrf``), per ADR-0239.
* **Tenant is ALWAYS ``rec.tenant_id``** from that session — never an env var,
  never a query parameter, never a header, never a body field (CLAUDE.md
  § Multi-tenant axis).  The request models are ``extra="forbid"``, so a body
  that smuggles a ``tenant_id`` is rejected rather than silently ignored.
* **The compliance layer is not disableable.**  ``POST .../disable`` on a
  ``layer=compliance`` plugin answers 403 and writes an audit event; it never
  answers 200 with a silent no-op.  The unload goes through
  ``registry.disable()`` — the operator-initiated entry point that raises
  ``PluginDisableRefused`` — and never through ``registry.unregister()``, which
  is the machinery path that would reach past the guard.
* **Every mutation is audited** with tenant_id, plugin_id and outcome; settings
  VALUES never reach the audit trail (key names only, see
  ``PluginLifecycle.set_settings``).
* **Ships dark.**  With the ``admin_control_plane`` feature flag off every route
  here answers 404 — the surface is absent, not merely hidden.

gRPC is **deferred**, not planned (ADR-0239): REST over the existing session auth
covers every known caller, and a second transport would be a pure dependency with
no consumer.  Nothing in this module anticipates it.

Mount point: the router declares ``/api/admin/*``.  The Console router is mounted
by the gateway at ``/v1/console``, so the effective path in a default install is
``/v1/console/api/admin/*``; a headless deployment that mounts
``corvin_console.app.router`` at the root serves ``/api/admin/*`` verbatim.
"""
from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status
from pydantic import BaseModel, Field

from .. import audit as console_audit
from .. import feature_flags as _feature_flags
from ..deps import require_csrf, require_session

log = logging.getLogger(__name__)

router = APIRouter()

#: The flag this whole surface hangs on.  Off (the default) means 404 everywhere.
FLAG_ID = "admin_control_plane"

#: Mutations additionally need the lifecycle flag — the admin plane must not be a
#: second, weaker path to change plugin state than the Console surface.
LIFECYCLE_FLAG_ID = "plugin_runtime_lifecycle"

#: Layers that are process-global by construction (ADR-0240): they are loaded
#: once for the whole installation, so showing them to every tenant is correct.
#: An ``installed``-layer runtime object belongs to whichever tenant enabled it,
#: and is therefore only shown when THIS tenant also has a record for it —
#: otherwise the admin plane would leak another tenant's plugin ids.
_GLOBAL_LAYERS = frozenset({"compliance", "core", "bundled"})


# The plugin package lives outside the console package; import it the same way
# routes/plugins.py does (optional, path-extended, guarded).
_PLUGINS_AVAILABLE = False
try:
    # routes → corvin_console → console → core, so parents[3] is `core/`.
    _core_plugins = Path(__file__).resolve().parents[3] / "plugins"
    if (_core_plugins / "corvin_plugins").is_dir() and str(_core_plugins) not in sys.path:
        # append, NOT insert(0): this directory also contains generic top-level
        # names (tests/, templates/) with no __init__.py, so putting it FIRST on
        # sys.path lets them shadow another package's `tests` — the same class as
        # the operator/ stdlib-shadow trap.  Appending means existing paths win.
        sys.path.append(str(_core_plugins))
    from corvin_plugins.manifest import (  # type: ignore[import-not-found]
        InvalidPluginID,
        PluginError,
        PluginLayer,
        PluginNotFound,
        SettingsValidator,
        UnknownPluginType,
        ValidationError,
    )
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
    log.warning("corvin_plugins not importable — /api/admin routes will report 503")


# ── Models ────────────────────────────────────────────────────────────────────


class HealthOut(BaseModel):
    """One plugin's health as the admin plane reports it."""

    ok: bool
    message: str = ""


class AdminPluginOut(BaseModel):
    """List view of one plugin."""

    plugin_id: str
    version: str
    display_name: str
    plugin_type: str
    #: compliance | core | bundled | installed (ADR-0243).  NOT "tier" — that word
    #: means ADR-0156's capability boundary repo-wide (CLAUDE.md).
    layer: str
    #: builtin | vetted | community.  ``None`` for a plugin that is loaded in this
    #: process but has no registry record: provenance is genuinely unknown then,
    #: and guessing "builtin" would be a claim the surface cannot support.
    origin: str | None = None
    #: Active for this tenant: the record's flag, or "it is running" when the
    #: plugin has no record.
    enabled: bool
    #: Registered in THIS process right now.  Can diverge from ``enabled``.
    runtime_loaded: bool
    #: False for the compliance layer.  Fail-closed conjunction of the record's
    #: layer and the runtime layer — either saying "compliance" wins.
    can_disable: bool
    #: registry | runtime | both — where this entry came from.
    source: str
    #: ``None`` when the plugin is not loaded (nothing to ask) or the check failed.
    health: HealthOut | None = None


class AdminPluginDetailOut(AdminPluginOut):
    """Detail view — adds the declarations and the settings surface."""

    pii_risk: str | None = None
    locality: str | None = None
    network_egress: str | None = None
    egress_hosts: list[str] = Field(default_factory=list)
    requires_consent: bool = False
    settings: dict[str, Any] = Field(default_factory=dict)
    settings_schema: dict[str, Any] = Field(default_factory=dict)
    dependencies: list[str] = Field(default_factory=list)
    replaces: str | None = None
    installed_at: str | None = None
    last_error_type: str | None = None


class EnableIn(BaseModel):
    """Body of ``POST .../enable``.

    ``extra="forbid"``: a caller that tries to steer the target tenant with a
    ``tenant_id`` field gets 422 instead of having the field quietly dropped.
    Silently ignoring it would look identical to honouring it from the outside.
    """

    consent_granted: bool = False
    model_config = {"extra": "forbid"}


class ConfigIn(BaseModel):
    """Body of ``PUT .../config``."""

    settings: dict[str, Any] = Field(default_factory=dict)
    model_config = {"extra": "forbid"}


# ── Gates ─────────────────────────────────────────────────────────────────────


def _require_plane(tenant_id: str) -> None:
    """404 while the flag is off — the route does not exist for you.

    The flag is checked BEFORE the availability of the plugin package: a 503 on a
    stripped install would tell an unauthorised-to-know caller that the surface
    exists and is merely broken.  A dark feature must be indistinguishable from
    an absent one.
    """
    if not _feature_flags.is_enabled(FLAG_ID, tenant_id):
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND)
    if not _PLUGINS_AVAILABLE:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="plugin subsystem unavailable in this installation",
        )


async def require_plane(rec: Annotated[Any, Depends(require_session)]) -> Any:
    """Session + flag gate as a DEPENDENCY, not an in-body check.

    It must be a dependency: FastAPI validates the request body only after
    dependencies resolve, so an in-function check would let a malformed PUT
    answer 422 while the flag is off — telling the caller the route exists.
    """
    _require_plane(rec.tenant_id)
    return rec


async def require_plane_csrf(rec: Annotated[Any, Depends(require_csrf)]) -> Any:
    """Same gate for mutations: CSRF first (401/403), then the flag (404)."""
    _require_plane(rec.tenant_id)
    return rec


def _require_lifecycle(tenant_id: str) -> None:
    """409 when runtime plugin changes are switched off.

    Checked BEFORE anything is touched, so a refused mutation never leaves the
    runtime and the registry in different states.
    """
    if not _feature_flags.is_enabled(LIFECYCLE_FLAG_ID, tenant_id):
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=(
                "runtime plugin changes are switched off — enable the "
                "plugin_runtime_lifecycle feature flag in Settings → Features"
            ),
        )


# ── The merged view ───────────────────────────────────────────────────────────


@dataclass
class _Entry:
    """One plugin as seen from both sources.

    Two sources exist and neither is complete on its own:

    * ``registry.yaml`` for this tenant — the operator's declared state.  Bundled
      and compliance plugins are loaded by the boot path and need no record here.
    * the in-process ``PluginRegistry`` — what is actually running, with the
      authoritative ``layer`` of the loaded object.

    Reading only the file would answer 404 for exactly the plugin whose disable
    must be refused with 403, which would make the compliance guard unreachable.
    """

    plugin_id: str
    record: Any | None = None
    runtime: dict[str, Any] | None = None
    #: Set to False after a successful unload of a runtime-only plugin: the
    #: metadata is kept so the response can describe WHAT was just disabled,
    #: while ``loaded`` already reports the end state.
    loaded_override: bool | None = None

    @property
    def loaded(self) -> bool:
        if self.loaded_override is not None:
            return self.loaded_override
        return self.runtime is not None

    @property
    def source(self) -> str:
        if self.record is not None and self.runtime is not None:
            return "both"
        return "registry" if self.record is not None else "runtime"

    @property
    def layer(self) -> str:
        """The layer that governs this plugin right now.

        The loaded object wins when both are known: it is the thing the process
        is actually running.  ``can_disable`` does not follow that preference —
        it is the fail-closed conjunction, so a disagreement can never widen
        permissions.
        """
        if self.runtime is not None:
            return str(self.runtime.get("layer") or PluginLayer.INSTALLED.value)
        if self.record is not None:
            return self.record.layer.value
        return PluginLayer.INSTALLED.value

    @property
    def can_disable(self) -> bool:
        """Fail closed: any source that says "no" wins, silence also says no.

        The runtime clause is ``not …get("can_disable", False)``, not
        ``… is False``: a loaded plugin whose entry lost that key (a future
        refactor, a partial read) is treated as protected.  Refusing a disable
        that should have been allowed is a support ticket; allowing one that
        should have been refused is a compliance incident.
        """
        if self.layer == PluginLayer.COMPLIANCE.value:
            return False
        if self.record is not None and not self.record.can_disable():
            return False
        if self.runtime is not None and not self.runtime.get("can_disable", False):
            return False
        return True


def _runtime_entries() -> dict[str, dict[str, Any]]:
    """What is registered in THIS process, keyed by plugin_id.  Never raises."""
    out: dict[str, dict[str, Any]] = {}
    try:
        from corvin_plugins.registry import get_registry

        registry = get_registry()
        for plugin_id in registry.discover():
            try:
                plugin = registry.get(plugin_id)
                layer = registry.layer_of(plugin_id)
                out[plugin_id] = {
                    "layer": layer.value,
                    "can_disable": registry.can_disable(plugin_id),
                    "version": getattr(plugin, "version", ""),
                    "display_name": getattr(plugin, "display_name", plugin_id),
                    "plugin_type": getattr(plugin, "plugin_type", ""),
                }
            except Exception:  # noqa: BLE001 - one odd plugin must not blank the list
                continue
    except Exception:  # noqa: BLE001 - a status read must not fail the request
        return {}
    return out


def _load_registry(tenant_id: str) -> Any:
    try:
        return TenantRegistry.load(tenant_id=tenant_id)
    except RegistryCorrupt as exc:
        # Fail closed and say so: the operator must repair the file.  Returning an
        # empty registry here would invite a later save that overwrites it.
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"plugin registry unreadable: {type(exc).__name__}",
        ) from exc
    except PluginError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"plugin registry rejected: {exc}",
        ) from exc


def _entries(tenant_id: str) -> dict[str, _Entry]:
    """Merge the tenant's registry records with the process-wide runtime view."""
    merged: dict[str, _Entry] = {}
    registry = _load_registry(tenant_id)
    for plugin_id, record in registry.records.items():
        merged[plugin_id] = _Entry(plugin_id=plugin_id, record=record)

    for plugin_id, runtime in _runtime_entries().items():
        if plugin_id in merged:
            merged[plugin_id].runtime = runtime
        elif runtime.get("layer") in _GLOBAL_LAYERS:
            merged[plugin_id] = _Entry(plugin_id=plugin_id, runtime=runtime)
        # else: an installed-layer runtime object with no record in THIS tenant
        # belongs to another tenant — not ours to show.
    return merged


def _entry(tenant_id: str, plugin_id: str) -> _Entry:
    found = _entries(tenant_id).get(plugin_id)
    if found is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail="plugin not installed"
        )
    return found


def _health_map() -> dict[str, HealthOut]:
    """Live health of the loaded plugins.  Never raises.

    This calls into every registered plugin, under its circuit breaker (an open
    breaker is reported, not called).  The admin plane is an explicit operator
    surface behind a flag, so paying that on a request is the honest trade: a
    cached number would let the API report health it never measured.
    """
    try:
        from corvin_plugins import registry as runtime_registry

        return {
            plugin_id: HealthOut(ok=status.ok, message=status.message or "")
            for plugin_id, status in runtime_registry.health_check_all().items()
        }
    except Exception:  # noqa: BLE001
        return {}


def _to_out(entry: _Entry, health: dict[str, HealthOut]) -> AdminPluginOut:
    record, runtime = entry.record, entry.runtime
    return AdminPluginOut(
        plugin_id=entry.plugin_id,
        version=(record.version if record is not None else (runtime or {}).get("version", "")),
        display_name=(
            record.display_name
            if record is not None
            else (runtime or {}).get("display_name", entry.plugin_id)
        ),
        plugin_type=(
            record.plugin_type if record is not None else (runtime or {}).get("plugin_type", "")
        ),
        layer=entry.layer,
        origin=record.origin.value if record is not None else None,
        enabled=record.enabled if record is not None else entry.loaded,
        runtime_loaded=entry.loaded,
        can_disable=entry.can_disable,
        source=entry.source,
        health=health.get(entry.plugin_id),
    )


def _to_detail(entry: _Entry, health: dict[str, HealthOut]) -> AdminPluginDetailOut:
    base = _to_out(entry, health).model_dump()
    record = entry.record
    if record is None:
        # A runtime-only plugin has no settings surface: settings live in the
        # registry record, and inventing an empty schema would suggest one exists.
        return AdminPluginDetailOut(**base)
    return AdminPluginDetailOut(
        **base,
        pii_risk=record.pii_risk.value,
        locality=record.locality.value,
        network_egress=record.network_egress.value,
        egress_hosts=list(record.egress_hosts),
        requires_consent=record.consent_required(),
        settings=record.settings,
        settings_schema=record.settings_schema,
        dependencies=list(record.dependencies),
        replaces=record.replaces,
        installed_at=record.installed_at.isoformat() if record.installed_at else None,
        last_error_type=record.last_error_type,
    )


def _detail_after(tenant_id: str, plugin_id: str, previous: _Entry) -> AdminPluginDetailOut:
    """Re-read the plugin after a successful mutation and render the end state.

    A runtime-only plugin that was just unloaded is gone from BOTH sources, so a
    plain re-read would 404 on our own success.  The previous entry supplies the
    identity fields; ``loaded_override`` reports that it is no longer running.
    """
    refreshed = _entries(tenant_id).get(plugin_id)
    if refreshed is None:
        refreshed = _Entry(
            plugin_id=plugin_id,
            record=previous.record,
            runtime=previous.runtime,
            loaded_override=False,
        )
    return _to_detail(refreshed, _health_map())


def _lifecycle(tenant_id: str) -> Any:
    return PluginLifecycle(
        tenant_id=tenant_id,
        lifecycle_enabled=lambda: _feature_flags.is_enabled(LIFECYCLE_FLAG_ID, tenant_id),
    )


# ── Audit ─────────────────────────────────────────────────────────────────────
#
# Every mutating call lands in the console chain with tenant_id, the plugin id and
# the outcome.  The reason strings are a closed vocabulary of slugs, never
# ``str(exc)``: a plugin error message routinely carries a path or a host, and the
# chain is append-only, so an oversized or leaky record cannot be redacted later.
# Settings VALUES never appear here at all — ``PluginLifecycle.set_settings``
# records key NAMES only, and this surface adds nothing beyond the plugin id.


def _audit_ok(rec: Any, action: str, plugin_id: str, outcome: str) -> None:
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action=action,
        target_kind="plugin",
        target_id=f"{plugin_id}={outcome}",
    )


def _audit_denied(rec: Any, action: str, plugin_id: str, reason: str) -> None:
    console_audit.action_denied(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action=action,
        target_kind="plugin",
        target_id=plugin_id,
        reason=reason,
    )


def _audit_failed(rec: Any, action: str, plugin_id: str, reason: str) -> None:
    console_audit.action_failed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action=action,
        target_kind="plugin",
        target_id=plugin_id,
        reason=reason,
    )


def _failure_reason(exc: Exception) -> str:
    """A short, closed-vocabulary slug for the audit trail."""
    if isinstance(exc, LifecycleDisabled):
        return "lifecycle-disabled"
    if isinstance(exc, ConsentRequired):
        return "consent-required"
    if isinstance(exc, EgressNotDeclared):
        return "egress-not-declared"
    if isinstance(exc, PluginNotFound):
        return "not-installed"
    if isinstance(exc, (ValidationError, UnknownPluginType, InvalidPluginID)):
        return "invalid-input"
    if isinstance(exc, PluginError):
        return "refused"
    return "internal-error"


def _schema_detail(exc: Exception) -> str:
    """Render a settings rejection as KEY + CONSTRAINT, without the value.

    ``SettingsValidator`` forwards ``jsonschema``'s own message, and that message
    is the wrong shape for an operator-facing API in both directions: it echoes
    the rejected VALUE ("42 is not of type 'string'") while never naming the
    offending KEY, so a schema with ten properties gives no clue which one broke.

    The chained ``jsonschema.ValidationError`` carries both properly, so this
    reads them off the cause instead of validating a second time — one validation
    path, no drift.  ``validator_value`` comes from the plugin's SCHEMA, not from
    the request, so quoting it leaks nothing.
    """
    cause = exc.__cause__
    path = getattr(cause, "json_path", None)
    validator = getattr(cause, "validator", None)
    if not path or not validator:
        # No jsonschema (required-keys fallback) or an unexpected shape: that
        # message is already key-only ("required setting missing: channel").
        return str(exc)
    constraint = getattr(cause, "validator_value", None)
    if isinstance(constraint, (str, int, float, bool)) or (
        isinstance(constraint, list)
        and all(isinstance(v, (str, int, float, bool)) for v in constraint)
    ):
        return f"settings rejected at {path}: fails {validator}={constraint!r}"
    return f"settings rejected at {path}: fails the {validator} constraint"


def _mutation_error(exc: Exception) -> HTTPException:
    """Map a lifecycle exception to a status code without leaking internals."""
    if isinstance(exc, LifecycleDisabled):
        # 409, not 403: the caller is authorised, the installation is in a state
        # that does not accept the change.  A 500 here would read as a bug.
        return HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=(
                "runtime plugin changes are switched off — enable the "
                "plugin_runtime_lifecycle feature flag in Settings → Features"
            ),
        )
    if isinstance(exc, PluginNotFound):
        return HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail="plugin not installed"
        )
    # A rejected settings payload / plugin_type / plugin_id is unprocessable INPUT
    # (422), not a state conflict (409).  These inherit from PluginError, so
    # without this branch they would fall through to 409 below.
    if isinstance(exc, ValidationError):
        return HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_schema_detail(exc),
        )
    if isinstance(exc, (UnknownPluginType, InvalidPluginID)):
        return HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )
    if isinstance(exc, (ConsentRequired, EgressNotDeclared, PluginError)):
        return HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=str(exc))
    return HTTPException(
        status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=f"plugin operation failed: {type(exc).__name__}",
    )


# ── Read ──────────────────────────────────────────────────────────────────────


@router.get("/api/admin/plugins")
async def list_plugins(
    rec: Annotated[Any, Depends(require_plane)],
) -> dict[str, Any]:
    """Every plugin this tenant administers, from both sources."""
    entries = _entries(rec.tenant_id)
    health = _health_map()
    plugins = [_to_out(entries[pid], health) for pid in sorted(entries)]
    return {
        "plugins": plugins,
        "total": len(plugins),
        "tenant_id": rec.tenant_id,
        "lifecycle_enabled": _feature_flags.is_enabled(LIFECYCLE_FLAG_ID, rec.tenant_id),
    }


@router.get("/api/admin/plugins/{plugin_id}")
async def get_plugin(
    plugin_id: str,
    rec: Annotated[Any, Depends(require_plane)],
) -> AdminPluginDetailOut:
    return _to_detail(_entry(rec.tenant_id, plugin_id), _health_map())


@router.get("/api/admin/health")
async def aggregated_health(
    rec: Annotated[Any, Depends(require_plane)],
) -> dict[str, Any]:
    """Aggregated plugin health for this tenant.

    ``ok`` is true when nothing that could be checked reported a problem.  A
    plugin that is not loaded is ``checked: false`` rather than "healthy" — the
    admin plane must not report health it never measured.
    """
    entries = _entries(rec.tenant_id)
    health = _health_map()

    plugins: dict[str, Any] = {}
    healthy = unhealthy = unchecked = 0
    by_layer: dict[str, int] = {}
    for plugin_id in sorted(entries):
        entry = entries[plugin_id]
        status = health.get(plugin_id)
        if status is None:
            unchecked += 1
        elif status.ok:
            healthy += 1
        else:
            unhealthy += 1
        by_layer[entry.layer] = by_layer.get(entry.layer, 0) + 1
        plugins[plugin_id] = {
            "checked": status is not None,
            "ok": status.ok if status is not None else None,
            "message": status.message if status is not None else "",
            "layer": entry.layer,
            "runtime_loaded": entry.runtime is not None,
            "can_disable": entry.can_disable,
        }

    return {
        "ok": unhealthy == 0,
        "tenant_id": rec.tenant_id,
        "total": len(plugins),
        "healthy": healthy,
        "unhealthy": unhealthy,
        "unchecked": unchecked,
        "by_layer": by_layer,
        "plugins": plugins,
    }


# ── Mutations (CSRF-protected) ────────────────────────────────────────────────


@router.post("/api/admin/plugins/{plugin_id}/enable")
async def enable_plugin(
    plugin_id: str,
    rec: Annotated[Any, Depends(require_plane_csrf)],
    body: EnableIn | None = None,
) -> AdminPluginDetailOut:
    """Enable a plugin for this tenant (consent gate + L34/L35 gate apply)."""
    _require_lifecycle(rec.tenant_id)
    entry = _entry(rec.tenant_id, plugin_id)
    if entry.record is None:
        # Loaded by the boot path with no per-tenant record: there is no flag to
        # flip.  Saying 200 would claim an effect this call did not have.
        _audit_failed(rec, "admin.plugin_enable", plugin_id, "no-registry-record")
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=(
                f"{plugin_id} is loaded by the boot path and has no registry "
                f"record for this tenant; it cannot be enabled per tenant"
            ),
        )

    granted = "console" if (body and body.consent_granted) else None
    try:
        _lifecycle(rec.tenant_id).enable(plugin_id, consent_granted_by=granted)
    except Exception as exc:  # noqa: BLE001 - mapped to a status below
        _audit_failed(rec, "admin.plugin_enable", plugin_id, _failure_reason(exc))
        raise _mutation_error(exc) from exc

    _audit_ok(rec, "admin.plugin_enable", plugin_id, "enabled")
    return _detail_after(rec.tenant_id, plugin_id, entry)


@router.post("/api/admin/plugins/{plugin_id}/disable")
async def disable_plugin(
    plugin_id: str,
    rec: Annotated[Any, Depends(require_plane_csrf)],
) -> AdminPluginDetailOut:
    """Disable a plugin.  Refuses the compliance layer with 403 + an audit event.

    Two independent guards, deliberately not one:

    1. the merged layer view (record AND loaded object, fail-closed conjunction)
       answers 403 before anything is touched;
    2. the unload itself goes through ``registry.disable()``, the operator-
       initiated entry point, which re-checks the layer and raises
       ``PluginDisableRefused``.  ``registry.unregister()`` is deliberately NOT
       called here — that is the machinery path, and calling it directly is
       exactly the bypass the layer guard exists to prevent.

    The compliance guard runs BEFORE the lifecycle-flag gate, so the refusal is
    unconditional.  The other order would answer 409 "switch the
    plugin_runtime_lifecycle flag on" to someone asking whether the audit writer
    can be disabled — technically true about this request, and exactly the wrong
    thing to imply.
    """
    entry = _entry(rec.tenant_id, plugin_id)

    if not entry.can_disable:
        _audit_denied(rec, "admin.plugin_disable", plugin_id, "compliance-layer")
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail=(
                f"{plugin_id} is on the compliance layer and cannot be disabled "
                f"(GDPR Art. 30/32, EU AI Act Art. 50)"
            ),
        )

    _require_lifecycle(rec.tenant_id)

    # Unload first, persist second.  The order is what makes guard (2) reachable:
    # persisting first would hot-unload through the machinery path inside
    # PluginLifecycle.disable() and leave registry.disable() with nothing to
    # refuse — a dead compliance mechanism.  The cost is a narrow window: if the
    # persist step below is refused (an enabled dependent), the plugin is already
    # unloaded while the record still says enabled.  That divergence is visible —
    # `runtime_loaded: false, enabled: true` — and the call answers 409.
    if entry.runtime is not None:
        try:
            from corvin_plugins.registry import disable as runtime_disable

            runtime_disable(plugin_id)
        except PluginDisableRefused as exc:
            _audit_denied(rec, "admin.plugin_disable", plugin_id, "compliance-layer")
            raise HTTPException(
                status_code=http_status.HTTP_403_FORBIDDEN,
                detail=(
                    f"{plugin_id} is on the compliance layer and cannot be "
                    f"disabled (GDPR Art. 30/32, EU AI Act Art. 50)"
                ),
            ) from exc
        except PluginNotFound:
            # Unloaded between the read and here — the desired end state already.
            pass
        except Exception as exc:  # noqa: BLE001
            _audit_failed(rec, "admin.plugin_disable", plugin_id, _failure_reason(exc))
            raise _mutation_error(exc) from exc

    if entry.record is not None:
        try:
            _lifecycle(rec.tenant_id).disable(plugin_id)
        except Exception as exc:  # noqa: BLE001
            _audit_failed(rec, "admin.plugin_disable", plugin_id, _failure_reason(exc))
            raise _mutation_error(exc) from exc

    _audit_ok(rec, "admin.plugin_disable", plugin_id, "disabled")
    return _detail_after(rec.tenant_id, plugin_id, entry)


@router.put("/api/admin/plugins/{plugin_id}/config")
async def set_config(
    plugin_id: str,
    body: ConfigIn,
    rec: Annotated[Any, Depends(require_plane_csrf)],
) -> AdminPluginDetailOut:
    """Replace a plugin's settings, validated against its own JSON Schema.

    A rejected payload answers 422 with the offending key plus the violated
    constraint (never the value) and leaves the stored settings untouched.

    The compliance layer is refused here for the same reason as in ``disable``:
    its configuration is immutable by design.  "Where does the audit writer
    write" is not an operator setting, and a route that refuses to switch the
    mechanism off while happily letting it be reconfigured would be the same
    hole with an extra step.  Like ``disable``, that refusal runs before the
    lifecycle-flag gate so it is unconditional.
    """
    entry = _entry(rec.tenant_id, plugin_id)
    if not entry.can_disable:
        _audit_denied(rec, "admin.plugin_config", plugin_id, "compliance-layer")
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail=(
                f"{plugin_id} is on the compliance layer; its configuration is "
                f"immutable (GDPR Art. 30/32, EU AI Act Art. 50)"
            ),
        )

    _require_lifecycle(rec.tenant_id)
    if entry.record is None:
        _audit_failed(rec, "admin.plugin_config", plugin_id, "no-registry-record")
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=(
                f"{plugin_id} is loaded by the boot path and has no registry "
                f"record for this tenant; it has no settings to write"
            ),
        )

    # Validate before taking the registry lock, so a bad payload never reaches
    # the mutation path at all.  set_settings() validates again — that second
    # check is the one that protects callers who do not come through here.
    try:
        SettingsValidator(entry.record.settings_schema).validate(body.settings)
    except ValidationError as exc:
        _audit_failed(rec, "admin.plugin_config", plugin_id, "invalid-input")
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_schema_detail(exc),
        ) from exc

    try:
        _lifecycle(rec.tenant_id).set_settings(plugin_id, body.settings)
    except Exception as exc:  # noqa: BLE001
        _audit_failed(rec, "admin.plugin_config", plugin_id, _failure_reason(exc))
        raise _mutation_error(exc) from exc

    # Key NAMES only — a settings value can be a token or a webhook URL, and the
    # audit chain cannot be redacted afterwards.
    _audit_ok(rec, "admin.plugin_config", plugin_id, "configured")
    return _detail_after(rec.tenant_id, plugin_id, entry)
