"""FastAPI routes for Skill-Creator (console integration).

Endpoints:
  POST /skill-creator/generate — Generate new skill
  GET /skill-creator/status/<run_id> — Poll generation status
  GET /skill-creator/skills — List generated skills
  GET /skill-creator/stats — Get aggregated stats
"""

import asyncio
import logging
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Annotated, Dict, Any, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import auth as session_auth
from .. import audit as console_audit
from ..deps import require_csrf, require_session

logger = logging.getLogger(__name__)

# Import the Skill-Creator.
#
# operator/ has no __init__.py (it would shadow the stdlib `operator`), so the
# package is reached by putting operator/ on sys.path — but APPENDED, never
# inserted at the front. Inserting it first used to shadow the SkillForge
# REGISTRY package, which was also called `skill_forge` at the time: every
# later `from skill_forge.multi_registry import ...` in the process resolved
# into the Skill-Creator instead and raised ModuleNotFoundError, breaking
# skill injection, promote.py and the bridge adapter. The package has since
# been renamed to `skill_creator`, and appending keeps a future name clash
# from silently winning against an installed package. (ADR-0405)
try:
    _operator_dir = Path(__file__).resolve().parents[4] / "operator"
    if _operator_dir.is_dir() and str(_operator_dir) not in sys.path:
        sys.path.append(str(_operator_dir))

    from skill_creator.skill_creator import (
        SkillCreatorOrchestrator,
        SkillCreatorError,
    )
    from skill_creator.llm_client import resolve_llm_client, engine_id_of
    from skill_creator.registry_bridge import (
        delete_skill,
        list_skills,
        read_skill,
        skill_body,
        strip_front_matter,
    )
except ImportError as e:
    logger.warning(f"SkillCreatorOrchestrator import failed: {e}")
    SkillCreatorOrchestrator = None
    SkillCreatorError = Exception
    resolve_llm_client = None
    engine_id_of = None
    list_skills = None
    read_skill = None
    delete_skill = None
    skill_body = None
    strip_front_matter = None

from .. import _bootstrap
_forge_paths = _bootstrap.forge_paths


def _require_valid_name(name: str) -> str:
    """Reject anything the registry would not accept, before a path join.

    Registry names are alphanumeric plus `.` and `_`. One guard, used by
    every route that takes a name, so a new endpoint cannot forget it.
    """
    if (not name or len(name) > 128 or ".." in name
            or not all(c.isalnum() or c in "._" for c in name)):
        raise HTTPException(status_code=400, detail="invalid skill name")
    return name


def _registry_root(tenant_id: str) -> Path:
    """`<tenant_home>/skill-forge` for the caller's tenant.

    `tenant_home()`, NOT `tenant_global_dir()`. The on-disk contract is
    `<corvin_home>/tenants/<tid>/{global,sessions,forge,skill-forge,...}` —
    `skill-forge` is a sibling of `global`, not a child of it — and this is
    the path `MultiSkillRegistry._root_for("user")` resolves, so it is the
    only place `skill_inject` will ever look. Writing to
    `<tenant>/global/skill-forge` produces a registry that is real, valid,
    and read by nothing (which is what `skills_manual.py` does today).

    Derived from the authenticated session, never from an env var — the
    console tenant-routing rule (CLAUDE.md, ADR-0007).
    """
    return _forge_paths.tenant_home(tenant_id) / "skill-forge"

router = APIRouter(prefix="/skill-creator", tags=["skill-creator"])

# Canonical phase order of the 5-phase orchestrator. The console renders
# these labels; keep it in sync with SkillCreatorOrchestrator.create_skill.
PHASES = ("planning", "validation", "ldd_iteration", "review", "promotion")

# In-memory store for generation runs (in production: use DB).
# Mutated from the generation worker thread → guarded by _runs_lock.
_generation_runs: Dict[str, Dict[str, Any]] = {}
_runs_lock = threading.Lock()
_skill_stats = {
    "total_generated": 0,
    "avg_quality": 0.0,
    "total_iterations": 0,
    "last_generated_at": None,
}


# ============================================================================
# REQUEST/RESPONSE MODELS
# ============================================================================

