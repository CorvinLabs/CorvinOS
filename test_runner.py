#!/usr/bin/env python3
"""Simple test runner without pytest dependency.

Tests all Quality Gates modules.
"""

import sys
import os
import tempfile
import traceback
from pathlib import Path

# Add project to path
sys.path.insert(0, str(Path(__file__).parent))

# Try to import required modules
try:
    import duckdb
except ImportError:
    print("ERROR: duckdb not installed. Install with: pip install duckdb")
    sys.exit(1)

# Import Quality Gates modules
try:
    from core.quality_gates.models import (
        GateResult, AuditEvent, KGNode, KGEdge, VerdictType, KGNodeType
    )
    from core.quality_gates.graph import KnowledgeGraph
    from core.quality_gates.validators import (
        IdeaGateValidator, ConceptGateValidator, ADRGateValidator,
        ImplementationPlanGateValidator
    )
    from core.quality_gates.audit import QualityGateAuditLogger
    from core.quality_gates.cli import QualityGateCLI
except Exception as e:
    print(f"ERROR: Failed to import Quality Gates modules: {e}")
    traceback.print_exc()
    sys.exit(1)


def test_models():
    """Test models."""
    print("Testing models...")

    # Test GateResult
    result = GateResult(
        gate_name="TestGate",
        artifact_id="TEST-001",
        verdict=VerdictType.PASS,
        confidence=0.95,
        reason="Test reason",
        tenant_id="test-tenant",
    )
    assert result.verdict == VerdictType.PASS
    assert result.confidence == 0.95
    print("  ✓ GateResult")

    # Test KGNode
    node = KGNode(
        id="ADR-0688",
        node_type=KGNodeType.ADR,
        tenant_id="test-tenant",
        data={"status": "proposed"},
    )
    assert node.id == "ADR-0688"
    print("  ✓ KGNode")

    # Test KGEdge
    edge = KGEdge(
        source_id="ADR-0688",
        target_id="ADR-0687",
        relationship_type="depends_on",
        tenant_id="test-tenant",
    )
    assert edge.source_id == "ADR-0688"
    print("  ✓ KGEdge")


def test_graph():
    """Test KnowledgeGraph."""
    print("Testing KnowledgeGraph...")

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        graph = KnowledgeGraph(db_path, tenant_id="test-tenant")

        # Write node
        node = KGNode(
            id="ADR-0688",
            node_type=KGNodeType.ADR,
            tenant_id="test-tenant",
            data={"status": "proposed"},
        )
        node_id = graph.write_node(node)
        assert node_id == "ADR-0688"
        print("  ✓ write_node")

        # Query nodes
        nodes = graph.query_nodes()
        assert len(nodes) == 1
        print("  ✓ query_nodes")

        # Write edge
        edge = KGEdge(
            source_id="ADR-0688",
            target_id="ADR-0687",
            relationship_type="depends_on",
            tenant_id="test-tenant",
        )
        graph.write_edge(edge)
        print("  ✓ write_edge")

        # Query edges
        edges = graph.query_edges()
        assert len(edges) == 1
        print("  ✓ query_edges")

        # Verify chain
        result = graph.verify_chain()
        assert result.verified is True
        print("  ✓ verify_chain")

        graph.close()


def test_validators():
    """Test validators."""
    print("Testing validators...")

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        graph = KnowledgeGraph(db_path, tenant_id="test-tenant")

        # Test IdeaGateValidator
        idea_validator = IdeaGateValidator(graph, "test-tenant")
        idea_artifact = {
            "id": "IDEA-001",
            "description": "Test idea",
            "evidence_tasks": ["task1", "task2"],
            "evidence_commits": [],
            "recurrence_count": 0,
        }
        result = idea_validator.validate(idea_artifact)
        assert result.verdict == VerdictType.PASS
        print("  ✓ IdeaGateValidator")

        # Test ConceptGateValidator
        concept_validator = ConceptGateValidator(graph, "test-tenant")
        concept_artifact = {
            "id": "CONCEPT-0040",
            "narrative": " ".join(["word"] * 150),
            "boundaries": "Boundaries",
            "evidence_commits": ["commit1", "commit2"],
        }
        result = concept_validator.validate(concept_artifact)
        assert result.verdict == VerdictType.PASS
        print("  ✓ ConceptGateValidator")

        # Test ADRGateValidator
        adr_validator = ADRGateValidator(graph, "test-tenant")
        adr_artifact = {
            "id": "ADR-0688",
            "status": "proposed",
            "depends_on": [],
            "paths": ["core/quality_gates/"],
            "docs": ["docs/quality-gates/"],
            "commits": ["abc123"],
        }
        result = adr_validator.validate(adr_artifact)
        assert result.verdict == VerdictType.PASS
        print("  ✓ ADRGateValidator")

        # Test ImplementationPlanGateValidator
        plan_validator = ImplementationPlanGateValidator(graph, "test-tenant")
        plan_artifact = {
            "id": "PLAN-001",
            "subsystem": "quality_gates",
            "phases": [{"name": "P1"}, {"name": "P2"}, {"name": "P3"}],
            "success_criteria": "All tests pass",
            "resource_estimate": "40 hours",
            "timeline_weeks": 3,
        }
        result = plan_validator.validate(plan_artifact)
        assert result.verdict == VerdictType.PASS
        print("  ✓ ImplementationPlanGateValidator")

        graph.close()


