"""Projects API (ADR-2315): ``/v1/console/projects``.

Behind a session; every mutation also needs the CSRF token. The tenant is ALWAYS the session's
(``rec.tenant_id``); a project of another tenant is 404, never a hint that it exists. Paths from
the client exist only in ``plan`` and ``adopt`` and are judged by ``core.projects.registry``;
every other call carries a project id and resolves the path from the registry.
"""
from __future__ import annotations

from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from core.projects import fragments as F
from core.projects import manifest, registry
from core.projects.fsutil import UnsafePath
from core.task_tracking import service as tt

from .. import auth as session_auth
from .. import projects_service as svc
from ..deps import require_csrf, require_session

router = APIRouter(prefix="/projects")

Sess = Annotated[session_auth.SessionRecord, Depends(require_session)]
SessCsrf = Annotated[session_auth.SessionRecord, Depends(require_csrf)]


class PlanBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=120)
    project_id: Optional[str] = Field(default=None, max_length=28)
    location: Optional[str] = Field(default=None, max_length=1000)
    features: Optional[list[str]] = Field(default=None, max_length=64)
    docs_mode: Optional[str] = Field(default=None, max_length=10)


class CreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_id: str = Field(min_length=8, max_length=80)


class AdoptBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    location: str = Field(min_length=1, max_length=1000)


class FeaturesBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    set: dict[str, bool] = Field(min_length=1, max_length=64)
    accept_dependencies: bool = False
    cascade: bool = False


class VideoBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=60)


class RepairBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dry_run: bool = False


def _call(fn, *args, **kwargs) -> Any:
    try:
        return fn(*args, **kwargs)
    except svc.Conflict as exc:
        raise HTTPException(status_code=409, detail={"message": str(exc), "reason_class": exc.reason_class, **exc.extra})
    except svc.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except svc.ProjectsError as exc:
        raise HTTPException(status_code=exc.status, detail={"message": str(exc), "reason_class": exc.reason_class})
    except registry.ProjectPathError as exc:
        raise HTTPException(status_code=422, detail={"message": str(exc), "reason_class": "path_rule"})
    except registry.RegistryError as exc:
        raise HTTPException(status_code=409, detail={"message": str(exc), "reason_class": "registry"})
    except (F.FragmentError, manifest.ManifestError, UnsafePath) as exc:
        raise HTTPException(status_code=422, detail={"message": str(exc)[:300], "reason_class": "invalid"})
    except tt.AuditUnavailable:
        raise HTTPException(status_code=503, detail="audit chain unavailable - nothing was written")


@router.get("")
def list_projects(rec: Sess) -> dict:
    return _call(svc.list_projects, rec.tenant_id)


@router.get("/templates")
def templates(rec: Sess) -> dict:
    return _call(svc.list_templates, rec.tenant_id)


@router.post("/plan")
def plan(body: PlanBody, rec: SessCsrf) -> dict:
    return _call(svc.plan_project, rec.tenant_id, template=body.template, title=body.title,
                 project_id=body.project_id, location=body.location, features=body.features,
                 docs_mode=body.docs_mode)


@router.post("", status_code=201)
def create(body: CreateBody, rec: SessCsrf) -> dict:
    return _call(svc.create_project, rec.tenant_id, body.plan_id)


@router.post("/adopt", status_code=201)
def adopt(body: AdoptBody, rec: SessCsrf) -> dict:
    return _call(svc.adopt_project, rec.tenant_id, body.location)


@router.get("/{project_id}")
def get_project(project_id: str, rec: Sess) -> dict:
    return _call(svc.get_project, rec.tenant_id, project_id)


@router.get("/{project_id}/structure")
def get_structure(project_id: str, rec: Sess, depth: int = 2) -> dict:
    """The project folder's entries (read-only), its knowledge-base counts and its video rows (T-0359)."""
    return _call(svc.structure, rec.tenant_id, project_id, depth)


@router.get("/{project_id}/health")
def get_health(project_id: str, rec: Sess) -> dict:
    return _call(svc.health, rec.tenant_id, project_id)


@router.put("/{project_id}/features")
def put_features(project_id: str, body: FeaturesBody, rec: SessCsrf) -> dict:
    return _call(svc.set_features, rec.tenant_id, project_id, body.set,
                 accept_dependencies=body.accept_dependencies, cascade=body.cascade)


@router.post("/{project_id}/repair")
def post_repair(project_id: str, body: RepairBody, rec: SessCsrf) -> dict:
    return _call(svc.repair, rec.tenant_id, project_id, dry_run=body.dry_run)


@router.post("/{project_id}/videos", status_code=201)
def post_video(project_id: str, body: VideoBody, rec: SessCsrf) -> dict:
    """Creates one video's folder, sources.md and (with the knowledge base on) its epic and tasks (T-0361)."""
    return _call(svc.new_video, rec.tenant_id, project_id, body.name)


@router.post("/{project_id}/open")
def post_open(project_id: str, rec: SessCsrf) -> dict:
    return _call(svc.open_project, rec.tenant_id, project_id)


@router.delete("/{project_id}")
def delete_project(project_id: str, rec: SessCsrf) -> dict:
    """Removes the registry entry ONLY. The directory and every file in it stay."""
    return _call(svc.remove_from_registry, rec.tenant_id, project_id)
