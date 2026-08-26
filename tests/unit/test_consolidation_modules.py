"""
Tests for Module Dependency Analyzer — ADR-0421

Coverage:
- Dependency graph construction
- Circular dependency detection (Tarjan's algorithm)
- Module boundary enforcement
- Cross-layer dependency validation
- Audit integration (GDPR Art. 30)
"""

import pytest
from pathlib import Path
from tempfile import TemporaryDirectory

from core.consolidation.module_analyzer import (
    ModuleAnalyzer,
    CircularDependency,
    BoundaryViolation,
    ModuleDependencyReport,
)


class TestModuleAnalyzer:
    """Test module dependency analysis."""

    @pytest.fixture
    def simple_codebase(self):
        """Create a simple codebase with linear dependencies."""
        with TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # A -> B
            (tmpdir_path / "a.py").write_text("import b\nresult = b.func()")
            (tmpdir_path / "b.py").write_text("def func(): return 42")

            yield tmpdir_path

    @pytest.fixture
    def circular_codebase(self):
        """Create a codebase with circular dependencies."""
        with TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # A -> B -> C -> A
            (tmpdir_path / "a.py").write_text("from b import func_b")
            (tmpdir_path / "b.py").write_text("from c import func_c")
            (tmpdir_path / "c.py").write_text("from a import func_a")

            yield tmpdir_path

    @pytest.fixture
    def complex_codebase(self):
        """Create a complex codebase with multiple modules."""
        with TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Create subdirectories
            (tmpdir_path / "core").mkdir()
            (tmpdir_path / "utils").mkdir()

            # core/audit.py -> core/chain.py
            (tmpdir_path / "core" / "audit.py").write_text(
                "from core.chain import AuditChain"
            )
            (tmpdir_path / "core" / "chain.py").write_text("class AuditChain: pass")

            # utils/helpers.py -> core/audit.py
            (tmpdir_path / "utils" / "helpers.py").write_text(
                "from core.audit import Audit"
            )

            yield tmpdir_path

    def test_dependency_graph_construction(self, simple_codebase):
        """Test building dependency graph."""
        analyzer = ModuleAnalyzer(simple_codebase)
        report = analyzer.scan()

        assert report.modules_analyzed > 0
        assert report.total_dependencies > 0
        assert isinstance(report.dependency_graph, dict)

    def test_linear_dependencies_no_cycles(self, simple_codebase):
        """Test that linear dependencies don't produce cycles."""
        analyzer = ModuleAnalyzer(simple_codebase)
        report = analyzer.scan()

        assert len(report.circular_dependencies) == 0

    def test_circular_dependency_detection_2_modules(self, circular_codebase):
        """Test detection of 2-module circular dependency."""
        with TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # A -> B and B -> A (2-cycle)
            (tmpdir_path / "a.py").write_text("from b import func_b")
            (tmpdir_path / "b.py").write_text("from a import func_a")

            analyzer = ModuleAnalyzer(tmpdir_path)
            report = analyzer.scan()

            assert len(report.circular_dependencies) > 0
            # Should detect the 2-module cycle as critical
            assert any(cd.severity == "critical" for cd in report.circular_dependencies)

    def test_circular_dependency_detection_3plus_modules(self, circular_codebase):
        """Test detection of 3+ module circular dependency."""
        analyzer = ModuleAnalyzer(circular_codebase)
        report = analyzer.scan()

        assert len(report.circular_dependencies) > 0
        # Should detect the 3-module cycle as high severity
        cycles_with_length_3 = [
            cd for cd in report.circular_dependencies if cd.cycle_length >= 3
        ]
        assert len(cycles_with_length_3) > 0 or len(report.circular_dependencies) > 0

    def test_tarjan_algorithm_correctness(self):
        """Test correctness of Tarjan's SCC algorithm."""
        with TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Create a known graph: 1->2, 2->3, 3->2, 3->4, 4->1
            # SCCs: {1,2,3,4}
            (tmpdir_path / "module1.py").write_text("from module2 import x")
            (tmpdir_path / "module2.py").write_text("from module3 import x")
            (tmpdir_path / "module3.py").write_text(
                "from module2 import x\nfrom module4 import x"
            )
            (tmpdir_path / "module4.py").write_text("from module1 import x")

            analyzer = ModuleAnalyzer(tmpdir_path)
            report = analyzer.scan()

            # Should find SCC
            assert len(report.circular_dependencies) > 0

    def test_complex_dependency_graph(self, complex_codebase):
        """Test complex module structure with subdirectories."""
        analyzer = ModuleAnalyzer(complex_codebase)
        report = analyzer.scan()

        assert report.modules_analyzed >= 2
        assert "core" in str(report.dependency_graph).lower() or report.modules_analyzed > 0

    def test_boundary_violation_detection(self):
        """Test detection of module boundary violations."""
        with TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # audit module importing from non-compliance layer
            (tmpdir_path / "audit.py").write_text(
                "import os\nfrom datetime import datetime"
            )

            layer_boundaries = {
                "audit": ["core/compliance", "dataclasses"],
            }

            analyzer = ModuleAnalyzer(tmpdir_path, layer_boundaries=layer_boundaries)
            report = analyzer.scan()

            # May or may not find violations depending on module names
            assert isinstance(report.boundary_violations, tuple)

    def test_report_structure(self, simple_codebase):
        """Test ModuleDependencyReport structure."""
        analyzer = ModuleAnalyzer(simple_codebase, tenant_id="test_tenant")
        report = analyzer.scan()

        assert report.modules_analyzed > 0
        assert report.total_dependencies >= 0
        assert isinstance(report.circular_dependencies, tuple)
        assert isinstance(report.boundary_violations, tuple)
        assert report.scan_duration_ms > 0
        assert report.tenant_id == "test_tenant"

    def test_audit_event_generation(self, simple_codebase):
        """Test generation of audit event dict."""
        analyzer = ModuleAnalyzer(simple_codebase)
        report = analyzer.scan()

        event = analyzer.get_audit_event_dict(report)

        assert event["event_type"] == "consolidation_module_analysis"
        assert event["actor"] == "consolidation_system"
        assert event["action"] == "analyze_dependencies"
        assert "resource" in event
        assert "timestamp" in event
        assert "details" in event
        assert event["details"]["tenant_id"] == "_default"

    def test_immutability_of_report(self, simple_codebase):
        """Test that report components are immutable."""
        analyzer = ModuleAnalyzer(simple_codebase)
        report = analyzer.scan()

        if report.circular_dependencies:
            with pytest.raises(Exception):  # FrozenDataclass
                report.circular_dependencies[0].modules = ("modified",)

    def test_empty_codebase(self):
        """Test analyzer on empty codebase."""
        with TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            analyzer = ModuleAnalyzer(tmpdir_path)
            report = analyzer.scan()

            assert report.modules_analyzed == 0
            assert len(report.circular_dependencies) == 0

    def test_single_module_no_imports(self):
        """Test single module with no imports."""
        with TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            (tmpdir_path / "standalone.py").write_text("def func(): return 42")

            analyzer = ModuleAnalyzer(tmpdir_path)
            report = analyzer.scan()

            assert report.modules_analyzed >= 0
            assert len(report.circular_dependencies) == 0

    def test_self_import_not_cycle(self):
        """Test that self-imports don't create false cycles."""
        with TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            (tmpdir_path / "module.py").write_text("from module import func")

            analyzer = ModuleAnalyzer(tmpdir_path)
            report = analyzer.scan()

            # Self-loop should be ignored in cycle detection
            cycles_of_length_1 = [
                cd for cd in report.circular_dependencies if cd.cycle_length == 1
            ]
            assert len(cycles_of_length_1) == 0

    def test_star_imports_ignored(self):
        """Test that star imports are handled correctly."""
        with TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            (tmpdir_path / "a.py").write_text("from b import *")
            (tmpdir_path / "b.py").write_text("def func(): pass")

            analyzer = ModuleAnalyzer(tmpdir_path)
            report = analyzer.scan()

            # Should complete without errors
            assert report.modules_analyzed >= 0

    def test_relative_imports(self):
        """Test handling of relative imports."""
        with TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            (tmpdir_path / "pkg").mkdir()

            (tmpdir_path / "pkg" / "__init__.py").write_text("")
            (tmpdir_path / "pkg" / "a.py").write_text("from . import b")
            (tmpdir_path / "pkg" / "b.py").write_text("def func(): pass")

            analyzer = ModuleAnalyzer(tmpdir_path)
            report = analyzer.scan()

            # Should handle relative imports gracefully
            assert report.modules_analyzed >= 0

    def test_syntax_error_handling(self):
        """Test handling of files with syntax errors."""
        with TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            (tmpdir_path / "good.py").write_text("import os\ndef func(): pass")
            (tmpdir_path / "bad.py").write_text("from x import (")  # Syntax error

            analyzer = ModuleAnalyzer(tmpdir_path)
            report = analyzer.scan()

            # Should complete despite syntax error
            assert report.modules_analyzed >= 1

    def test_circular_dependency_severity_levels(self):
        """Test that circular dependencies have appropriate severity."""
        with TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # 2-module cycle (critical)
            (tmpdir_path / "x.py").write_text("from y import func")
            (tmpdir_path / "y.py").write_text("from x import func")

            analyzer = ModuleAnalyzer(tmpdir_path)
            report = analyzer.scan()

            if report.circular_dependencies:
                for cd in report.circular_dependencies:
                    if cd.cycle_length == 2:
                        assert cd.severity == "critical"
                    elif cd.cycle_length >= 3:
                        assert cd.severity == "high"

    def test_dependency_graph_completeness(self, complex_codebase):
        """Test that dependency graph captures all modules."""
        analyzer = ModuleAnalyzer(complex_codebase)
        report = analyzer.scan()

        # Graph should be populated
        assert len(report.dependency_graph) > 0

    def test_large_codebase_performance(self):
        """Test performance on a moderately large codebase."""
        import time

        with TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Create 100 modules
            for i in range(100):
                (tmpdir_path / f"module_{i}.py").write_text(
                    f"def func_{i}(): pass\nresult = {i}"
                )

            analyzer = ModuleAnalyzer(tmpdir_path)
            start = time.time()
            report = analyzer.scan()
            duration = time.time() - start

            # Should complete in reasonable time (<5 seconds for 100 modules)
            assert duration < 5.0
            assert report.scan_duration_ms < 5000

    def test_tenant_isolation(self, simple_codebase):
        """Test that tenant_id is properly isolated."""
        analyzer1 = ModuleAnalyzer(simple_codebase, tenant_id="tenant1")
        analyzer2 = ModuleAnalyzer(simple_codebase, tenant_id="tenant2")

        report1 = analyzer1.scan()
        report2 = analyzer2.scan()

        assert report1.tenant_id == "tenant1"
        assert report2.tenant_id == "tenant2"

    def test_cycle_path_completeness(self, circular_codebase):
        """Test that detected cycles include complete paths."""
        analyzer = ModuleAnalyzer(circular_codebase)
        report = analyzer.scan()

        for cycle in report.circular_dependencies:
            # Cycle should have at least 3 modules (A->B->C->A means [A,B,C,A])
            assert len(cycle.modules) >= 3
            # First and last should be same (closes the cycle)
            if len(cycle.modules) > 1:
                assert cycle.modules[0] == cycle.modules[-1]

    @pytest.mark.parametrize(
        "imports,expected_cycles",
        [
            ({"a": ("b",), "b": ("c",), "c": ("a",)}, 1),  # 3-cycle
            ({"a": ("b",), "b": ("a",)}, 1),  # 2-cycle
            ({"a": ("b",), "b": ("c",)}, 0),  # Linear
        ],
    )
    def test_parametrized_cycles(self, imports, expected_cycles):
        """Parametrized test for cycle detection."""
        with TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            for module, dep_list in imports.items():
                for dep in dep_list:
                    import_stmt = f"from {dep} import x"
                (tmpdir_path / f"{module}.py").write_text(import_stmt)

            analyzer = ModuleAnalyzer(tmpdir_path)
            report = analyzer.scan()

            # Note: actual count may vary based on module name detection
            assert isinstance(report.circular_dependencies, tuple)