class SkillGenerationRequest(BaseModel):
    user_request: str = Field(..., min_length=10, description="Description of skill to create")
    async_: bool = Field(default=True, alias="async", description="Run async and return run_id")
    # Refine an existing skill instead of creating a new one: the current
    # body becomes the starting point and the name is preserved, so
    # iterating updates the skill in place.
    base_skill: Optional[str] = Field(
        default=None, max_length=128,
        description="Name of an existing skill to refine",
    )


class ReviewFindingOut(BaseModel):
    dimension: str
    summary: str
    verdict: str


class SkillArtifact(BaseModel):
    name: str
    purpose: str
    scope: str
    quality: float
    iterations: int
    dependencies: list = Field(default_factory=list)
    # Why the quality score is what it is. Without these an operator saw a
    # bare "Quality: 0%" and had nothing to act on.
    findings: list[ReviewFindingOut] = Field(default_factory=list)
    # Is the skill actually reachable? Registered AND bootstrap-graded, or it
    # sits below skill_inject's eligibility gate and is never injected.
    injectable: bool = False
    registry_path: str = ""


class GenerationStatusResponse(BaseModel):
    run_id: str
    status: str  # running | success | failed
    phase: str
    progress: int
    message: str
    engine: str = "unknown"      # claude_code (Max subscription) | api | local
    phases: list = Field(default_factory=lambda: list(PHASES))
    error: Optional[str] = None
    # Set when this run refines an existing skill in place.
    base_skill: Optional[str] = None
    skill: Optional[SkillArtifact] = None


class GeneratedSkill(BaseModel):
    name: str
    file: str
    created_at: str


# ============================================================================
# ENDPOINTS
# ============================================================================

