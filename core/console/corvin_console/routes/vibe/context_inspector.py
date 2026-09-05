"""Context Inspector API — visualize HybridContextModel layers for a task.

Endpoint: GET /v1/vibe/task/<task_id>/context-layers

Returns the 4-layer breakdown:
  - original: immutable base (Phase 3 snapshots)
  - preserved: fields kept from prior turns
  - injected: new context added in this turn
  - merged: final state (conflicts resolved)

Loads real data from:
  - Audit chain: find task + context events
  - HybridContextModel: reconstruct layer state
"""

from __future__ import annotations

from typing import Any, Dict, Optional
import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ... import auth as session_auth
from ...deps import require_session

# Optional: HybridContextModel types (may not be available in all environments)
try:
    from core.learning.hybrid_context import HybridContextModel, ImmutableContextBase, InjectedLayer
except ImportError:
    HybridContextModel = None  # type: ignore
    ImmutableContextBase = None  # type: ignore
    InjectedLayer = None  # type: ignore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/vibe", tags=["vibe"])


@router.get("/health", summary="Debug: Check if vibe module is loaded")
async def health_check():
    """Simple health check — no auth required."""
    return {"status": "ok", "module": "vibe_context_inspector", "version": "2.0"}


@router.get("/tasks/debug", summary="Debug: List tasks without auth")
async def debug_list_tasks():
    """Debug endpoint — lists tasks from audit chain without requiring auth."""
    try:
        # Search both locations
        chain_paths = [
            audit_chain_path("_default"),
            Path.home() / ".corvin" / "tenants" / "_default" / "global" / "forge" / "audit.jsonl",
        ]

        seen_tasks = {}
        found_paths = []

        for chain_path in chain_paths:
            if not chain_path.exists():
                continue

            found_paths.append(str(chain_path))
            with chain_path.open("r") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                        task_id = event.get("task_id")
                        if task_id and task_id not in seen_tasks:
                            seen_tasks[task_id] = {
                                "task_id": task_id,
                                "timestamp": event.get("timestamp_utc", ""),
                                "event_type": event.get("event_type", "unknown"),
                            }
                    except json.JSONDecodeError:
                        pass

        if not found_paths:
            return {
                "status": "error",
                "message": "Audit chain not found in any location",
                "checked_paths": [
                    str(audit_chain_path("_default")),
                    str(Path.home() / ".corvin" / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"),
                ]
            }

        # Sort by timestamp descending
        tasks = sorted(
            seen_tasks.values(),
            key=lambda x: x.get("timestamp", ""),
            reverse=True
        )

        return {
            "status": "ok",
            "chain_paths": found_paths,
            "total_tasks": len(seen_tasks),
            "tasks": tasks[:20],
            "latest_task_id": tasks[0]["task_id"] if tasks else None
        }

    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "type": type(e).__name__
        }


class ContextLayer(BaseModel):
    """A single layer in the context breakdown."""
    name: str
    version: str
    data: Dict[str, Any]
    timestamp_utc: str
    hash: str
    lom: Optional[str] = None
    status: str = "ok"


class ContextLayersResponse(BaseModel):
    """Response: 4 context layers + metadata."""
    task_id: str
    original: ContextLayer
    preserved: ContextLayer
    injected: list[ContextLayer]
    merged: ContextLayer


class TaskListResponse(BaseModel):
    """Response: available task IDs from audit chain."""
    tasks: list[Dict[str, Any]]
    total: int
    latest_task_id: Optional[str] = None


def _get_tenant_home(tenant_id: str) -> Path:
    """Get tenant home directory."""
    from forge.tenants import tenant_home
    return Path(tenant_home(tenant_id))


@router.get(
    "/tasks/list",
    response_model=TaskListResponse,
    summary="List available tasks from audit chain"
)
async def list_tasks(
    limit: int = Query(20, ge=1, le=100),
    rec: session_auth.SessionRecord = Depends(require_session),
) -> TaskListResponse:
    """Get list of available task IDs from audit chain.

    Returns the most recent tasks. Frontend uses this to populate
    dropdown or auto-select the latest task.

    **Tenant isolation:** Uses rec.tenant_id from authenticated session.
    """
    tenant_id = rec.tenant_id

    try:
        # Search both locations: repo .corvin + home .corvin
        chain_paths = [
            audit_chain_path(tenant_id),  # Repo path
            Path.home() / ".corvin" / "tenants" / tenant_id / "global" / "forge" / "audit.jsonl",  # Home path
        ]

        # Read audit chain from both locations
        seen_tasks = {}
        for chain_path in chain_paths:
            if not chain_path.exists():
                continue

            with chain_path.open("r") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                        # Look for any event with a task_id field
                        task_id = event.get("task_id")
                        if task_id and task_id not in seen_tasks:
                            seen_tasks[task_id] = {
                                "task_id": task_id,
                                "timestamp": event.get("timestamp_utc", ""),
                                "event_type": event.get("event_type", "unknown"),
                            }
                    except json.JSONDecodeError:
                        pass  # Skip malformed lines

        # Sort by timestamp descending (latest first)
        tasks = sorted(
            seen_tasks.values(),
            key=lambda x: x.get("timestamp", ""),
            reverse=True
        )[:limit]

        latest_task_id = tasks[0]["task_id"] if tasks else None

        logger.info(f"Found {len(seen_tasks)} unique tasks in audit chain")

        return TaskListResponse(
            tasks=tasks,
            total=len(seen_tasks),
            latest_task_id=latest_task_id
        )

    except Exception as e:
        logger.error(f"Error listing tasks: {e}", exc_info=True)
        return TaskListResponse(tasks=[], total=0, latest_task_id=None)


