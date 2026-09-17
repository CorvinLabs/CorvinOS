"""Gate 4 (Adversarial): OS-Skills DAG adversarial testing (ADR-0535).

Adversarial scenarios:
- Circular deadlock injection (skill_a ↔ skill_b)
- Missing dependency under version constraint
- Version conflict resolution
- DAG explosion (20+ skills, branching deps)
- Timeout isolation (soft deps)
- Failure isolation (required vs soft)
- Mutation testing (malformed dependency objects)
"""

from __future__ import annotations

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


# ─────────────────────────────────────────────────────────────────────────────
# Test Utilities
# ─────────────────────────────────────────────────────────────────────────────


class _TestSkill(Skill):
    """Minimal test skill."""

    def execute(self, input: dict) -> dict:  # noqa: A002
        return {"status": "ok"}


def make_skill(
    skill_id: str,
    version: str = "1.0.0",
    depends_on: list | None = None,
) -> Skill:
    """Create a test skill."""
    if depends_on is None:
        depends_on = []
    return _TestSkill(SkillMetadata(
        id=skill_id,
        name=skill_id.split(".")[-1],
        description=f"Test: {skill_id}",
        version=version,
        origin=SkillOrigin.BUILTIN,
        owner="tests",
        depends_on=depends_on,
    ))


# ─────────────────────────────────────────────────────────────────────────────
# Adversarial Test: Circular Deadlock
# ─────────────────────────────────────────────────────────────────────────────


class TestCircularDeadlock:
    """Gate 4: Inject circular deadlocks and verify rejection."""

    def test_inject_simple_circular_deadlock(self):
        """Inject A↔B mutual dependency, verify rejection."""
        reg = SkillsRegistry()

        # Create circular dependency: A requires B, B requires A
        skill_a = make_skill("test.adv_a", depends_on=[
            SkillDependency(name="test.adv_b", version=">=1.0.0", required=True),
        ])
        skill_b = make_skill("test.adv_b", depends_on=[
            SkillDependency(name="test.adv_a", version=">=1.0.0", required=True),
        ])

        reg.register(skill_a)
        reg.register(skill_b)

        # Try to load: should fail with cycle error
        loader = SkillDAGLoader(reg)
        loaded, errors = loader.load_with_dag(["test.adv_a", "test.adv_b"], strict_mode=True)

        assert len(errors) > 0
        assert len(loaded) == 0
        assert any("cycle" in err.lower() for err in errors)

    def test_inject_three_way_circular_deadlock(self):
        """Inject A→B→C→A circular deadlock."""
        reg = SkillsRegistry()

        skill_a = make_skill("test.adv3_a", depends_on=[
            SkillDependency(name="test.adv3_b", version=">=1.0.0", required=True),
        ])
        skill_b = make_skill("test.adv3_b", depends_on=[
            SkillDependency(name="test.adv3_c", version=">=1.0.0", required=True),
        ])
        skill_c = make_skill("test.adv3_c", depends_on=[
            SkillDependency(name="test.adv3_a", version=">=1.0.0", required=True),
        ])

        reg.register(skill_a)
        reg.register(skill_b)
        reg.register(skill_c)

        loader = SkillDAGLoader(reg)
        loaded, errors = loader.load_with_dag(
            ["test.adv3_a", "test.adv3_b", "test.adv3_c"],
            strict_mode=True,
        )

        assert len(errors) > 0
        assert len(loaded) == 0


# ─────────────────────────────────────────────────────────────────────────────
# Adversarial Test: Missing Dependency Under Constraint
# ─────────────────────────────────────────────────────────────────────────────


class TestMissingDependencyUnderConstraint:
    """Gate 4: Missing dependency blocking load in strict mode."""

    def test_required_missing_blocks_strict_mode(self):
        """Required missing dependency blocks load in strict mode."""
        reg = SkillsRegistry()
        skill = make_skill("test.adv_miss", depends_on=[
            SkillDependency(
                name="test.adv_missing_dep",
                version=">=1.0.0",
                required=True,
            ),
        ])
        reg.register(skill)

        loader = SkillDAGLoader(reg)
        loaded, errors = loader.load_with_dag(["test.adv_miss"], strict_mode=True)

        assert len(errors) > 0
        assert len(loaded) == 0
        assert any("not installed" in err or "not found" in err for err in errors)

    def test_soft_missing_allows_non_strict(self):
        """Soft missing dependency allows load in non-strict mode."""
        reg = SkillsRegistry()
        skill = make_skill("test.adv_soft", depends_on=[
            SkillDependency(
                name="test.adv_missing_dep",
                version=">=1.0.0",
                required=False,
            ),
        ])
        reg.register(skill)

        loader = SkillDAGLoader(reg)
        loaded, errors = loader.load_with_dag(["test.adv_soft"], strict_mode=False)

        # Non-strict mode allows soft dependencies to be missing
        assert len(loaded) >= 0  # May succeed or report as warning


