"""Autonomous Skill Forge — console routes (ADR-2094).

The console surface of the skill canary: a candidate body for a registered
skill is served to a share of chats, both variants are graded apart, and the
operator (or the autopilot, within its gates) moves it through the lifecycle.

  GET  /v1/console/autonomous-forge/status             autopilot, canaries, skills
  GET  /v1/console/autonomous-forge/metrics?skill_id=  per-variant grade series
  GET  /v1/console/autonomous-forge/history?limit=     canary lifecycle records
  GET  /v1/console/autonomous-forge/candidate/{skill}  live vs candidate body
  POST /v1/console/autonomous-forge/fork               forge a candidate (async)
  GET  /v1/console/autonomous-forge/fork/{run_id}      fork run status
  POST /v1/console/autonomous-forge/{approve,defer,pause,resume,rollback}
  POST /v1/console/autonomous-forge/autopilot          switch the autopilot
  POST /v1/console/autonomous-forge/tick               run one autopilot pass now

Every route is session-gated and every mutation CSRF-gated by the router-level
guard (the console's ``X-CSRF-Token`` header). The body-carried
``session_token`` / ``client_nonce`` scheme these routes used to demand could
never validate — the token was derived for ``/status`` with the current time
and checked for ``/approve`` with ``csrf_nonce_issued_at`` — so no request
ever passed it (ADR-2094).

Tenant: always ``rec.tenant_id``. Registry root: ``<tenant_home>/skill-forge``
— the root ``skill_inject`` serves from. Canary transitions are written to the
tenant audit chain by ``skill_forge.canary`` BEFORE they take effect.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Dict, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from .. import audit as console_audit
from .. import auth as session_auth
from ..api_schemas.autonomous_forge import ManifestResponse
from ..deps import require_csrf, require_session, require_session_csrf_on_mutation
from ..validation.input_validator import validate_skill_id, validate_version
from .skill_creator_api import (
    _forge_paths, _registry_root, _require_namespace, quota_exceeded_to_http,
    require_skill_forge_quota,
)

log = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_session_csrf_on_mutation)])

try:  # skill_creator_api put corvin_operator/ on sys.path
    from skill_creator import autonomous as _auto
    from skill_creator.registry_bridge import registry_for as _registry_for
except ImportError as exc:  # pragma: no cover — reported, never silent
    log.warning("Autonomous Skill Forge unavailable: %s", exc)
    _auto = None
    _registry_for = None


def _require_available() -> None:
    if _auto is None:
        raise HTTPException(status_code=503, detail="Autonomous Skill Forge is not available on this build")


def _canary_mod():
    return _auto._canary_module()


def _store(tenant_id: str):
    return _auto.canary_store(_registry_root(tenant_id))


def _audit_path(tenant_id: str) -> Path:
    return _registry_for(_registry_root(tenant_id)).audit_path()


# ─────────────────────────────────────────────────────────────────────────────
# Views
# ─────────────────────────────────────────────────────────────────────────────

def _view(state: dict) -> dict:
    cm = _canary_mod()
    ref, ref_source = cm.reference_mean(state)
    return {
        "skill_id": state["skill"],
        "canary_id": state["canary_id"],
        "status": state["status"],
        "traffic_percent": int(state.get("traffic_percent", 0)),
        "source": state.get("source"),
        "trigger": state.get("trigger") or {},
        "quality": state.get("quality"),
        "findings": state.get("findings") or [],
        "created_at": state.get("created_at"),
        "updated_at": state.get("updated_at"),
        "stats": cm.variant_stats(state),
        "reference": {"mean": ref, "source": ref_source},
        "verdict": cm.evaluate(state),
        "gates": {"min_samples": cm.MIN_SAMPLES, "rollback_margin": cm.ROLLBACK_MARGIN,
                  "traffic_steps": list(cm.TRAFFIC_STEPS)},
        "candidate_sha": state.get("candidate_sha"),
        "live_sha": state.get("live_sha"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Operator fork runs (in-process, tenant-bound)
# ─────────────────────────────────────────────────────────────────────────────

_fork_runs: Dict[str, Dict[str, Any]] = {}
_fork_lock = threading.Lock()


def _spawn_fork(tenant_id: str, skill_id: str, *, source: str, instruction: str,
                sid_fingerprint: str) -> str:
    run_id = f"fork-{uuid4().hex[:12]}"
    with _fork_lock:
        _fork_runs[run_id] = {"run_id": run_id, "tenant_id": tenant_id, "skill_id": skill_id,
                              "status": "running", "phase": "planning", "progress": 5,
                              "message": "Starting…", "error": None, "started_at": time.time()}

    def _progress(phase: str, progress: int, message: str) -> None:
        with _fork_lock:
            _fork_runs[run_id].update(phase=phase, progress=progress, message=message)

    def _run() -> None:
        try:
            state = _auto.fork(_registry_root(tenant_id), skill_id, source=source,
                               instruction=instruction, progress_cb=_progress,
                               trigger={"reason": "operator_request"})
            with _fork_lock:
                _fork_runs[run_id].update(status="success", progress=100, phase="canary",
                                          canary_id=state["canary_id"],
                                          message=f"Canary started at {state['traffic_percent']}%")
            console_audit.action_performed(
                tenant_id=tenant_id, sid_fingerprint=sid_fingerprint,
                action="skill.canary_forked", target_kind="skill_canary",
                target_id=skill_id, run_id=run_id)
        except Exception as exc:  # noqa: BLE001 — surfaced in the run record
            log.exception("candidate fork %s for %s failed", run_id, skill_id)
            with _fork_lock:
                _fork_runs[run_id].update(status="failed", error=str(exc)[:500],
                                          message=str(exc)[:200])
            console_audit.action_failed(
                tenant_id=tenant_id, sid_fingerprint=sid_fingerprint,
                action="skill.canary_forked", target_kind="skill_canary",
                target_id=skill_id, reason=type(exc).__name__)

    threading.Thread(target=_run, name=f"skill-fork-{run_id}", daemon=True).start()
    return run_id


# ─────────────────────────────────────────────────────────────────────────────
# Read routes
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/status")
def get_status(rec: Annotated[session_auth.SessionRecord, Depends(require_session)]) -> dict:
    _require_available()
    root = _registry_root(rec.tenant_id)
    store = _store(rec.tenant_id)
    cm = _canary_mod()
    states = store.list_states()
    canaries = [_view(s) for s in states if s.get("status") in cm.ACTIVE]
    settings = _auto.autopilot_settings(root)
    with _fork_lock:
        runs = [dict(r) for r in _fork_runs.values()
                if r["tenant_id"] == rec.tenant_id and r["status"] == "running"]
    return {
        "autopilot": {
            "enabled": bool(settings.get("enabled")),
            "last_tick": settings.get("last_tick"),
            "last_actions": settings.get("last_actions") or [],
            "interval_s": _auto.TICK_INTERVAL_S,
            "loss_rule": {"threshold": _auto.LOSS_THRESHOLD,
                          "min_outcomes": _auto.LOSS_MIN_OUTCOMES,
                          "window_days": _auto.LOSS_WINDOW_S // 86400},
        },
        "canaries": canaries,
        "skills": _auto.skills_overview(root) if (root / "skills_registry.json").exists() else [],
        "forks_in_flight": _auto.forks_in_flight(root),
        "fork_runs": [{k: r[k] for k in ("run_id", "skill_id", "status", "phase", "progress", "message")}
                      for r in runs],
    }


@router.get("/metrics")
def get_metrics(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    skill_id: str = Query(..., max_length=128),
) -> dict:
    """Every grade the canary recorded, in order, with the running mean of
    its variant's OUTCOME grades — the measurement the gates act on."""
    _require_available()
    _require_namespace(skill_id)
    state = _store(rec.tenant_id).state(skill_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"no canary for {skill_id}")
    running: dict[str, list[float]] = {"live": [], "candidate": []}
    points = []
    for g in sorted(state.get("grades", []), key=lambda g: g.get("ts", 0)):
        if g.get("kind") == "outcome":
            running[g["variant"]].append(float(g["score"]))
        vals = running[g["variant"]]
        points.append({
            "ts": g.get("ts"), "variant": g["variant"], "kind": g.get("kind"),
            "score": g.get("score"),
            "outcome_mean": round(sum(vals) / len(vals), 4) if vals else None,
            "outcome_n": len(vals),
        })
    return {"skill_id": skill_id, "canary_id": state["canary_id"], "points": points,
            "stats": _canary_mod().variant_stats(state)}


