"""DataHub Phase 3: Console API Routes

Endpoints:
  POST /v1/console/datahub/create      — Create artifact from data
  GET /v1/console/datahub/{artifact_id} — Retrieve artifact metadata + preview
  DELETE /v1/console/datahub/{artifact_id} — Delete artifact (soft delete, audit logged)
  GET /v1/console/datahub/list          — List all artifacts (paginated)
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Annotated, Any, Optional
from datetime import datetime

from fastapi import APIRouter, Depends, Query, HTTPException, status
from pydantic import BaseModel, Field

import corvin_console.auth as _auth
from ..deps import require_session
from forge import paths as _forge_paths

# Import DataHub skill classes
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "skills" / "os_skills"))
from datahub_unified.models import (
    DataSourceType, DataIngestion, CreationType, CreationRequest, CreationResult
)
from datahub_unified.datahub import DataHubSkill
from datahub_unified.creator import UnifiedCreator

router = APIRouter()


# ============================================================================
# Models
# ============================================================================

class ArtifactCreateRequest(BaseModel):
    """Request to create an artifact."""
    name: str = Field(..., min_length=1, max_length=255)
    description: str = Field(..., min_length=1, max_length=2000)
    creation_type: str = Field(..., description="skill, tool, dataset, or pipeline")
    data_source: str = Field(..., description="json, csv, sql, api, or parquet")
    data_path: str = Field(..., min_length=1)
    sample_rows: int = Field(100, ge=1, le=10000)
    complexity: str = Field("medium", pattern="^(low|medium|high)$")


class ArtifactMetadata(BaseModel):
    """Artifact metadata response."""
    artifact_id: str
    name: str
    description: str
    creation_type: str
    created_at: str
    status: str  # "pending", "completed", "failed"
    test_count: int
    validation_errors: list[str]


class ArtifactCreateResponse(BaseModel):
    """Response to create request."""
    artifact_id: str
    status: str
    message: str
    metadata: Optional[ArtifactMetadata] = None


class ArtifactListResponse(BaseModel):
    """Paginated artifact list."""
    items: list[ArtifactMetadata]
    total: int
    limit: int
    offset: int


# ============================================================================
# Storage
# ============================================================================

def _artifacts_dir(tenant_id: str) -> Path:
    """Get artifacts directory for tenant."""
    global_dir = _forge_paths.tenant_global_dir(tenant_id)
    artifacts_dir = global_dir / "datahub_artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    return artifacts_dir


def _artifact_path(tenant_id: str, artifact_id: str) -> Path:
    """Get path to artifact JSON file."""
    return _artifacts_dir(tenant_id) / f"{artifact_id}.json"


def _artifact_list_path(tenant_id: str) -> Path:
    """Get path to artifact index JSONL."""
    return _artifacts_dir(tenant_id) / "index.jsonl"


def _write_artifact(tenant_id: str, artifact: dict) -> None:
    """Write artifact metadata to disk."""
    path = _artifact_path(tenant_id, artifact["artifact_id"])
    with open(path, "w") as f:
        json.dump(artifact, f, indent=2)
    
    # Also append to index
    index_path = _artifact_list_path(tenant_id)
    with open(index_path, "a") as f:
        f.write(json.dumps({
            "artifact_id": artifact["artifact_id"],
            "name": artifact["name"],
            "created_at": artifact["created_at"],
            "status": artifact["status"]
        }) + "\n")


def _read_artifact(tenant_id: str, artifact_id: str) -> Optional[dict]:
    """Read artifact metadata from disk."""
    path = _artifact_path(tenant_id, artifact_id)
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _artifact_exists(tenant_id: str, name: str) -> bool:
    """Check if artifact with name already exists."""
    artifacts_dir = _artifacts_dir(tenant_id)
    for path in artifacts_dir.glob("*.json"):
        try:
            with open(path) as f:
                artifact = json.load(f)
                if artifact.get("name") == name and artifact.get("status") != "deleted":
                    return True
        except (json.JSONDecodeError, OSError):
            continue
    return False


# ============================================================================
# Endpoints
# ============================================================================

@router.post("/datahub/create")
def create_artifact(
    req: ArtifactCreateRequest,
    rec: Annotated[_auth.SessionRecord, Depends(require_session)],
) -> ArtifactCreateResponse:
    """Create an artifact from data source.
    
    Returns 201 on success, 409 if artifact with same name already exists.
    """
    # Check if artifact already exists
    if _artifact_exists(rec.tenant_id, req.name):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Artifact with name '{req.name}' already exists"
        )
    
    artifact_id = str(uuid.uuid4())[:8]
    
    try:
        # Phase 1: Ingest data
        hub = DataHubSkill()
        ingestion = DataIngestion(
            source_type=DataSourceType(req.data_source),
            source_path=req.data_path,
            sample_rows=req.sample_rows,
            infer_schema=True
        )
        
        if not hub.ingest(ingestion):
            raise ValueError("Data ingestion failed")
        
        # Phase 2: Create artifact
        creator = UnifiedCreator()
        creation_req = CreationRequest(
            data=ingestion,
            creation_type=CreationType(req.creation_type),
            name=req.name,
            description=req.description,
            complexity=req.complexity,
            fail_closed=True
        )
        
        result = creator.create(creation_req)
        
        # Store metadata
        artifact = {
            "artifact_id": artifact_id,
            "name": req.name,
            "description": req.description,
            "creation_type": req.creation_type,
            "created_at": datetime.utcnow().isoformat() + "Z",
            "status": "completed" if result.success else "failed",
            "test_count": result.test_count,
            "validation_errors": result.validation_errors,
            "artifact_body_length": len(result.artifact_body),
            "tenant_id": rec.tenant_id,
            "created_by": rec.user_id,
        }
        
        _write_artifact(rec.tenant_id, artifact)
        
        metadata = ArtifactMetadata(
            artifact_id=artifact_id,
            name=req.name,
            description=req.description,
            creation_type=req.creation_type,
            created_at=artifact["created_at"],
            status=artifact["status"],
            test_count=result.test_count,
            validation_errors=result.validation_errors,
        )
        
        return ArtifactCreateResponse(
            artifact_id=artifact_id,
            status="completed" if result.success else "failed",
            message="Artifact created successfully" if result.success else "Artifact created with validation errors",
            metadata=metadata
        )
    
    except Exception as e:
        artifact = {
            "artifact_id": artifact_id,
            "name": req.name,
            "description": req.description,
            "creation_type": req.creation_type,
            "created_at": datetime.utcnow().isoformat() + "Z",
            "status": "failed",
            "error": str(e),
            "tenant_id": rec.tenant_id,
            "created_by": rec.user_id,
        }
        _write_artifact(rec.tenant_id, artifact)
        
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Artifact creation failed: {str(e)}"
        )


@router.get("/datahub/{artifact_id}")
def get_artifact(
    artifact_id: str,
    rec: Annotated[_auth.SessionRecord, Depends(require_session)],
) -> ArtifactMetadata:
    """Retrieve artifact metadata and preview."""
    artifact = _read_artifact(rec.tenant_id, artifact_id)
    
    if not artifact:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Artifact '{artifact_id}' not found"
        )
    
    if artifact.get("status") == "deleted":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Artifact '{artifact_id}' has been deleted"
        )
    
    return ArtifactMetadata(
        artifact_id=artifact_id,
        name=artifact["name"],
        description=artifact["description"],
        creation_type=artifact["creation_type"],
        created_at=artifact["created_at"],
        status=artifact["status"],
        test_count=artifact.get("test_count", 0),
        validation_errors=artifact.get("validation_errors", []),
    )


@router.delete("/datahub/{artifact_id}")
def delete_artifact(
    artifact_id: str,
    rec: Annotated[_auth.SessionRecord, Depends(require_session)],
) -> dict[str, str]:
    """Delete artifact (soft delete, audit logged)."""
    artifact = _read_artifact(rec.tenant_id, artifact_id)
    
    if not artifact:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Artifact '{artifact_id}' not found"
        )
    
    # Soft delete: mark status as deleted
    artifact["status"] = "deleted"
    artifact["deleted_at"] = datetime.utcnow().isoformat() + "Z"
    artifact["deleted_by"] = rec.user_id
    
    _write_artifact(rec.tenant_id, artifact)
    
    return {"status": "deleted", "artifact_id": artifact_id}


@router.get("/datahub/list")
def list_artifacts(
    rec: Annotated[_auth.SessionRecord, Depends(require_session)],
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> ArtifactListResponse:
    """List all artifacts (paginated)."""
    artifacts_dir = _artifacts_dir(rec.tenant_id)
    
    items: list[ArtifactMetadata] = []
    
    # Read all artifact files
    for path in sorted(artifacts_dir.glob("*.json")):
        if path.name == "index.jsonl":
            continue
        
        try:
            with open(path) as f:
                artifact = json.load(f)
                
                # Skip deleted artifacts
                if artifact.get("status") == "deleted":
                    continue
                
                items.append(ArtifactMetadata(
                    artifact_id=artifact["artifact_id"],
                    name=artifact["name"],
                    description=artifact["description"],
                    creation_type=artifact["creation_type"],
                    created_at=artifact["created_at"],
                    status=artifact["status"],
                    test_count=artifact.get("test_count", 0),
                    validation_errors=artifact.get("validation_errors", []),
                ))
        except (json.JSONDecodeError, OSError, KeyError):
            continue
    
    # Sort by created_at descending
    items.sort(key=lambda x: x.created_at, reverse=True)
    
    # Apply pagination
    total = len(items)
    paginated = items[offset:offset + limit]
    
    return ArtifactListResponse(
        items=paginated,
        total=total,
        limit=limit,
        offset=offset,
    )
