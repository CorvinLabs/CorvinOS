"""DataHub console API.

Endpoints (all under ``/v1/console``, session required, CSRF on mutations):
  POST   /datahub/analyze          — ingest + scan a source file, return its summary
  POST   /datahub/create           — create an artifact from a source file (201)
  GET    /datahub/list             — list artifacts (paginated)
  GET    /datahub/{artifact_id}    — artifact metadata + generated body
  DELETE /datahub/{artifact_id}    — soft delete

Every ingested row goes through the ADR-0661 security scanner first; a source with a
detected secret is refused. Mutations are chained as ``console.action_performed``.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

import corvin_console.auth as _auth
from forge import paths as _forge_paths

from core.skills.os_skills.datahub_unified.creator import UnifiedCreator
from core.skills.os_skills.datahub_unified.datahub import DataHubSkill, IngestionError
from core.skills.os_skills.datahub_unified.models import (
    CreationRequest, CreationType, DataIngestion, DataSourceType,
)

from .. import audit as console_audit
from ..deps import require_session, require_session_csrf_on_mutation

router = APIRouter(dependencies=[Depends(require_session_csrf_on_mutation)])

_ARTIFACT_ID = re.compile(r"^[0-9a-f]{8}$")
_EXTENSIONS = {DataSourceType.JSON: {".json"}, DataSourceType.CSV: {".csv"}}


class SourceRequest(BaseModel):
    data_source: str = Field(..., description="json or csv")
    data_path: str = Field(..., min_length=1, max_length=4096)
    sample_rows: int = Field(100, ge=1, le=10000)


class ArtifactCreateRequest(SourceRequest):
    name: str = Field(..., min_length=1, max_length=255)
    description: str = Field(..., min_length=1, max_length=2000)
    creation_type: str = Field(..., description="skill, tool, dataset, or pipeline")
    complexity: str = Field("medium", pattern="^(low|medium|high)$")


class ArtifactMetadata(BaseModel):
    artifact_id: str
    name: str
    description: str
    creation_type: str
    created_at: str
    status: str  # "completed" | "failed"
    row_count: int = 0
    validation_errors: list[str] = []


class ArtifactDetail(ArtifactMetadata):
    body: str = ""


class ArtifactCreateResponse(BaseModel):
    artifact_id: str
    status: str
    message: str
    metadata: ArtifactMetadata


class ArtifactListResponse(BaseModel):
    items: list[ArtifactMetadata]
    total: int
    limit: int
    offset: int


# ── Storage ──────────────────────────────────────────────────────────────────

def _artifacts_dir(tenant_id: str) -> Path:
    d = _forge_paths.tenant_global_dir(tenant_id) / "datahub_artifacts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _artifact_path(tenant_id: str, artifact_id: str) -> Path:
    return _artifacts_dir(tenant_id) / f"{artifact_id}.json"


def _write_artifact(tenant_id: str, artifact: dict) -> None:
    path = _artifact_path(tenant_id, artifact["artifact_id"])
    tmp = path.with_suffix(".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(artifact, f, indent=2)
    os.replace(tmp, path)


def _read_artifact(tenant_id: str, artifact_id: str) -> Optional[dict]:
    if not _ARTIFACT_ID.match(artifact_id):
        return None
    path = _artifact_path(tenant_id, artifact_id)
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _live_artifacts(tenant_id: str) -> list[dict]:
    out = []
    for path in _artifacts_dir(tenant_id).glob("*.json"):
        try:
            with open(path) as f:
                artifact = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if artifact.get("status") != "deleted":
            out.append(artifact)
    return out


def _metadata(artifact: dict) -> ArtifactMetadata:
    return ArtifactMetadata(**{k: artifact[k] for k in ArtifactMetadata.model_fields if k in artifact})


# ── Source handling ──────────────────────────────────────────────────────────

def _source_type(raw: str) -> DataSourceType:
    try:
        source = DataSourceType(raw)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unknown data_source '{raw[:20]}'")
    if source not in _EXTENSIONS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"source type '{source.value}' is not supported on this build")
    return source


def _source_path(tenant_id: str, raw: str, source: DataSourceType) -> Path:
    """Resolve the file; refuse another tenant's data and a mismatched extension."""
    path = Path(raw).expanduser().resolve()
    corvin_home = _forge_paths.tenant_home(tenant_id).resolve().parent.parent
    own_tenant = _forge_paths.tenant_home(tenant_id).resolve()
    if path.is_relative_to(corvin_home) and not path.is_relative_to(own_tenant):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "data_path is outside this tenant's data")
    if path.suffix.lower() not in _EXTENSIONS[source]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"data_path must be a {'/'.join(sorted(_EXTENSIONS[source]))} file")
    return path


