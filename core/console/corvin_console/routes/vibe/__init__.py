"""Vibe Engineering suite routers for ACP era.

Exposed:
  - context_inspector: Task context layer visualization
  - audit_graph: Audit chain DAG visualization
"""

from fastapi import APIRouter

from . import context_inspector, audit_graph

router = APIRouter()
router.include_router(context_inspector.router)
router.include_router(audit_graph.router)

__all__ = ["router"]