@router.post("/generate", status_code=202)
async def generate_skill(
    req: SkillGenerationRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> Dict[str, Any]:
    """POST /skill-creator/generate

    Generate a new skill using async 6-phase LDD orchestration.

    Request:
    {
      "user_request": "erzeuge einen Skill der JSON validiert",
      "async": true
    }

    Response (async):
    {
      "status": "accepted",
      "run_id": "run-abc123",
      "message": "Generation in progress..."
    }
    """
    if SkillCreatorOrchestrator is None:
        raise HTTPException(status_code=500, detail="Skill-Creator not available")

    try:
        user_request = req.user_request.strip()

        if not user_request or len(user_request) < 10:
            raise HTTPException(status_code=400, detail="Request must be at least 10 characters")

        base = _resolve_base_skill(rec.tenant_id, req.base_skill)

        if req.async_:
            # Async mode: spawn background task, return run_id
            run_id = _spawn_generation_task(user_request, rec.tenant_id, base=base)
            verb = "refinement" if base else "generation"
            return {
                "status": "accepted",
                "run_id": run_id,
                "engine": _engine_id(),
                "base_skill": base["name"] if base else None,
                "message": f"Skill {verb} started. Poll /status/{run_id} for progress."
            }

        # Sync mode: block until complete (not recommended for long tasks).
        # Runs on a worker thread so the generation subprocess cannot block
        # the server's event loop for the whole run.
        orchestrator = SkillCreatorOrchestrator(
            registry_root=str(_registry_root(rec.tenant_id))
        )
        artifact = await asyncio.to_thread(
            lambda: asyncio.run(orchestrator.create_skill(user_request, base=base))
        )
        return {
            "status": "success",
            "engine": orchestrator.engine_id,
            "skill": {
                "name": artifact.spec.name,
                "purpose": artifact.spec.purpose,
                "scope": artifact.spec.scope.value,
                "dependencies": artifact.spec.dependencies,
                "quality": artifact.quality_score,
                "iterations": artifact.ldd_iterations,
            },
            "message": f"Skill '{artifact.spec.name}' generated successfully."
        }

    except HTTPException:
        raise
    except SkillCreatorError as e:
        logger.error(f"Skill generation failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Unexpected error in skill generation: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status/{run_id}", response_model=GenerationStatusResponse)
async def check_status(run_id: str) -> GenerationStatusResponse:
    """GET /skill-creator/status/<run_id>

    Poll the status of an async skill generation run.
    """
    with _runs_lock:
        run = dict(_generation_runs.get(run_id) or {})

    if not run:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

    response = {
        "run_id": run_id,
        "status": run["status"],
        "phase": run.get("phase", "pending"),
        "progress": run.get("progress", 0),
        "message": run.get("message", ""),
        "engine": run.get("engine", "unknown"),
        "phases": list(PHASES),
        "error": run.get("error"),
        "base_skill": run.get("base_skill"),
    }

    if run["status"] == "success":
        response["skill"] = run.get("skill")

    return GenerationStatusResponse(**response)


@router.get("/skills")
async def list_generated_skills(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> Dict[str, Any]:
    """GET /skill-creator/skills

    List the skills registered in the caller's tenant SkillForge registry.

    Reads the REGISTRY, not a directory glob: the manifest is what every
    consumer (skill_inject, the engine plugin slot) actually resolves
    against, so a directory listing could show skills nothing can use.
    Each entry carries `injectable`, the honest answer to "will this be
    used?" — registered and graded, or invisible to the injection gate.
    """
    if list_skills is None:
        raise HTTPException(status_code=500, detail="Skill-Creator not available")

    skills = list_skills(_registry_root(rec.tenant_id))
    return {
        "tenant_id": rec.tenant_id,
        "count": len(skills),
        "injectable_count": sum(1 for s in skills if s.get("injectable")),
        "skills": skills,
    }


@router.get("/skills/{name}")
async def get_generated_skill(
    name: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> Dict[str, Any]:
    """GET /skill-creator/skills/<name>

    Full body + metadata of one registered skill — what the console's
    "View" action shows. The button existed with no endpoint behind it.
    """
    if read_skill is None:
        raise HTTPException(status_code=500, detail="Skill-Creator not available")

    _require_valid_name(name)
    detail = read_skill(_registry_root(rec.tenant_id), name)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"skill not found: {name}")
    return detail


@router.delete("/skills/{name}")
async def delete_generated_skill(
    name: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> Dict[str, Any]:
    """DELETE /skill-creator/skills/<name>

    Remove a generated skill from the tenant registry, its directory and the
    engine plugin slot. Goes through the registry rather than removing the
    directory, so the manifest, the slot and the hash-chained skill audit
    stay consistent.
    """
    if delete_skill is None:
        raise HTTPException(status_code=500, detail="Skill-Creator not available")

    _require_valid_name(name)
    removed = delete_skill(_registry_root(rec.tenant_id), name,
                           reason="deleted from console Skill Creator")
    if not removed:
        raise HTTPException(status_code=404, detail=f"skill not found: {name}")

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="skill.generated_deleted",
        target_kind="generated_skill",
        target_id=name,
    )
    return {"ok": True, "name": name}


@router.get("/stats")
async def get_stats(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> Dict[str, Any]:
    """GET /skill-creator/stats

    Returns aggregated statistics about skill generation.
    """
    return _skill_stats


# ============================================================================
# BACKGROUND TASK HELPERS
# ============================================================================

def _engine_id() -> str:
    """Which engine a new run would use — reported to the console."""
    if resolve_llm_client is None or engine_id_of is None:
        return "unknown"
    try:
        return engine_id_of(resolve_llm_client())
    except Exception:  # noqa: BLE001 — status reporting must never raise
        return "unknown"


def _resolve_base_skill(tenant_id: str, name: Optional[str]) -> Optional[Dict[str, str]]:
    """Load the skill a refine round starts from, or None for a fresh skill.

    Fails loudly on an unknown name: silently generating a NEW skill when the
    operator asked to modify an existing one would leave the original
    untouched and register a near-duplicate beside it.
    """
    if not name:
        return None
    if skill_body is None or strip_front_matter is None:
        raise HTTPException(status_code=500, detail="Skill-Creator not available")

    _require_valid_name(name)
    body = skill_body(_registry_root(tenant_id), name)
    if body is None:
        raise HTTPException(status_code=404, detail=f"skill not found: {name}")
    return {"name": name, "body": strip_front_matter(body)}


def _update_run(run_id: str, **fields: Any) -> None:
    """Thread-safe partial update of a run record."""
    with _runs_lock:
        run = _generation_runs.get(run_id)
        if run is not None:
            run.update(fields)


def _spawn_generation_task(user_request: str, tenant_id: str,
                           base: Optional[Dict[str, str]] = None) -> str:
    """Spawn the generation worker thread; return its run_id.

    The orchestrator drives a `claude -p` subprocess per phase (Max
    subscription), which is blocking and minutes-long — hence a thread with
    its own event loop rather than a task on the server's loop.
    """
    run_id = f"run-{uuid4().hex[:12]}"

    with _runs_lock:
        _generation_runs[run_id] = {
            "status": "running",
            "phase": PHASES[0],
            "progress": 5,
            "message": "Initializing…",
            "engine": "unknown",
            "base_skill": base["name"] if base else None,
            "created_at": datetime.utcnow().isoformat(),
        }

    def on_progress(phase: str, progress: int, message: str) -> None:
        _update_run(run_id, phase=phase, progress=progress, message=message)

    def run_task() -> None:
        """Background worker for skill generation.

        `tenant_id` is captured from the authenticated request, not read
        inside the thread: a worker thread has no session, and reaching for
        an env var here is exactly the console tenant-routing violation
        CLAUDE.md forbids.
        """
        try:
            orchestrator = SkillCreatorOrchestrator(
                progress_cb=on_progress,
                registry_root=str(_registry_root(tenant_id)),
            )
            _update_run(run_id, engine=orchestrator.engine_id,
                        message=f"Generating via {orchestrator.engine_id}…")

            artifact = asyncio.run(orchestrator.create_skill(user_request, base=base))

            _update_run(
                run_id,
                status="success",
                phase=PHASES[-1],
                progress=100,
                error=None,
                message=f"Skill '{artifact.spec.name}' generated successfully.",
                skill={
                    "name": artifact.spec.name,
                    "purpose": artifact.spec.purpose,
                    "scope": artifact.spec.scope.value,
                    "quality": artifact.quality_score,
                    "iterations": artifact.ldd_iterations,
                    "dependencies": list(artifact.spec.dependencies),
                    "findings": [
                        {"dimension": f.dimension, "summary": f.summary,
                         "verdict": f.verdict.value}
                        for f in (artifact.review_findings or [])
                    ],
                    "injectable": bool(artifact.registration.get("injectable")),
                    "registry_path": str(artifact.registration.get("path") or ""),
                },
            )
            _record_stats(artifact)

        except Exception as e:  # noqa: BLE001 — surface, never crash the thread
            logger.exception("Skill generation run %s failed", run_id)
            _update_run(
                run_id,
                status="failed",
                error=str(e),
                message=_operator_hint(e),
            )

    thread = threading.Thread(target=run_task, name=f"skill-creator-{run_id}", daemon=True)
    thread.start()

    return run_id


def _operator_hint(exc: Exception) -> str:
    """Turn an engine failure into something an operator can act on.

    The raw SDK message ("Could not resolve authentication method…") told
    operators nothing about what to do on a CorvinOS install, which
    authenticates through the Claude Code CLI, not an API key.
    """
    text = str(exc)
    if "claude binary not found" in text:
        return ("Claude Code CLI not found. Install it or set CORVIN_CLAUDE_BIN "
                "to its path — skill generation runs on your Claude subscription.")
    if "Could not resolve authentication" in text:
        return ("No engine authentication. Log in with `claude` (Max subscription) "
                "or set ANTHROPIC_API_KEY.")
    if "timed out" in text:
        return (f"Engine timed out: {text}. Raise CORVIN_SKILL_CREATOR_TIMEOUT_S "
                f"if the model needs longer.")
    return text


def _record_stats(artifact: Any) -> None:
    """Fold a finished run into the aggregate the /stats endpoint serves."""
    with _runs_lock:
        total = _skill_stats["total_generated"]
        avg = _skill_stats["avg_quality"]
        _skill_stats["total_generated"] = total + 1
        _skill_stats["avg_quality"] = round(
            (avg * total + artifact.quality_score) / (total + 1), 3
        )
        _skill_stats["total_iterations"] += artifact.ldd_iterations
        _skill_stats["last_generated_at"] = datetime.utcnow().isoformat()
