"""Gate 3 (Red→Green): OS-Skills DAG validator + topological loader (ADR-0535).

Test coverage:
- Circular dependency detection (simple cycles, complex cycles)
- Missing dependency detection
- Version constraint validation
- Diamond graph dependencies
- Topological sort correctness
- Large graph performance
- Composition with soft/required dependencies
"""

from __future__ import annotations

import time
from typing import List

from core.skills.skill_dag_loader import (
    DependencyValidator,
    SkillDAGLoader,
    SkillDependency,
    ValidationReport,
    VersionConstraint,
    topological_sort_skills,
)
from core.skills.skill_registry_phase1 import (
    Skill,
    SkillMetadata,
    SkillOrigin,
    SkillsRegistry,
    SkillTier,
)

# pytest fixtures and decorators are optional
try:
    import pytest
except ImportError:
    # Provide no-op pytest module
    class _NoPytest:
        @staticmethod
        def fixture(func):
            return func
    pytest = _NoPytest()


# ─────────────────────────────────────────────────────────────────────────────
# Test Skills with Various Dependency Configurations
# ─────────────────────────────────────────────────────────────────────────────


class _BaseSkill(Skill):
    """Test skill base."""

    def execute(self, input: dict) -> dict:  # noqa: A002
        return {"status": "ok", "skill_id": self.metadata.id}


def make_skill(
    skill_id: str,
    version: str = "1.0.0",
    depends_on: List[SkillDependency] | None = None,
) -> Skill:
    """Create a test skill with optional dependencies."""
    if depends_on is None:
        depends_on = []
    metadata = SkillMetadata(
        id=skill_id,
        name=skill_id.split(".")[-1],
        description=f"Test skill {skill_id}",
        version=version,
        origin=SkillOrigin.BUILTIN,
        owner="tests",
        depends_on=depends_on,
    )
    return _BaseSkill(metadata)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def empty_registry() -> SkillsRegistry:
    """Empty registry for testing."""
    return SkillsRegistry()


@pytest.fixture
def simple_registry(empty_registry: SkillsRegistry) -> SkillsRegistry:
    """Registry with 3 independent skills."""
    reg = empty_registry
    reg.register(make_skill("test.skill_a"))
    reg.register(make_skill("test.skill_b"))
    reg.register(make_skill("test.skill_c"))
    return reg


@pytest.fixture
def chain_registry(empty_registry: SkillsRegistry) -> SkillsRegistry:
    """Registry with linear dependency: C → B → A."""
    reg = empty_registry
    reg.register(make_skill("test.skill_a"))
    reg.register(make_skill("test.skill_b", depends_on=[
        SkillDependency(name="test.skill_a", version=">=1.0.0"),
    ]))
    reg.register(make_skill("test.skill_c", depends_on=[
        SkillDependency(name="test.skill_b", version=">=1.0.0"),
    ]))
    return reg


@pytest.fixture
def diamond_registry(empty_registry: SkillsRegistry) -> SkillsRegistry:
    """Registry with diamond graph: D depends on B and C, both depend on A."""
    reg = empty_registry
    reg.register(make_skill("test.skill_a"))
    reg.register(make_skill("test.skill_b", depends_on=[
        SkillDependency(name="test.skill_a", version=">=1.0.0"),
    ]))
    reg.register(make_skill("test.skill_c", depends_on=[
        SkillDependency(name="test.skill_a", version=">=1.0.0"),
    ]))
    reg.register(make_skill("test.skill_d", depends_on=[
        SkillDependency(name="test.skill_b", version=">=1.0.0"),
        SkillDependency(name="test.skill_c", version=">=1.0.0"),
    ]))
    return reg


# ─────────────────────────────────────────────────────────────────────────────
# Test: Circular Dependency Detection
# ─────────────────────────────────────────────────────────────────────────────