@router.get("/history")
def get_history(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    limit: int = Query(20, ge=1, le=200),
) -> dict:
    _require_available()
    states = _store(rec.tenant_id).list_states()[:limit]
    return {"attempts": [{**_view(s), "events": s.get("events", [])} for s in states],
            "total_count": len(states)}


@router.get("/candidate/{skill_id}")
def get_candidate(
    skill_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict:
    """The two bodies the operator decides between."""
    _require_available()
    _require_namespace(skill_id)
    store = _store(rec.tenant_id)
    state = store.state(skill_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"no canary for {skill_id}")
    try:
        live_at_start = (store.dir / skill_id / "live_at_start.md").read_text(encoding="utf-8")
    except OSError:
        live_at_start = ""
    return {"skill_id": skill_id, "canary_id": state["canary_id"], "status": state["status"],
            "live_body": live_at_start, "candidate_body": store.candidate_body(skill_id) or ""}


@router.get("/fork/{run_id}")
def get_fork_run(
    run_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict:
    with _fork_lock:
        run = dict(_fork_runs.get(run_id) or {})
    if not run or run.get("tenant_id") != rec.tenant_id:
        raise HTTPException(status_code=404, detail="unknown fork run")
    run.pop("tenant_id", None)
    return run


# ─────────────────────────────────────────────────────────────────────────────
# Mutations
# ─────────────────────────────────────────────────────────────────────────────

class ForkRequest(BaseModel):
    skill_id: str = Field(..., max_length=128)
    instruction: str = Field(default="", max_length=2000,
                             description="What the candidate should change; empty = derived from the loss")


class SkillAction(BaseModel):
    skill_id: str = Field(..., max_length=128)
    reason: str = Field(default="", max_length=200)


class AutopilotRequest(BaseModel):
    enabled: bool


def _conflict(exc: Exception) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


def _operator_action(rec: session_auth.SessionRecord, action: str, skill_id: str) -> None:
    console_audit.action_performed(
        tenant_id=rec.tenant_id, sid_fingerprint=rec.sid_fingerprint,
        action=f"skill.canary_{action}", target_kind="skill_canary", target_id=skill_id)


@router.post("/fork", status_code=status.HTTP_202_ACCEPTED)
def fork_candidate(
    body: ForkRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
    _: Annotated[session_auth.SessionRecord, Depends(require_skill_forge_quota)],
) -> dict:
    """Forge a candidate for an existing skill and start its canary.

    A fork is a skill generation: on the free tier it consumes one daily
    skill credit (ADR-2095) — pre-checked here, charged when the candidate
    is staged. Approve and rollback then write without a further charge."""
    _require_available()
    _require_namespace(body.skill_id)
    root = _registry_root(rec.tenant_id)
    if _registry_for(root).get(body.skill_id) is None:
        raise HTTPException(status_code=404, detail=f"skill not found: {body.skill_id}")
    if _store(rec.tenant_id).active(body.skill_id) is not None:
        raise HTTPException(status_code=409, detail=f"{body.skill_id} already has an active canary")
    if body.skill_id in _auto.forks_in_flight(root):
        raise HTTPException(status_code=409, detail=f"a candidate for {body.skill_id} is already being forged")
    run_id = _spawn_fork(rec.tenant_id, body.skill_id, source="operator",
                         instruction=body.instruction.strip(), sid_fingerprint=rec.sid_fingerprint)
    return {"status": "accepted", "run_id": run_id, "skill_id": body.skill_id}


def _lifecycle(rec: session_auth.SessionRecord, body: SkillAction, action: str) -> dict:
    _require_available()
    _require_namespace(body.skill_id)
    cm = _canary_mod()
    store = _store(rec.tenant_id)
    try:
        if action in ("approve", "rollback"):
            reg = _registry_for(_registry_root(rec.tenant_id))
            state = (store.approve(body.skill_id, reg) if action == "approve"
                     else store.rollback(body.skill_id, reg,
                                         reason_code=body.reason or "operator_rollback"))
        elif action == "defer":
            state = store.defer(body.skill_id, reason_code=body.reason or "deferred")
        elif action == "pause":
            state = store.pause(body.skill_id)
        else:
            state = store.resume(body.skill_id)
    except cm.CanaryConflict as exc:
        raise _conflict(exc)
    except cm.CanaryError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ValueError as exc:
        if str(exc).startswith("license_limit"):
            raise quota_exceeded_to_http(exc)
        if str(exc).startswith("license_required"):
            raise HTTPException(status_code=402, detail={"error": "license_required",
                                                         "reason": str(exc)[:200]})
        raise
    _operator_action(rec, action, body.skill_id)
    return {"canary": _view(state)}


@router.post("/approve")
def approve(body: SkillAction,
            rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]) -> dict:
    """Make the candidate the live body (its canary grades become the skill's grades)."""
    return _lifecycle(rec, body, "approve")


@router.post("/defer")
def defer(body: SkillAction,
          rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]) -> dict:
    """End the canary without a rollout; the live body stays."""
    return _lifecycle(rec, body, "defer")


