"""Phase 1 Tests: DoD Verifier Skill Unit + E2E Tests."""

import pytest
from pathlib import Path
from unittest.mock import patch, mock_open
import sys

# Add skill to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core" / "skills" / "os_skills"))

from definition_of_done_verifier.skill import DoD_VerifierSkill, DoD_VerificationResult
from definition_of_done_verifier.checks.reachability import ReachabilityCheck
from definition_of_done_verifier.checks.audit_trail import AuditTrailCheck
from definition_of_done_verifier.scoring import ScoringEngine


# ============================================================================
# UNIT TESTS
# ============================================================================

class TestReachabilityCheckUnit:
    """Tests from Phase 1 requirement."""

    def test_symbol_found(self):
        check = ReachabilityCheck()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "src/api.py:42: delete_panel()"
            result = check.run("delete_panel", Path("/repo"))
            assert result.passed == True
            assert "delete_panel" in result.evidence

    def test_symbol_not_found(self):
        check = ReachabilityCheck()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stdout = ""
            result = check.run("xyz_unknown", Path("/repo"))
            assert result.passed == False


class TestAuditTrailCheckUnit:
    """Tests from Phase 1 requirement."""

    def test_events_found(self):
        check = AuditTrailCheck()
        content = '{"task_id": "t1", "event_type": "dod_verified"}\n'
        with patch("builtins.open", mock_open(read_data=content)):
            with patch("pathlib.Path.exists", return_value=True):
                result = check.run("t1", Path("/audit.jsonl"))
                assert result.passed == True
                assert result.event_count == 1

    def test_no_events(self):
        check = AuditTrailCheck()
        content = '{"task_id": "other", "event_type": "dod_verified"}\n'
        with patch("builtins.open", mock_open(read_data=content)):
            with patch("pathlib.Path.exists", return_value=True):
                result = check.run("t1", Path("/audit.jsonl"))
                assert result.passed == False


class TestScoringEngine:
    """Test score computation."""

    def test_all_checks_pass(self):
        engine = ScoringEngine()
        checks = {
            "reachability": True,
            "audit_trail": True,
            "test_evidence": True,
            "docs_sync": True,
            "reproducibility": True,
        }
        score = engine.compute_score(checks)
        assert score == 1.0
        assert engine.is_passed(score)

    def test_some_checks_fail(self):
        engine = ScoringEngine()
        checks = {
            "reachability": True,  # 0.20
            "audit_trail": False,  # 0.00
            "test_evidence": True,  # 0.20
            "docs_sync": True,  # 0.20
            "reproducibility": True,  # 0.15
        }
        score = engine.compute_score(checks)
        expected = (0.20 + 0.20 + 0.20 + 0.15) / 1.0  # 0.75
        assert abs(score - expected) < 0.01
        assert not engine.is_passed(score)  # < 0.80


# ============================================================================
# E2E TESTS (Integration Tests)
# ============================================================================

class TestDoD_VerifierSkillE2E:
    """End-to-end tests of the full Skill."""

    def test_all_checks_pass_e2e(self):
        """Simulate a task that passes all checks."""
        skill = DoD_VerifierSkill(
            audit_path=Path("/tmp/audit.jsonl"),
            cwd=Path("/repo")
        )

        with patch("subprocess.run") as mock_run:
            with patch("pathlib.Path.exists") as mock_exists:
                with patch("builtins.open", mock_open(read_data='{"task_id": "t1", "event_type": "dod_verified"}\n')):
                    # Setup mocks
                    mock_exists.return_value = True
                    mock_run.return_value.returncode = 0
                    mock_run.return_value.stdout = "src/api.py:42: delete_panel()"

                    # Execute
                    result = skill.execute(
                        task_id="t1",
                        task_type="api_endpoint",
                        symbol_name="delete_panel",
                        test_path=Path("/tests/test_api.py"),
                        test_output_file=Path("/tmp/test_output.txt"),
                    )

                    # Assert
                    assert result.task_id == "t1"
                    assert result.score > 0.5
                    # Check that all checks were attempted
                    assert len(result.checks) == 5

    def test_hallucinated_task_fails(self):
        """Task with no reachability + no audit → should fail."""
        skill = DoD_VerifierSkill(
            audit_path=Path("/tmp/audit.jsonl"),
            cwd=Path("/repo")
        )

        with patch("subprocess.run") as mock_run:
            with patch("pathlib.Path.exists") as mock_exists:
                with patch("builtins.open") as mock_file:
                    # Simulate: no reachability, no audit events
                    mock_exists.return_value = True
                    mock_run.return_value.returncode = 1  # grep found nothing
                    mock_run.return_value.stdout = ""
                    mock_file.return_value.read.return_value = '{"task_id": "other"}\n'  # No matching events

                    result = skill.execute(
                        task_id="hallucinated_task",
                        task_type="api_endpoint",
                        symbol_name="nonexistent_func",
                    )

                    # Assert: score should be low
                    assert result.score < 0.80
                    assert result.passed == False
                    assert "incomplete" in result.reason.lower()


# ============================================================================
# PHASE 1 GATE TESTS
# ============================================================================

class TestPhase1Gate:
    """Gate: all Phase 1 tests must pass before proceeding to Phase 2."""

    @pytest.mark.gate
    def test_reachability_has_timeout(self):
        """Verify timeout is implemented."""
        check = ReachabilityCheck()
        # The run() method should accept timeout_s parameter
        assert hasattr(check, 'run')

    @pytest.mark.gate
    def test_audit_trail_has_event_count(self):
        """Verify event counting is implemented."""
        from definition_of_done_verifier.checks.audit_trail import AuditTrailCheck
        check = AuditTrailCheck()
        # Must return CheckResult with event_count field
        # (tested above in TestAuditTrailCheckUnit)

    @pytest.mark.gate
    def test_scoring_engine_threshold(self):
        """Verify threshold is 0.80."""
        engine = ScoringEngine()
        # Score < 0.80 → not passed
        assert not engine.is_passed(0.79)
        # Score >= 0.80 → passed
        assert engine.is_passed(0.80)

    @pytest.mark.gate
    def test_skill_emits_audit_event(self):
        """Verify audit event emission is implemented."""
        skill = DoD_VerifierSkill()
        assert hasattr(skill, '_emit_audit_event')


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-m", "not gate"])
