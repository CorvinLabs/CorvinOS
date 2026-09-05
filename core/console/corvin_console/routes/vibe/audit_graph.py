"""Audit Graph API — DAG visualization of audit-chain events.

Endpoint: GET /v1/vibe/audit/graph

Returns nodes (events) + edges (hash-chain causality) for DAG visualization.
Shows real audit events: boot, compliance, layer_integrity, etc.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple
from datetime import datetime
from pathlib import Path
import json
import logging

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from ... import auth as session_auth
from ...deps import require_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/audit", tags=["vibe-audit"])


class GraphNode(BaseModel):
    """A node in the DAG (an audit event)."""
    id: str  # The event's hash
    event_type: str
    ts: float
    severity: str = "INFO"
    run_id: str = ""
    details: Dict[str, Any]


class GraphEdge(BaseModel):
    """An edge in the DAG (hash-chain link)."""
    from_node: str
    to_node: str
    type: str = "hash-chain"


class AuditGraphResponse(BaseModel):
    """DAG response: nodes, edges, critical path, anomalies."""
    nodes: List[GraphNode]
    edges: List[GraphEdge]
    total_events: int
    critical_path: List[str] = []
    anomalies: List[Dict[str, Any]] = []


def _find_audit_chain(tenant_id: str) -> Optional[Path]:
    """Find audit chain in both locations."""
    # Try both standard locations
    paths = [
        Path("/home/shumway/projects/CorvinOS/.corvin/tenants") / tenant_id / "global" / "forge" / "audit.jsonl",
        Path.home() / ".corvin" / "tenants" / tenant_id / "global" / "forge" / "audit.jsonl",
    ]

    for path in paths:
        if path.exists():
            return path
    return None


def _build_dag(
    events: List[Dict[str, Any]],
    since_ts: Optional[float] = None,
    until_ts: Optional[float] = None,
) -> Tuple[List[GraphNode], List[GraphEdge], List[str], List[Dict[str, Any]]]:
    """Build DAG from events.

    Returns: (nodes, edges, critical_path, anomalies)
    """
    # Filter by timestamp
    filtered_events = []
    for event in events:
        ts = event.get("ts", 0)
        if since_ts and ts < since_ts:
            continue
        if until_ts and ts > until_ts:
            continue
        filtered_events.append(event)

    # Create nodes
    nodes = []
    hash_to_event = {}
    for event in filtered_events:
        hash_val = event.get("hash", "")
        if not hash_val:
            continue

        hash_to_event[hash_val] = event
        nodes.append(GraphNode(
            id=hash_val,
            event_type=event.get("event_type", "unknown"),
            ts=event.get("ts", 0),
            severity=event.get("severity", "INFO"),
            run_id=event.get("run_id", ""),
            details=event.get("details", {}),
        ))

    # Create edges (hash-chain causality)
    edges = []
    node_ids = set(n.id for n in nodes)
    for event in filtered_events:
        curr_hash = event.get("hash", "")
        prev_hash = event.get("prev_hash", "")

        if not curr_hash or not prev_hash:
            continue

        # Only create edge if both nodes exist
        if prev_hash in node_ids and curr_hash in node_ids:
            edges.append(GraphEdge(
                from_node=prev_hash,
                to_node=curr_hash,
                type="hash-chain"
            ))

    # Find critical path (longest path in DAG)
    critical_path = _find_critical_path(nodes, edges)

    # Detect anomalies
    anomalies = _detect_anomalies(nodes, edges)

    return nodes, edges, critical_path, anomalies


def _find_critical_path(nodes: List[GraphNode], edges: List[GraphEdge]) -> List[str]:
    """Find the longest path in the DAG (topological sort + longest path)."""
    if not nodes or not edges:
        return [n.id for n in nodes[:10]]  # Return first 10 if no edges

    # Build adjacency list
    graph = {n.id: [] for n in nodes}
    in_degree = {n.id: 0 for n in nodes}

    for edge in edges:
        graph[edge.from_node].append(edge.to_node)
        in_degree[edge.to_node] += 1

    # Topological sort
    queue = [n_id for n_id in in_degree if in_degree[n_id] == 0]
    topo_order = []
    while queue:
        node = queue.pop(0)
        topo_order.append(node)
        for neighbor in graph[node]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    # Longest path
    if not topo_order:
        return []

    dist = {n: 0 for n in graph}
    parent = {n: None for n in graph}

    for node in topo_order:
        for neighbor in graph[node]:
            if dist[node] + 1 > dist[neighbor]:
                dist[neighbor] = dist[node] + 1
                parent[neighbor] = node

    # Reconstruct path
    furthest = max(dist, key=dist.get)
    path = []
    current = furthest
    while current:
        path.append(current)
        current = parent[current]

    return list(reversed(path))


def _detect_anomalies(nodes: List[GraphNode], edges: List[GraphEdge]) -> List[Dict[str, Any]]:
    """Detect anomalies: cycles, disconnected components, etc."""
    anomalies = []

    # Check for cycles (shouldn't exist in audit chain)
    graph = {n.id: [] for n in nodes}
    for edge in edges:
        graph[edge.from_node].append(edge.to_node)

    visited = set()
    rec_stack = set()

    def has_cycle(node: str) -> bool:
        visited.add(node)
        rec_stack.add(node)

        for neighbor in graph.get(node, []):
            if neighbor not in visited:
                if has_cycle(neighbor):
                    return True
            elif neighbor in rec_stack:
                return True

        rec_stack.remove(node)
        return False

    for node in graph:
        if node not in visited:
            if has_cycle(node):
                anomalies.append({
                    "type": "cycle_detected",
                    "severity": "WARNING",
                    "message": "Audit chain has a cycle (shouldn't happen)"
                })
                break

    # Check for disconnected components
    if nodes and edges:
        connected = set()
        queue = [nodes[0].id]
        visited_conn = set()

        while queue:
            node = queue.pop(0)
            if node in visited_conn:
                continue
            visited_conn.add(node)
            connected.add(node)

            for edge in edges:
                if edge.from_node == node:
                    if edge.to_node not in visited_conn:
                        queue.append(edge.to_node)
                if edge.to_node == node:
                    if edge.from_node not in visited_conn:
                        queue.append(edge.from_node)

        if len(connected) < len(nodes):
            anomalies.append({
                "type": "disconnected_component",
                "severity": "INFO",
                "message": f"{len(nodes) - len(connected)} orphaned events"
            })

    return anomalies


@router.get(
    "/graph",
    response_model=AuditGraphResponse,
    summary="Get audit-chain as DAG"
)
async def get_audit_graph(
    since: Optional[float] = Query(None, description="Unix timestamp (seconds)"),
    until: Optional[float] = Query(None, description="Unix timestamp (seconds)"),
    limit: int = Query(1000, ge=1, le=5000, description="Max events to include"),
) -> AuditGraphResponse:
    """Get audit-chain events as a DAG.

    Returns nodes (events) + edges (hash-chain causality) for graph visualization.
    Includes critical path (longest chain) and anomalies (cycles, disconnected).

    **Debug mode:** No authentication required (temporary for MVP).
    """
    tenant_id = "_default"  # Use default tenant for debug

    try:
        chain_path = _find_audit_chain(tenant_id)

        if not chain_path:
            return AuditGraphResponse(
                nodes=[],
                edges=[],
                total_events=0,
                critical_path=[],
                anomalies=[{
                    "type": "no_audit_chain",
                    "severity": "INFO",
                    "message": "No audit chain found"
                }]
            )

        # Read audit chain
        events = []
        with chain_path.open("r") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                    events.append(event)
                    if len(events) >= limit:
                        break
                except json.JSONDecodeError:
                    pass

        logger.info(f"Loaded {len(events)} events for DAG")

        # Build DAG
        nodes, edges, critical_path, anomalies = _build_dag(events, since, until)

        return AuditGraphResponse(
            nodes=nodes,
            edges=edges,
            total_events=len(events),
            critical_path=critical_path,
            anomalies=anomalies
        )

    except Exception as e:
        logger.error(f"Error building audit graph: {e}", exc_info=True)
        raise
