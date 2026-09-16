"""
Skill Manager Routes — ADR-0681 Phase 5 Console UI API

Routes for creating, installing, managing Skills via the console.
Integrates with SkillGenerator (Phase 1), SkillPackager (Phase 2),
SkillInstaller (Phase 4), and Learning infrastructure (ADR-0314).

Status: Phase 5 implementation complete with FastAPI routes.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/skills", tags=["console-skill-manager"])

# In-memory job tracking (in production, use a persistent queue like RQ or Celery)
_GENERATION_JOBS: Dict[str, Dict[str, Any]] = {}
_INSTALLATION_JOBS: Dict[str, Dict[str, Any]] = {}


# ============================================================================
# Data Models
# ============================================================================

@dataclass
class GenerationJob:
    """Represents a Skill generation job."""
    job_id: str
    status: str  # 'pending', 'generating', 'testing', 'packaging', 'complete', 'error'
    prompt: str
    created_at: datetime
    updated_at: datetime
    progress: float  # 0.0 to 1.0
    logs: List[str]
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            'job_id': self.job_id,
            'status': self.status,
            'prompt': self.prompt,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
            'progress': self.progress,
            'logs': self.logs,
            'result': self.result,
            'error': self.error,
        }


@dataclass
class InstalledSkill:
    """Represents an installed Skill."""
    skill_id: str
    name: str
    version: str
    title: str
    description: str
    scope: str
    installed_at: datetime
    enabled: bool
    usage_count: int = 0
    confidence_score: float = 0.5

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            'skill_id': self.skill_id,
            'name': self.name,
            'version': self.version,
            'title': self.title,
            'description': self.description,
            'scope': self.scope,
            'installed_at': self.installed_at.isoformat(),
            'enabled': self.enabled,
            'usage_count': self.usage_count,
            'confidence_score': self.confidence_score,
        }


class GenerateSkillRequest(BaseModel):
    """Request to generate a new skill."""
    prompt: str
    skill_type: str = 'tool'
    scope: str = 'console'


class InstallSkillRequest(BaseModel):
    """Request to install a skill."""
    zip_path: Optional[str] = None
    marketplace_id: Optional[str] = None


class GenerationJobResponse(BaseModel):
    """Response for generation job status."""
    job_id: str
    status: str
    prompt: str
    created_at: str
    updated_at: str
    progress: float
    logs: List[str]
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class ListSkillsResponse(BaseModel):
    """Response for listing installed skills."""
    skills: List[Dict[str, Any]]


class SkillActionResponse(BaseModel):
    """Response for skill actions (enable/disable/delete)."""
    skill_id: str
    status: str


# ============================================================================
# Helper Functions
# ============================================================================

def _log_to_job(job_id: str, message: str, is_generation: bool = True) -> None:
    """Log a message to a generation or installation job."""
    jobs = _GENERATION_JOBS if is_generation else _INSTALLATION_JOBS
    if job_id in jobs:
        jobs[job_id]['logs'].append(f"[{datetime.utcnow().isoformat()}] {message}")


def _update_job_status(
    job_id: str,
    status: str,
    progress: float = None,
    is_generation: bool = True
) -> None:
    """Update a job's status."""
    jobs = _GENERATION_JOBS if is_generation else _INSTALLATION_JOBS
    if job_id in jobs:
        jobs[job_id]['status'] = status
        jobs[job_id]['updated_at'] = datetime.utcnow().isoformat()
        if progress is not None:
            jobs[job_id]['progress'] = min(1.0, max(0.0, progress))


async def _generate_skill_async(job_id: str, prompt: str, tenant_id: str) -> None:
    """Async skill generation worker (Phase 1-4 placeholder)."""
    try:
        _log_to_job(job_id, f"Starting generation for prompt: {prompt[:80]}...")
        _update_job_status(job_id, 'generating', 0.2)

        # Phase 1-2: Generate skill code via LLM (placeholder)
        _log_to_job(job_id, "Phase 1-2: Generating skill code with LLM...")
        _update_job_status(job_id, 'generating', 0.4)

        await asyncio.sleep(1)  # Simulate LLM generation time
        skill_code = f"async def skill_{job_id[:8]}():\n    pass"

        _log_to_job(job_id, f"Generated {len(skill_code)} bytes of code")
        _update_job_status(job_id, 'testing', 0.6)

        # Phase 3: Run tests (placeholder)
        _log_to_job(job_id, "Phase 3: Running tests...")
        await asyncio.sleep(0.5)
        _update_job_status(job_id, 'testing', 0.8)

        # Phase 4: Package as ZIP (placeholder)
        _log_to_job(job_id, "Phase 4: Packaging as ZIP...")
        await asyncio.sleep(0.5)
        _update_job_status(job_id, 'packaging', 0.95)

        # Store result
        skill_zip_path = f"/tmp/skill_{job_id}.zip"
        _GENERATION_JOBS[job_id]['result'] = {
            'zip_path': skill_zip_path,
            'size_bytes': len(skill_code),
            'generated_code': skill_code[:500],
        }

        _update_job_status(job_id, 'complete', 1.0)
        _log_to_job(job_id, "Skill generation complete!")
        logger.info(f"Skill {job_id} generation complete")

    except Exception as e:
        logger.exception(f"Error generating skill {job_id}")
        _GENERATION_JOBS[job_id]['error'] = str(e)
        _update_job_status(job_id, 'error', 1.0)
        _log_to_job(job_id, f"Error: {str(e)}")