def test_audit():
    """Test audit chain."""
    print("Testing audit chain...")

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        graph = KnowledgeGraph(db_path, tenant_id="test-tenant")
        audit_logger = QualityGateAuditLogger(graph.conn)

        # Write event
        result = GateResult(
            gate_name="TestGate",
            artifact_id="TEST-001",
            verdict=VerdictType.PASS,
            confidence=0.95,
            reason="Test reason",
            tenant_id="test-tenant",
        )
        event_hash = audit_logger.write_gate_event(result)
        assert event_hash is not None
        assert len(event_hash) == 64  # SHA256
        print("  ✓ write_gate_event")

        # Verify chain
        verified = audit_logger.verify_chain("test-tenant")
        assert verified is True
        print("  ✓ verify_chain")

        # Write multiple events
        for i in range(3):
            result = GateResult(
                gate_name="TestGate",
                artifact_id=f"TEST-{i:03d}",
                verdict=VerdictType.PASS,
                confidence=0.95,
                reason=f"Event {i}",
                tenant_id="test-tenant",
            )
            audit_logger.write_gate_event(result)

        # Verify chain integrity
        verified = audit_logger.verify_chain("test-tenant")
        assert verified is True
        print("  ✓ chain_linking")

        graph.close()


def test_cli():
    """Test CLI."""
    print("Testing CLI...")

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        cli = QualityGateCLI(db_path, "test-tenant")

        # Run validator
        artifact = {
            "id": "IDEA-001",
            "description": "Test idea",
            "evidence_tasks": ["task1", "task2"],
            "evidence_commits": [],
            "recurrence_count": 0,
        }
        result = cli.run_validator("IdeaGate", artifact)
        assert result.verdict == VerdictType.PASS
        print("  ✓ run_validator")

        # Format output
        human_output = cli.format_result_human(result)
        assert "IdeaGate" in human_output
        print("  ✓ format_result_human")

        # JSON output
        json_dict = cli.format_result_json(result)
        assert json_dict["verdict"] == "pass"
        print("  ✓ format_result_json")

        # Exit code
        results = {"IdeaGate": result}
        exit_code = cli.get_exit_code(results)
        assert exit_code == 0
        print("  ✓ get_exit_code")

        cli.close()


def test_tenant_isolation():
    """Test tenant isolation."""
    print("Testing tenant isolation...")

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")

        # Write to tenant-a
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

        print("  ✓ tenant_isolation")


def test_e2e_flow():
    """Test end-to-end flow."""
    print("Testing E2E flow...")

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        graph = KnowledgeGraph(db_path, tenant_id="test-tenant")
        audit_logger = QualityGateAuditLogger(graph.conn)

        # Run multiple validators
        validators = [
            (IdeaGateValidator(graph, "test-tenant"), {
                "id": "IDEA-001",
                "description": "Test idea",
                "evidence_tasks": ["task1", "task2"],
                "evidence_commits": [],
                "recurrence_count": 0,
            }),
            (ConceptGateValidator(graph, "test-tenant"), {
                "id": "CONCEPT-001",
                "narrative": " ".join(["word"] * 150),
                "boundaries": "Boundaries",
                "evidence_commits": ["commit1", "commit2"],
            }),
            (ADRGateValidator(graph, "test-tenant"), {
                "id": "ADR-0688",
                "status": "proposed",
                "depends_on": [],
                "paths": ["core/quality_gates/"],
                "docs": ["docs/quality-gates/"],
                "commits": ["abc123"],
            }),
        ]

        for validator, artifact in validators:
            result = validator.validate(artifact)
            assert result.verdict == VerdictType.PASS
            audit_logger.write_gate_event(result)

        # Verify chain
        verified = audit_logger.verify_chain("test-tenant")
        assert verified is True

        print("  ✓ E2E flow")

        graph.close()


def main():
    """Run all tests."""
    print("\n" + "="*60)
    print("Quality Gates System — Phase 1 Test Suite")
    print("="*60 + "\n")

    tests = [
        test_models,
        test_graph,
        test_validators,
        test_audit,
        test_cli,
        test_tenant_isolation,
        test_e2e_flow,
    ]

    passed = 0
    failed = 0

    for test_func in tests:
        try:
            test_func()
            passed += 1
        except Exception as e:
            print(f"  ✗ {test_func.__name__}: {e}")
            traceback.print_exc()
            failed += 1

    print("\n" + "="*60)
    print(f"Results: {passed} passed, {failed} failed")
    print("="*60 + "\n")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
