"""Tests for tenant isolation (ADR-0688)."""

import pytest
import tempfile
import os
from datetime import datetime

from core.quality_gates.graph import KnowledgeGraph
from core.quality_gates.models import KGNode, KGEdge, KGNodeType, GateResult, VerdictType


class TestTenantIsolation:
    """Test tenant isolation in queries and validators."""

    def test_graph_enforces_tenant_isolation_nodes(self):
        """Test that graph enforces tenant isolation for nodes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")

            # Write node in tenant-a
            graph_a = KnowledgeGraph(db_path, tenant_id="tenant-a")
            node_a = KGNode(
                id="ADR-0688",
                node_type=KGNodeType.ADR,
                tenant_id="tenant-a",
                data={"status": "proposed"},
            )
            graph_a.write_node(node_a)
            graph_a.close()

            # Read as tenant-b
            graph_b = KnowledgeGraph(db_path, tenant_id="tenant-b")
            nodes = graph_b.query_nodes()
            assert len(nodes) == 0
            graph_b.close()

    def test_graph_enforces_tenant_isolation_edges(self):
        """Test that graph enforces tenant isolation for edges."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")

            # Write edge in tenant-a
            graph_a = KnowledgeGraph(db_path, tenant_id="tenant-a")
            edge_a = KGEdge(
                source_id="ADR-0688",
                target_id="ADR-0687",
                relationship_type="depends_on",
                tenant_id="tenant-a",
            )
            graph_a.write_edge(edge_a)
            graph_a.close()

            # Read as tenant-b
            graph_b = KnowledgeGraph(db_path, tenant_id="tenant-b")
            edges = graph_b.query_edges()
            assert len(edges) == 0
            graph_b.close()

    def test_multiple_tenants_isolated(self):
        """Test that multiple tenants can coexist without cross-pollution."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")

            # Write to tenant-a
            graph_a = KnowledgeGraph(db_path, tenant_id="tenant-a")
            for i in range(3):
                node = KGNode(
                    id=f"ADR-{i}",
                    node_type=KGNodeType.ADR,
                    tenant_id="tenant-a",
                    data={"index": i},
                )
                graph_a.write_node(node)
            graph_a.close()

            # Write to tenant-b
            graph_b = KnowledgeGraph(db_path, tenant_id="tenant-b")
            for i in range(5):
                node = KGNode(
                    id=f"CONCEPT-{i}",
                    node_type=KGNodeType.CONCEPT,
                    tenant_id="tenant-b",
                    data={"index": i},
                )
                graph_b.write_node(node)

            # Verify isolation
            nodes_b = graph_b.query_nodes()
            assert len(nodes_b) == 5

            # Verify all are CONCEPT type
            for node in nodes_b:
                assert node.node_type == KGNodeType.CONCEPT

            graph_b.close()

            # Re-verify from tenant-a
            graph_a = KnowledgeGraph(db_path, tenant_id="tenant-a")
            nodes_a = graph_a.query_nodes()
            assert len(nodes_a) == 3

            for node in nodes_a:
                assert node.node_type == KGNodeType.ADR

            graph_a.close()

    def test_validator_respects_tenant_scope(self):
        """Test that validators respect tenant boundaries."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")

            # Create validators for different tenants with same graph DB
            from core.quality_gates.validators import IdeaGateValidator

            graph_a = KnowledgeGraph(db_path, tenant_id="tenant-a")
            validator_a = IdeaGateValidator(graph_a, "tenant-a")

            artifact = {
                "id": "IDEA-001",
                "description": "Test idea",
                "evidence_tasks": ["task1", "task2"],
                "evidence_commits": [],
                "recurrence_count": 0,
            }

            result = validator_a.validate(artifact)
            assert result.verdict == VerdictType.PASS
            assert result.tenant_id == "tenant-a"

            graph_a.close()

    def test_gate_result_requires_tenant_id(self):
        """Test that GateResult requires tenant_id."""
        with pytest.raises(ValueError, match="tenant_id"):
            GateResult(
                gate_name="IdeaGate",
                artifact_id="IDEA-001",
                verdict=VerdictType.PASS,
                confidence=0.85,
                reason="Test",
                tenant_id="",
            )

    def test_kg_node_requires_tenant_id(self):
        """Test that KGNode requires tenant_id."""
        with pytest.raises(ValueError, match="tenant_id"):
            KGNode(
                id="ADR-0688",
                node_type=KGNodeType.ADR,
                tenant_id="",
                data={},
            )

    def test_kg_edge_requires_tenant_id(self):
        """Test that KGEdge requires tenant_id."""
        with pytest.raises(ValueError, match="tenant_id"):
            KGEdge(
                source_id="ADR-0688",
                target_id="ADR-0687",
                relationship_type="depends_on",
                tenant_id="",
            )

    def test_graph_write_node_tenant_mismatch(self):
        """Test that writing node with mismatched tenant fails."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            graph = KnowledgeGraph(db_path, tenant_id="tenant-a")

            node = KGNode(
                id="ADR-0688",
                node_type=KGNodeType.ADR,
                tenant_id="tenant-b",
                data={},
            )

            with pytest.raises(ValueError, match="tenant_id"):
                graph.write_node(node)

            graph.close()

    def test_graph_write_edge_tenant_mismatch(self):
        """Test that writing edge with mismatched tenant fails."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            graph = KnowledgeGraph(db_path, tenant_id="tenant-a")

            edge = KGEdge(
                source_id="ADR-0688",
                target_id="ADR-0687",
                relationship_type="depends_on",
                tenant_id="tenant-b",
            )

            with pytest.raises(ValueError, match="tenant_id"):
                graph.write_edge(edge)

            graph.close()