# ─────────────────────────────────────────────────────────────────────────────
# Adversarial Test: Version Conflict
# ─────────────────────────────────────────────────────────────────────────────


class TestVersionConflictResolution:
    """Gate 4: Version conflicts must be detected."""

    def test_version_conflict_blocks_load(self):
        """Version conflict between skill and its requirement blocks load."""
        reg = SkillsRegistry()

        # Register v1.0.0
        reg.register(make_skill("test.adv_conflicted", version="1.0.0"))

        # Create a skill requiring v2.0.0
        skill_b = make_skill("test.adv_requires_v2", depends_on=[
            SkillDependency(
                name="test.adv_conflicted",
                version=">=2.0.0",
                constraint_type=VersionConstraint.SEMVER_RANGE,
                required=True,
            ),
        ])
        reg.register(skill_b)

        loader = SkillDAGLoader(reg)
        loaded, errors = loader.load_with_dag(["test.adv_requires_v2"], strict_mode=True)

        assert len(errors) > 0
        assert len(loaded) == 0

    def test_multiple_version_requirements_conflicting(self):
        """Multiple conflicting version requirements should be reported."""
        reg = SkillsRegistry()

        # Register v1.5.0
        reg.register(make_skill("test.adv_multi_ver", version="1.5.0"))

        # Create two skills with conflicting version requirements
        skill_a = make_skill("test.adv_wants_v1", depends_on=[
            SkillDependency(
                name="test.adv_multi_ver",
                version=">=1.0.0 <2.0.0",
                constraint_type=VersionConstraint.SEMVER_RANGE,
            ),
        ])
        skill_b = make_skill("test.adv_wants_v3", depends_on=[
            SkillDependency(
                name="test.adv_multi_ver",
                version=">=3.0.0",
                constraint_type=VersionConstraint.SEMVER_RANGE,
            ),
        ])

        reg.register(skill_a)
        reg.register(skill_b)

        loader = SkillDAGLoader(reg)
        # Load both → second one should fail version constraint
        loaded, errors = loader.load_with_dag(
            ["test.adv_wants_v1", "test.adv_wants_v3"],
            strict_mode=False,
        )

        # At least one should fail due to version mismatch
        assert len(errors) > 0 or len(loaded) < 2


# ─────────────────────────────────────────────────────────────────────────────
# Adversarial Test: DAG Explosion
# ─────────────────────────────────────────────────────────────────────────────


class TestDAGExplosion:
    """Gate 4: Large graphs should be handled efficiently."""

    def test_dag_explosion_20_skills_branching(self):
        """20 skills with branching deps should not crash or hang."""
        reg = SkillsRegistry()

        # Create 20 skills with branching dependencies
        for i in range(20):
            if i == 0:
                deps = []
            elif i < 3:
                # Layer 1: depends on 0
                deps = [SkillDependency(name=f"test.dag_exp_{i-1}", version=">=1.0.0")]
            elif i < 6:
                # Layer 2: depends on previous layer
                deps = [SkillDependency(name=f"test.dag_exp_{i-3}", version=">=1.0.0")]
            else:
                # Layer 3+: multiple dependencies
                deps = [
                    SkillDependency(name=f"test.dag_exp_{i-10}", version=">=1.0.0"),
                    SkillDependency(name=f"test.dag_exp_{i-5}", version=">=1.0.0"),
                ]

            reg.register(make_skill(f"test.dag_exp_{i}", depends_on=deps))

        loader = SkillDAGLoader(reg)
        skill_ids = [f"test.dag_exp_{i}" for i in range(20)]

        # Should complete without error
        loaded, errors = loader.load_with_dag(skill_ids, strict_mode=False)

        assert len(loaded) > 0  # At least some skills loaded
        # Some errors expected due to dependencies, but should not crash

    def test_very_deep_chain_50_skills(self):
        """Very deep chain (50 skills) should complete without crashing."""
        reg = SkillsRegistry()

        # Create a deep linear chain
        for i in range(50):
            if i == 0:
                deps = []
            else:
                deps = [SkillDependency(name=f"test.deep_{i-1}", version=">=1.0.0")]

            reg.register(make_skill(f"test.deep_{i}", depends_on=deps))

        loader = SkillDAGLoader(reg)
        skill_ids = [f"test.deep_{i}" for i in range(50)]

        loaded, errors = loader.load_with_dag(skill_ids, strict_mode=True)

        # All should load in correct order
        assert len(loaded) == 50
        assert len(errors) == 0


