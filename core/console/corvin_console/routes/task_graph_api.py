"""
ADR-0400: Task Graph API Endpoints

REST API for TaskGraph visualization and queries.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Optional, Dict, Any, List
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
import logging
import json
import sys
from pathlib import Path

from .. import auth as session_auth
from ..deps import require_session
from .. import _bootstrap

_forge_paths = _bootstrap.forge_paths

logger = logging.getLogger(__name__)

# Add core to path for imports
_core_path = Path(__file__).parent.parent.parent.parent / "vibe_engineering"
if str(_core_path.parent) not in sys.path:
    sys.path.insert(0, str(_core_path.parent))

# Import task graph modules
TaskGraph = None
GraphQueries = None
CheckpointManager = None
CheckpointToGraphConverter = None

try:
    from vibe_engineering.task_graph import TaskGraph, Node, Edge
    from vibe_engineering.graph_queries import GraphQueries
    from vibe_engineering.checkpoint_manager import CheckpointManager
    from vibe_engineering.checkpoint_to_graph import CheckpointToGraphConverter
    logger.info("✅ Task graph modules loaded successfully")
except ImportError as e:
    logger.warning(f"⚠️  Task graph modules not available: {e}")

# NOTE: Authentication + tenant isolation are enforced per-endpoint via
# ``Depends(require_session)`` (deps.require_session → 401 when no live session).
# The authenticated ``SessionRecord.tenant_id`` — never an env var, never a
# hardcoded default — is the ONLY source of the tenant used to scope checkpoint
# reads (CLAUDE.md multi-tenant axis, ADR-0007; GDPR Art. 5/32).


# ===== Request/Response Models =====

class NodeResponse(BaseModel):
    """Single graph node."""
    id: str
    type: str
    timestamp: str
    data: Dict[str, Any]


class EdgeResponse(BaseModel):
    """Single graph edge."""
    from_id: str
    to_id: str
    edge_type: str
    label: str
    metadata: Dict[str, Any]


class TaskGraphResponse(BaseModel):
    """Complete task graph."""
    task_id: str
    created_at: str
    nodes: List[NodeResponse]
    edges: List[EdgeResponse]
    nodes_by_type: Dict[str, List[str]]
    iterations: Dict[int, str]
    stats: Dict[str, Any]


class ReachabilityResponse(BaseModel):
    """Reachability query result."""
    source_node_id: str
    reachable_nodes: List[str]
    count: int


class CriticalPathResponse(BaseModel):
    """Critical path query result."""
    path: List[str]
    length: int
    node_types: List[str]


class ImpactAnalysisResponse(BaseModel):
    """Impact analysis query result."""
    source_node_id: str
    affected_nodes: List[str]
    affected_by_type: Dict[str, int]


class TimelineResponse(BaseModel):
    """Timeline of events."""
    events: List[Dict[str, str]]  # [{timestamp, node_id, node_type}, ...]


class TaskSummaryResponse(BaseModel):
    """One task that has at least one persisted checkpoint."""
    task_id: str
    checkpoint_id: str
    iteration_num: int
    timestamp: str
    phase: str
    goal: str
    checkpoint_count: int


class TaskListResponse(BaseModel):
    """All tasks a graph can be rendered for."""
    tasks: List[TaskSummaryResponse]
    count: int


class GraphExportResponse(BaseModel):
    """Graph export result."""
    format: str
    content: str
    size_bytes: int


# ===== Router Setup =====

router = APIRouter(prefix="/api/tasks", tags=["task-graph"])


# ===== Utilities =====

def _safe_task_id(task_id: str) -> str:
    """Reject any task_id that could escape the tenant's checkpoint dir.

    Defense-in-depth: tenant isolation must be intrinsic to the checkpoint
    layer, not merely a side effect of the router's ``[^/]+`` path constraint.
    ``CheckpointManager.list_checkpoints`` globs ``f"{task_id}_*.json"`` and
    ``get_latest`` builds on it, so a task_id carrying a path separator or a
    ``..`` component would read (or list) checkpoints OUTSIDE the tenant's dir —
    e.g. ``../../<other-tenant>/vibe/checkpoints/foo``. This guard fails closed
    with a clean 400 before any glob/read touches the filesystem, so the
    isolation guarantee holds even if the route constraint is loosened or the
    function is called directly (CLAUDE.md multi-tenant axis, ADR-0007; GDPR
    Art. 5/32).

    Raises:
        HTTPException 400 — empty/blank task_id or one containing a NUL byte,
        ``/``, ``\\``, or a ``..`` component.
    """
    if not task_id or not task_id.strip():
        raise HTTPException(status_code=400, detail="task_id must not be empty")
    if "\x00" in task_id or "/" in task_id or "\\" in task_id or ".." in task_id:
        raise HTTPException(status_code=400, detail="Invalid task_id")
    return task_id


def _manager_for_tenant(tenant_id: str) -> Optional[CheckpointManager]:
    """Return a CheckpointManager scoped to a single tenant's checkpoint dir.

    Checkpoints live under ``<tenant_home>/vibe/checkpoints/`` so one tenant can
    never read another's task graphs. ``forge_paths.tenant_home`` validates the
    tenant_id fail-closed (path-traversal safe); the tenant_id itself always
    comes from the authenticated ``SessionRecord``, never an env var.
    """
    if CheckpointManager is None:
        return None
    checkpoint_dir = _forge_paths.tenant_home(tenant_id) / "vibe" / "checkpoints"
    return CheckpointManager(checkpoint_dir=checkpoint_dir)


def get_task_graph(task_id: str, manager: Optional[CheckpointManager]) -> Optional[TaskGraph]:
    """
    Load task graph from latest checkpoint.

    Args:
        task_id: Task identifier
        manager: Tenant-scoped CheckpointManager (from ``_manager_for_tenant``)

    Returns:
        TaskGraph or None if not found
    """
    # Intrinsic path-traversal guard — runs BEFORE any glob/read so a hostile
    # task_id can never escape the tenant dir even when this function is called
    # directly (not just through the router). Raises HTTPException 400.
    _safe_task_id(task_id)

    if not manager or not CheckpointToGraphConverter:
        logger.error("Checkpoint manager or converter not available")
        return None

    try:
        latest_checkpoint = manager.get_latest(task_id)
        if not latest_checkpoint:
            logger.warning(f"No checkpoint found for task {task_id}")
            return None

        # Try to get pre-built graph from checkpoint if available
        if hasattr(latest_checkpoint, "graph") and latest_checkpoint.graph:
            try:
                return TaskGraph.from_json(latest_checkpoint.graph)
            except Exception as e:
                logger.warning(f"Failed to load graph from checkpoint: {e}")

        # Fallback: convert checkpoint to graph
        graph = CheckpointToGraphConverter.convert(latest_checkpoint)
        return graph
    except Exception as e:
        logger.error(f"Failed to load graph for task {task_id}: {e}")
        return None


# ===== Endpoints =====


@router.get("/graphs", response_model=TaskListResponse)
async def list_tasks_with_graphs(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
):
    """
    GET /api/tasks/graphs

    List every task that has at least one persisted checkpoint, so the
    console can offer a picker instead of requiring a task_id to be known
    up front. Registered BEFORE the ``/{task_id}/...`` routes on purpose:
    FastAPI matches in declaration order, so a later registration would be
    shadowed by ``/{task_id}`` and "graphs" would be read as a task id.

    Returns:
        TaskListResponse — newest checkpoint first, empty list when none exist.
    """
    manager = _manager_for_tenant(rec.tenant_id)
    if not manager:
        return TaskListResponse(tasks=[], count=0)

    summaries: Dict[str, TaskSummaryResponse] = {}
    counts: Dict[str, int] = {}

    try:
        for filepath in sorted(manager.checkpoint_dir.glob("*.json")):
            try:
                checkpoint = manager.load(filepath)
            except Exception as e:  # noqa: BLE001 — one bad file must not hide the rest
                logger.warning(f"Skipping unreadable checkpoint {filepath.name}: {e}")
                continue

            counts[checkpoint.task_id] = counts.get(checkpoint.task_id, 0) + 1
            previous = summaries.get(checkpoint.task_id)
            if previous is not None and previous.timestamp >= checkpoint.timestamp_iso:
                continue

            task_state = checkpoint.task_state or {}
            summaries[checkpoint.task_id] = TaskSummaryResponse(
                task_id=checkpoint.task_id,
                checkpoint_id=checkpoint.checkpoint_id,
                iteration_num=checkpoint.iteration_num,
                timestamp=checkpoint.timestamp_iso,
                phase=checkpoint.phase,
                goal=str(task_state.get("goal", "")),
                checkpoint_count=0,
            )
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed to list checkpoints: {e}")
        return TaskListResponse(tasks=[], count=0)

    tasks = [
        summary.model_copy(update={"checkpoint_count": counts[task_id]})
        for task_id, summary in summaries.items()
    ]
    tasks.sort(key=lambda t: t.timestamp, reverse=True)
    return TaskListResponse(tasks=tasks, count=len(tasks))

@router.get("/{task_id}/graph", response_model=TaskGraphResponse)
async def get_graph(
    task_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
):
    """
    GET /api/tasks/{task_id}/graph

    Retrieve complete task graph.

    Args:
        task_id: Task identifier

    Returns:
        TaskGraphResponse with full graph structure

    Raises:
        404: Task or graph not found
    """
    try:
        graph = get_task_graph(task_id, _manager_for_tenant(rec.tenant_id))
        if not graph:
            raise HTTPException(status_code=404, detail=f"Graph not found for task {task_id}")

        # Convert to response model
        nodes = [
            NodeResponse(
                id=node.id,
                type=node.type,
                timestamp=node.timestamp,
                data=node.data
            )
            for node in graph.nodes.values()
        ]

        edges = [
            EdgeResponse(
                from_id=edge.from_id,
                to_id=edge.to_id,
                edge_type=edge.edge_type,
                label=edge.label,
                metadata=edge.metadata
            )
            for edge in graph.edges
        ]

        return TaskGraphResponse(
            task_id=graph.task_id,
            created_at=graph.created_at,
            nodes=nodes,
            edges=edges,
            nodes_by_type=graph.nodes_by_type,
            iterations=graph.iterations,
            stats=graph.get_stats()
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to retrieve graph for task {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{task_id}/graph/query", response_model=Dict[str, Any])
async def query_graph(
    task_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    type: str = Query(..., description="Query type: reachability|critical_path|impact|timeline|blocking_nodes"),
    node: Optional[str] = Query(None, description="Node ID for reachability/impact queries"),
):
    """
    GET /api/tasks/{task_id}/graph/query?type={type}&node={id}

    Execute graph queries.

    Query types:
    - reachability: Find all nodes reachable from given node
    - critical_path: Find longest path in graph
    - impact: Find nodes affected by given node (same as reachability)
    - timeline: Get chronological sequence of events
    - blocking_nodes: Find high-fan-out nodes

    Args:
        task_id: Task identifier
        type: Query type
        node: Node ID (required for reachability/impact)

    Returns:
        Query-specific response

    Raises:
        400: Invalid query parameters
        404: Task/node not found
    """
    try:
        graph = get_task_graph(task_id, _manager_for_tenant(rec.tenant_id))
        if not graph:
            raise HTTPException(status_code=404, detail=f"Graph not found for task {task_id}")

        if type == "reachability":
            if not node:
                raise HTTPException(status_code=400, detail="node parameter required for reachability query")
            if node not in graph.nodes:
                raise HTTPException(status_code=404, detail=f"Node {node} not found")

            reachable = GraphQueries.reachability(graph, node)
            return {
                "query_type": "reachability",
                "source_node_id": node,
                "reachable_nodes": sorted(reachable),
                "count": len(reachable)
            }

        elif type == "critical_path":
            path = GraphQueries.critical_path(graph)
            node_types = [graph.nodes[nid].type for nid in path if nid in graph.nodes]
            return {
                "query_type": "critical_path",
                "path": path,
                "length": len(path),
                "node_types": node_types
            }

        elif type == "impact":
            if not node:
                raise HTTPException(status_code=400, detail="node parameter required for impact query")
            if node not in graph.nodes:
                raise HTTPException(status_code=404, detail=f"Node {node} not found")

            affected = GraphQueries.impact_analysis(graph, node)
            type_counts = {}
            for nid in affected:
                ntype = graph.nodes[nid].type
                type_counts[ntype] = type_counts.get(ntype, 0) + 1

            return {
                "query_type": "impact",
                "source_node_id": node,
                "affected_nodes": sorted(affected),
                "affected_by_type": type_counts
            }

        elif type == "timeline":
            timeline = GraphQueries.get_timeline(graph)
            events = [
                {
                    "timestamp": ts,
                    "node_id": nid,
                    "node_type": ntype
                }
                for ts, nid, ntype in timeline
            ]
            return {
                "query_type": "timeline",
                "events": events,
                "total_events": len(events)
            }

        elif type == "blocking_nodes":
            blocking = GraphQueries.find_blocking_nodes(graph)
            return {
                "query_type": "blocking_nodes",
                "blocking_nodes": [{"node_id": nid, "fan_out": count} for nid, count in blocking[:10]],
                "total_blocking": len(blocking)
            }

        else:
            raise HTTPException(status_code=400, detail=f"Unknown query type: {type}")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Query failed for task {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{task_id}/graph/snapshot")
async def get_graph_snapshot(
    task_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    t: Optional[str] = Query(None, description="ISO timestamp for historical snapshot"),
):
    """
    GET /api/tasks/{task_id}/graph/snapshot?t={timestamp}

    Get historical graph at specific timestamp.

    Args:
        task_id: Task identifier
        t: ISO timestamp (if omitted, returns latest)

    Returns:
        TaskGraphResponse at given timestamp

    Raises:
        404: Graph or timestamp not found
    """
    try:
        # Intrinsic path-traversal guard — this endpoint globs via
        # ``list_checkpoints`` directly (not through ``get_task_graph``), so it
        # needs its own check. Raises HTTPException 400 on a hostile task_id.
        _safe_task_id(task_id)

        manager = _manager_for_tenant(rec.tenant_id)
        if not manager or not CheckpointToGraphConverter:
            raise HTTPException(status_code=503, detail="Checkpoint system unavailable")

        # Get all checkpoints for task
        checkpoints = manager.list_checkpoints(task_id)
        if not checkpoints:
            raise HTTPException(status_code=404, detail=f"No checkpoints for task {task_id}")

        # Filter by timestamp if provided
        if t:
            try:
                target_time = datetime.fromisoformat(t)
                # Find checkpoint closest to target time (before or equal)
                matching = [
                    cp for cp in checkpoints
                    if cp.timestamp <= target_time
                ]
                if not matching:
                    raise HTTPException(
                        status_code=404,
                        detail=f"No checkpoint before {t}"
                    )
                checkpoint_metadata = matching[0]  # Most recent before target
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid timestamp format: {t}")
        else:
            checkpoint_metadata = checkpoints[0]  # Latest

        # Load and convert checkpoint
        checkpoint = manager.load(checkpoint_metadata.file_path)
        graph = CheckpointToGraphConverter.convert(checkpoint)

        # Convert to response
        nodes = [
            NodeResponse(
                id=node.id,
                type=node.type,
                timestamp=node.timestamp,
                data=node.data
            )
            for node in graph.nodes.values()
        ]

        edges = [
            EdgeResponse(
                from_id=edge.from_id,
                to_id=edge.to_id,
                edge_type=edge.edge_type,
                label=edge.label,
                metadata=edge.metadata
            )
            for edge in graph.edges
        ]

        return TaskGraphResponse(
            task_id=graph.task_id,
            created_at=graph.created_at,
            nodes=nodes,
            edges=edges,
            nodes_by_type=graph.nodes_by_type,
            iterations=graph.iterations,
            stats=graph.get_stats()
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get snapshot for task {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{task_id}/graph/export")
async def export_graph(
    task_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    format: str = Query("json", description="Export format: json|dot"),
):
    """
    POST /api/tasks/{task_id}/graph/export?format={format}

    Export graph in specified format.

    Supported formats:
    - json: JSON serialization (native)
    - dot: DOT format (for Graphviz)

    Args:
        task_id: Task identifier
        format: Export format

    Returns:
        GraphExportResponse with serialized graph

    Raises:
        404: Graph not found
        400: Unsupported format
    """
    try:
        graph = get_task_graph(task_id, _manager_for_tenant(rec.tenant_id))
        if not graph:
            raise HTTPException(status_code=404, detail=f"Graph not found for task {task_id}")

        if format == "json":
            content = graph.to_json()
            return {
                "format": "json",
                "content": content,
                "size_bytes": len(content.encode())
            }

        elif format == "dot":
            # Convert to DOT format (Graphviz)
            dot_lines = [
                "digraph TaskGraph {",
                '    rankdir=LR;',
                '    graph [overlap=false];'
            ]

            # Add nodes
            for node in graph.nodes.values():
                label = f"{node.type}\\n{node.id[:8]}"
                color = _get_node_color(node.type)
                dot_lines.append(
                    f'    "{node.id}" [label="{label}", shape=box, color="{color}"];'
                )

            # Add edges
            for edge in graph.edges:
                style = "solid" if edge.edge_type == "hard_dependency" else "dashed"
                dot_lines.append(
                    f'    "{edge.from_id}" -> "{edge.to_id}" [label="{edge.edge_type}", style={style}];'
                )

            dot_lines.append("}")
            content = "\n".join(dot_lines)
            return {
                "format": "dot",
                "content": content,
                "size_bytes": len(content.encode())
            }

        else:
            raise HTTPException(status_code=400, detail=f"Unsupported format: {format}")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Export failed for task {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _get_node_color(node_type: str) -> str:
    """Get color for node type (for DOT visualization)."""
    colors = {
        "checkpoint": "lightblue",
        "decision": "lightgreen",
        "error": "lightcoral",
        "context": "lightyellow",
        "metric": "lightgray",
        "subgoal": "plum"
    }
    return colors.get(node_type, "white")
