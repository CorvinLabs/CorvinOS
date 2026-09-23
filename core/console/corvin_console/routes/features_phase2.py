"""Phase 2 Feature APIs — licensing audit, monitoring metrics, model list.

Every endpoint here answered with invented data until 2026-09-16 (ADR-0856).
The shape was always the same: a ``try`` block that could not succeed, and an
``except`` that returned plausible constants.

  * ``/models/available`` imported ``core.operator.bridges.shared.engine_switch``
    — a module path that does not exist (it is ``corvin_operator``) for a symbol,
    ``ENGINE_COSTS``, that exists nowhere in the repo. So the fallback ran on
    EVERY request and served a two-model list with ``cost_per_1k: 0.015`` for
    Opus 5 (neither its input rate, $0.005/1k, nor its output rate, $0.025/1k)
    and ``latency_ms: 50``, a number nothing has ever measured.
  * ``/monitoring/metrics`` imported ``core.orchestration.brain``, which does
    not exist either, and fell back to a block commented "Fallback demo
    metrics": 125 ms latency, 2 % errors, 0.82 convergence.
  * ``/licensing/audit-events`` called ``EventStore()`` with no tenant, awaited
    a synchronous method, and derived ``granted``/``denied`` from
    ``e.signal > 0.7`` where ``signal`` is a dict.
  * ``POST /models/config`` and ``POST /marketplace/install`` returned
    ``success: True`` and persisted nothing.

ADR-0763: a route that cannot answer returns an EMPTY result and says so. It
never returns sample data, because a plausible number is indistinguishable from
a measured one once it is on screen.

Where a real owner already exists, these routes name it instead of growing a
second writer: engine/model config is owned by /v1/console/settings/engine, and
marketplace install by /v1/console/api/v1/marketplace/.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status as http_status

from .. import auth as session_auth
from ..deps import require_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["phase2-features"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────────────────
# Licensing audit events
# ─────────────────────────────────────────────────────────────────────────

@router.get("/licensing/audit-events")
async def get_audit_events(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    limit: int = 100,
    status: Optional[str] = None,
) -> dict[str, Any]:
    """Licensing-relevant learning events for THIS tenant, from the event store.

    Tenant comes from the authenticated session, never a literal. The previous
    version passed the string "default" — which is not this install's tenant
    (``_default``) — to a store constructed with no tenant at all.
    """
    limit = max(1, min(int(limit), 1000))
    try:
        from core.learning.event_store import EventStore  # noqa: PLC0415
        from core.paths.tenant import tenant_home  # noqa: PLC0415

        store = EventStore(tenant_home(rec.tenant_id), tenant_id=rec.tenant_id)
        events = store.query_events(rec.tenant_id, limit=limit, newest_first=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("licensing audit events unavailable: %s", exc)
        return {
            "events": [], "total": 0, "available": False,
            "detail": "Event store not available on this build.",
            "timestamp": _now(),
        }

    rows = []
    for e in events:
        # `signal` is a dict for outcome records and None for many others. The
        # old code compared it to 0.7 and labelled the result granted/denied —
        # a verdict invented from a type error waiting to happen.
        signal = getattr(e, "signal", None)
        outcome = None
        if isinstance(signal, dict):
            if "success" in signal:
                outcome = "success" if signal["success"] else "failure"
            elif "status" in signal:
                outcome = str(signal["status"])

        rows.append({
            "id": getattr(e, "event_id", "") or "",
            "timestamp": getattr(e, "timestamp", "") or "",
            "event_type": getattr(getattr(e, "event_type", None), "value", "") or "",
            "skill_id": getattr(e, "skill_id", "") or "",
            "outcome": outcome,
            "lom": getattr(e, "lom", "") or "",
            "audit_ref": getattr(e, "audit_ref", None),
        })

    if status:
        rows = [r for r in rows if r["outcome"] == status]

    return {
        "events": rows,
        "total": len(rows),
        "available": True,
        "tenant_id": rec.tenant_id,
        "timestamp": _now(),
        # The disk record is content-free by construction and carries an
        # audit_ref into the hash chain; no payload is echoed here.
        "compliance": "content-free projection of the tenant event store",
    }


# ─────────────────────────────────────────────────────────────────────────
# Monitoring metrics
# ─────────────────────────────────────────────────────────────────────────

@router.get("/monitoring/metrics")
async def get_metrics(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    range: str = "1h",
) -> dict[str, Any]:
    """Metrics that are actually measured on this install.

    Returns only what a real source reports. Skill latency, error rate and
    convergence are NOT among them on this build: the module the old code read
    them from (``core.orchestration.brain``) does not exist, so every value it
    ever showed came from its own fallback constants.
    """
    metrics: list[dict[str, Any]] = []
    sources: list[str] = []

    # Learning event volume — real counts from the tenant's event store.
    try:
        from core.learning.event_store import EventStore  # noqa: PLC0415
        from core.learning.learning_events import EventType  # noqa: PLC0415
        from core.paths.tenant import tenant_home  # noqa: PLC0415

        store = EventStore(tenant_home(rec.tenant_id), tenant_id=rec.tenant_id)
        for et, label in ((EventType.SKILL_EXECUTED, "skill_executions"),
                          (EventType.OUTCOME, "task_outcomes"),
                          (EventType.FEEDBACK, "operator_feedback")):
            metrics.append({
                "name": label,
                "value": store.count_events(rec.tenant_id, event_type=et),
                "unit": "events",
                "status": "ok",
                "source": "learning.event_store",
            })
        sources.append("learning.event_store")
    except Exception as exc:  # noqa: BLE001
        logger.info("learning metrics unavailable: %s", exc)

    # Plugin health — real collector state.
    try:
        from corvin_plugins import health as plugin_health  # noqa: PLC0415

        snap = plugin_health.snapshot() if hasattr(plugin_health, "snapshot") else None
        if isinstance(snap, dict) and isinstance(snap.get("plugins"), dict):
            plugins = snap["plugins"]
            healthy = sum(1 for p in plugins.values() if isinstance(p, dict) and p.get("ok"))
            metrics.append({
                "name": "plugins_healthy",
                "value": healthy,
                "unit": "plugins",
                "status": "ok" if healthy == len(plugins) else "warning",
                "source": "corvin_plugins.health",
                "total": len(plugins),
            })
            sources.append("corvin_plugins.health")
    except Exception as exc:  # noqa: BLE001
        logger.info("plugin health metrics unavailable: %s", exc)

    return {
        "metrics": metrics,
        "alerts": [],
        "available": bool(metrics),
        "sources": sources,
        "detail": "" if metrics else "No metric source is available on this build.",
        "range": range,
        "timestamp": _now(),
    }


# ─────────────────────────────────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────────────────────────────────

@router.get("/models/available")
async def get_models(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Every model the engine registry declares, with its published price.

    Source is ``engine_models.registry_as_dict()`` — the same YAML registry
    /v1/console/models/registry and the engine settings serve, so this list
    cannot disagree with them. Prices come from ``model_price_per_1k``, the
    one rate card the cost panels bill against.

    Input and output rates are reported SEPARATELY. They differ by 5x on every
    current model, so a single "cost_per_1k" cannot be correct for both, and
    the value this endpoint used to publish for Opus 5 (0.015) was neither.

    No latency figure: nothing on this install measures per-model latency, and
    the previous constants (50/20/10 ms) were invented.
    """
    try:
        import sys  # noqa: PLC0415
        from pathlib import Path  # noqa: PLC0415

        shared = str(Path(__file__).resolve().parents[4]
                     / "corvin_operator" / "bridges" / "shared")
        if shared not in sys.path:
            sys.path.insert(0, shared)
        import engine_models  # type: ignore  # noqa: PLC0415

        from core.learning.model_selection_learner import (  # noqa: PLC0415
            model_price_per_1k,
        )

        registry = engine_models.registry_as_dict(force_reload=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("model registry unavailable: %s", exc)
        # ADR-0763: empty + a reason, never a sample list.
        return {
            "models": [], "total": 0, "available": False,
            "detail": "Engine model registry is not available on this build.",
            "timestamp": _now(),
        }

    by_id: dict[str, dict[str, Any]] = {}
    for engine_id, engine in (registry or {}).items():
        if not isinstance(engine, dict):
            continue
        for field, turn in (("os_models", "os"), ("worker_models", "worker")):
            for entry in engine.get(field) or []:
                if not isinstance(entry, dict):
                    continue
                mid = (entry.get("id") or "").strip()
                if not mid:
                    # The empty id is the registry's "adaptive / engine
                    # default" sentinel, not a model.
                    continue
                row = by_id.setdefault(mid, {
                    "id": mid,
                    "name": entry.get("label") or mid,
                    "engines": [],
                    "turns": [],
                })
                if engine_id not in row["engines"]:
                    row["engines"].append(engine_id)
                if turn not in row["turns"]:
                    row["turns"].append(turn)

    models = []
    for mid, row in by_id.items():
        price = model_price_per_1k(mid)
        models.append({
            **row,
            # null, not 0.0 — an unpriced model must not read as free.
            "input_usd_per_1k": price[0] if price else None,
            "output_usd_per_1k": price[1] if price else None,
            "priced": price is not None,
        })
    models.sort(key=lambda m: m["id"])

    return {
        "models": models,
        "total": len(models),
        "available": True,
        "unpriced": [m["id"] for m in models if not m["priced"]],
        "timestamp": _now(),
    }


@router.get("/models/config")
async def get_model_config(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """The tenant's REAL engine/model pins (ADR-0759).

    Was a hardcoded ``{"default_model": "claude-opus-5", "cost_threshold": 0.5}``
    that read nothing, so the panel showed "claude-opus-5" no matter what the
    tenant actually ran.
    """
    try:
        import sys  # noqa: PLC0415
        from pathlib import Path  # noqa: PLC0415

        shared = str(Path(__file__).resolve().parents[4]
                     / "corvin_operator" / "bridges" / "shared")
        if shared not in sys.path:
            sys.path.insert(0, shared)
        import engine_models  # type: ignore  # noqa: PLC0415

        default_engine = engine_models.get_tenant_default_engine(rec.tenant_id) \
            if hasattr(engine_models, "get_tenant_default_engine") else None
        pins = {}
        for turn in ("os_model", "worker_model"):
            try:
                pins[turn] = engine_models.get_tenant_engine_model(
                    rec.tenant_id, default_engine or "claude_code", turn)
            except Exception:  # noqa: BLE001
                pins[turn] = None
    except Exception as exc:  # noqa: BLE001
        logger.warning("model config unavailable: %s", exc)
        return {
            "config": {}, "available": False,
            "detail": "Engine model configuration is not readable on this build.",
            "timestamp": _now(),
        }

    return {
        "config": {
            "tenant_id": rec.tenant_id,
            "default_engine": default_engine,
            **pins,
        },
        "available": True,
        "timestamp": _now(),
    }


@router.post("/models/config", status_code=http_status.HTTP_501_NOT_IMPLEMENTED)
async def save_model_config(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    default_model: str,
    cost_threshold: float = 0.5,
) -> dict[str, Any]:
    """Not a writer. Engine/model config has exactly one.

    This returned ``{"success": True, "config": {...}}`` and wrote nothing —
    the operator changed a model, saw it confirmed, and the next run used the
    old one.

    ``/v1/console/settings/engine`` owns ``spec.engine_models`` in
    tenant.corvin.yaml, including the 0o600 mode the gateway reads fail-closed
    (ADR-0759). A second writer would be a second way to get that mode wrong.
    """
    raise HTTPException(
        status_code=http_status.HTTP_501_NOT_IMPLEMENTED,
        detail=("Model configuration is not written here. Use "
                "POST /v1/console/settings/engine, which owns spec.engine_models "
                "in tenant.corvin.yaml and its fail-closed file mode."),
    )


# ─────────────────────────────────────────────────────────────────────────
# Marketplace — the real implementation lives under /api/v1/marketplace
# ─────────────────────────────────────────────────────────────────────────

_MARKETPLACE_DETAIL = (
    "Not served here. The marketplace API is /v1/console/api/v1/marketplace/ "
    "(index, plugins, search, install), which reads the real catalogue and "
    "installed set."
)


@router.get("/marketplace/skills", status_code=http_status.HTTP_501_NOT_IMPLEMENTED)
async def list_marketplace_skills(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Was a two-entry literal ({skill-forge, datahub}) unrelated to the catalogue."""
    raise HTTPException(status_code=http_status.HTTP_501_NOT_IMPLEMENTED,
                        detail=_MARKETPLACE_DETAIL)


@router.get("/marketplace/skills/{skill_id}", status_code=http_status.HTTP_501_NOT_IMPLEMENTED)
async def get_marketplace_skill(
    skill_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Was a generator: ANY id returned a 200 describing a skill that need not exist,
    with a fabricated source_url on a domain this install never contacts."""
    raise HTTPException(status_code=http_status.HTTP_501_NOT_IMPLEMENTED,
                        detail=_MARKETPLACE_DETAIL)


@router.post("/marketplace/install", status_code=http_status.HTTP_501_NOT_IMPLEMENTED)
async def install_marketplace_skill(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    skill_id: str,
    version: str = "latest",
) -> dict[str, Any]:
    """Was ``{"success": True, "message": "Installed ..."}`` — installing nothing."""
    raise HTTPException(status_code=http_status.HTTP_501_NOT_IMPLEMENTED,
                        detail=_MARKETPLACE_DETAIL)
