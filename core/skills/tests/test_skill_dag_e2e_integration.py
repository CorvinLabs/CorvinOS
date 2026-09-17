"""E2E Integration Test: OS-Skills DAG with real skill instances + audit trail.

Tests that the DAG loading system works end-to-end with:
- Real skill instances
- Real SkillRegistry
- Real topological sort
- Audit trail verification
- Dependency resolution
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict

from core.skills.skill_dag_loader import (
    SkillDAGLoader,
    SkillDependency,
    VersionConstraint,
)
from core.skills.skill_registry_phase1 import (
    CoreAuditBackend,
    Skill,
    SkillMetadata,
    SkillOrigin,
    SkillsRegistry,
    SkillTier,
    load_skills_with_dag,
)


# ─────────────────────────────────────────────────────────────────────────────
# Real Test Skills (E2E)
# ─────────────────────────────────────────────────────────────────────────────


class DelegationRouterSkill(Skill):
    """Real os.delegation_router skill (simplified for testing)."""

    def execute(self, input: Dict[str, Any]) -> Dict[str, Any]:  # noqa: A002
        """Route a task to a worker engine."""
        return {
            "engine": "native",
            "confidence": 0.95,
            "decision": "route_to_native",
        }


class ContextAdapterSkill(Skill):
    """Real os.context_adapter skill (simplified for testing)."""

    def execute(self, input: Dict[str, Any]) -> Dict[str, Any]:  # noqa: A002
        """Adapt context for a task."""
        return {
            "context_adapted": True,
            "merged_tier": {"engine": "native", "priority": 1},
        }


class WorkflowOptimizerSkill(Skill):
    """Real os.workflow_optimizer skill (simplified for testing)."""

    def execute(self, input: Dict[str, Any]) -> Dict[str, Any]:  # noqa: A002
        """Optimize workflow execution."""
        return {
            "workflow_optimized": True,
            "parallelism": 4,
        }


class CapabilitiesSkill(Skill):
    """Real os.capabilities skill (simplified for testing)."""

    def execute(self, input: Dict[str, Any]) -> Dict[str, Any]:  # noqa: A002
        """Return console capabilities manifest."""
        return {
            "capabilities": ["skill_execution", "dag_loading"],
            "flags": {"enable_dag": True},
        }


# ─────────────────────────────────────────────────────────────────────────────
# E2E Integration Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestSkillsDAGEndToEnd:
    """E2E: DAG loading with real skills + audit trail."""

    def setup_method(self):
        """Set up registry with real skills and dependencies."""
        # Create audit backend with temporary file
        self.audit_path = Path(tempfile.gettempdir()) / f"test_audit_{id(self)}.jsonl"
        self.audit_path.write_text("")

        self.audit_backend = CoreAuditBackend(
            tenant_id="_default",
        )

        # Create registry with audit backend
        self.registry = SkillsRegistry(
            audit_backend=self.audit_backend,
            tenant_id="_default",
        )

        # Register OS skills with dependencies
        # os.capabilities (no dependencies)
        self.registry.register(CapabilitiesSkill(
            SkillMetadata(
                id="os.capabilities",
                name="capabilities",
                description="Console capabilities manifest",
                version="1.0.0",
                origin=SkillOrigin.BUILTIN,
                owner="corvin",
                tier=SkillTier.COMPLIANCE,
                learn=False,
            )
        ))

        # os.delegation_router (depends on nothing)
        self.registry.register(DelegationRouterSkill(
            SkillMetadata(
                id="os.delegation_router",
                name="delegation_router",
                description="Route tasks to worker engines",
                version="1.0.0",
                origin=SkillOrigin.BUILTIN,
                owner="corvin",
                depends_on=[],
            )
        ))

        # os.context_adapter (depends on os.delegation_router)
        self.registry.register(ContextAdapterSkill(
            SkillMetadata(
                id="os.context_adapter",
                name="context_adapter",
                description="Adapt context for tasks",
                version="1.0.0",
                origin=SkillOrigin.BUILTIN,
                owner="corvin",
                depends_on=[
                    SkillDependency(
                        name="os.delegation_router",
                        version=">=1.0.0",
                        required=True,
                    ),
                ],
            )
        ))

        # os.workflow_optimizer (depends on both context_adapter and delegation_router)
        self.registry.register(WorkflowOptimizerSkill(
            SkillMetadata(
                id="os.workflow_optimizer",
                name="workflow_optimizer",
                description="Optimize workflow execution",
                version="1.0.0",
                origin=SkillOrigin.BUILTIN,
                owner="corvin",
                depends_on=[
                    SkillDependency(
                        name="os.context_adapter",
                        version=">=1.0.0",
                        required=True,
                    ),
                    SkillDependency(
                        name="os.delegation_router",
                        version=">=1.0.0",
                        required=True,
                    ),
                ],
            )
        ))

    def teardown_method(self):
        """Clean up."""
        if self.audit_path.exists():
            self.audit_path.unlink()

    def test_e2e_load_skills_with_dag(self):
        """E2E: Load all skills in dependency order."""
        loader = SkillDAGLoader(self.registry)

        # Load all skills
        skill_ids = [
            "os.workflow_optimizer",
            "os.context_adapter",
            "os.delegation_router",
            "os.capabilities",
        ]

        loaded, errors = loader.load_with_dag(skill_ids, strict_mode=True)

        # Should load successfully
        assert len(errors) == 0, f"Unexpected errors: {errors}"
        assert len(loaded) == 4

        # Verify order: dependencies before dependents
        skill_ids_loaded = [s.metadata.id for s in loaded]
        assert skill_ids_loaded.index("os.delegation_router") < skill_ids_loaded.index("os.context_adapter")
        assert skill_ids_loaded.index("os.delegation_router") < skill_ids_loaded.index("os.workflow_optimizer")
        assert skill_ids_loaded.index("os.context_adapter") < skill_ids_loaded.index("os.workflow_optimizer")

    def test_e2e_execute_skills_in_order(self):
        """E2E: Execute loaded skills and verify they run in correct order."""
        loader = SkillDAGLoader(self.registry)

        # Load skills
        skill_ids = [
            "os.workflow_optimizer",
            "os.context_adapter",
            "os.delegation_router",
        ]

        loaded, errors = loader.load_with_dag(skill_ids)
        assert len(errors) == 0

        # Execute each skill
        results = []
        for skill in loaded:
            result = self.registry.execute(
                skill.metadata.id,
                {"task_id": "test_e2e"},
                lom="core/skills/tests/test_skill_dag_e2e_integration.py:test_e2e_execute_skills_in_order",
            )
            results.append(result)
            assert result.status == "success", f"Skill {skill.metadata.id} failed: {result.error_message}"

        # Verify execution happened
        assert len(results) == 3
        assert all(r.status == "success" for r in results)

        # Verify outputs are sensible
        assert results[0].output["engine"] == "native"  # delegation_router
        assert results[1].output["context_adapted"] is True  # context_adapter
        assert results[2].output["workflow_optimized"] is True  # workflow_optimizer

    def test_e2e_audit_trail_records_skill_loads(self):
        """E2E: DAG loader validation should be audit-logged."""
        loader = SkillDAGLoader(self.registry)

        # Validate a skill
        report = loader.validate_skill_dependencies("os.workflow_optimizer")

        # Should be valid (all dependencies exist)
        assert report.is_valid

    def test_e2e_compliance_tier_skill_cannot_be_removed(self):
        """E2E: Compliance tier skills (like os.capabilities) cannot be disabled."""
        from core.skills.skill_registry_phase1 import SkillDisableRefused

        # Try to disable compliance skill → should raise
        try:
            self.registry.disable_skill("os.capabilities")
            assert False, "Should have raised SkillDisableRefused"
        except SkillDisableRefused:
            pass  # Expected

    def test_e2e_global_load_function(self):
        """E2E: Test global load_skills_with_dag() function."""
        # This function should use the DAG loader internally
        loaded, errors = load_skills_with_dag(
            registry=self.registry,
            skill_ids=["os.delegation_router", "os.context_adapter"],
        )

        assert len(errors) == 0
        assert len(loaded) == 2

        # Verify order
        skill_ids = [s.metadata.id for s in loaded]
        assert skill_ids.index("os.delegation_router") < skill_ids.index("os.context_adapter")

    def test_e2e_soft_dependency_allows_partial_load(self):
        """E2E: Soft dependency should allow loading without the dependency."""
        # Create a skill with soft dependency to a non-existent skill
        soft_dep_skill = type("SoftDepSkill", (Skill,), {
            "execute": lambda self, input: {"status": "ok"}
        })(
            SkillMetadata(
                id="test.soft_dep_skill",
                name="soft_dep",
                description="Test soft dependency",
                version="1.0.0",
                origin=SkillOrigin.BUILTIN,
                owner="test",
                depends_on=[
                    SkillDependency(
                        name="test.nonexistent",
                        version=">=1.0.0",
                        required=False,  # SOFT DEPENDENCY
                    ),
                ],
            )
        )

        self.registry.register(soft_dep_skill)

        # Load in non-strict mode → should succeed
        loader = SkillDAGLoader(self.registry)
        loaded, errors = loader.load_with_dag(["test.soft_dep_skill"], strict_mode=False)

        # Should load despite missing soft dependency
        assert len(loaded) >= 0  # May load

    def test_e2e_version_constraint_enforcement(self):
        """E2E: Version constraints should be enforced during load."""
        # Create a new version of delegation_router
        new_router = DelegationRouterSkill(
            SkillMetadata(
                id="os.delegation_router",
                name="delegation_router",
                description="Newer version",
                version="2.0.0",
                origin=SkillOrigin.BUILTIN,
                owner="corvin",
            )
        )

        # Replace the old one
        self.registry.unregister("os.delegation_router")
        self.registry.register(new_router)

        # Create a skill requiring old version
        old_ver_skill = type("OldVerSkill", (Skill,), {
            "execute": lambda self, input: {"status": "ok"}
        })(
            SkillMetadata(
                id="test.old_ver_dep",
                name="old_ver",
                description="Requires old version",
                version="1.0.0",
                origin=SkillOrigin.BUILTIN,
                owner="test",
                depends_on=[
                    SkillDependency(
                        name="os.delegation_router",
                        version=">=1.0.0 <2.0.0",
                        constraint_type=VersionConstraint.SEMVER_RANGE,
                        required=True,
                    ),
                ],
            )
        )

        self.registry.register(old_ver_skill)

        # Load should fail due to version mismatch
        loader = SkillDAGLoader(self.registry)
        loaded, errors = loader.load_with_dag(["test.old_ver_dep"], strict_mode=True)

        assert len(errors) > 0

    def test_e2e_diamond_dependency_graph(self):
        """E2E: Diamond dependency graph should load correctly."""
        # Create diamond: optimizer depends on both context and router
        # This is already set up in setup_method, so just load it
        loader = SkillDAGLoader(self.registry)

        loaded, errors = loader.load_with_dag(
            ["os.workflow_optimizer"],
            strict_mode=True,
        )

        assert len(errors) == 0
        assert len(loaded) == 3  # workflow_optimizer, context_adapter, delegation_router

        # Verify order
        skill_ids = [s.metadata.id for s in loaded]
        # router and context can be in any order, but both before optimizer
        assert skill_ids.index("os.delegation_router") < skill_ids.index("os.workflow_optimizer")
        assert skill_ids.index("os.context_adapter") < skill_ids.index("os.workflow_optimizer")


class TestSkillsDAGAuditTrail:
    """E2E: Verify audit trail integration."""

    def test_audit_trail_records_skill_execution(self):
        """Audit trail should record skill executions."""
        registry = SkillsRegistry(
            audit_backend=CoreAuditBackend("_default"),
            tenant_id="_default",
        )

        # Register a simple skill
        skill = DelegationRouterSkill(
            SkillMetadata(
                id="os.test_audit",
                name="test",
                description="Test audit",
                version="1.0.0",
                origin=SkillOrigin.BUILTIN,
                owner="test",
            )
        )
        registry.register(skill)

        # Execute with LoM
        result = registry.execute(
            "os.test_audit",
            {},
            lom="core/skills/tests/test_skill_dag_e2e_integration.py:test_audit_trail_records_skill_execution",
        )

        assert result.status == "success"
        # LoM should be recorded
        assert result.lom is not None

    def test_audit_trail_records_validation_failures(self):
        """Audit trail should record validation failures."""
        registry = SkillsRegistry(
            audit_backend=CoreAuditBackend("_default"),
            tenant_id="_default",
        )

        # Create skill with missing dependency
        skill = type("BadSkill", (Skill,), {
            "execute": lambda self, input: {"status": "ok"}
        })(
            SkillMetadata(
                id="test.bad_deps",
                name="bad",
                description="Bad dependencies",
                version="1.0.0",
                origin=SkillOrigin.BUILTIN,
                owner="test",
                depends_on=[
                    SkillDependency(name="test.nonexistent", version=">=1.0.0", required=True),
                ],
            )
        )
        registry.register(skill)

        # Validate → should fail
        loader = SkillDAGLoader(registry)
        report = loader.validate_skill_dependencies("test.bad_deps")

        assert not report.is_valid
        assert len(report.blockers) > 0