class TestCircularDependencyDetection:
    """Gate 3: Verify circular dependency detection."""

    def test_simple_cycle_a_to_b_to_a(self, empty_registry: SkillsRegistry):
        """Detect simple cycle: A → B → A."""
        # Create A (depends on B) and B (depends on A)
        skill_a = make_skill("test.skill_a", depends_on=[
            SkillDependency(name="test.skill_b", version=">=1.0.0"),
        ])
        skill_b = make_skill("test.skill_b", depends_on=[
            SkillDependency(name="test.skill_a", version=">=1.0.0"),
        ])

        empty_registry.register(skill_a)
        empty_registry.register(skill_b)

        validator = DependencyValidator(empty_registry)
        report = validator.validate_dependencies("test.skill_a", skill_a.metadata.depends_on)

        assert not report.is_valid
        assert len(report.blockers) > 0
        assert any("cycle" in blocker.lower() for blocker in report.blockers)

    def test_complex_cycle_a_to_b_to_c_to_a(self, empty_registry: SkillsRegistry):
        """Detect complex cycle: A → B → C → A."""
        skill_a = make_skill("test.skill_a", depends_on=[
            SkillDependency(name="test.skill_b", version=">=1.0.0"),
        ])
        skill_b = make_skill("test.skill_b", depends_on=[
            SkillDependency(name="test.skill_c", version=">=1.0.0"),
        ])
        skill_c = make_skill("test.skill_c", depends_on=[
            SkillDependency(name="test.skill_a", version=">=1.0.0"),
        ])

        empty_registry.register(skill_a)
        empty_registry.register(skill_b)
        empty_registry.register(skill_c)

        validator = DependencyValidator(empty_registry)
        report = validator.validate_dependencies("test.skill_a", skill_a.metadata.depends_on)

        assert not report.is_valid
        assert len(report.blockers) > 0
        assert any("cycle" in blocker.lower() for blocker in report.blockers)

    def test_self_dependency_rejected(self, empty_registry: SkillsRegistry):
        """Reject a skill depending on itself."""
        skill = make_skill("test.skill_self", depends_on=[
            SkillDependency(name="test.skill_self", version=">=1.0.0"),
        ])
        empty_registry.register(skill)

        validator = DependencyValidator(empty_registry)
        report = validator.validate_dependencies("test.skill_self", skill.metadata.depends_on)

        assert not report.is_valid
        assert any("cycle" in blocker.lower() for blocker in report.blockers)

    def test_acyclic_graph_passes(self, chain_registry: SkillsRegistry):
        """Acyclic graphs should pass validation."""
        validator = DependencyValidator(chain_registry)
        skill_c = chain_registry._skills["test.skill_c"]

        report = validator.validate_dependencies("test.skill_c", skill_c.metadata.depends_on)

        assert report.is_valid


# ─────────────────────────────────────────────────────────────────────────────
# Test: Missing Dependency Detection
# ─────────────────────────────────────────────────────────────────────────────


class TestMissingDependencyDetection:
    """Gate 3: Verify missing dependency detection."""

    def test_required_dependency_missing(self, empty_registry: SkillsRegistry):
        """Required missing dependency should block."""
        skill = make_skill("test.skill_a", depends_on=[
            SkillDependency(name="test.nonexistent", version=">=1.0.0", required=True),
        ])
        empty_registry.register(skill)

        validator = DependencyValidator(empty_registry)
        report = validator.validate_dependencies("test.skill_a", skill.metadata.depends_on)

        assert not report.is_valid
        assert any("not installed" in blocker for blocker in report.blockers)

    def test_soft_dependency_missing_is_warning(self, empty_registry: SkillsRegistry):
        """Soft (optional) missing dependency should be a warning."""
        skill = make_skill("test.skill_a", depends_on=[
            SkillDependency(name="test.nonexistent", version=">=1.0.0", required=False),
        ])
        empty_registry.register(skill)

        validator = DependencyValidator(empty_registry)
        report = validator.validate_dependencies("test.skill_a", skill.metadata.depends_on)

        assert report.is_valid
        assert any("not installed" in warning for warning in report.warnings)

    def test_multiple_missing_dependencies(self, empty_registry: SkillsRegistry):
        """Multiple missing required dependencies should all be reported."""
        skill = make_skill("test.skill_a", depends_on=[
            SkillDependency(name="test.missing1", version=">=1.0.0", required=True),
            SkillDependency(name="test.missing2", version=">=1.0.0", required=True),
        ])
        empty_registry.register(skill)

        validator = DependencyValidator(empty_registry)
        report = validator.validate_dependencies("test.skill_a", skill.metadata.depends_on)

        assert not report.is_valid
        assert len(report.blockers) >= 2


# ─────────────────────────────────────────────────────────────────────────────
# Test: Version Constraint Validation
# ─────────────────────────────────────────────────────────────────────────────


