"""FastAPI routes for Skill-Creator (console integration).

Endpoints:
  POST /skill-creator/generate — Generate new skill
  GET /skill-creator/status/<run_id> — Poll generation status
  GET /skill-creator/skills — List generated skills
  GET /skill-creator/stats — Get aggregated stats
"""

import asyncio
import logging
from datetime import datetime
from typing import Dict, Any, Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

# Import Skill-Creator orchestrator
try:
    from operator.skill_forge.skill_creator import (
        SkillCreatorOrchestrator,
        SkillCreatorError,
    )
except ImportError:
    SkillCreatorOrchestrator = None
    SkillCreatorError = Exception

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/skill-creator", tags=["skill-creator"])

# In-memory store for generation runs (in production: use DB)
_generation_runs: Dict[str, Dict[str, Any]] = {}
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


class SkillArtifact(BaseModel):
    name: str
    purpose: str
    scope: str
    quality: float
    iterations: int
    dependencies: list = Field(default_factory=list)


class GenerationStatusResponse(BaseModel):
    run_id: str
    status: str  # running | success | failed
    phase: str
    progress: int
    message: str
    skill: Optional[SkillArtifact] = None


class GeneratedSkill(BaseModel):
    name: str
    file: str
    created_at: str


# ============================================================================
# ENDPOINTS
# ============================================================================

@router.post("/generate", status_code=202)
async def generate_skill(req: SkillGenerationRequest) -> Dict[str, Any]:
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

        # Create orchestrator
        orchestrator = SkillCreatorOrchestrator()

        if req.async_:
            # Async mode: spawn background task, return run_id
            run_id = _spawn_generation_task(orchestrator, user_request)
            return {
                "status": "accepted",
                "run_id": run_id,
                "message": f"Skill generation started. Poll /status/{run_id} for progress."
            }
        else:
            # Sync mode: block until complete (not recommended for long tasks)
            loop = asyncio.new_event_loop()
            try:
                artifact = loop.run_until_complete(
                    orchestrator.create_skill(user_request)
                )
                return {
                    "status": "success",
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
            finally:
                loop.close()

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
    if run_id not in _generation_runs:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

    run = _generation_runs[run_id]

    response = {
        "run_id": run_id,
        "status": run["status"],
        "phase": run.get("phase", "pending"),
        "progress": run.get("progress", 0),
        "message": run.get("message", ""),
    }

    if run["status"] == "success":
        response["skill"] = run.get("skill")

    return GenerationStatusResponse(**response)


@router.get("/skills")
async def list_generated_skills() -> Dict[str, Any]:
    """GET /skill-creator/skills

    List all generated skills (from disk).
    """
    from pathlib import Path

    skills_dir = Path.home() / ".claude" / "skills"
    skills = []

    if skills_dir.exists():
        for skill_file in skills_dir.glob("assistant_*.md"):
            skill_name = skill_file.stem.replace("_", ".")
            skills.append({
                "name": skill_name,
                "file": str(skill_file),
                "created_at": datetime.fromtimestamp(skill_file.stat().st_ctime).isoformat(),
            })

    return {"skills": skills}


@router.get("/stats")
async def get_stats() -> Dict[str, Any]:
    """GET /skill-creator/stats

    Returns aggregated statistics about skill generation.
    """
    return _skill_stats


# ============================================================================
# BACKGROUND TASK HELPERS
# ============================================================================

def _spawn_generation_task(orchestrator, user_request: str) -> str:
    """Spawn async generation task; return run_id."""
    import threading

    run_id = f"run-{uuid4().hex[:12]}"

    _generation_runs[run_id] = {
        "status": "running",
        "phase": "planning",
        "progress": 10,
        "message": "Initializing...",
        "created_at": datetime.utcnow().isoformat(),
    }

    def run_task():
        """Background worker for skill generation."""
        try:
            loop = asyncio.new_event_loop()

            # Update progress as phases complete
            _generation_runs[run_id]["phase"] = "planning"
            _generation_runs[run_id]["progress"] = 20

            artifact = loop.run_until_complete(
                orchestrator.create_skill(user_request)
            )

            _generation_runs[run_id].update({
                "status": "success",
                "phase": "promotion",
                "progress": 100,
                "message": f"Skill '{artifact.spec.name}' generated successfully.",
                "skill": {
                    "name": artifact.spec.name,
                    "purpose": artifact.spec.purpose,
                    "scope": artifact.spec.scope.value,
                    "quality": artifact.quality_score,
                    "iterations": artifact.ldd_iterations,
                },
            })

            loop.close()

        except Exception as e:
            logger.error(f"Task failed: {e}")
            _generation_runs[run_id].update({
                "status": "failed",
                "message": str(e),
            })

    # Spawn thread
    thread = threading.Thread(target=run_task, daemon=True)
    thread.start()

    return run_id
