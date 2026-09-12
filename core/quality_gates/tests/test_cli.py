"""Tests for CLI integration (ADR-0688)."""

import pytest
import tempfile
import os
import json

from core.quality_gates.cli import QualityGateCLI
from core.quality_gates.models import VerdictType


class TestCLI:
    """Test CLI interface."""

    @pytest.fixture
    def cli(self):
        """Create a test CLI instance."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            cli = QualityGateCLI(db_path, "test-tenant")
            yield cli
            cli.close()

    def test_run_single_validator(self, cli):
        """Test running a single validator through CLI."""
        artifact = {
            "id": "IDEA-001",
            "description": "Test idea",
            "evidence_tasks": ["task1", "task2"],
            "evidence_commits": [],
            "recurrence_count": 0,
        }

        result = cli.run_validator("IdeaGate", artifact)

        assert result.verdict == VerdictType.PASS
        assert result.gate_name == "IdeaGate"
        assert result.artifact_id == "IDEA-001"

    def test_run_all_validators(self, cli):
        """Test running all validators."""
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

        results = cli.run_all_validators(artifacts)

        assert len(results) == 2
        assert all(isinstance(v, VerdictType) for v in [r.verdict for r in results.values()])

    def test_format_result_human(self, cli):
        """Test human-readable output formatting."""
        artifact = {
            "id": "IDEA-001",
            "description": "Test idea",
            "evidence_tasks": ["task1", "task2"],
            "evidence_commits": [],
            "recurrence_count": 0,
        }

        result = cli.run_validator("IdeaGate", artifact)
        human = cli.format_result_human(result)

        assert "Gate: IdeaGate" in human
        assert "Artifact: IDEA-001" in human
        assert "PASS" in human
        assert "Confidence:" in human

    def test_format_result_human_with_findings(self, cli):
        """Test human formatting with findings."""
        artifact = {
            "id": "IDEA-001",
            "description": "Test idea",
            "evidence_tasks": ["task1"],
            "evidence_commits": [],
            "recurrence_count": 0,
        }

        result = cli.run_validator("IdeaGate", artifact)
        human = cli.format_result_human(result)

        assert "Findings:" in human
        assert "FAIL" in human

    def test_format_results_human(self, cli):
        """Test human formatting for multiple results."""
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

        results = cli.run_all_validators(artifacts)
        human = cli.format_results_human(results)

        assert "IdeaGate" in human
        assert "ADRGate" in human
        assert "PASS" in human

    def test_format_result_json(self, cli):
        """Test JSON formatting."""
        artifact = {
            "id": "IDEA-001",
            "description": "Test idea",
            "evidence_tasks": ["task1", "task2"],
            "evidence_commits": [],
            "recurrence_count": 0,
        }

        result = cli.run_validator("IdeaGate", artifact)
        json_dict = cli.format_result_json(result)

        assert json_dict["gate_name"] == "IdeaGate"
        assert json_dict["artifact_id"] == "IDEA-001"
        assert json_dict["verdict"] == "pass"
        assert isinstance(json_dict["confidence"], float)

    def test_format_results_json(self, cli):
        """Test JSON formatting for multiple results."""
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

        results = cli.run_all_validators(artifacts)
        json_dict = cli.format_results_json(results)

        assert "results" in json_dict
        assert len(json_dict["results"]) == 2
        assert all("gate_name" in r for r in json_dict["results"])

    def test_get_exit_code_all_pass(self, cli):
        """Test exit code when all gates pass."""
        artifacts = {
            "IdeaGate": {
                "id": "IDEA-001",
                "description": "Test idea",
                "evidence_tasks": ["task1", "task2"],
                "evidence_commits": [],
                "recurrence_count": 0,
            },
        }

        results = cli.run_all_validators(artifacts)
        exit_code = cli.get_exit_code(results)

        assert exit_code == 0

    def test_get_exit_code_with_fail(self, cli):
        """Test exit code when a gate fails."""
        artifacts = {
            "IdeaGate": {
                "id": "IDEA-001",
                "description": "Test idea",
                "evidence_tasks": ["task1"],
                "evidence_commits": [],
                "recurrence_count": 0,
            },
        }

        results = cli.run_all_validators(artifacts)
        exit_code = cli.get_exit_code(results)

        assert exit_code == 1

    def test_error_handling_invalid_gate(self, cli):
        """Test error handling for invalid gate."""
        artifact = {
            "id": "TEST-001",
            "description": "Test",
        }

        with pytest.raises(KeyError):
            cli.run_validator("InvalidGate", artifact)

    def test_cli_audit_events_logged(self, cli):
        """Test that CLI logs audit events."""
        artifact = {
            "id": "IDEA-001",
            "description": "Test idea",
            "evidence_tasks": ["task1", "task2"],
            "evidence_commits": [],
            "recurrence_count": 0,
        }

        cli.run_validator("IdeaGate", artifact)

        # Verify audit event logged
        query = "SELECT COUNT(*) FROM gate_events WHERE tenant_id = ?"
        count = cli.graph.conn.execute(query, ["test-tenant"]).fetchall()[0][0]

        assert count == 1