@router.post("/pause")
def pause(body: SkillAction,
          rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]) -> dict:
    """Serve every chat the live body until resumed."""
    return _lifecycle(rec, body, "pause")


@router.post("/resume")
def resume(body: SkillAction,
           rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]) -> dict:
    return _lifecycle(rec, body, "resume")


@router.post("/rollback")
def rollback(body: SkillAction,
             rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]) -> dict:
    """Active canary: stop serving the candidate. Approved: restore the previous body."""
    return _lifecycle(rec, body, "rollback")


@router.post("/autopilot")
def set_autopilot(body: AutopilotRequest,
                  rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]) -> dict:
    _require_available()
    root = _registry_root(rec.tenant_id)
    writer = _canary_mod().audit_writer(_audit_path(rec.tenant_id))
    settings = _auto.set_autopilot(root, body.enabled, audit=writer)
    _operator_action(rec, "autopilot_" + ("on" if body.enabled else "off"), "autopilot")
    return {"enabled": settings["enabled"]}


@router.post("/tick", status_code=status.HTTP_202_ACCEPTED)
def run_tick(rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]) -> dict:
    """Run one autopilot pass now (gates, then at most one loss-signal fork)
    on a worker thread; the result lands in ``status.autopilot``."""
    _require_available()
    root = _registry_root(rec.tenant_id)
    threading.Thread(target=_auto.tick, args=(root,), name="skill-forge-tick", daemon=True).start()
    _operator_action(rec, "tick", "autopilot")
    return {"status": "accepted"}


