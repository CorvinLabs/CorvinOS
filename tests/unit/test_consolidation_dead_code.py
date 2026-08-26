"""
Tests for Dead-Code Detector — ADR-0421

Coverage:
- Unused import detection (high confidence)
- Unreachable function detection (medium confidence)
- Orphaned class detection (medium confidence)
- Unused variable detection (high confidence)
- Audit integration (GDPR Art. 30)
- Performance on large codebases
"""

import pytest
from pathlib import Path
from tempfile import TemporaryDirectory

from core.consolidation.dead_code_detector import (
    DeadCodeDetector,
    DeadCodeFinding,
    DeadCodeReport,
)


class TestDeadCodeDetector:
    """Test dead-code detection."""

    @pytest.fixture
    def temp_codebase(self):
        """Create a temporary codebase with various dead code patterns."""
        with TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # File 1: Unused import
            (tmpdir_path / "module1.py").write_text(
                """
import os  # Unused
import sys  # Used
print(sys.version)
"""
            )

            # File 2: Unreachable function
            (tmpdir_path / "module2.py").write_text(
                """
def called_function():
    return 42

def unused_function():
    return 99

result = called_function()
"""
            )

            # File 3: Orphaned class
            (tmpdir_path / "module3.py").write_text(
                """
class UsedClass:
    pass

class UnusedClass:
    pass

obj = UsedClass()
"""
            )

            # File 4: Unused variable
            (tmpdir_path / "module4.py").write_text(
                """
used_var = 42
unused_var = 99
print(used_var)
"""
            )

            # File 5: Complex patterns (private names shouldn't be reported)
            (tmpdir_path / "module5.py").write_text(
                """
import pytest  # Unused but OK (pytest fixture)
_internal_var = 42
__dunder_var = 99

def _private_func():
    return 42

def __dunder_func():
    return 99

def used_public_func():
    return 42

result = used_public_func()
"""
            )

            yield tmpdir_path

    def test_detect_unused_import(self, temp_codebase):
        """Test detection of unused imports."""
        detector = DeadCodeDetector(temp_codebase)
        report = detector.scan()

        # Should find unused 'os' import
        unused_imports = [
            f
            for f in report.findings
            if f.finding_type == "unused_import" and f.module_path == "module1.py"
        ]
        assert len(unused_imports) >= 1
        assert any(f.name == "os" for f in unused_imports)
        assert all(f.confidence > 0.8 for f in unused_imports)

    def test_detect_unreachable_function(self, temp_codebase):
        """Test detection of unreachable functions."""
        detector = DeadCodeDetector(temp_codebase)
        report = detector.scan()

        # Should find unused_function
        unreachable = [
            f
            for f in report.findings
            if f.finding_type == "unreachable_function"
            and f.module_path == "module2.py"
            and f.name == "unused_function"
        ]
        assert len(unreachable) == 1
        assert unreachable[0].confidence >= 0.6

    def test_detect_orphaned_class(self, temp_codebase):
        """Test detection of orphaned classes."""
        detector = DeadCodeDetector(temp_codebase)
        report = detector.scan()

        # Should find UnusedClass
        orphaned = [
            f
            for f in report.findings
            if f.finding_type == "orphaned_class"
            and f.module_path == "module3.py"
            and f.name == "UnusedClass"
        ]
        assert len(orphaned) == 1
        assert orphaned[0].confidence >= 0.5

    def test_detect_unused_variable(self, temp_codebase):
        """Test detection of unused variables."""
        detector = DeadCodeDetector(temp_codebase)
        report = detector.scan()

        # Should find unused_var
        unused_vars = [
            f
            for f in report.findings
            if f.finding_type == "unused_variable"
            and f.module_path == "module4.py"
            and f.name == "unused_var"
        ]
        assert len(unused_vars) == 1
        assert unused_vars[0].confidence >= 0.75

    def test_skip_private_names(self, temp_codebase):
        """Test that private/dunder names are skipped."""
        detector = DeadCodeDetector(temp_codebase)
        report = detector.scan()

        # Should NOT report _private_func, __dunder_func, _internal_var, __dunder_var
        private_findings = [
            f for f in report.findings if f.module_path == "module5.py"
            and (f.name.startswith("_") or "internal" in f.name or "dunder" in f.name)
        ]
        assert len(private_findings) == 0

    def test_report_structure(self, temp_codebase):
        """Test DeadCodeReport structure and metadata."""
        detector = DeadCodeDetector(temp_codebase, tenant_id="test_tenant")
        report = detector.scan()

        assert report.total_files_scanned > 0
        assert report.files_with_issues > 0
        assert isinstance(report.findings, tuple)
        assert report.scan_duration_ms > 0
        assert report.tenant_id == "test_tenant"

    def test_audit_event_generation(self, temp_codebase):
        """Test generation of audit event dict."""
        detector = DeadCodeDetector(temp_codebase)
        report = detector.scan()

        event = detector.get_audit_event_dict(report)

        assert event["event_type"] == "consolidation_dead_code_scan"
        assert event["actor"] == "consolidation_system"
        assert event["action"] == "detect_dead_code"
        assert "resource" in event
        assert "timestamp" in event
        assert "details" in event
        assert event["details"]["tenant_id"] == "_default"

    def test_immutability_of_findings(self, temp_codebase):
        """Test that findings are immutable."""
        detector = DeadCodeDetector(temp_codebase)
        report = detector.scan()

        if report.findings:
            with pytest.raises(Exception):  # FrozenDataclass raises on modification
                report.findings[0].confidence = 1.0

    def test_syntax_error_handling(self):
        """Test graceful handling of syntax errors."""
        with TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # File with syntax error
            (tmpdir_path / "bad_syntax.py").write_text(
                """
def broken_function(
    # Missing closing paren and function body
"""
            )

            detector = DeadCodeDetector(tmpdir_path)
            report = detector.scan()

            # Should complete without raising
            assert report.total_files_scanned >= 1

    def test_empty_codebase(self):
        """Test detection on empty codebase."""
        with TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            detector = DeadCodeDetector(tmpdir_path)
            report = detector.scan()

            assert report.total_files_scanned == 0
            assert len(report.findings) == 0

    def test_large_file_handling(self):
        """Test handling of large files."""
        with TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Create a large file
            large_code = "\n".join([f"var_{i} = {i}" for i in range(1000)])
            large_code += "\nprint(var_0)\n"
            (tmpdir_path / "large.py").write_text(large_code)

            detector = DeadCodeDetector(tmpdir_path)
            report = detector.scan()

            assert report.total_files_scanned > 0
            # Should find ~999 unused variables
            unused = [f for f in report.findings if f.finding_type == "unused_variable"]
            assert len(unused) > 900

    def test_tenant_isolation(self, temp_codebase):
        """Test that tenant_id is properly isolated."""
        detector1 = DeadCodeDetector(temp_codebase, tenant_id="tenant1")
        detector2 = DeadCodeDetector(temp_codebase, tenant_id="tenant2")

        report1 = detector1.scan()
        report2 = detector2.scan()

        assert report1.tenant_id == "tenant1"
        assert report2.tenant_id == "tenant2"

    def test_no_dead_code_case(self):
        """Test codebase with no dead code."""
        with TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # All symbols used
            (tmpdir_path / "clean.py").write_text(
                """
import sys
def my_function():
    return 42

class MyClass:
    pass

used_var = 99
obj = MyClass()
result = my_function()
print(sys.version, used_var, result, obj)
"""
            )

            detector = DeadCodeDetector(tmpdir_path)
            report = detector.scan()

            # Clean code should have minimal findings
            assert len(report.findings) < 3

    def test_multiple_files_aggregation(self, temp_codebase):
        """Test aggregation of findings across multiple files."""
        detector = DeadCodeDetector(temp_codebase)
        report = detector.scan()

        # Should scan multiple files
        assert report.total_files_scanned >= 4
        # Should find issues in multiple files
        modules_with_issues = set(f.module_path for f in report.findings)
        assert len(modules_with_issues) >= 3

    def test_finding_line_numbers(self, temp_codebase):
        """Test that line numbers are correctly reported."""
        detector = DeadCodeDetector(temp_codebase)
        report = detector.scan()

        # All findings should have valid line numbers
        for finding in report.findings:
            assert finding.line_number > 0
            assert finding.module_path
            assert finding.name

    def test_confidence_scores(self, temp_codebase):
        """Test that confidence scores are appropriate."""
        detector = DeadCodeDetector(temp_codebase)
        report = detector.scan()

        for finding in report.findings:
            assert 0.0 <= finding.confidence <= 1.0
            if finding.finding_type == "unused_import":
                assert finding.confidence >= 0.85
            elif finding.finding_type == "unreachable_function":
                assert finding.confidence >= 0.6
            elif finding.finding_type == "orphaned_class":
                assert finding.confidence >= 0.5
            elif finding.finding_type == "unused_variable":
                assert finding.confidence >= 0.75

    def test_chained_references(self):
        """Test that chained references are detected."""
        with TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            (tmpdir_path / "test.py").write_text(
                """
import sys
class A:
    pass

def func1():
    return A()

def func2():
    return func1()

x = func2()
print(x)
"""
            )

            detector = DeadCodeDetector(tmpdir_path)
            report = detector.scan()

            # Should recognize chain: func2 -> func1 -> A
            # None of these should be reported as dead code
            dead_names = {f.name for f in report.findings}
            assert "func1" not in dead_names
            assert "func2" not in dead_names
            assert "A" not in dead_names

    @pytest.mark.parametrize(
        "code,expected_dead",
        [
            ("import os\nx = 1", ["os"]),
            ("def foo(): pass", ["foo"]),
            ("class Bar: pass", ["Bar"]),
        ],
    )
    def test_parametrized_detection(self, code, expected_dead):
        """Parametrized test for various dead code patterns."""
        with TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            (tmpdir_path / "test.py").write_text(code)

            detector = DeadCodeDetector(tmpdir_path)
            report = detector.scan()

            found_dead = {f.name for f in report.findings}
            for expected_name in expected_dead:
                assert expected_name in found_dead
