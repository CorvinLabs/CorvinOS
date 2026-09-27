"""
Control Plane Routes — Plugin Management Stream 1.

GET    /v1/console/control-plane/plugins
GET    /v1/console/control-plane/plugins/<id>
PATCH  /v1/console/control-plane/plugins/<id>/enable
PATCH  /v1/console/control-plane/plugins/<id>/disable
DELETE /v1/console/control-plane/plugins/<id>
GET    /v1/console/control-plane/plugins/audit-log

ADR-2029: User-Centric CorvinOS Control Plane — Stream 1

There is deliberately NO install route here. ADR-0892 allows exactly one plugin
install path: ``POST /api/v1/marketplace/plugins/{id}/install``
(``marketplace_install.py``), which resolves real source from the marketplace
checkout, runs the manifest + licence gates and writes the tenant registry. The
``PUT /install`` that lived here recorded an operator-typed id/name/version in a
separate store and installed no code — a second install path, removed
2026-09-26. Guard: ``tests/e2e/test_marketplace_single_install_route.py``.

DEFUSED 2026-09-27 (adversarial review): this router was mounted but served a SECOND plugin registry (``~/.corvin/plugins.json``, hard-wired past ``CORVIN_HOME``) that nothing loads plugins from — enable/disable/uninstall reported success while the real registry (``corvin_plugins``, served by ``routes/plugins.py``) was untouched, and its "audit log" was an in-memory list. It also had no router-level session guard. Every route now requires a console session (CSRF on mutations) and answers 501 ``not_implemented``; the real surface is ``/v1/console/plugins``. The backing module under ``corvin_console/control_plane/`` is kept, unrouted.
"""


from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from corvin_console.deps import require_session_csrf_on_mutation

router = APIRouter(
    prefix="/control-plane/plugins",
    tags=["control-plane"],
    dependencies=[Depends(require_session_csrf_on_mutation)],
)

_REASON = 'the real surface is /v1/console/plugins'


def _not_implemented() -> HTTPException:
    return HTTPException(status_code=501, detail={"status": "not_implemented", "reason": _REASON})


@router.get("")
async def list_plugins() -> Dict[str, Any]:
    raise _not_implemented()


@router.get("/audit-log")
async def get_audit_log() -> Dict[str, Any]:
    raise _not_implemented()


@router.get("/{plugin_id}")
async def get_plugin(plugin_id: str) -> Dict[str, Any]:
    raise _not_implemented()


@router.patch("/{plugin_id}/enable")
async def enable_plugin(plugin_id: str) -> Dict[str, Any]:
    raise _not_implemented()


@router.patch("/{plugin_id}/disable")
async def disable_plugin(plugin_id: str) -> Dict[str, Any]:
    raise _not_implemented()


@router.delete("/{plugin_id}")
async def uninstall_plugin(plugin_id: str) -> Dict[str, Any]:
    raise _not_implemented()