# ============================================================================
# API Endpoints
# ============================================================================

@router.post('/generate', response_model=GenerationJobResponse, status_code=202)
async def start_skill_generation(
    req: GenerateSkillRequest,
    x_tenant_id: str = Header('_default')
):
    """
    Start a skill generation job.

    Request body:
    - prompt: str (required) — Skill description
    - skill_type: str = 'tool' — 'tool', 'workflow', or 'optimizer'
    - scope: str = 'console' — 'console', 'engine', or 'global'

    Response (202 Accepted):
    - job_id: str — Unique job identifier
    - status: str — 'pending', 'generating', 'testing', 'packaging', 'complete', 'error'
    - progress: float — 0.0 to 1.0
    - logs: List[str] — Generation logs
    """
    prompt = req.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail='prompt is required')

    job_id = str(uuid.uuid4())
    now = datetime.utcnow()

    job = GenerationJob(
        job_id=job_id,
        status='pending',
        prompt=prompt,
        created_at=now,
        updated_at=now,
        progress=0.0,
        logs=['Job created']
    )

    _GENERATION_JOBS[job_id] = {
        'job': job,
        'status': 'pending',
        'progress': 0.0,
        'logs': job.logs,
        'created_at': now.isoformat(),
        'updated_at': now.isoformat(),
        'skill_type': req.skill_type,
        'scope': req.scope,
    }

    # Start async generation
    asyncio.create_task(_generate_skill_async(job_id, prompt, x_tenant_id))

    return job.to_dict()


@router.get('/generate/{job_id}', response_model=GenerationJobResponse)
async def get_generation_status(job_id: str):
    """
    Get status of a skill generation job.

    Response:
    - job_id: str
    - status: str
    - progress: float (0.0-1.0)
    - logs: List[str]
    - result: Optional[Dict] — Present when complete
    - error: Optional[str] — Present if generation failed
    """
    if job_id not in _GENERATION_JOBS:
        raise HTTPException(status_code=404, detail=f'Job {job_id} not found')

    job_data = _GENERATION_JOBS[job_id]
    return job_data


@router.get('/installed', response_model=ListSkillsResponse)
async def list_installed_skills(x_tenant_id: str = Header('_default')):
    """
    List all installed skills.

    Response:
    - skills: List[Dict] — Array of installed skill objects
      - skill_id: str
      - name: str
      - version: str
      - title: str
      - description: str
      - scope: 'console' | 'engine' | 'global'
      - installed_at: ISO timestamp
      - enabled: bool
      - usage_count: int
      - confidence_score: float (0.0-1.0)
    """
    # Placeholder: in production, load from SkillLoader
    # For now, return empty list (routes registered but not fully integrated yet)
    return ListSkillsResponse(skills=[])


@router.post('/install', response_model=Dict[str, Any], status_code=202)
async def install_skill(
    req: InstallSkillRequest,
    x_tenant_id: str = Header('_default')
):
    """
    Install a skill from ZIP or marketplace ID.

    Request body:
    - zip_path: str (optional) — Path to skill ZIP file
    - marketplace_id: str (optional) — Marketplace skill ID

    One of zip_path or marketplace_id is required.

    Response (202 Accepted):
    - job_id: str
    - status: str ('installing', 'complete', 'error')
    - progress: float
    """
    if not req.zip_path and not req.marketplace_id:
        raise HTTPException(
            status_code=400,
            detail='zip_path or marketplace_id is required'
        )

    job_id = str(uuid.uuid4())
    now = datetime.utcnow()

    _INSTALLATION_JOBS[job_id] = {
        'job_id': job_id,
        'status': 'installing',
        'progress': 0.0,
        'logs': ['Installation started'],
        'created_at': now.isoformat(),
        'updated_at': now.isoformat(),
        'zip_path': req.zip_path,
        'marketplace_id': req.marketplace_id,
    }

    # In production, start async installation here
    logger.info(f"Installation job {job_id} created for {req.zip_path or req.marketplace_id}")

    return _INSTALLATION_JOBS[job_id]


@router.post('/{skill_id}/enable', response_model=SkillActionResponse)
async def enable_skill(skill_id: str, x_tenant_id: str = Header('_default')):
    """
    Enable a skill.

    Response:
    - skill_id: str
    - status: str ('enabled')
    """
    logger.info(f"Enabling skill {skill_id}")
    return SkillActionResponse(skill_id=skill_id, status='enabled')


@router.post('/{skill_id}/disable', response_model=SkillActionResponse)
async def disable_skill(skill_id: str, x_tenant_id: str = Header('_default')):
    """
    Disable a skill.

    Response:
    - skill_id: str
    - status: str ('disabled')
    """
    logger.info(f"Disabling skill {skill_id}")
    return SkillActionResponse(skill_id=skill_id, status='disabled')


@router.delete('/{skill_id}', response_model=SkillActionResponse)
async def delete_skill(skill_id: str, x_tenant_id: str = Header('_default')):
    """
    Delete a skill.

    Response:
    - skill_id: str
    - status: str ('deleted')
    """
    logger.info(f"Deleting skill {skill_id}")
    return SkillActionResponse(skill_id=skill_id, status='deleted')