# ─────────────────────────────────────────────────────────────────────────────
# Autopilot scheduler — one per console process, over every tenant's root
# ─────────────────────────────────────────────────────────────────────────────

def _tenant_roots() -> list[Path]:
    tenants = Path(_forge_paths.corvin_home()) / "tenants"
    if not tenants.is_dir():
        return []
    return [d / "skill-forge" for d in sorted(tenants.iterdir())
            if (d / "skill-forge" / "skills_registry.json").is_file()]


if _auto is not None and "pytest" not in sys.modules:
    _auto.start_scheduler(_tenant_roots)


# ─────────────────────────────────────────────────────────────────────────────
# Generated-skill manifest (read-only, path-traversal hardened)
# ─────────────────────────────────────────────────────────────────────────────

def _get_manifest(skill_id: str, version: str, tenant_id: str) -> Optional[ManifestResponse]:
    """Fetch generated skill manifest from storage.

    SECURITY: Validates skill_id and version to prevent path traversal (OWASP A01:2021).
    Fail-closed: Invalid inputs → return None.

    Reads ``<tenant_home>/skill-forge/<skill_id>/<version>/skill.json``; the
    resolved path must stay under the tenant's skill-forge directory. An absent
    or unreadable manifest is None (404) — never a generated placeholder.
    """
    # Validate skill_id to prevent path traversal (../../ escape)
    if not validate_skill_id(skill_id):
        log.warning(f"Invalid skill_id in _get_manifest: {skill_id} (tenant {tenant_id})")
        return None

    # Validate version to prevent path traversal
    if not validate_version(version):
        log.warning(f"Invalid version in _get_manifest: {version} (tenant {tenant_id})")
        return None

    try:
        from core.paths import tenant_home  # noqa: PLC0415

        base = Path(tenant_home(tenant_id)).resolve() / "skill-forge"
        manifest_path = (base / skill_id / version / "skill.json").resolve()
        if base not in manifest_path.parents or not manifest_path.is_file():
            return None
        import json as _json  # noqa: PLC0415

        skill_json = _json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — unreadable/absent manifest = not found
        log.info(f"manifest unavailable for {skill_id} v{version} (tenant {tenant_id}): {type(exc).__name__}")
        return None
    if not isinstance(skill_json, dict):
        return None
    return ManifestResponse(
        skill_json=skill_json,
        generation_context=skill_json.get("generation_context") or {},
        timestamp=datetime.utcfromtimestamp(manifest_path.stat().st_mtime),
    )



