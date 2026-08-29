"""
Comprehensive test suite for Phase 5: Auto-Integration

Tests cover:
1. Auto-merge orchestration (create, validate, merge)
2. PR validation (lint, unit, integration, e2e, coverage checks)
3. CI/CD pipeline execution (generate and test pipelines)
4. Registry management (add, update, query scenarios)
5. Error recovery (failure handling, rollback)
6. Integration workflows (end-to-end scenarios)

Test count: 20+ unit and integration tests
"""

import pytest
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, MagicMock
import json
import tempfile

from operator.e2e_autogen.auto_merge import (
    MergeStatus,
    ValidationResult,
    ValidationCheck,
    MergeOperation,
    PRValidator,
    PRCreator,
    MergeExecutor,
    AutoMergeOrchestrator,
)

from operator.e2e_autogen.ci_integration import (
    PipelineStatus,
    PipelineType,
    PipelineStage,
    PipelineExecution,
    TestScenario,
    PipelineExecutor,
    RegistryManager,
    CICDIntegrationOrchestrator,
)


# ============================================================================
# Tests for ValidationCheck
# ============================================================================

class TestValidationCheck:
    """Tests for ValidationCheck dataclass."""

    def test_create_validation_check(self):
        """Create a ValidationCheck."""
        check = ValidationCheck(
            name="lint",
            status=ValidationResult.PASSED,
            message="Lint checks passed",
            duration_seconds=5.5
        )

        assert check.name == "lint"
        assert check.status == ValidationResult.PASSED
        assert check.message == "Lint checks passed"
        assert check.duration_seconds == 5.5

    def test_validation_check_to_dict(self):
        """Convert ValidationCheck to dict."""
        check = ValidationCheck(
            name="unit_tests",
            status=ValidationResult.FAILED,
            message="3 tests failed",
            duration_seconds=10.0
        )

        data = check.to_dict()
        assert data["name"] == "unit_tests"
        assert data["status"] == "failed"
        assert data["message"] == "3 tests failed"
        assert data["duration_seconds"] == 10.0
        assert "timestamp" in data

    def test_validation_check_default_timestamp(self):
        """ValidationCheck has default timestamp."""
        check = ValidationCheck(
            name="test",
            status=ValidationResult.PASSED,
            message="OK"
        )

        assert check.timestamp is not None
        assert isinstance(check.timestamp, datetime)


# ============================================================================
# Tests for MergeOperation
# ============================================================================

class TestMergeOperation:
    """Tests for MergeOperation dataclass."""

    def test_create_merge_operation(self):
        """Create a MergeOperation."""
        op = MergeOperation(
            pr_number=123,
            branch_name="feat/test",
            base_branch="main"
        )

        assert op.pr_number == 123
        assert op.branch_name == "feat/test"
        assert op.base_branch == "main"
        assert op.merge_status == MergeStatus.PENDING

    def test_merge_operation_to_dict(self):
        """Convert MergeOperation to dict."""
        op = MergeOperation(
            pr_number=456,
            branch_name="feat/feature",
            base_branch="develop"
        )

        data = op.to_dict()
        assert data["pr_number"] == 456
        assert data["branch_name"] == "feat/feature"
        assert data["base_branch"] == "develop"
        assert data["merge_status"] == "pending"

    def test_merge_operation_with_checks(self):
        """MergeOperation with validation checks."""
        checks = [
            ValidationCheck("lint", ValidationResult.PASSED, "OK"),
            ValidationCheck("unit_tests", ValidationResult.PASSED, "OK"),
        ]

        op = MergeOperation(
            pr_number=789,
            branch_name="feat/tests",
            validation_checks=checks,
            validation_status=ValidationResult.PASSED
        )

        assert len(op.validation_checks) == 2
        assert op.validation_status == ValidationResult.PASSED


# ============================================================================
# Tests for PRValidator
# ============================================================================

