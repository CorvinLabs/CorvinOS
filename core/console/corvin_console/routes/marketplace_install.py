"""
Marketplace Installation API — real builtin installation (ADR-0511 / ADR-0247).

Mounted UNDER ``routes/marketplace.py``'s router, whose prefix is
``/api/v1/marketplace`` — so this router carries NO prefix of its own. (Both
routers used to declare the prefix, which doubled it to
``/api/v1/marketplace/api/v1/marketplace/...`` — unreachable for the SPA,
reachable for anyone else. Adversarial review E-03, 2026-09-03.)

Effective paths (under ``/v1/console``):
- POST  /api/v1/marketplace/plugins/{id}/install
- POST  /api/v1/marketplace/plugins/{id}/uninstall
- PATCH /api/v1/marketplace/plugins/{id}/enable
- PATCH /api/v1/marketplace/plugins/{id}/disable
- GET   /api/v1/marketplace/install/{job_id}/progress

What "install" REALLY does now (builtin scope):
* the ``{id}`` is a marketplace index-id (``plugin:buildin-<cat>-<name>``); it is
  checked against the loaded marketplace index, then resolved to a local builtin
  source directory (``marketplace_resolve``);
* the ADR-0247 manifest gate runs on that directory — it is NOT bypassed;
* the manifest is projected onto a ``PluginRecord`` whose ``origin`` is a
  LOCATION fact (``builtin`` under the in-wheel root, ``vetted`` under the
  Corvin-Marketplace checkout — never a manifest claim) and ``boot_layer=installed``;
* ``PluginLifecycle(tenant).install(record)`` writes the tenant-scoped
  ``registry.yaml`` — which is what ``GET /api/v1/plugins`` lists from.

Out of scope (stated, not silently skipped): remote download / install of
community (contributor-tier) plugins. Those have no local source to resolve and
would need a signed-artifact fetch path; ``install`` FAILS them with a clear
reason rather than pretending.

Security contract:
* mutations → ``require_csrf``; the progress read → ``require_session``.
* tenant ONLY from ``rec.tenant_id`` — never from the request body.
* every mutation is audited (``console.action_performed``).
* ``_install_jobs`` is BOUNDED (``_MAX_JOBS``, oldest evicted) and every job
  is bound to the tenant that created it — another tenant polling the job id
  gets 404.
* ``plugin_id`` is validated against a closed character class.
* builtin origin is location-derived; a community plugin cannot claim it.
"""
from __future__ import annotations

import logging
import re
import threading
import uuid
from collections import OrderedDict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException

from .. import audit as console_audit
from .. import auth as session_auth
from .. import feature_flags as _feature_flags
from ..deps import require_csrf, require_session
from . import marketplace_resolve as _resolve

# Licensing gate (ADR-0700, ADR-0701, ADR-0703)
try:
    from corvin_operator.license.capability_api import (
        require_capability,
        LicenseDenied,
        get_tier
    )
    _LICENSING_AVAILABLE = True
except ImportError:
    _LICENSING_AVAILABLE = False

log = logging.getLogger(__name__)

router = APIRouter(tags=["marketplace-install"])

# Job tracking (in-memory, bounded). The install itself is synchronous — the job
# already carries its FINAL status when the POST returns; the progress endpoint
# just reads it back (the SPA polls once, sees COMPLETED/FAILED, and stops).
_MAX_JOBS = 256
_install_jobs: "OrderedDict[str, InstallJob]" = OrderedDict()
_jobs_lock = threading.Lock()

_PLUGIN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")

# corvin_plugins lifecycle — imported the same guarded way routes/plugins.py does.
try:
    from corvin_plugins.state import (  # type: ignore[import-not-found]
        LifecycleDisabled,
        PluginLifecycle,
    )
    from corvin_plugins.manifest import PluginError  # type: ignore[import-not-found]

    _LIFECYCLE_AVAILABLE = True
except ImportError:  # pragma: no cover - stripped install without core/plugins
    _LIFECYCLE_AVAILABLE = False


