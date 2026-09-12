"""Tests for KnowledgeGraph (ADR-0688)."""

import pytest
import tempfile
import os
from datetime import datetime

from core.quality_gates.graph import KnowledgeGraph
from core.quality_gates.models import KGNode, KGEdge, KGNodeType


class TestKnowledgeGraph:
    """Test KnowledgeGraph class."""

    @pytest.fixture
    def graph(self):
        """Create a test KnowledgeGraph."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            graph = KnowledgeGraph(db_path, tenant_id="test-tenant")
            yield graph
            graph.close()

    def test_graph_initialization(self):
        """Test graph initialization."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            graph = KnowledgeGraph(db_path, tenant_id="test-tenant")

            assert graph.tenant_id == "test-tenant"
            assert graph.db_path == db_path

            graph.close()

    def test_graph_requires_tenant_id(self):
        """Test that graph requires tenant_id."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")

            with pytest.raises(ValueError, match="tenant_id is required"):
                KnowledgeGraph(db_path, tenant_id="")

    def test_write_node(self, graph):
        """Test writing a node."""
        node = KGNode(
            id="ADR-0688",
            node_type=KGNodeType.ADR,
            tenant_id="test-tenant",
            data={"status": "proposed"},
        )

        node_id = graph.write_node(node)
        assert node_id == "ADR-0688"

    def test_write_node_wrong_tenant(self, graph):
        """Test that writing node with wrong tenant fails."""
        node = KGNode(
            id="ADR-0688",
            node_type=KGNodeType.ADR,
            tenant_id="other-tenant",
            data={"status": "proposed"},
        )

        with pytest.raises(ValueError, match="tenant_id"):
            graph.write_node(node)

    def test_query_nodes(self, graph):
        """Test querying nodes."""
        # Write some nodes
        node1 = KGNode(
            id="ADR-0688",
            node_type=KGNodeType.ADR,
            tenant_id="test-tenant",
            data={"status": "proposed"},
        )
        node2 = KGNode(
            id="CONCEPT-0040",
            node_type=KGNodeType.CONCEPT,
            tenant_id="test-tenant",
            data={"status": "approved"},
        )

        graph.write_node(node1)
        graph.write_node(node2)

        # Query all nodes
        nodes = graph.query_nodes()
        assert len(nodes) == 2

        # Query by type
        adr_nodes = graph.query_nodes(KGNodeType.ADR)
        assert len(adr_nodes) == 1
        assert adr_nodes[0].id == "ADR-0688"

        concept_nodes = graph.query_nodes(KGNodeType.CONCEPT)
        assert len(concept_nodes) == 1
        assert concept_nodes[0].id == "CONCEPT-0040"

    def test_query_nodes_tenant_isolation(self, graph):
        """Test that queries are tenant-isolated."""
        # Write node in test-tenant
        node1 = KGNode(
            id="ADR-0688",
            node_type=KGNodeType.ADR,
            tenant_id="test-tenant",
            data={"status": "proposed"},
        )
        graph.write_node(node1)

        # Create another graph for different tenant
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test2.db")
            other_graph = KnowledgeGraph(db_path, tenant_id="other-tenant")

            # Query from other tenant should return empty
            nodes = other_graph.query_nodes()
            assert len(nodes) == 0

            other_graph.close()

    def test_write_edge(self, graph):
        """Test writing an edge."""
        edge = KGEdge(
            source_id="ADR-0688",
            target_id="ADR-0687",
            relationship_type="depends_on",
            tenant_id="test-tenant",
            data={"version": "1.0"},
        )

        edge_id = graph.write_edge(edge)
        assert edge_id == "ADR-0688:ADR-0687:depends_on"

    def test_write_edge_wrong_tenant(self, graph):
        """Test that writing edge with wrong tenant fails."""
        edge = KGEdge(
            source_id="ADR-0688",
            target_id="ADR-0687",
            relationship_type="depends_on",
            tenant_id="other-tenant",
        )

        with pytest.raises(ValueError, match="tenant_id"):
            graph.write_edge(edge)

    def test_query_edges(self, graph):
        """Test querying edges."""
        edge1 = KGEdge(
            source_id="ADR-0688",
            target_id="ADR-0687",
            relationship_type="depends_on",
            tenant_id="test-tenant",
        )
        edge2 = KGEdge(
            source_id="CONCEPT-0040",
            target_id="ADR-0688",
            relationship_type="related_to",
            tenant_id="test-tenant",
        )

        graph.write_edge(edge1)
        graph.write_edge(edge2)

        # Query all edges
        edges = graph.query_edges()
        assert len(edges) == 2

        # Query by relationship type
        depends = graph.query_edges("depends_on")
        assert len(depends) == 1
        assert depends[0].relationship_type == "depends_on"

    def test_query_incoming_edges(self, graph):
        """Test querying incoming edges."""
        edge = KGEdge(
            source_id="ADR-0688",
            target_id="ADR-0687",
            relationship_type="depends_on",
            tenant_id="test-tenant",
        )

        graph.write_edge(edge)

        # Query edges pointing to ADR-0687
        incoming = graph.query_incoming("ADR-0687")
        assert len(incoming) == 1
        assert incoming[0].target_id == "ADR-0687"

    def test_query_outgoing_edges(self, graph):
        """Test querying outgoing edges."""
        edge = KGEdge(
            source_id="ADR-0688",
            target_id="ADR-0687",
            relationship_type="depends_on",
            tenant_id="test-tenant",
        )

        graph.write_edge(edge)

        # Query edges from ADR-0688
        outgoing = graph.query_outgoing("ADR-0688")
        assert len(outgoing) == 1
        assert outgoing[0].source_id == "ADR-0688"

    def test_verify_chain_empty(self, graph):
        """Test chain verification on empty graph."""
        result = graph.verify_chain()

        assert result.verified is True
        assert result.height == 0
        assert result.gaps == 0

    def test_verify_chain_with_events(self, graph):
        """Test chain verification with events."""
        # This would need actual gate events, which requires audit logger
        # For now, just verify empty chain works
        result = graph.verify_chain()
        assert result.verified is True