class TestPRValidator:
    """Tests for PR validation logic."""

    def test_validator_initialization(self):
        """Initialize PRValidator."""
        repo_root = Path("/tmp/test-repo")
        validator = PRValidator(repo_root, timeout_seconds=300)

        assert validator.repo_root == repo_root
        assert validator.timeout_seconds == 300
        assert len(validator.REQUIRED_CHECKS) > 0

    def test_validator_required_checks(self):
        """PRValidator has all required checks."""
        validator = PRValidator(Path("/tmp"))

        assert "lint" in validator.REQUIRED_CHECKS
        assert "unit_tests" in validator.REQUIRED_CHECKS
        assert "integration_tests" in validator.REQUIRED_CHECKS
        assert "e2e_tests" in validator.REQUIRED_CHECKS
        assert "coverage" in validator.REQUIRED_CHECKS

    @patch('operator.e2e_autogen.auto_merge.subprocess.run')
    def test_validate_all_all_passed(self, mock_run):
        """Run all validation checks — all pass."""
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")

        validator = PRValidator(Path("/tmp"))
        status, checks = validator.validate_all()

        assert status == ValidationResult.PASSED
        assert len(checks) == len(validator.REQUIRED_CHECKS)
        assert all(c.status == ValidationResult.PASSED for c in checks)

    @patch('operator.e2e_autogen.auto_merge.subprocess.run')
    def test_validate_all_some_failed(self, mock_run):
        """Run all validation checks — some fail."""
        mock_run.return_value = Mock(returncode=1, stdout="lint failed", stderr="")

        validator = PRValidator(Path("/tmp"))
        status, checks = validator.validate_all()

        assert status == ValidationResult.FAILED
        assert len(checks) == len(validator.REQUIRED_CHECKS)

    @patch('operator.e2e_autogen.auto_merge.subprocess.run')
    def test_run_check_lint(self, mock_run):
        """Run lint check."""
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")

        validator = PRValidator(Path("/tmp"))
        check = validator._run_lint()

        assert check.name == "lint"
        assert check.status == ValidationResult.PASSED

    @patch('operator.e2e_autogen.auto_merge.subprocess.run')
    def test_run_check_unit_tests(self, mock_run):
        """Run unit tests check."""
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")

        validator = PRValidator(Path("/tmp"))
        check = validator._run_unit_tests()

        assert check.name == "unit_tests"
        assert check.status == ValidationResult.PASSED

    @patch('operator.e2e_autogen.auto_merge.subprocess.run')
    def test_run_check_timeout(self, mock_run):
        """Handle check timeout."""
        mock_run.side_effect = TimeoutError()

        validator = PRValidator(Path("/tmp"))
        check = validator._run_lint()

        # Should catch exception and continue
        assert check.name == "lint"
        assert check.status == ValidationResult.FAILED


# ============================================================================
# Tests for PRCreator
# ============================================================================

class TestPRCreator:
    """Tests for PR creation."""

    def test_pr_creator_initialization(self):
        """Initialize PRCreator."""
        repo_root = Path("/tmp/test-repo")
        creator = PRCreator(repo_root, github_token="test-token")

        assert creator.repo_root == repo_root
        assert creator.github_token == "test-token"

    def test_create_pr(self):
        """Create a GitHub PR."""
        creator = PRCreator(Path("/tmp"))
        pr_number = creator.create_pr(
            branch_name="feat/test",
            title="Test PR",
            description="Test description",
            base_branch="main"
        )

        assert pr_number is not None
        assert isinstance(pr_number, int)
        assert pr_number > 0

    def test_create_pr_multiple_calls_different_results(self):
        """Multiple PR creations produce different PR numbers."""
        creator = PRCreator(Path("/tmp"))

        pr1 = creator.create_pr("feat/test1", "PR1", "desc1")
        pr2 = creator.create_pr("feat/test2", "PR2", "desc2")

        assert pr1 != pr2

    def test_check_pr_status(self):
        """Check PR status."""
        creator = PRCreator(Path("/tmp"))
        status = creator.check_pr_status(123)

        assert status in ["open", "closed", "merged"]


# ============================================================================
# Tests for MergeExecutor
# ============================================================================

class TestMergeExecutor:
    """Tests for merge execution."""

    def test_merge_executor_initialization(self):
        """Initialize MergeExecutor."""
        executor = MergeExecutor(Path("/tmp"), github_token="token")

        assert executor.repo_root == Path("/tmp")
        assert executor.github_token == "token"

    def test_merge_pr_success(self):
        """Merge PR successfully."""
        executor = MergeExecutor(Path("/tmp"))
        success, commit_sha, error = executor.merge_pr(123)

        assert success is True
        assert commit_sha is not None
        assert error is None

    def test_merge_pr_with_merge_method(self):
        """Merge PR with specific method."""
        executor = MergeExecutor(Path("/tmp"))

        for method in ["squash", "merge", "rebase"]:
            success, commit_sha, error = executor.merge_pr(123, merge_method=method)
            assert success is True

    def test_rollback_merge(self):
        """Rollback a merge."""
        executor = MergeExecutor(Path("/tmp"))
        success = executor.rollback_merge("commit_sha_123")

        assert success is True


