"""Knowledge Graph implementation for Quality Gates System (ADR-0688).

Provides graph queries and mutations via DuckDB backend.
"""

import duckdb
import json
from datetime import datetime
from typing import List, Optional
import logging

from .models import KGNode, KGEdge, KGNodeType
from .schema import GateSchema

logger = logging.getLogger(__name__)


class VerifyResult:
    """Result of chain verification."""

    def __init__(self, verified: bool, height: int, gaps: int, message: str):
        self.verified = verified
        self.height = height
        self.gaps = gaps
        self.message = message


class KnowledgeGraph:
    """Knowledge Graph backed by DuckDB."""

    def __init__(self, db_path: str, tenant_id: str):
        """Initialize Knowledge Graph.

        Args:
            db_path: Path to DuckDB database
            tenant_id: Tenant ID for all operations

        Raises:
            ValueError: If tenant_id is empty
        """
        if not tenant_id:
            raise ValueError("tenant_id is required")

        self.db_path = db_path
        self.tenant_id = tenant_id
        self.conn = GateSchema.initialize(db_path)

    def write_node(self, node: KGNode) -> str:
        """Write a node to the graph.

        Args:
            node: KGNode to write

        Returns:
            Node ID

        Raises:
            ValueError: If node.tenant_id != self.tenant_id
        """
        if node.tenant_id != self.tenant_id:
            raise ValueError(
                f"Node tenant_id {node.tenant_id} != graph tenant_id {self.tenant_id}"
            )

        created_at = node.created_at or datetime.utcnow().isoformat() + "Z"
        data_json = json.dumps(node.data)

        self.conn.execute(
            "INSERT OR REPLACE INTO kg_nodes (id, tenant_id, node_type, data, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            [node.id, node.tenant_id, node.node_type.value, data_json, created_at],
        )

        logger.debug(f"Wrote node {node.id} to graph")
        return node.id

    def write_edge(self, edge: KGEdge) -> str:
        """Write an edge to the graph.

        Args:
            edge: KGEdge to write

        Returns:
            Edge ID (source_id:target_id:relationship_type)

        Raises:
            ValueError: If edge.tenant_id != self.tenant_id
        """
        if edge.tenant_id != self.tenant_id:
            raise ValueError(
                f"Edge tenant_id {edge.tenant_id} != graph tenant_id {self.tenant_id}"
            )

        created_at = edge.created_at or datetime.utcnow().isoformat() + "Z"
        data_json = json.dumps(edge.data) if edge.data else None

        self.conn.execute(
            "INSERT OR REPLACE INTO kg_edges "
            "(source_id, target_id, relationship_type, tenant_id, data, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                edge.source_id,
                edge.target_id,
                edge.relationship_type,
                edge.tenant_id,
                data_json,
                created_at,
            ],
        )

        edge_id = f"{edge.source_id}:{edge.target_id}:{edge.relationship_type}"
        logger.debug(f"Wrote edge {edge_id} to graph")
        return edge_id

    def query_nodes(self, node_type: Optional[KGNodeType] = None) -> List[KGNode]:
        """Query nodes by type.

        Args:
            node_type: Filter by node type (None = all)

        Returns:
            List of nodes
        """
        if node_type:
            query = (
                "SELECT id, node_type, tenant_id, data, created_at "
                "FROM kg_nodes WHERE tenant_id = ? AND node_type = ?"
            )
            result = self.conn.execute(query, [self.tenant_id, node_type.value]).fetchall()
        else:
            query = (
                "SELECT id, node_type, tenant_id, data, created_at "
                "FROM kg_nodes WHERE tenant_id = ?"
            )
            result = self.conn.execute(query, [self.tenant_id]).fetchall()

        nodes = []
        for row in result:
            node_id, node_type_str, tenant_id, data_json, created_at = row
            data = json.loads(data_json) if data_json else {}
            node = KGNode(
                id=node_id,
                node_type=KGNodeType(node_type_str),
                tenant_id=tenant_id,
                data=data,
                created_at=created_at,
            )
            nodes.append(node)

        return nodes

    def query_edges(self, relationship_type: Optional[str] = None) -> List[KGEdge]:
        """Query edges by relationship type.

        Args:
            relationship_type: Filter by relationship type (None = all)

        Returns:
            List of edges
        """
        if relationship_type:
            query = (
                "SELECT source_id, target_id, relationship_type, tenant_id, data, created_at "
                "FROM kg_edges WHERE tenant_id = ? AND relationship_type = ?"
            )
            result = self.conn.execute(query, [self.tenant_id, relationship_type]).fetchall()
        else:
            query = (
                "SELECT source_id, target_id, relationship_type, tenant_id, data, created_at "
                "FROM kg_edges WHERE tenant_id = ?"
            )
            result = self.conn.execute(query, [self.tenant_id]).fetchall()

        edges = []
        for row in result:
            source_id, target_id, rel_type, tenant_id, data_json, created_at = row
            data = json.loads(data_json) if data_json else {}
            edge = KGEdge(
                source_id=source_id,
                target_id=target_id,
                relationship_type=rel_type,
                tenant_id=tenant_id,
                data=data,
                created_at=created_at,
            )
            edges.append(edge)

        return edges

    def query_incoming(self, target_id: str) -> List[KGEdge]:
        """Query edges pointing to a target node.

        Args:
            target_id: Target node ID

        Returns:
            List of incoming edges
        """
        query = (
            "SELECT source_id, target_id, relationship_type, tenant_id, data, created_at "
            "FROM kg_edges WHERE tenant_id = ? AND target_id = ?"
        )
        result = self.conn.execute(query, [self.tenant_id, target_id]).fetchall()

        edges = []
        for row in result:
            source_id, target_id, rel_type, tenant_id, data_json, created_at = row
            data = json.loads(data_json) if data_json else {}
            edge = KGEdge(
                source_id=source_id,
                target_id=target_id,
                relationship_type=rel_type,
                tenant_id=tenant_id,
                data=data,
                created_at=created_at,
            )
            edges.append(edge)

        return edges

    def query_outgoing(self, source_id: str) -> List[KGEdge]:
        """Query edges from a source node.

        Args:
            source_id: Source node ID

        Returns:
            List of outgoing edges
        """
        query = (
            "SELECT source_id, target_id, relationship_type, tenant_id, data, created_at "
            "FROM kg_edges WHERE tenant_id = ? AND source_id = ?"
        )
        result = self.conn.execute(query, [self.tenant_id, source_id]).fetchall()

        edges = []
        for row in result:
            source_id, target_id, rel_type, tenant_id, data_json, created_at = row
            data = json.loads(data_json) if data_json else {}
            edge = KGEdge(
                source_id=source_id,
                target_id=target_id,
                relationship_type=rel_type,
                tenant_id=tenant_id,
                data=data,
                created_at=created_at,
            )
            edges.append(edge)

        return edges

    def verify_chain(self) -> VerifyResult:
        """Verify gate_events hash-chain integrity.

        Returns:
            VerifyResult with status and details
        """
        query = (
            "SELECT event_hash, prior_hash FROM gate_events "
            "WHERE tenant_id = ? ORDER BY timestamp ASC"
        )
        result = self.conn.execute(query, [self.tenant_id]).fetchall()

        height = len(result)
        gaps = 0
        verified = True

        # Walk chain and verify links
        prev_hash = None
        for event_hash, prior_hash in result:
            if prior_hash and prior_hash != prev_hash:
                gaps += 1
                verified = False
            prev_hash = event_hash

        message = (
            f"Chain height: {height}, gaps: {gaps}, "
            f"verified: {verified}"
        )

        return VerifyResult(verified, height, gaps, message)

    def close(self) -> None:
        """Close database connection."""
        self.conn.close()
        logger.debug("Closed DuckDB connection")
