"""Tests for CausalGraph."""

import pytest
from daemon.causal_graph import CausalGraph, CausalNode, CausalEdge


def test_add_nodes():
    """Test adding nodes to the graph."""
    graph = CausalGraph()

    graph.add_node("source1", "source", "My source")
    graph.add_node("skill1", "skill", "My skill")
    graph.add_node("outcome1", "outcome")

    assert len(graph.nodes) == 3
    assert graph.nodes["source1"].node_type == "source"
    assert graph.nodes["skill1"].description == "My skill"


def test_add_edge_valid():
    """Test adding edges (valid DAG)."""
    graph = CausalGraph()

    graph.add_node("s1", "source")
    graph.add_node("sk1", "skill")
    graph.add_node("o1", "outcome")

    graph.add_edge("s1", "sk1", "uses", weight=0.7)
    graph.add_edge("sk1", "o1", "produces", weight=0.9)

    assert len(graph.edges) == 2
    assert graph.validate_dag()


def test_add_edge_cycle_detection():
    """Test that cycles are detected and rejected."""
    graph = CausalGraph()

    graph.add_node("s1", "source")
    graph.add_node("s2", "source")
    graph.add_node("s3", "source")

    graph.add_edge("s1", "s2", "uses")
    graph.add_edge("s2", "s3", "uses")

    # This would create a cycle: s3 -> s1 -> s2 -> s3
    with pytest.raises(ValueError):
        graph.add_edge("s3", "s1", "uses")


def test_get_outgoing_edges():
    """Test retrieving outgoing edges."""
    graph = CausalGraph()

    graph.add_node("s1", "source")
    graph.add_node("sk1", "skill")
    graph.add_node("sk2", "skill")

    graph.add_edge("s1", "sk1", "uses")
    graph.add_edge("s1", "sk2", "uses")

    outgoing = graph.get_outgoing_edges("s1")
    assert len(outgoing) == 2


def test_compute_influence():
    """Test computing source influence on outcomes."""
    graph = CausalGraph()

    graph.add_node("source1", "source")
    graph.add_node("skill1", "skill")
    graph.add_node("outcome1", "outcome")

    graph.add_edge("source1", "skill1", "uses", weight=0.8)
    graph.add_edge("skill1", "outcome1", "produces", weight=0.9)

    # Influence = 0.8 * 0.9 = 0.72
    influence = graph.compute_influence("source1")
    assert influence == pytest.approx(0.72, rel=1e-6)


def test_compute_influence_multiple_paths():
    """Test influence computation with multiple paths."""
    graph = CausalGraph()

    graph.add_node("source1", "source")
    graph.add_node("skill1", "skill")
    graph.add_node("skill2", "skill")
    graph.add_node("outcome1", "outcome")

    # Two paths: source -> skill1 -> outcome and source -> skill2 -> outcome
    graph.add_edge("source1", "skill1", "uses", weight=0.5)
    graph.add_edge("skill1", "outcome1", "produces", weight=0.8)
    graph.add_edge("source1", "skill2", "uses", weight=0.6)
    graph.add_edge("skill2", "outcome1", "produces", weight=0.7)

    influence = graph.compute_influence("source1")
    # Total influence = 0.5*0.8 + 0.6*0.7 = 0.4 + 0.42 = 0.82
    expected = min(0.82, 1.0)
    assert influence == pytest.approx(expected, rel=1e-6)


def test_get_top_sources_by_influence():
    """Test ranking sources by influence."""
    graph = CausalGraph()

    # Create three sources with different influence
    graph.add_node("s1", "source")
    graph.add_node("s2", "source")
    graph.add_node("s3", "source")
    graph.add_node("skill1", "skill")
    graph.add_node("outcome1", "outcome")

    graph.add_edge("s1", "skill1", "uses", weight=0.9)
    graph.add_edge("s2", "skill1", "uses", weight=0.5)
    graph.add_edge("s3", "skill1", "uses", weight=0.1)
    graph.add_edge("skill1", "outcome1", "produces", weight=1.0)

    top = graph.get_top_sources_by_influence(k=2)
    assert len(top) == 2
    assert top[0][0] == "s1"  # Highest influence
    assert top[1][0] == "s2"  # Second highest


def test_detect_new_nodes():
    """Test detecting new sources/skills."""
    graph = CausalGraph()

    graph.add_node("s1", "source")
    graph.add_node("sk1", "skill")

    new_sources, new_skills = graph.detect_new_nodes(
        current_sources={"s1", "s2", "s3"},
        current_skills={"sk1", "sk2"},
    )

    assert new_sources == {"s2", "s3"}
    assert new_skills == {"sk2"}


def test_to_dict():
    """Test serializing graph to dict."""
    graph = CausalGraph()

    graph.add_node("s1", "source", "Source 1")
    graph.add_node("sk1", "skill")
    graph.add_edge("s1", "sk1", "uses", weight=0.8)

    d = graph.to_dict()
    assert len(d["nodes"]) == 2
    assert len(d["edges"]) == 1
    assert d["nodes"]["s1"]["node_type"] == "source"


def test_reset():
    """Test resetting graph."""
    graph = CausalGraph()

    graph.add_node("s1", "source")
    graph.add_node("sk1", "skill")
    graph.add_edge("s1", "sk1", "uses")

    graph.reset()
    assert len(graph.nodes) == 0
    assert len(graph.edges) == 0
    assert len(graph.node_types["source"]) == 0