# ============================================================================
# Tests for AutoMergeOrchestrator
# ============================================================================

class TestAutoMergeOrchestrator:
    """Tests for auto-merge orchestration."""

    def test_orchestrator_initialization(self):
        """Initialize AutoMergeOrchestrator."""
        orchestrator = AutoMergeOrchestrator(Path("/tmp"))

        assert orchestrator.repo_root == Path("/tmp")
        assert orchestrator.validator is not None
        assert orchestrator.pr_creator is not None
        assert orchestrator.merge_executor is not None
        assert len(orchestrator.operations) == 0

    @patch('operator.e2e_autogen.auto_merge.PRValidator.validate_all')
    @patch('operator.e2e_autogen.auto_merge.PRCreator.create_pr')
    @patch('operator.e2e_autogen.auto_merge.MergeExecutor.merge_pr')
    def test_execute_auto_merge_success(self, mock_merge, mock_create_pr, mock_validate):
        """Execute auto-merge workflow — success."""
        mock_create_pr.return_value = 123
        mock_validate.return_value = (ValidationResult.PASSED, [
            ValidationCheck("lint", ValidationResult.PASSED, "OK"),
        ])
        mock_merge.return_value = (True, "commit_sha", None)

        orchestrator = AutoMergeOrchestrator(Path("/tmp"))
        result = orchestrator.execute_auto_merge(
            branch_name="feat/test",
            pr_title="Test PR",
            pr_description="Test description"
        )

        assert result.pr_number == 123
        assert result.merge_status == MergeStatus.MERGED
        assert result.merge_commit_sha == "commit_sha"
        assert len(orchestrator.operations) == 1

    @patch('operator.e2e_autogen.auto_merge.PRValidator.validate_all')
    @patch('operator.e2e_autogen.auto_merge.PRCreator.create_pr')
    def test_execute_auto_merge_validation_failed(self, mock_create_pr, mock_validate):
        """Execute auto-merge workflow — validation fails."""
        mock_create_pr.return_value = 456
        mock_validate.return_value = (ValidationResult.FAILED, [
            ValidationCheck("lint", ValidationResult.FAILED, "Issues found"),
        ])

        orchestrator = AutoMergeOrchestrator(Path("/tmp"))
        result = orchestrator.execute_auto_merge(
            branch_name="feat/bad",
            pr_title="Bad PR",
            pr_description="Will fail validation"
        )

        assert result.pr_number == 456
        assert result.merge_status == MergeStatus.VALIDATION_FAILED
        assert result.error_message is not None

    @patch('operator.e2e_autogen.auto_merge.PRCreator.create_pr')
    def test_execute_auto_merge_pr_creation_failed(self, mock_create_pr):
        """Execute auto-merge workflow — PR creation fails."""
        mock_create_pr.return_value = None

        orchestrator = AutoMergeOrchestrator(Path("/tmp"))
        result = orchestrator.execute_auto_merge(
            branch_name="feat/fail",
            pr_title="Fail PR",
            pr_description=""
        )

        assert result.merge_status == MergeStatus.MERGE_FAILED
        assert "Failed to create PR" in result.error_message

    def test_save_operations_log(self):
        """Save operations log to file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            orchestrator = AutoMergeOrchestrator(Path("/tmp"))

            # Add some mock operations
            op = MergeOperation(
                pr_number=123,
                branch_name="feat/test",
                merge_status=MergeStatus.MERGED,
                merge_commit_sha="abc123"
            )
            orchestrator.operations.append(op)

            log_path = Path(tmpdir) / "operations.json"
            success = orchestrator.save_operations_log(log_path)

            assert success is True
            assert log_path.exists()

            with open(log_path) as f:
                data = json.load(f)
                assert len(data) == 1
                assert data[0]["pr_number"] == 123


# ============================================================================
# Tests for PipelineStage
# ============================================================================

class TestPipelineStage:
    """Tests for pipeline stages."""

    def test_create_pipeline_stage(self):
        """Create a PipelineStage."""
        stage = PipelineStage(
            name="lint",
            status=PipelineStatus.SUCCESS,
            output="Lint passed"
        )

        assert stage.name == "lint"
        assert stage.status == PipelineStatus.SUCCESS

    def test_pipeline_stage_to_dict(self):
        """Convert PipelineStage to dict."""
        stage = PipelineStage(
            name="unit_tests",
            status=PipelineStatus.SUCCESS,
            output="10 tests passed",
            duration_seconds=15.5
        )

        data = stage.to_dict()
        assert data["name"] == "unit_tests"
        assert data["status"] == "success"
        assert data["duration_seconds"] == 15.5


# ============================================================================
# Tests for PipelineExecution
# ============================================================================

class TestPipelineExecution:
    """Tests for pipeline execution."""

    def test_create_pipeline_execution(self):
        """Create a PipelineExecution."""
        exec = PipelineExecution(
            pipeline_type=PipelineType.GENERATE,
            execution_id="exec_001",
            branch_name="feat/test"
        )

        assert exec.pipeline_type == PipelineType.GENERATE
        assert exec.execution_id == "exec_001"
        assert exec.status == PipelineStatus.PENDING

    def test_pipeline_execution_to_dict(self):
        """Convert PipelineExecution to dict."""
        exec = PipelineExecution(
            pipeline_type=PipelineType.TEST,
            execution_id="exec_002",
            branch_name="feat/feature",
            status=PipelineStatus.SUCCESS,
            tests_passed=10,
            tests_failed=0
        )

        data = exec.to_dict()
        assert data["pipeline_type"] == "test:e2e"
        assert data["execution_id"] == "exec_002"
        assert data["tests_passed"] == 10


# ============================================================================
# Tests for TestScenario
# ============================================================================

class TestTestScenario:
    """Tests for test scenarios."""

    def test_create_test_scenario(self):
        """Create a TestScenario."""
        scenario = TestScenario(
            scenario_id="sc_001",
            name="Test 1",
            category="golden-path",
            test_file="tests/test_001.py",
            test_function="test_scenario_001",
            priority="high"
        )

        assert scenario.scenario_id == "sc_001"
        assert scenario.category == "golden-path"
        assert scenario.priority == "high"

    def test_test_scenario_to_dict(self):
        """Convert TestScenario to dict."""
        scenario = TestScenario(
            scenario_id="sc_002",
            name="Test 2",
            category="edge-case",
            test_file="tests/test_002.py",
            test_function="test_scenario_002",
            priority="medium",
            adr_references=["ADR-0124"]
        )

        data = scenario.to_dict()
        assert data["scenario_id"] == "sc_002"
        assert data["adr_references"] == ["ADR-0124"]


# ============================================================================
# Tests for PipelineExecutor
# ============================================================================

class TestPipelineExecutor:
    """Tests for pipeline execution."""

    def test_executor_initialization(self):
        """Initialize PipelineExecutor."""
        executor = PipelineExecutor(Path("/tmp"), timeout_seconds=600)

        assert executor.repo_root == Path("/tmp")
        assert executor.timeout_seconds == 600

    @patch('operator.e2e_autogen.ci_integration.subprocess.run')
    def test_execute_generate_pipeline(self, mock_run):
        """Execute generate pipeline."""
        mock_run.return_value = Mock(returncode=0, stdout="OK", stderr="")

        executor = PipelineExecutor(Path("/tmp"))
        result = executor.execute_generate_pipeline("exec_001", "feat/test")

        assert result.pipeline_type == PipelineType.GENERATE
        assert result.status == PipelineStatus.SUCCESS

    @patch('operator.e2e_autogen.ci_integration.subprocess.run')
    def test_execute_test_pipeline(self, mock_run):
        """Execute test pipeline."""
        mock_run.return_value = Mock(returncode=0, stdout="5 passed", stderr="")

        executor = PipelineExecutor(Path("/tmp"))
        result = executor.execute_test_pipeline("exec_002", "feat/feature")

        assert result.pipeline_type == PipelineType.TEST
        assert result.status == PipelineStatus.SUCCESS

    @patch('operator.e2e_autogen.ci_integration.subprocess.run')
    def test_execute_pipeline_failure(self, mock_run):
        """Handle pipeline failure."""
        mock_run.return_value = Mock(returncode=1, stdout="", stderr="Command failed")

        executor = PipelineExecutor(Path("/tmp"))
        result = executor.execute_generate_pipeline("exec_003", "feat/bad")

        assert result.status == PipelineStatus.FAILURE


# ============================================================================
# Tests for RegistryManager
# ============================================================================

class TestRegistryManager:
    """Tests for test scenario registry."""

    def test_registry_initialization(self):
        """Initialize RegistryManager."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path = Path(tmpdir) / "registry.json"
            registry = RegistryManager(registry_path)

            assert registry.registry_path == registry_path
            assert len(registry.scenarios) == 0

    def test_add_scenario(self):
        """Add a scenario to registry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = RegistryManager(Path(tmpdir) / "registry.json")

            scenario = TestScenario(
                scenario_id="sc_001",
                name="Test 1",
                category="golden-path",
                test_file="tests/test_001.py",
                test_function="test_001",
                priority="high"
            )

            success = registry.add_scenario(scenario)
            assert success is True
            assert len(registry.scenarios) == 1

    def test_add_multiple_scenarios(self):
        """Add multiple scenarios."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = RegistryManager(Path(tmpdir) / "registry.json")

            scenarios = [
                TestScenario(f"sc_{i:03d}", f"Test {i}", "golden-path",
                           f"tests/test_{i:03d}.py", f"test_{i:03d}", "medium")
                for i in range(5)
            ]

            count = registry.add_scenarios(scenarios)
            assert count == 5
            assert len(registry.scenarios) == 5

    def test_get_scenario(self):
        """Get scenario by ID."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = RegistryManager(Path(tmpdir) / "registry.json")

            scenario = TestScenario(
                scenario_id="sc_test",
                name="Test",
                category="happy-path",
                test_file="tests/test.py",
                test_function="test_func",
                priority="low"
            )

            registry.add_scenario(scenario)
            retrieved = registry.get_scenario("sc_test")

            assert retrieved is not None
            assert retrieved.scenario_id == "sc_test"

    def test_get_scenarios_by_category(self):
        """Get scenarios by category."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = RegistryManager(Path(tmpdir) / "registry.json")

            for i in range(3):
                registry.add_scenario(TestScenario(
                    f"sc_golden_{i}", f"Golden {i}", "golden-path",
                    f"tests/test_{i}.py", f"test_{i}", "high"
                ))

            for i in range(2):
                registry.add_scenario(TestScenario(
                    f"sc_edge_{i}", f"Edge {i}", "edge-case",
                    f"tests/test_edge_{i}.py", f"test_edge_{i}", "medium"
                ))

            golden = registry.get_scenarios_by_category("golden-path")
            assert len(golden) == 3

            edge = registry.get_scenarios_by_category("edge-case")
            assert len(edge) == 2

    def test_update_registry_index(self):
        """Update registry index file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path = Path(tmpdir) / "registry.json"
            registry = RegistryManager(registry_path)

            scenario = TestScenario(
                scenario_id="sc_001",
                name="Test",
                category="golden-path",
                test_file="tests/test.py",
                test_function="test",
                priority="medium"
            )

            registry.add_scenario(scenario)
            success = registry.update_index()

            assert success is True
            assert registry_path.exists()

            with open(registry_path) as f:
                data = json.load(f)
                assert data["metadata"]["total_scenarios"] == 1
                assert len(data["scenarios"]) == 1


# ============================================================================
# Tests for CICDIntegrationOrchestrator
# ============================================================================

class TestCICDIntegrationOrchestrator:
    """Tests for CI/CD orchestration."""

    def test_orchestrator_initialization(self):
        """Initialize CICDIntegrationOrchestrator."""
        with tempfile.TemporaryDirectory() as tmpdir:
            orchestrator = CICDIntegrationOrchestrator(Path(tmpdir))

            assert orchestrator.repo_root == Path(tmpdir)
            assert orchestrator.registry is not None
            assert len(orchestrator.executions) == 0

    @patch('operator.e2e_autogen.ci_integration.PipelineExecutor.execute_generate_pipeline')
    @patch('operator.e2e_autogen.ci_integration.PipelineExecutor.execute_test_pipeline')
    def test_run_full_pipeline(self, mock_test, mock_generate):
        """Run full CI/CD pipeline."""
        mock_generate.return_value = PipelineExecution(
            pipeline_type=PipelineType.GENERATE,
            execution_id="exec_001",
            branch_name="feat/test",
            status=PipelineStatus.SUCCESS,
            tests_generated=5
        )

        mock_test.return_value = PipelineExecution(
            pipeline_type=PipelineType.TEST,
            execution_id="exec_001_test",
            branch_name="feat/test",
            status=PipelineStatus.SUCCESS,
            tests_passed=5,
            tests_failed=0
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            orchestrator = CICDIntegrationOrchestrator(Path(tmpdir))
            gen_exec, test_exec = orchestrator.run_full_pipeline("exec_001", "feat/test")

            assert gen_exec.status == PipelineStatus.SUCCESS
            assert test_exec is not None
            assert test_exec.status == PipelineStatus.SUCCESS
            assert len(orchestrator.executions) == 2

    def test_save_executions_log(self):
        """Save executions log to file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            orchestrator = CICDIntegrationOrchestrator(Path(tmpdir))

            exec = PipelineExecution(
                pipeline_type=PipelineType.GENERATE,
                execution_id="exec_001",
                branch_name="feat/test",
                status=PipelineStatus.SUCCESS
            )

            orchestrator.executions.append(exec)

            log_path = Path(tmpdir) / "executions.json"
            success = orchestrator.save_executions_log(log_path)

            assert success is True
            assert log_path.exists()