class TestVersionConstraintValidation:
    """Gate 3: Verify version constraint matching."""

    def test_exact_version_match(self, empty_registry: SkillsRegistry):
        """EXACT constraint should require exact version match."""
        empty_registry.register(make_skill("test.skill_a", version="1.2.3"))

        skill_b = make_skill("test.skill_b", depends_on=[
            SkillDependency(
                name="test.skill_a",
                version="1.2.3",
                constraint_type=VersionConstraint.EXACT,
            ),
        ])
        empty_registry.register(skill_b)

        validator = DependencyValidator(empty_registry)
        report = validator.validate_dependencies("test.skill_b", skill_b.metadata.depends_on)
        assert report.is_valid

    def test_exact_version_mismatch(self, empty_registry: SkillsRegistry):
        """EXACT constraint should fail on version mismatch."""
        empty_registry.register(make_skill("test.skill_a", version="1.2.3"))

        skill_b = make_skill("test.skill_b", depends_on=[
            SkillDependency(
                name="test.skill_a",
                version="1.2.4",
                constraint_type=VersionConstraint.EXACT,
            ),
        ])
        empty_registry.register(skill_b)

        validator = DependencyValidator(empty_registry)
        report = validator.validate_dependencies("test.skill_b", skill_b.metadata.depends_on)
        assert not report.is_valid

    def test_semver_range_greater_equal(self, empty_registry: SkillsRegistry):
        """SEMVER range >=X.Y.Z should accept equal or higher versions."""
        empty_registry.register(make_skill("test.skill_a", version="1.5.0"))

        skill_b = make_skill("test.skill_b", depends_on=[
            SkillDependency(
                name="test.skill_a",
                version=">=1.2.0",
                constraint_type=VersionConstraint.SEMVER_RANGE,
            ),
        ])
        empty_registry.register(skill_b)

        validator = DependencyValidator(empty_registry)
        report = validator.validate_dependencies("test.skill_b", skill_b.metadata.depends_on)
        assert report.is_valid

    def test_semver_range_less_than(self, empty_registry: SkillsRegistry):
        """SEMVER range <X.Y.Z should reject higher versions."""
        empty_registry.register(make_skill("test.skill_a", version="2.0.0"))

        skill_b = make_skill("test.skill_b", depends_on=[
            SkillDependency(
                name="test.skill_a",
                version="<2.0.0",
                constraint_type=VersionConstraint.SEMVER_RANGE,
            ),
        ])
        empty_registry.register(skill_b)

        validator = DependencyValidator(empty_registry)
        report = validator.validate_dependencies("test.skill_b", skill_b.metadata.depends_on)
        assert not report.is_valid

    def test_semver_range_complex(self, empty_registry: SkillsRegistry):
        """SEMVER range with multiple constraints (>=X.Y.Z <A.B.C)."""
        empty_registry.register(make_skill("test.skill_a", version="1.5.0"))

        skill_b = make_skill("test.skill_b", depends_on=[
            SkillDependency(
                name="test.skill_a",
                version=">=1.2.0 <2.0.0",
                constraint_type=VersionConstraint.SEMVER_RANGE,
            ),
        ])
        empty_registry.register(skill_b)

        validator = DependencyValidator(empty_registry)
        report = validator.validate_dependencies("test.skill_b", skill_b.metadata.depends_on)
        assert report.is_valid

    def test_any_version_constraint(self, empty_registry: SkillsRegistry):
        """ANY version constraint should accept any installed version."""
        empty_registry.register(make_skill("test.skill_a", version="1.0.0"))

        skill_b = make_skill("test.skill_b", depends_on=[
            SkillDependency(
                name="test.skill_a",
                version="*",
                constraint_type=VersionConstraint.ANY,
            ),
        ])
        empty_registry.register(skill_b)

        validator = DependencyValidator(empty_registry)
        report = validator.validate_dependencies("test.skill_b", skill_b.metadata.depends_on)
        assert report.is_valid


# ─────────────────────────────────────────────────────────────────────────────
# Test: Topological Sort Correctness
# ─────────────────────────────────────────────────────────────────────────────


