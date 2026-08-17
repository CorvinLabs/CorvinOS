"""TreeOfThoughts Learning Dashboard API — /v1/console/learning

Endpoints:
  GET /v1/console/learning/nodes    — fetch all TreeNodes with confidences
  POST /v1/console/learning/grade   — operator grades a pattern
  POST /v1/console/learning/note    — operator adds note to pattern
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.learning import LearningEventStore, LearningIntegration
import corvin_console.auth as _auth
from ..deps import require_session

router = APIRouter()


class GradeRequest(BaseModel):
    """Operator grades a pattern."""
    pattern_id: str
    grade: float  # -1.0 to +1.0
    reason: str = ""


class NoteRequest(BaseModel):
    """Operator adds a note to a pattern."""
    pattern_id: str
    text: str


class TreeNodeJSON(BaseModel):
    """JSON serialization of TreeNode."""
    id: str
    level: str  # "pattern" | "method" | "framework"
    name: str
    confidence: float
    calls_in_production: int
    when: list[str] = []
    anti_when: list[str] = []
    children: list[str] = []
    operator_notes: list = []
    adr_link: str | None = None


def get_learning_integration(session = Depends(require_session)) -> LearningIntegration:
    """Get LearningIntegration for this tenant."""
    # Per-tenant storage: ~/.corvin/tenants/{tenant_id}/learning/
    store_path = Path.home() / ".corvin" / "tenants" / session.tenant_id / "learning"
    return LearningIntegration(store_path)


@router.get("/v1/console/learning/debug", response_model=dict)
async def debug_learning(session = Depends(require_session)):
    """Debug endpoint — test if learning system is initialized."""
    try:
        store_path = Path.home() / ".corvin" / "tenants" / session.tenant_id / "learning"
        return {
            "tenant_id": session.tenant_id,
            "store_path": str(store_path),
            "store_exists": store_path.exists(),
            "store_is_dir": store_path.is_dir() if store_path.exists() else None,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Debug error: {str(e)}")


@router.get("/v1/console/learning/nodes", response_model=dict)
async def get_learning_nodes(
    integration: LearningIntegration = Depends(get_learning_integration),
    session = Depends(require_session),
):
    """Fetch all TreeNodes (Pattern/Method/Framework) with current confidences."""
    try:
        store = integration.store
        nodes = store.all_nodes()

        # Serialize to JSON
        serialized = []
        for node in nodes:
            serialized.append(TreeNodeJSON(
                id=node.id,
                level=node.level,
                name=node.name,
                confidence=node.confidence,
                calls_in_production=node.calls_in_production,
                when=node.when,
                anti_when=node.anti_when,
                children=node.children,
                operator_notes=node.operator_notes,
                adr_link=node.adr_link,
            ).model_dump())

        return {"nodes": serialized}
    except Exception as e:
        import traceback
        error_detail = f"{str(e)}\n{traceback.format_exc()}"
        raise HTTPException(status_code=500, detail=error_detail)


@router.post("/v1/console/learning/grade")
async def grade_pattern(
    request: GradeRequest,
    integration: LearningIntegration = Depends(get_learning_integration),
    session = Depends(require_session),
):
    """Operator manually grades a pattern."""
    try:
        # Clamp grade to [-1.0, +1.0]
        grade = max(-1.0, min(1.0, request.grade))
        
        integration.grade_pattern(
            request.pattern_id,
            grade,
            reason=f"Operator: {request.reason}"
        )
        
        # Return updated node
        node = integration.store.get_node(request.pattern_id)
        return {
            "pattern_id": request.pattern_id,
            "new_confidence": node.confidence if node else None,
            "status": "success"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/v1/console/learning/note")
async def add_operator_note(
    request: NoteRequest,
    integration: LearningIntegration = Depends(get_learning_integration),
    session = Depends(require_session),
):
    """Operator adds a note to a pattern."""
    try:
        node = integration.store.get_node(request.pattern_id)
        if not node:
            raise HTTPException(status_code=404, detail="Pattern not found")
        
        node.add_operator_note(session.user_id, request.text)
        
        return {
            "pattern_id": request.pattern_id,
            "notes_count": len(node.operator_notes),
            "status": "success"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