class JobStatus(str, Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    INSTALLING = "installing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class InstallJob:
    """Tracks the outcome of a plugin installation."""
    job_id: str
    plugin_id: str
    tenant_id: str
    status: JobStatus
    progress: int  # 0-100
    message: str
    created_at: str
    updated_at: str
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_plugin_id(plugin_id: str) -> str:
    if not _PLUGIN_ID_RE.match(plugin_id or ""):
        raise HTTPException(status_code=400, detail="invalid plugin id")
    return plugin_id


def _remember(job: InstallJob) -> None:
    with _jobs_lock:
        _install_jobs[job.job_id] = job
        while len(_install_jobs) > _MAX_JOBS:
            _install_jobs.popitem(last=False)  # evict oldest


def _audit(rec: session_auth.SessionRecord, action: str, plugin_id: str) -> None:
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action=action,
        target_kind="marketplace_plugin",
        target_id=plugin_id,
    )


def _lifecycle(tenant_id: str):
    """A ``PluginLifecycle`` gated on the ``plugin_runtime_lifecycle`` flag."""
    return PluginLifecycle(
        tenant_id=tenant_id,
        lifecycle_enabled=lambda: _feature_flags.is_enabled(
            "plugin_runtime_lifecycle", tenant_id
        ),
    )


def _is_utility_plugin(plugin_id: str) -> bool:
    """
    Determine if a plugin is a utility (free tier allowed) or requires forge.create.

    Utility plugins: integrations, datasources, analyzers, etc.
    Generator plugins: Forge, SkillForge, Plugin-Builder (require forge.create)

    Plugin ID format: "plugin:buildin-<category>-<name>"
    Categories that require forge.create: "generator", "forge", "skillforge", "plugin-builder"
    """
    parsed = _resolve.parse_index_id(plugin_id)
    if parsed is None:
        return False
    _tier, category, _name = parsed

    # List of categories that require forge.create (member-only)
    member_only_categories = [
        "generator",
        "forge",
        "skillforge",
        "plugin-builder",
        "skill-creator"
    ]

    # If category is in member-only list, it's not a utility plugin
    return category not in member_only_categories


def _index_has(plugin_id: str) -> bool:
    """True when the marketplace index knows this id (install source of truth)."""
    from . import marketplace as _mkt

    try:
        index = _mkt._index_manager.get_index()
    except Exception:  # noqa: BLE001 - a broken index must not 500 the install
        return False
    return plugin_id in (index.get("by_id") or {})


#: The install's phases, each a REAL step with its own failure mode, recorded on
#: the job as it is reached (progress = the phase's share). The SPA renders the
#: bar from these — it never advances a bar on its own (ADR-0763: no progress
#: theatre). Percentages are the share of wall-clock a phase takes on a cold
#: cache, rounded to what an operator can read.
_PHASES: tuple[tuple[str, int], ...] = (
    ("Checking the marketplace index", 10),
    ("Resolving the local source", 25),
    ("Validating the manifest (ADR-0247 gate)", 45),
    ("Checking the licence", 60),
    ("Projecting the record", 70),
    ("Registering with the tenant", 90),
)


class _LicenseRefused(Exception):
    def __init__(self, detail: Dict[str, Any]) -> None:
        super().__init__("license_required")
        self.detail = detail