def _ingest(rec: _auth.SessionRecord, req: SourceRequest) -> tuple[DataHubSkill, DataIngestion]:
    source = _source_type(req.data_source)
    ingestion = DataIngestion(
        source_type=source,
        source_path=str(_source_path(rec.tenant_id, req.data_path, source)),
        sample_rows=req.sample_rows,
        infer_schema=True,
    )
    hub = DataHubSkill()
    try:
        hub.ingest(ingestion)
    except IngestionError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    except OSError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "source file could not be read")
    return hub, ingestion


def _audit(rec: _auth.SessionRecord, action: str, artifact_id: str) -> None:
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action=action,
        target_kind="datahub_artifact",
        target_id=artifact_id,
    )


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/datahub/analyze")
def analyze_source(
    req: SourceRequest,
    rec: Annotated[_auth.SessionRecord, Depends(require_session)],
) -> dict:
    hub, _ = _ingest(rec, req)
    return {"analysis": hub.analyze()}


@router.post("/datahub/create", status_code=status.HTTP_201_CREATED)
def create_artifact(
    req: ArtifactCreateRequest,
    rec: Annotated[_auth.SessionRecord, Depends(require_session)],
) -> ArtifactCreateResponse:
    try:
        creation_type = CreationType(req.creation_type)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "creation_type must be skill, tool, dataset or pipeline")
    if any(a.get("name") == req.name for a in _live_artifacts(rec.tenant_id)):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Artifact with name '{req.name}' already exists")

    hub, ingestion = _ingest(rec, req)
    if hub.security.get("secret", 0) > 0:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"source contains {hub.security['secret']} secret(s); remove them first")

    rows = len(hub.ingested_data or [])
    if rows == 0:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "source has no rows")

    # The creator describes the data it is given: hand it the rows actually read,
    # not the requested sample size.
    result = UnifiedCreator().create(CreationRequest(
        data=DataIngestion(source_type=ingestion.source_type, source_path=ingestion.source_path,
                           sample_rows=rows, infer_schema=False),
        creation_type=creation_type,
        name=req.name,
        description=req.description,
        complexity=req.complexity,
        fail_closed=True,
    ))

    artifact_id = uuid.uuid4().hex[:8]
    artifact = {
        "artifact_id": artifact_id,
        "name": req.name,
        "description": req.description,
        "creation_type": creation_type.value,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "completed" if result.success else "failed",
        "row_count": rows,
        "validation_errors": result.validation_errors,
        "security": hub.security,
        "body": result.artifact_body,
        "tenant_id": rec.tenant_id,
    }
    _write_artifact(rec.tenant_id, artifact)
    _audit(rec, "datahub.create", artifact_id)

    return ArtifactCreateResponse(
        artifact_id=artifact_id,
        status=artifact["status"],
        message="Artifact created" if result.success else "Artifact failed validation",
        metadata=_metadata(artifact),
    )


@router.get("/datahub/list")
def list_artifacts(
    rec: Annotated[_auth.SessionRecord, Depends(require_session)],
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> ArtifactListResponse:
    items = sorted((_metadata(a) for a in _live_artifacts(rec.tenant_id)),
                   key=lambda m: m.created_at, reverse=True)
    return ArtifactListResponse(items=items[offset:offset + limit], total=len(items),
                                limit=limit, offset=offset)


@router.get("/datahub/{artifact_id}")
def get_artifact(
    artifact_id: str,
    rec: Annotated[_auth.SessionRecord, Depends(require_session)],
) -> ArtifactDetail:
    artifact = _read_artifact(rec.tenant_id, artifact_id)
    if not artifact or artifact.get("status") == "deleted":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artifact not found")
    return ArtifactDetail(**_metadata(artifact).model_dump(), body=artifact.get("body", ""))


@router.delete("/datahub/{artifact_id}")
def delete_artifact(
    artifact_id: str,
    rec: Annotated[_auth.SessionRecord, Depends(require_session)],
) -> dict[str, str]:
    artifact = _read_artifact(rec.tenant_id, artifact_id)
    if not artifact or artifact.get("status") == "deleted":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artifact not found")
    artifact["status"] = "deleted"
    artifact["deleted_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    _write_artifact(rec.tenant_id, artifact)
    _audit(rec, "datahub.delete", artifact_id)
    return {"status": "deleted", "artifact_id": artifact_id}
