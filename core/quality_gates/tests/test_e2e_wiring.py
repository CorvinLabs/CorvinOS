"""End-to-end wiring tests for Quality Gates System (ADR-0688).

Tests the full flow: artifact → validator → audit chain → result
No mocks, real DuckDB, real audit chain.
"""

import pytest
import tempfile
import os

from core.quality_gates.graph import KnowledgeGraph
from core.quality_gates.validators import (
    IdeaGateValidator,
    ConceptGateValidator,
    ADRGateValidator,
)
from core.quality_gates.audit import QualityGateAuditLogger
from core.quality_gates.cli import QualityGateCLI
from core.quality_gates.models import VerdictType


class TestE2EWiring:
    """End-to-end wiring tests."""

    def test_e2e_idea_gate_flow(self):
        """Test complete flow: artifact → validator → audit → result."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            graph = KnowledgeGraph(db_path, tenant_id="test-tenant")
            audit_logger = QualityGateAuditLogger(graph.conn)

            # Create artifact
            artifact = {
                "id": "IDEA-001",
                "description": "Quality Gates implementation",
                "evidence_tasks": ["task-001", "task-002"],
                "evidence_commits": ["commit-001"],
                "recurrence_count": 1,
            }

            # Run validator
            validator = IdeaGateValidator(graph, "test-tenant")
            result = validator.validate(artifact)

            # Verify result
            assert result.verdict == VerdictType.PASS
            assert result.gate_name == "IdeaGate"
            assert result.artifact_id == "IDEA-001"

            # Log to audit chain
            event_hash = audit_logger.write_gate_event(result)
            assert event_hash is not None

            # Verify audit chain contains event
            query = "SELECT COUNT(*) FROM gate_events WHERE tenant_id = ?"
            count = graph.conn.execute(query, ["test-tenant"]).fetchall()[0][0]
            assert count == 1

            # Verify chain integrity
            verified = audit_logger.verify_chain("test-tenant")
            assert verified is True

            graph.close()

    def test_e2e_concept_gate_flow(self):
        """Test complete flow for Concept gate."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            graph = KnowledgeGraph(db_path, tenant_id="test-tenant")
            audit_logger = QualityGateAuditLogger(graph.conn)

            # Create artifact
            artifact = {
                "id": "CONCEPT-0040",
                "narrative": " ".join(["word"] * 150),
                "boundaries": "This is the boundary definition",
                "evidence_commits": ["commit-001", "commit-002"],
                "skills": ["skill-001"],
            }

            # Run validator
            validator = ConceptGateValidator(graph, "test-tenant")
            result = validator.validate(artifact)

            # Verify result
            assert result.verdict == VerdictType.PASS
            assert result.gate_name == "ConceptGate"

            # Log to audit chain
            event_hash = audit_logger.write_gate_event(result)

            # Verify audit chain
            verified = audit_logger.verify_chain("test-tenant")
            assert verified is True

            graph.close()

    def test_e2e_adr_gate_flow(self):
        """Test complete flow for ADR gate."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            graph = KnowledgeGraph(db_path, tenant_id="test-tenant")
            audit_logger = QualityGateAuditLogger(graph.conn)

            # Create artifact
            artifact = {
                "id": "ADR-0688",
                "status": "proposed",
                "depends_on": ["ADR-0687"],
                "paths": ["core/quality_gates/"],
                "docs": ["docs/quality-gates/"],
                "commits": ["abc123"],
            }

            # Run validator
            validator = ADRGateValidator(graph, "test-tenant")
            result = validator.validate(artifact)

            # Verify result
            assert result.verdict == VerdictType.PASS
            assert result.gate_name == "ADRGate"
            assert result.confidence == 0.95

            # Log to audit chain
            event_hash = audit_logger.write_gate_event(result)

            # Verify audit chain
            verified = audit_logger.verify_chain("test-tenant")
            assert verified is True

            graph.close()

    def test_e2e_multiple_validators_chain(self):
        """Test running multiple validators in sequence."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            graph = KnowledgeGraph(db_path, tenant_id="test-tenant")
            audit_logger = QualityGateAuditLogger(graph.conn)

            # Define artifacts
            idea_artifact = {
                "id": "IDEA-001",
                "description": "Test idea",
                "evidence_tasks": ["task1", "task2"],
                "evidence_commits": [],
                "recurrence_count": 0,
            }

            concept_artifact = {
                "id": "CONCEPT-001",
                "narrative": " ".join(["word"] * 150),
                "boundaries": "Boundaries",
                "evidence_commits": ["commit1", "commit2"],
            }

            adr_artifact = {
                "id": "ADR-0688",
                "status": "proposed",
                "depends_on": [],
                "paths": ["core/quality_gates/"],
                "docs": ["docs/quality-gates/"],
                "commits": ["abc123"],
            }

            # Run validators in sequence
            validators = [
                IdeaGateValidator(graph, "test-tenant"),
                ConceptGateValidator(graph, "test-tenant"),
                ADRGateValidator(graph, "test-tenant"),
            ]

            artifacts = [idea_artifact, concept_artifact, adr_artifact]
            results = []

            for validator, artifact in zip(validators, artifacts):
                result = validator.validate(artifact)
                results.append(result)

                # Log each result
                audit_logger.write_gate_event(result)

            # Verify all passed
            assert all(r.verdict == VerdictType.PASS for r in results)

            # Verify audit chain contains 3 events
            query = "SELECT COUNT(*) FROM gate_events WHERE tenant_id = ?"
            count = graph.conn.execute(query, ["test-tenant"]).fetchall()[0][0]
            assert count == 3

            # Verify chain integrity
            verified = audit_logger.verify_chain("test-tenant")
            assert verified is True

            # Verify chain linking
            query = "SELECT event_hash, prior_hash FROM gate_events WHERE tenant_id = ? ORDER BY timestamp ASC"
            chain = graph.conn.execute(query, ["test-tenant"]).fetchall()

            # First event should have no prior
            assert chain[0][1] is None

            # Second event should link to first
            assert chain[1][1] == chain[0][0]

            # Third event should link to second
            assert chain[2][1] == chain[1][0]

            graph.close()

    def test_e2e_cli_integration(self):
        """Test CLI integration with real database."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            cli = QualityGateCLI(db_path, "test-tenant")

            # Create artifacts
            artifacts = {
                "IdeaGate": {
                    "id": "IDEA-001",
                    "description": "Test idea",
                    "evidence_tasks": ["task1", "task2"],
                    "evidence_commits": [],
                    "recurrence_count": 0,
                },
                "ADRGate": {
                    "id": "ADR-0688",
                    "status": "proposed",
                    "depends_on": [],
                    "paths": ["core/quality_gates/"],
                    "docs": ["docs/quality-gates/"],
                    "commits": ["abc123"],
                },
            }

            # Run validators through CLI
            results = cli.run_all_validators(artifacts)

            # Verify results
            assert len(results) == 2
            assert all(r.verdict == VerdictType.PASS for r in results.values())

            # Verify audit events logged
            query = "SELECT COUNT(*) FROM gate_events WHERE tenant_id = ?"
            count = cli.graph.conn.execute(query, ["test-tenant"]).fetchall()[0][0]
            assert count == 2

            # Verify output formatting
            human_output = cli.format_results_human(results)
            assert "IdeaGate" in human_output
            assert "ADRGate" in human_output

            json_output = cli.format_results_json(results)
            assert "results" in json_output
            assert len(json_output["results"]) == 2

            # Verify exit codes
            exit_code = cli.get_exit_code(results)
            assert exit_code == 0  # All pass

            cli.close()

    def test_e2e_audit_chain_hash_verification(self):
        """Test that audit chain hashes are correctly computed and verified."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            graph = KnowledgeGraph(db_path, tenant_id="test-tenant")
            audit_logger = QualityGateAuditLogger(graph.conn)

            # Write multiple events
            for i in range(5):
                from core.quality_gates.models import GateResult

                result = GateResult(
                    gate_name="TestGate",
                    artifact_id=f"TEST-{i:03d}",
                    verdict=VerdictType.PASS,
                    confidence=0.85,
                    reason=f"Event {i}",
                    tenant_id="test-tenant",
                )
                audit_logger.write_gate_event(result)

            # Verify chain
            verified = audit_logger.verify_chain("test-tenant")
            assert verified is True

            # Verify chain has 5 events
            query = "SELECT COUNT(*) FROM gate_events WHERE tenant_id = ?"
            count = graph.conn.execute(query, ["test-tenant"]).fetchall()[0][0]
            assert count == 5

            graph.close()