def _run_install(job: InstallJob, rec: session_auth.SessionRecord, plugin_id: str, version: str) -> Dict[str, Any]:
    """Perform the install, advancing ``job`` phase by phase. Returns the
    response document; raises :class:`_LicenseRefused` for the 403 path."""

    def _phase(i: int) -> None:
        job.status = JobStatus.INSTALLING
        job.message, job.progress = _PHASES[i]
        job.updated_at = _now()
        _remember(job)

    def _fail(reason: str) -> Dict[str, Any]:
        job.status = JobStatus.FAILED
        job.progress = 100
        job.message = "Installation failed"
        job.error = reason
        job.updated_at = _now()
        _remember(job)
        _audit(rec, "marketplace.install_failed", plugin_id)
        return {"status": "failed", "job_id": job.job_id, "plugin_id": plugin_id, "error": reason}

    def _done(message: str, extra: Dict[str, Any]) -> Dict[str, Any]:
        job.status = JobStatus.COMPLETED
        job.progress = 100
        job.message = message
        job.updated_at = _now()
        _remember(job)
        _audit(rec, "marketplace.install", plugin_id)
        return {"status": "completed", "job_id": job.job_id, "plugin_id": plugin_id, **extra}

    if not _LIFECYCLE_AVAILABLE or not _resolve.available():
        return _fail("plugin subsystem unavailable in this installation")

    # 1. The index is the install allowlist: an id it does not know is not
    #    installable here (no arbitrary local paths).
    _phase(0)
    if not _index_has(plugin_id):
        return _fail(f"{plugin_id} is not in the marketplace index")

    # 2. Resolve to a local source dir + manifest under a trusted root
    #    (buildin → vetted, contributor → community; ADR-0892).
    _phase(1)
    try:
        plugin_dir, manifest = _resolve.load_manifest(plugin_id)
    except _resolve.MarketplaceResolveError as exc:
        return _fail(str(exc))

    # 3. ADR-0247 manifest gate — NOT bypassed.
    _phase(2)
    report = _resolve.validate_builtin_manifest(plugin_dir)
    if not report.ok:
        return _fail(
            "manifest failed the ADR-0247 gate: "
            + "; ".join(f.message for f in report.errors[:3])
        )

    # 3.5. LICENSING GATE (ADR-0700, ADR-0701, ADR-0703) — some plugins require
    # forge.create (member tier).
    _phase(3)
    if _LICENSING_AVAILABLE:
        try:
            if not _is_utility_plugin(plugin_id):
                require_capability(
                    "forge.create",
                    requested=1,
                    tenant_id=rec.tenant_id,
                    entry_point="http"
                )
        except LicenseDenied as exc:
            job.status = JobStatus.FAILED
            job.progress = 100
            job.message = "Installation refused by the licence"
            job.error = "license_required"
            job.updated_at = _now()
            _remember(job)
            _audit(rec, "marketplace.install_denied_license", plugin_id)
            raise _LicenseRefused({
                "error": "license_required",
                "capability": exc.capability,
                "tier": exc.tier,
                "reason": exc.reason,
                "upgrade_url": exc.upgrade_url
            })

    # 4. Project onto a record (origin is location-derived) and install.
    _phase(4)
    try:
        record = _resolve.record_from_manifest(manifest, plugin_dir=plugin_dir)
    except Exception as exc:  # noqa: BLE001 - malformed manifest values
        return _fail(f"invalid manifest: {type(exc).__name__}: {exc}")

    _phase(5)
    try:
        _lifecycle(rec.tenant_id).install(record, installed_by="console")
    except LifecycleDisabled:
        return _fail(
            "runtime plugin changes are switched off — enable "
            "plugin_runtime_lifecycle in Settings → Features"
        )
    except PluginError as exc:
        # "already installed" is idempotent success, not a failure.
        if "already installed" in str(exc):
            return _done("Already installed", {"registry_id": record.plugin_id, "already_installed": True})
        return _fail(str(exc))
    except Exception as exc:  # noqa: BLE001 - mapped to a failed job
        return _fail(f"install failed: {type(exc).__name__}")

    return _done("Installation completed", {
        "registry_id": record.plugin_id,
        "version": version,
        "tenant_id": rec.tenant_id,
        "origin": record.origin.value,
        "requires_consent": record.consent_required(),
    })


@router.post("/plugins/{plugin_id}/install")
async def install_plugin(
    plugin_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
    body: Optional[Dict[str, Any]] = Body(None),
) -> Dict[str, Any]:
    """Install a marketplace plugin into the tenant's ``registry.yaml``.

    Request: ``{"version": "1.0.0", "wait": true}`` (a ``tenant_id`` in the body
    is ignored — the tenant is the authenticated session's).

    ``wait`` (default true): the install runs to completion and the response
    carries the job's FINAL status. ``wait: false`` (the SPA's progress bar):
    the response is the job in its first phase and the install continues on a
    worker thread; ``GET /install/{job_id}/progress`` reports each phase as it
    is reached — real steps, not a timer.
    """
    plugin_id = _validate_plugin_id(plugin_id)
    body = body or {}
    version = str(body.get("version", "1.0.0"))[:64]
    wait = bool(body.get("wait", True))

    job_id = f"install_{uuid.uuid4().hex[:12]}"
    now = _now()
    job = InstallJob(
        job_id=job_id,
        plugin_id=plugin_id,
        tenant_id=rec.tenant_id,
        status=JobStatus.PENDING,
        progress=0,
        message="Queued",
        created_at=now,
        updated_at=now,
    )
    _remember(job)

    if wait:
        try:
            return _run_install(job, rec, plugin_id, version)
        except _LicenseRefused as exc:
            raise HTTPException(status_code=403, detail=exc.detail)

    def _worker() -> None:
        try:
            _run_install(job, rec, plugin_id, version)
        except _LicenseRefused:
            pass  # the job already carries error=license_required
        except Exception as exc:  # noqa: BLE001 — the job must never stay "installing"
            job.status = JobStatus.FAILED
            job.progress = 100
            job.message = "Installation failed"
            job.error = f"install failed: {type(exc).__name__}"
            job.updated_at = _now()
            _remember(job)

    threading.Thread(target=_worker, name=f"marketplace-install-{job_id}", daemon=True).start()
    return job.to_dict()


