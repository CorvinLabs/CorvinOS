"""Audit Graph API — DAG visualization of audit-chain events.

Endpoint: GET /v1/console/audit/graph (session-authenticated)

Returns nodes (events) + edges (hash-chain causality) for DAG visualization.
Shows real audit events: boot, compliance, layer_integrity, etc.
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Any, Dict, List, Optional, Set, Tuple
from datetime import datetime
from pathlib import Path
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ... import _bootstrap
from ... import auth as session_auth
from ...chain_tail import DEFAULT_MAX_SCAN_BYTES, iter_chain_tail, scan_window
from ...deps import require_session

_forge_paths = _bootstrap.forge_paths

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
    # What the tail scan covered. ``truncated``: the chain is larger than the
    # scanned window and fewer than ``limit`` events were found in it.
    scan: Dict[str, Any] = {}


def _find_audit_chain(tenant_id: str) -> Optional[Path]:
    """The tenant's core audit chain under ``CORVIN_HOME`` (or None if absent)."""
    # The ONE resolver of the tenant chain - never composed by hand.
    path = Path(_forge_paths.tenant_audit_chain(tenant_id))
    return path if path.exists() else None


def _load_graph(
    tenant_id: str,
    limit: int,
    since: Optional[float],
    until: Optional[float],
    *,
    max_scan_bytes: int = DEFAULT_MAX_SCAN_BYTES,
) -> Optional[Tuple[int, List[GraphNode], List[GraphEdge], List[str], List[Dict[str, Any]]]]:
    """Read the newest ``limit`` chain records and build the DAG from them.

    BLOCKING (file read + graph build) — the handler awaits it through
    ``asyncio.to_thread``. Streams the last ``max_scan_bytes`` of the chain;
    returns None when the tenant has no chain file. It used to run on the
    event loop and return the OLDEST ``limit`` records of the file.
    """
    chain_path = _find_audit_chain(tenant_id)
    if not chain_path:
        return None
    # The time window is applied BEFORE the limit. It used to be applied after:
    # the newest ``limit`` records were taken first, so a window lying before
    # them came back empty - and was reported as a complete answer.
    events: deque[Dict[str, Any]] = deque(maxlen=limit)
    events.extend(
        event for event in iter_chain_tail(chain_path, max_scan_bytes=max_scan_bytes)
        if _in_window(event, since, until)
    )
    nodes, edges, critical_path, anomalies = _build_dag(list(events), since, until)
    return len(events), nodes, edges, critical_path, anomalies


def _in_window(event: Dict[str, Any], since: Optional[float], until: Optional[float]) -> bool:
    """The same test ``_build_dag`` applies (a record without ``ts`` counts as 0)."""
    ts = event.get("ts", 0)
    if not isinstance(ts, (int, float)) or isinstance(ts, bool):
        ts = 0
    if since and ts < since:
        return False
    if until and ts > until:
        return False
    return True


def _graph_scan(tenant_id: str, found: int, limit: int) -> Dict[str, Any]:
    """Scan coverage for the graph (BLOCKING).

    ``found`` is the number of records INSIDE the requested time window.
    ``limit_reached``: the limit was filled, so the answer is the newest
    ``limit`` of them and older ones in the window may exist. ``truncated``:
    the chain is larger than the scanned tail and the limit was not filled,
    so records of the window may lie in the unscanned part.
    """
    chain_path = _find_audit_chain(tenant_id)
    if not chain_path:
        return {"truncated": False, "limit_reached": False, "scanned_bytes": 0,
                "total_bytes": 0, "covers_since": None}
    scan = scan_window(chain_path)
    scan["limit_reached"] = found >= limit
    if found >= limit:
        scan["truncated"] = False
    return scan


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
    rec: session_auth.SessionRecord = Depends(require_session),
) -> AuditGraphResponse:
    """Get audit-chain events as a DAG.

    Returns nodes (events) + edges (hash-chain causality) for graph visualization.
    Includes critical path (longest chain) and anomalies (cycles, disconnected).

    **Tenant isolation:** the chain read is the authenticated session's
    ``rec.tenant_id`` — never a query parameter, never a hardcoded default.
    """
    tenant_id = rec.tenant_id

    try:
        loaded = await asyncio.to_thread(_load_graph, tenant_id, limit, since, until)

        if loaded is None:
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

        total, nodes, edges, critical_path, anomalies = loaded
        logger.info("Loaded %d events for DAG", total)
        scan = await asyncio.to_thread(_graph_scan, tenant_id, total, limit)

        return AuditGraphResponse(
            nodes=nodes,
            edges=edges,
            total_events=total,
            critical_path=critical_path,
            anomalies=anomalies,
            scan=scan,
        )

    except OSError as e:
        logger.error(f"Error building audit graph: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="failed to read audit chain")