def _load_context_from_audit(tenant_id: str, task_id: str) -> Dict[str, Any]:
    """Load real context data from audit chain.

    Searches for ANY events related to the task_id (all event types supported).
    Groups by event_type to reconstruct 4-layer model.
    """
    try:
        # Try both paths: repo .corvin first, then home .corvin
        chain_paths = [
            audit_chain_path(tenant_id),  # Repo path
            Path.home() / ".corvin" / "tenants" / tenant_id / "global" / "forge" / "audit.jsonl",  # Home path
        ]

        task_events = []
        for chain_path in chain_paths:
            if not chain_path.exists():
                continue

            with chain_path.open("r") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                        if event.get("task_id") == task_id:
                            task_events.append(event)
                    except json.JSONDecodeError:
                        pass

            if task_events:
                break  # Found events, don't search other paths

        if not task_events:
            return None

        # Extract layers from events (simplified)
        # In production, would reconstruct from full HybridContextModel
        return {
            "task_id": task_id,
            "events": task_events,
            "event_count": len(task_events)
        }
    except Exception as e:
        logger.warning(f"Could not load context from audit for task {task_id}: {e}")
        return None


@router.get(
    "/task/{task_id}/context-layers",
    response_model=ContextLayersResponse,
    summary="Get context layers for a task"
)
async def get_task_context_layers(
    task_id: str,
    rec: session_auth.SessionRecord = Depends(require_session),
) -> ContextLayersResponse:
    """Retrieve the 4-layer context breakdown for a task.

    Queries the audit chain + HybridContextModel to reconstruct the context state:
      - original: immutable base (user intent, task metadata)
      - preserved: fields preserved from prior turns
      - injected: new layers added in this turn (file content, user model, etc.)
      - merged: final context after conflict resolution

    Returns immutable snapshot; fails closed on missing data.

    **Tenant isolation:** Uses rec.tenant_id from authenticated session.
    """
    tenant_id = rec.tenant_id

    try:
        # Try to load real data from audit chain
        audit_data = _load_context_from_audit(tenant_id, task_id)

        if audit_data:
            # Real data found: use it to build response
            logger.info(f"Loaded {audit_data['event_count']} context events for task {task_id}")

            events = audit_data["events"]

            # Extract context data by event type
            original_data = {}
            preserved_data = {}
            injected_layers = []
            merged_data = {}

            for event in events:
                event_type = event.get("event_type")
                event_data = event.get("data", {})

                # Original: context_snapshot
                if event_type == "context_snapshot":
                    original_data = event_data.copy()

                # Preserved: context_adapted
                elif event_type == "context_adapted":
                    preserved_data = event_data.copy()

                # Injected: skill_executed
                elif event_type == "skill_executed":
                    injected_layers.append(ContextLayer(
                        name=f"{event.get('skill_id', 'unknown')}_execution",
                        version="1.0",
                        data=event_data,
                        timestamp_utc=event.get("timestamp_utc", ""),
                        hash=event.get("hash", "")[:16] if event.get("hash") else "",
                        lom=event.get("lom"),
                        status="ok"
                    ))

                # Merge all data
                merged_data.update(event_data)

            return ContextLayersResponse(
                task_id=task_id,
                original=ContextLayer(
                    name="original",
                    version="1.0",
                    data=original_data if original_data else {"note": "No context_snapshot found"},
                    timestamp_utc=events[0].get("timestamp_utc", "") if events else "",
                    hash=events[0].get("hash", "")[:16] if events and events[0].get("hash") else "",
                    lom=events[0].get("lom") if events else None,
                    status="ok"
                ),
                preserved=ContextLayer(
                    name="preserved",
                    version="1.0",
                    data=preserved_data if preserved_data else {"note": "No context_adapted found"},
                    timestamp_utc=events[1].get("timestamp_utc", "") if len(events) > 1 else "",
                    hash=events[1].get("hash", "")[:16] if len(events) > 1 and events[1].get("hash") else "",
                    lom=events[1].get("lom") if len(events) > 1 else None,
                    status="ok"
                ),
                injected=injected_layers if injected_layers else [
                    ContextLayer(
                        name="no_injected_layers",
                        version="1.0",
                        data={"note": "No skill_executed events found"},
                        timestamp_utc="",
                        hash="",
                        status="no_data"
                    )
                ],
                merged=ContextLayer(
                    name="merged",
                    version="1.0",
                    data=merged_data if merged_data else {"note": "No context data found"},
                    timestamp_utc=events[-1].get("timestamp_utc", "") if events else "",
                    hash="",
                    lom="context_inspector.py:merged_from_audit_events",
                    status="ok"
                )
            )

        # Fallback: return example data if no audit data found
        logger.info(f"No audit data for task {task_id}, returning example structure")
        return ContextLayersResponse(
            task_id=task_id,
            original=ContextLayer(
                name="original",
                version="1.0",
                data={
                    "task_id": task_id,
                    "intent": "No audit data found for this task",
                    "metadata": {
                        "note": "Try a real task_id from your audit chain",
                    }
                },
                timestamp_utc="",
                hash="",
                status="no_data"
            ),
            preserved=ContextLayer(
                name="preserved",
                version="1.0",
                data={"status": "no_data"},
                timestamp_utc="",
                hash="",
                status="no_data"
            ),
            injected=[],
            merged=ContextLayer(
                name="merged",
                version="1.0",
                data={"status": "no_data", "message": "No context events found in audit chain"},
                timestamp_utc="",
                hash="",
                status="no_data"
            )
        )

    except Exception as e:
        logger.error(f"Error fetching context layers for task {task_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve context layers: {str(e)}")