@router.get("/install/{job_id}/progress")
async def get_install_progress(
    job_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> Dict[str, Any]:
    """Poll installation progress (tenant-bound: another tenant's job is 404).

    Reports the phase the install has REACHED (``_PHASES``) — a worker thread
    advances it for a ``wait: false`` install; a synchronous install already
    carries its final status. Never a timer-driven bar.
    """
    with _jobs_lock:
        job = _install_jobs.get(job_id)
    if job is None or job.tenant_id != rec.tenant_id:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.to_dict()


def _registry_id_or_400(plugin_id: str) -> str:
    """Map an index-id to its registry key; pass a plain plugin_id through."""
    try:
        return _resolve.manifest_plugin_id(plugin_id)
    except _resolve.MarketplaceResolveError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _mutation_status(exc: Exception) -> int:
    if _LIFECYCLE_AVAILABLE and isinstance(exc, LifecycleDisabled):
        return 403
    if _LIFECYCLE_AVAILABLE and isinstance(exc, PluginError):
        # PluginNotFound is a PluginError subclass in protocol.py; treat "not
        # installed" as 404 and every other state conflict as 409.
        return 404 if "not" in type(exc).__name__.lower() else 409
    return 500


@router.post("/plugins/{plugin_id}/uninstall")
async def uninstall_plugin(
    plugin_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
    body: Optional[Dict[str, Any]] = Body(None),
) -> Dict[str, Any]:
    """Uninstall a plugin — delegates to ``PluginLifecycle.uninstall`` (real)."""
    plugin_id = _validate_plugin_id(plugin_id)
    if not _LIFECYCLE_AVAILABLE:
        raise HTTPException(status_code=503, detail="plugin subsystem unavailable")
    registry_id = _registry_id_or_400(plugin_id)
    try:
        _lifecycle(rec.tenant_id).uninstall(registry_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=_mutation_status(exc), detail=str(exc)) from exc
    _audit(rec, "marketplace.uninstall", plugin_id)
    # The audit trail outlives the plugin (GDPR Art. 30) — say so.
    return {
        "status": "completed",
        "plugin_id": plugin_id,
        "registry_id": registry_id,
        "audit_retained": True,
    }


@router.patch("/plugins/{plugin_id}/enable")
async def enable_plugin(
    plugin_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
    body: Optional[Dict[str, Any]] = Body(None),
) -> Dict[str, Any]:
    """Enable a plugin — delegates to ``PluginLifecycle.enable`` (real)."""
    plugin_id = _validate_plugin_id(plugin_id)
    if not _LIFECYCLE_AVAILABLE:
        raise HTTPException(status_code=503, detail="plugin subsystem unavailable")
    registry_id = _registry_id_or_400(plugin_id)
    granted = "console" if (body and body.get("consent_granted")) else None
    try:
        rec_out = _lifecycle(rec.tenant_id).enable(registry_id, consent_granted_by=granted)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=_mutation_status(exc), detail=str(exc)) from exc
    _audit(rec, "marketplace.enable", plugin_id)
    return {"status": "enabled", "plugin_id": plugin_id, "registry_id": registry_id,
            "enabled": rec_out.enabled}


@router.patch("/plugins/{plugin_id}/disable")
async def disable_plugin(
    plugin_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> Dict[str, Any]:
    """Disable a plugin — delegates to ``PluginLifecycle.disable`` (real)."""
    plugin_id = _validate_plugin_id(plugin_id)
    if not _LIFECYCLE_AVAILABLE:
        raise HTTPException(status_code=503, detail="plugin subsystem unavailable")
    registry_id = _registry_id_or_400(plugin_id)
    try:
        rec_out = _lifecycle(rec.tenant_id).disable(registry_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=_mutation_status(exc), detail=str(exc)) from exc
    _audit(rec, "marketplace.disable", plugin_id)
    return {"status": "disabled", "plugin_id": plugin_id, "registry_id": registry_id,
            "enabled": rec_out.enabled}