@router.get(
    "/manifest/{skill_id}/{version}",
    response_model=ManifestResponse,
    status_code=status.HTTP_200_OK,
    summary="View generated skill manifest",
    description="Returns the generated skill.json manifest and generation context.",
)
def get_manifest(
    skill_id: str,
    version: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> ManifestResponse:
    """Retrieve a generated skill manifest.

    Path parameters:
      - skill_id: str (validated against path traversal)
      - version: str (validated against path traversal)

    Returns:
      - skill_json: dict (the skill.json content)
      - generation_context: dict (loss signal, parameters)
      - timestamp: datetime

    Tenant isolation: Manifest must belong to rec.tenant_id.
    No audit logging (read-only operation).
    Fail-closed: Invalid input or missing manifest → 400/404.

    SECURITY: Validates skill_id and version to prevent path traversal (OWASP A01:2021).
    """
    tenant_id = rec.tenant_id

    # Validate skill_id format (prevent ../../ escape, etc.)
    if not validate_skill_id(skill_id):
        log.warning(
            f"get_manifest with invalid skill_id: {skill_id} (tenant {tenant_id})"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="skill_id format invalid (must be alphanumeric with -, _, . only)",
        )

    # Validate version format (prevent ../../ escape, etc.)
    if not validate_version(version):
        log.warning(
            f"get_manifest with invalid version: {version} "
            f"(skill {skill_id}, tenant {tenant_id})"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="version format invalid (must be semantic X.Y.Z)",
        )

    manifest = _get_manifest(skill_id, version, tenant_id)
    if not manifest:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Manifest not found: {skill_id} v{version}",
        )

    return manifest