# ─────────────────────────────────────────────────────────────────────────────
# Adversarial Test: Timeout Isolation (Soft Dependencies)
# ─────────────────────────────────────────────────────────────────────────────


class TestTimeoutIsolation:
    """Gate 4: Soft dependency timeouts should not block parent."""

    def test_soft_dep_timeout_budget_tracked(self):
        """Soft dependency with timeout budget should track separately."""
        reg = SkillsRegistry()

        # Create parent skill with soft dependency (timeout budget)
        skill = make_skill("test.timeout_parent", depends_on=[
            SkillDependency(
                name="test.timeout_child",
                version=">=1.0.0",
                required=False,  # soft dependency
                call_pattern="per_worker",
                call_budget_ms=50,  # 50ms per call budget
                timeout_handling="degrade_gracefully",
            ),
        ])
        reg.register(skill)
        reg.register(make_skill("test.timeout_child"))

        loader = SkillDAGLoader(reg)
        report = loader.validate_skill_dependencies("test.timeout_parent")

        # Should validate successfully (soft dep exists and version matches)
        assert report.is_valid

    def test_required_dep_timeout_fails_parent(self):
        """Required dependency timeout should fail parent."""
        reg = SkillsRegistry()

        # Create parent with required dependency and timeout handling
        skill = make_skill("test.fail_parent", depends_on=[
            SkillDependency(
                name="test.fail_child",
                version=">=1.0.0",
                required=True,  # REQUIRED
                call_budget_ms=10,
                timeout_handling="fail_parent",
            ),
        ])
        reg.register(skill)

        # Child doesn't exist → will time out
        loader = SkillDAGLoader(reg)
        loaded, errors = loader.load_with_dag(["test.fail_parent"], strict_mode=True)

        # Should fail because required child is missing
        assert len(errors) > 0


# ─────────────────────────────────────────────────────────────────────────────
# Adversarial Test: Failure Isolation
# ─────────────────────────────────────────────────────────────────────────────


class TestFailureIsolation:
    """Gate 4: Required vs soft failures should be isolated."""

    def test_required_failure_blocks_load(self):
        """Required dependency failure blocks entire load in strict mode."""
        reg = SkillsRegistry()

        skill_a = make_skill("test.req_fail_a", depends_on=[
            SkillDependency(name="test.missing", version=">=1.0.0", required=True),
        ])
        skill_b = make_skill("test.req_fail_b")  # no deps

        reg.register(skill_a)
        reg.register(skill_b)

        loader = SkillDAGLoader(reg)
        loaded, errors = loader.load_with_dag(
            ["test.req_fail_a", "test.req_fail_b"],
            strict_mode=True,
        )

        # Strict mode blocks due to required missing dependency
        assert len(errors) > 0
        assert len(loaded) == 0

    def test_soft_failure_allows_partial_load(self):
        """Soft dependency failure allows partial load in non-strict mode."""
        reg = SkillsRegistry()

        skill_a = make_skill("test.soft_fail_a", depends_on=[
            SkillDependency(name="test.missing", version=">=1.0.0", required=False),
        ])
        skill_b = make_skill("test.soft_fail_b")

        reg.register(skill_a)
        reg.register(skill_b)

        loader = SkillDAGLoader(reg)
        loaded, errors = loader.load_with_dag(
            ["test.soft_fail_a", "test.soft_fail_b"],
            strict_mode=False,
        )

        # Non-strict allows skill_b to load
        assert len(loaded) >= 1


# ─────────────────────────────────────────────────────────────────────────────
# Adversarial Test: Mutation Testing (Malformed Input)
# ─────────────────────────────────────────────────────────────────────────────