class TestTopologicalSort:
    """Gate 3: Verify topological sort (Kahn's algorithm)."""

    def test_linear_chain_ordering(self, chain_registry: SkillsRegistry):
        """Linear chain C→B→A should be sorted as A, B, C."""
        skills = [
            chain_registry._skills["test.skill_c"],
            chain_registry._skills["test.skill_b"],
            chain_registry._skills["test.skill_a"],
        ]

        sorted_skills, error = topological_sort_skills(skills, chain_registry)

        assert error is None
        skill_ids = [s.metadata.id for s in sorted_skills]
        assert skill_ids == ["test.skill_a", "test.skill_b", "test.skill_c"]

    def test_independent_skills_order_flexible(self, simple_registry: SkillsRegistry):
        """Independent skills can be in any order."""
        skills = [
            simple_registry._skills["test.skill_a"],
            simple_registry._skills["test.skill_b"],
            simple_registry._skills["test.skill_c"],
        ]

        sorted_skills, error = topological_sort_skills(skills, simple_registry)

        assert error is None
        assert len(sorted_skills) == 3
        skill_ids = {s.metadata.id for s in sorted_skills}
        assert skill_ids == {"test.skill_a", "test.skill_b", "test.skill_c"}

    def test_diamond_graph_ordering(self, diamond_registry: SkillsRegistry):
        """Diamond graph: A before B/C, B/C before D."""
        skills = [
            diamond_registry._skills["test.skill_d"],
            diamond_registry._skills["test.skill_c"],
            diamond_registry._skills["test.skill_b"],
            diamond_registry._skills["test.skill_a"],
        ]

        sorted_skills, error = topological_sort_skills(skills, diamond_registry)

        assert error is None
        skill_ids = [s.metadata.id for s in sorted_skills]

        # A must come first
        assert skill_ids.index("test.skill_a") == 0
        # B and C must come before D
        assert skill_ids.index("test.skill_b") < skill_ids.index("test.skill_d")
        assert skill_ids.index("test.skill_c") < skill_ids.index("test.skill_d")

    def test_sort_is_deterministic(self, diamond_registry: SkillsRegistry):
        """Topological sort should be deterministic (same input → same output)."""
        skills = [
            diamond_registry._skills["test.skill_d"],
            diamond_registry._skills["test.skill_c"],
            diamond_registry._skills["test.skill_b"],
            diamond_registry._skills["test.skill_a"],
        ]

        sorted1, _ = topological_sort_skills(skills, diamond_registry)
        sorted2, _ = topological_sort_skills(skills, diamond_registry)

        ids1 = [s.metadata.id for s in sorted1]
        ids2 = [s.metadata.id for s in sorted2]
        assert ids1 == ids2

    def test_cyclic_graph_returns_error(self, empty_registry: SkillsRegistry):
        """Cyclic graph should return an error string."""
        # Create a cycle: A → B → A
        skill_a = make_skill("test.skill_a", depends_on=[
            SkillDependency(name="test.skill_b", version=">=1.0.0"),
        ])
        skill_b = make_skill("test.skill_b", depends_on=[
            SkillDependency(name="test.skill_a", version=">=1.0.0"),
        ])

        empty_registry.register(skill_a)
        empty_registry.register(skill_b)

        skills = [skill_a, skill_b]
        sorted_skills, error = topological_sort_skills(skills, empty_registry)

        assert error is not None
        assert "cycle" in error.lower()
        assert sorted_skills == []


# ─────────────────────────────────────────────────────────────────────────────
# Test: DAG Loader Integration
# ─────────────────────────────────────────────────────────────────────────────