# ============================================================================
# Integration Tests
# ============================================================================

class TestPhase5IntegrationScenarios:
    """End-to-end integration tests for Phase 5."""

    @patch('operator.e2e_autogen.auto_merge.PRValidator.validate_all')
    @patch('operator.e2e_autogen.auto_merge.PRCreator.create_pr')
    @patch('operator.e2e_autogen.auto_merge.MergeExecutor.merge_pr')
    def test_e2e_auto_merge_workflow(self, mock_merge, mock_create_pr, mock_validate):
        """E2E: Complete auto-merge workflow."""
        mock_create_pr.return_value = 999
        mock_validate.return_value = (ValidationResult.PASSED, [
            ValidationCheck("lint", ValidationResult.PASSED, "OK"),
            ValidationCheck("unit_tests", ValidationResult.PASSED, "OK"),
        ])
        mock_merge.return_value = (True, "sha_merged_999", None)

        orchestrator = AutoMergeOrchestrator(Path("/tmp"))
        result = orchestrator.execute_auto_merge(
            branch_name="feat/e2e-phase5",
            pr_title="feat: Phase 5 Auto-Integration",
            pr_description="Complete auto-merge workflow"
        )

        assert result.pr_number == 999
        assert result.merge_status == MergeStatus.MERGED
        assert result.merge_commit_sha == "sha_merged_999"
        assert result.validation_status == ValidationResult.PASSED

    @patch('operator.e2e_autogen.ci_integration.PipelineExecutor.execute_generate_pipeline')
    @patch('operator.e2e_autogen.ci_integration.PipelineExecutor.execute_test_pipeline')
    def test_e2e_cicd_pipeline_workflow(self, mock_test, mock_generate):
        """E2E: Complete CI/CD pipeline workflow."""
        mock_generate.return_value = PipelineExecution(
            pipeline_type=PipelineType.GENERATE,
            execution_id="e2e_001",
            branch_name="feat/cicd",
            status=PipelineStatus.SUCCESS,
            tests_generated=8,
            start_time=datetime.utcnow()
        )

        mock_test.return_value = PipelineExecution(
            pipeline_type=PipelineType.TEST,
            execution_id="e2e_001_test",
            branch_name="feat/cicd",
            status=PipelineStatus.SUCCESS,
            tests_passed=8,
            tests_failed=0,
            coverage_percentage=85.5
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            orchestrator = CICDIntegrationOrchestrator(Path(tmpdir))
            gen_exec, test_exec = orchestrator.run_full_pipeline("e2e_001", "feat/cicd")

            assert gen_exec.status == PipelineStatus.SUCCESS
            assert gen_exec.tests_generated == 8
            assert test_exec.tests_passed == 8
            assert test_exec.coverage_percentage > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