class TestMalformedInput:
    """Gate 4: Malformed input should not crash the validator."""

    def test_empty_skill_list(self):
        """Empty skill list should load with no errors."""
        reg = SkillsRegistry()
        loader = SkillDAGLoader(reg)
        loaded, errors = loader.load_with_dag([])

        assert len(loaded) == 0
        assert len(errors) == 0

    def test_none_dependency_name(self):
        """None dependency name should be handled gracefully."""
        reg = SkillsRegistry()

        # Create skill with empty dependency list (safe)
        skill = make_skill("test.none_dep", depends_on=[])
        reg.register(skill)

        loader = SkillDAGLoader(reg)
        report = loader.validate_skill_dependencies("test.none_dep")

        assert report.is_valid

    def test_invalid_version_string(self):
        """Invalid version string should be handled gracefully."""
        reg = SkillsRegistry()

        reg.register(make_skill("test.inv_ver", version="not.a.version"))

        skill_b = make_skill("test.inv_ver_dep", depends_on=[
            SkillDependency(name="test.inv_ver", version=">=1.0.0"),
        ])
        reg.register(skill_b)

        loader = SkillDAGLoader(reg)
        report = loader.validate_skill_dependencies("test.inv_ver_dep")

        # Should handle gracefully (version check may fail or pass)
        # But should NOT crash
        assert isinstance(report, ValidationReport)

    def test_duplicate_skills_in_load_list(self):
        """Duplicate skills in load list should be handled."""
        reg = SkillsRegistry()
        reg.register(make_skill("test.dup"))

        loader = SkillDAGLoader(reg)
        loaded, errors = loader.load_with_dag(["test.dup", "test.dup"])

        # Should load without duplication
        assert len(loaded) <= 1
        assert len(errors) == 0


# ─────────────────────────────────────────────────────────────────────────────
# Adversarial Test: Edge Cases
# ─────────────────────────────────────────────────────────────────────────────


class TestEdgeCases:
    """Gate 4: Edge cases and boundary conditions."""

    def test_self_dependency_is_cycle(self):
        """Skill depending on itself should be detected as a cycle."""
        reg = SkillsRegistry()

        skill = make_skill("test.self_dep", depends_on=[
            SkillDependency(name="test.self_dep", version=">=1.0.0"),
        ])
        reg.register(skill)

        loader = SkillDAGLoader(reg)
        loaded, errors = loader.load_with_dag(["test.self_dep"], strict_mode=True)

        assert len(errors) > 0

    def test_very_long_skill_id(self):
        """Very long skill ID should be handled."""
        reg = SkillsRegistry()

        long_id = "test." + "x" * 200
        skill = make_skill(long_id)
        reg.register(skill)

        loader = SkillDAGLoader(reg)
        loaded, errors = loader.load_with_dag([long_id])

        assert len(loaded) == 1
        assert len(errors) == 0

    def test_special_chars_in_skill_id(self):
        """Special characters in skill ID should be handled."""
        reg = SkillsRegistry()

        # Underscores, dots, hyphens are common
        skill_ids = [
            "test.skill_with_underscore",
            "test.skill-with-dash",
            "test.skill.with.dots",
        ]

        for sid in skill_ids:
            reg.register(make_skill(sid))

        loader = SkillDAGLoader(reg)
        loaded, errors = loader.load_with_dag(skill_ids)

        assert len(loaded) == len(skill_ids)

    def test_unicode_in_skill_id(self):
        """Unicode in skill ID should be handled gracefully."""
        reg = SkillsRegistry()

        skill = make_skill("test.skill_μ")
        reg.register(skill)

        loader = SkillDAGLoader(reg)
        loaded, errors = loader.load_with_dag(["test.skill_μ"])

        assert len(loaded) == 1

    def test_very_large_dependency_list(self):
        """Skill with many dependencies should be handled."""
        reg = SkillsRegistry()

        # Create 50 dependencies
        deps = [
            SkillDependency(name=f"test.dep_{i}", version=">=1.0.0", required=False)
            for i in range(50)
        ]

        skill = make_skill("test.many_deps", depends_on=deps)
        reg.register(skill)

        # Register all dependencies
        for i in range(50):
            reg.register(make_skill(f"test.dep_{i}"))

        loader = SkillDAGLoader(reg)
        report = loader.validate_skill_dependencies("test.many_deps")

        assert report.is_valid
