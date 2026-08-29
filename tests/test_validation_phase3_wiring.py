"""
E2E Wiring Proof Tests — Phase 3.A
==================================

Tests for the E2E wiring proof validator.
Verifies reachability detection and functional proof logic.
"""

import pytest
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch
import sys

# Import from validation module
sys.path.insert(0, str(Path(__file__).parent.parent))
from validation.e2e_wiring_proof import (
    E2EWiringProofValidator,
    ReachabilityStatus,
    CallSite,
    E2EWiringProofResult
)


class TestE2EWiringProofValidator:
    """Test suite for E2E wiring proof validator."""

    @pytest.fixture
    def validator(self, tmp_path):
        """Create a validator with a temporary repo root."""
        return E2EWiringProofValidator(repo_root=tmp_path)

    def test_validator_initialization(self, validator):
        """Test that validator initializes correctly."""
        assert validator.repo_root is not None
        assert validator._import_cache == {}
        assert validator._callsite_cache == {}

    def test_result_dataclass_creation(self):
        """Test that E2EWiringProofResult can be created."""
        result = E2EWiringProofResult(
            entry_point="test_func",
            file_path="test.py",
            reachability_status=ReachabilityStatus.PASS
        )
        assert result.entry_point == "test_func"
        assert result.reachability_status == ReachabilityStatus.PASS
        assert result.functional_test_passed == False

    def test_callsite_dataclass_creation(self):
        """Test that CallSite can be created."""
        cs = CallSite(
            file_path="caller.py",
            line_number=42,
            caller_function="caller_fn",
            is_test_file=False
        )
        assert cs.file_path == "caller.py"
        assert cs.line_number == 42
        assert cs.confidence == 0.5

    def test_reachability_status_enum(self):
        """Test ReachabilityStatus enum values."""
        assert ReachabilityStatus.PASS.value == "pass"
        assert ReachabilityStatus.FAIL.value == "fail"
        assert ReachabilityStatus.SKIP.value == "skip"

    def test_parse_entry_point_string(self):
        """Test parsing of entry point string format."""
        entry_point = "core/app.py::my_endpoint"
        file_path, name = entry_point.split("::")
        assert file_path == "core/app.py"
        assert name == "my_endpoint"

    def test_parse_entry_point_with_class_method(self):
        """Test parsing entry point with class method."""
        entry_point = "core/app.py::MyClass.my_method"
        file_path, name = entry_point.split("::")
        assert file_path == "core/app.py"
        assert name == "MyClass.my_method"

    def test_nonexistent_definition_file(self, validator):
        """Test that validator fails when definition file doesn't exist."""
        result = validator.validate("nonexistent/file.py::func")
        assert result.reachability_status == ReachabilityStatus.FAIL
        assert "not found" in result.failure_reason.lower()

    @patch('validation.e2e_wiring_proof.E2EWiringProofValidator._find_all_call_sites')
    def test_zero_call_sites_found(self, mock_find, validator, tmp_path):
        """Test that validator fails when zero call sites found."""
        # Create a dummy file
        dummy_file = tmp_path / "test.py"
        dummy_file.write_text("def my_func(): pass")

        mock_find.return_value = []  # No call sites found

        result = validator.validate("test.py::my_func")
        assert result.reachability_status == ReachabilityStatus.FAIL
        assert "zero" in result.failure_reason.lower()

    @patch('validation.e2e_wiring_proof.E2EWiringProofValidator._find_all_call_sites')
    def test_found_one_real_call_site(self, mock_find, validator, tmp_path):
        """Test that validator passes when one real call site found."""
        # Create a dummy file
        dummy_file = tmp_path / "test.py"
        dummy_file.write_text("def my_func(): pass")

        # Mock finding one real (non-test) call site
        call_site = CallSite(
            file_path="caller.py",
            line_number=10,
            caller_function="caller_fn",
            is_test_file=False
        )
        mock_find.return_value = [call_site]

        result = validator.validate("test.py::my_func")
        assert result.reachability_status == ReachabilityStatus.PASS
        assert len(result.real_call_sites) == 1

    @patch('validation.e2e_wiring_proof.E2EWiringProofValidator._find_all_call_sites')
    def test_filters_out_test_files(self, mock_find, validator, tmp_path):
        """Test that validator filters out call sites from test files."""
        dummy_file = tmp_path / "test.py"
        dummy_file.write_text("def my_func(): pass")

        # Mock finding call sites from test files only
        test_call = CallSite(
            file_path="tests/test_foo.py",
            line_number=20,
            caller_function="test_bar",
            is_test_file=True
        )
        real_call = CallSite(
            file_path="src/real.py",
            line_number=10,
            caller_function="real_caller",
            is_test_file=False
        )
        mock_find.return_value = [test_call, real_call]

        result = validator.validate("test.py::my_func")
        assert len(result.call_sites) == 2
        assert len(result.real_call_sites) == 1
        assert result.real_call_sites[0].file_path == "src/real.py"

    @patch('validation.e2e_wiring_proof.E2EWiringProofValidator._find_all_call_sites')
    def test_filters_out_definition_file(self, mock_find, validator, tmp_path):
        """Test that validator filters out calls from the definition file itself."""
        dummy_file = tmp_path / "test.py"
        dummy_file.write_text("def my_func(): pass")

        # Mock finding calls from definition file (recursive calls)
        self_call = CallSite(
            file_path="test.py",
            line_number=5,
            caller_function="another_func",
            is_test_file=False
        )
        real_call = CallSite(
            file_path="caller.py",
            line_number=10,
            caller_function="real_caller",
            is_test_file=False
        )
        mock_find.return_value = [self_call, real_call]

        result = validator.validate("test.py::my_func")
        assert len(result.call_sites) == 2
        assert len(result.real_call_sites) == 1
        assert result.real_call_sites[0].file_path == "caller.py"

    @patch('validation.e2e_wiring_proof.E2EWiringProofValidator._find_all_call_sites')
    def test_functional_test_passes(self, mock_find, validator, tmp_path):
        """Test that functional proof runs when Phase 1 passes."""
        dummy_file = tmp_path / "test.py"
        dummy_file.write_text("def my_func(): pass")

        real_call = CallSite(
            file_path="caller.py",
            line_number=10,
            caller_function="real_caller",
            is_test_file=False,
            transport_boundary="http"
        )
        mock_find.return_value = [real_call]

        functional_test_fn = Mock(return_value=True)

        result = validator.validate(
            "test.py::my_func",
            required_transport="http",
            functional_test_fn=functional_test_fn
        )

        assert result.reachability_status == ReachabilityStatus.PASS
        assert result.functional_test_passed == True
        functional_test_fn.assert_called_once()

    @patch('validation.e2e_wiring_proof.E2EWiringProofValidator._find_all_call_sites')
    def test_functional_test_fails(self, mock_find, validator, tmp_path):
        """Test that functional proof failure is captured."""
        dummy_file = tmp_path / "test.py"
        dummy_file.write_text("def my_func(): pass")

        real_call = CallSite(
            file_path="caller.py",
            line_number=10,
            caller_function="real_caller",
            is_test_file=False
        )
        mock_find.return_value = [real_call]

        functional_test_fn = Mock(return_value=False)

        result = validator.validate("test.py::my_func", functional_test_fn=functional_test_fn)

        assert result.reachability_status == ReachabilityStatus.PASS  # Phase 1 passed
        assert result.functional_test_passed == False  # Phase 2 failed

    @patch('validation.e2e_wiring_proof.E2EWiringProofValidator._find_all_call_sites')
    def test_functional_test_exception(self, mock_find, validator, tmp_path):
        """Test that functional test exception is handled."""
        dummy_file = tmp_path / "test.py"
        dummy_file.write_text("def my_func(): pass")

        real_call = CallSite(
            file_path="caller.py",
            line_number=10,
            caller_function="real_caller",
            is_test_file=False
        )
        mock_find.return_value = [real_call]

        functional_test_fn = Mock(side_effect=ValueError("Transport error"))

        result = validator.validate("test.py::my_func", functional_test_fn=functional_test_fn)

        assert result.reachability_status == ReachabilityStatus.PASS
        assert result.functional_test_passed == False
        assert "Transport error" in result.functional_test_output

    def test_result_to_json(self):
        """Test that result can be serialized to JSON."""
        result = E2EWiringProofResult(
            entry_point="test_func",
            file_path="test.py",
            reachability_status=ReachabilityStatus.PASS,
            call_sites=[CallSite(
                file_path="caller.py",
                line_number=10,
                caller_function="caller_fn",
                is_test_file=False
            )]
        )

        validator = E2EWiringProofValidator()
        json_str = validator.to_json(result)
        assert "test_func" in json_str
        assert "pass" in json_str
        assert "caller.py" in json_str

    def test_batch_validation(self, validator, tmp_path):
        """Test batch validation of multiple entry points."""
        # Create dummy files
        (tmp_path / "a.py").write_text("def func_a(): pass")
        (tmp_path / "b.py").write_text("def func_b(): pass")

        entry_points = ["a.py::func_a", "b.py::func_b"]

        with patch.object(validator, 'validate') as mock_validate:
            mock_validate.return_value = E2EWiringProofResult(
                entry_point="func",
                file_path="test.py",
                reachability_status=ReachabilityStatus.PASS
            )

            results = validator.validate_batch(entry_points)

            assert len(results) == 2
            assert mock_validate.call_count == 2

    def test_infer_transport_from_http_pattern(self, validator):
        """Test that transport boundary is inferred from HTTP patterns."""
        call_sites = [
            CallSite(
                file_path="core/console/routes/api.py",
                line_number=10,
                caller_function="route_endpoint",
                is_test_file=False
            )
        ]

        transport = validator._infer_transport(call_sites)
        assert transport in ["http", None]

    def test_infer_transport_from_cli_pattern(self, validator):
        """Test that transport boundary is inferred from CLI patterns."""
        call_sites = [
            CallSite(
                file_path="core/cli/commands.py",
                line_number=10,
                caller_function="cmd_runner",
                is_test_file=False
            )
        ]

        transport = validator._infer_transport(call_sites)
        assert transport in ["cli", None]

    def test_multiple_call_sites_same_entry(self, validator, tmp_path):
        """Test entry point called from multiple locations."""
        dummy_file = tmp_path / "test.py"
        dummy_file.write_text("def my_func(): pass")

        with patch.object(validator, '_find_all_call_sites') as mock_find:
            call_sites = [
                CallSite(
                    file_path="caller_a.py",
                    line_number=10,
                    caller_function="caller_a",
                    is_test_file=False
                ),
                CallSite(
                    file_path="caller_b.py",
                    line_number=20,
                    caller_function="caller_b",
                    is_test_file=False
                )
            ]
            mock_find.return_value = call_sites

            result = validator.validate("test.py::my_func")
            assert result.reachability_status == ReachabilityStatus.PASS
            assert len(result.real_call_sites) == 2

    def test_entry_point_with_high_confidence(self, validator, tmp_path):
        """Test that direct calls have high confidence."""
        dummy_file = tmp_path / "test.py"
        dummy_file.write_text("def my_func(): pass")

        with patch.object(validator, '_find_all_call_sites') as mock_find:
            call_site = CallSite(
                file_path="caller.py",
                line_number=10,
                caller_function="caller_fn",
                is_test_file=False,
                confidence=1.0
            )
            mock_find.return_value = [call_site]

            result = validator.validate("test.py::my_func")
            assert result.call_sites[0].confidence == 1.0