class TestDAGLoader:
    """Gate 3: Verify SkillDAGLoader integration."""

    def test_load_with_dag_simple(self, simple_registry: SkillsRegistry):
        """Load simple independent skills."""
        loader = SkillDAGLoader(simple_registry)
        loaded, errors = loader.load_with_dag(
            ["test.skill_a", "test.skill_b", "test.skill_c"],
        )

        assert len(errors) == 0
        assert len(loaded) == 3
        skill_ids = {s.metadata.id for s in loaded}
        assert skill_ids == {"test.skill_a", "test.skill_b", "test.skill_c"}

    def test_load_with_dag_chain(self, chain_registry: SkillsRegistry):
        """Load chain of dependent skills in correct order."""
        loader = SkillDAGLoader(chain_registry)
        loaded, errors = loader.load_with_dag(["test.skill_c", "test.skill_a", "test.skill_b"])

        assert len(errors) == 0
        assert len(loaded) == 3
        skill_ids = [s.metadata.id for s in loaded]
        # A must come before B, B before C
        assert skill_ids.index("test.skill_a") < skill_ids.index("test.skill_b")
        assert skill_ids.index("test.skill_b") < skill_ids.index("test.skill_c")

    def test_load_with_dag_missing_skill(self, simple_registry: SkillsRegistry):
        """Missing skill should be reported."""
        loader = SkillDAGLoader(simple_registry)
        loaded, errors = loader.load_with_dag(["test.skill_a", "test.nonexistent"])

        assert len(errors) > 0
        assert any("not found" in err.lower() for err in errors)

    def test_load_with_dag_strict_mode(self, simple_registry: SkillsRegistry):
        """Strict mode should block on any error."""
        loader = SkillDAGLoader(simple_registry)
        loaded, errors = loader.load_with_dag(
            ["test.skill_a", "test.nonexistent"],
            strict_mode=True,
        )

        assert len(errors) > 0
        assert len(loaded) == 0

    def test_load_with_dag_non_strict_mode(self, simple_registry: SkillsRegistry):
        """Non-strict mode should load available skills."""
        loader = SkillDAGLoader(simple_registry)
        loaded, errors = loader.load_with_dag(
            ["test.skill_a", "test.nonexistent"],
            strict_mode=False,
        )

        assert len(errors) > 0
        assert len(loaded) == 1
        assert loaded[0].metadata.id == "test.skill_a"

    def test_validate_skill_dependencies(self, chain_registry: SkillsRegistry):
        """Validate a single skill's dependencies."""
        loader = SkillDAGLoader(chain_registry)
        report = loader.validate_skill_dependencies("test.skill_c")

        assert report.is_valid

    def test_validate_nonexistent_skill(self, simple_registry: SkillsRegistry):
        """Validate nonexistent skill should fail."""
        loader = SkillDAGLoader(simple_registry)
        report = loader.validate_skill_dependencies("test.nonexistent")

        assert not report.is_valid
        assert any("not found" in blocker for blocker in report.blockers)


# ─────────────────────────────────────────────────────────────────────────────
# Test: Performance & Scalability
# ─────────────────────────────────────────────────────────────────────────────


class TestDAGPerformance:
    """Gate 3: Performance tests for large graphs."""

    def test_large_graph_load_performance(self):
        """Large graph (20 skills, branching deps) should load <500ms."""
        reg = SkillsRegistry()

        # Create 20 skills with branching dependencies
        for i in range(20):
            if i == 0:
                deps = []
            elif i < 5:
                deps = [SkillDependency(name=f"test.skill_{i-1}", version=">=1.0.0")]
            else:
                # Branch dependency: multiple parents
                deps = [
                    SkillDependency(name=f"test.skill_{i-5}", version=">=1.0.0"),
                    SkillDependency(name=f"test.skill_{i-10}", version=">=1.0.0"),
                ]
            reg.register(make_skill(f"test.skill_{i}", depends_on=deps))

        loader = SkillDAGLoader(reg)
        skill_ids = [f"test.skill_{i}" for i in range(20)]

        start = time.time()
        loaded, errors = loader.load_with_dag(skill_ids)
        elapsed_ms = (time.time() - start) * 1000

        assert len(errors) == 0
        assert len(loaded) == 20
        assert elapsed_ms < 500, f"Load took {elapsed_ms:.1f}ms, expected <500ms"

    def test_deep_chain_performance(self):
        """Very deep chain (50 skills) should load <500ms."""
        reg = SkillsRegistry()

        # Create a deep chain: 50 → 49 → 48 → ... → 0
        for i in range(50):
            if i == 0:
                deps = []
            else:
                deps = [SkillDependency(name=f"test.skill_{i-1}", version=">=1.0.0")]
            reg.register(make_skill(f"test.skill_{i}", depends_on=deps))

        loader = SkillDAGLoader(reg)
        skill_ids = [f"test.skill_{i}" for i in range(50)]

        start = time.time()
        loaded, errors = loader.load_with_dag(skill_ids)
        elapsed_ms = (time.time() - start) * 1000

        assert len(errors) == 0
        assert len(loaded) == 50
        assert elapsed_ms < 500, f"Load took {elapsed_ms:.1f}ms, expected <500ms"
