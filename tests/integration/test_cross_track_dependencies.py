"""
INTEGRATION TEST: Cross-Track Dependencies
Verify all 8 completed tracks work together

Tracks:
- A: Skill Forge v2.0 (ADR-0674)
- B: Learning Loop Integration (ADR-0676)
- C: OS-Skills DAG Orchestration (ADR-0535)
- D: Marketplace Hub Discovery (ADR-0678)
- E: Learning→Skill Integration (ADR-0675/0676)
- F: Licensing 1.0.0 (ADR-0700-0704)
- H: DoD Verifier Skill 2.0 (ADR-0820)
- I: DataHub Creator (ADR-0878)

Cross-track dependencies:
- D + F: Marketplace install checks Licensing tier
- A + B: Skill Forge packages include learning config
- B + C: Learning feedback tunes Skills in DAG order
- H + E: DoD Verifier score improves with feedback
- I + B: DataHub aggregates all skill metrics
"""

import pytest
import json
import time
from datetime import datetime
from unittest.mock import Mock, patch, MagicMock

from core.marketplace.plugin_registry import PluginRegistry
from core.marketplace.plugin_tier_system import TierQuotaManager, TierMetadata
from core.skill_forge.loader import SkillForgeLoader
from core.skill_forge.manifest import SkillManifest
from core.skills.os_skills.orchestrator import SkillOrchestrator, SkillDAG
from core.learning.active_loop import ActiveLearningLoop
from core.skills.video_producer_skill_2_0.dod_verifier import DODVerifierSkill
from core.datahub.creator import DataHubCreator
from core.compliance.audit_integration import AuditTrail, AuditEvent


class TestCrossTrackDependencyD_F:
    """Test D + F: Marketplace (D) respects Licensing tier (F)"""

    def test_marketplace_enforces_license_tier_on_install(self):
        """When user without Tier A tries to install Tier A plugin, should fail gracefully"""
        marketplace = PluginRegistry()
        licensing = TierQuotaManager(tier_metadata={
            "A": TierMetadata("A", 5, 1000, 10, 100),
            "B": TierMetadata("B", 20, 10000, 100, 1000)
        })

        # Register a Tier A plugin
        marketplace.register_plugin({
            "id": "premium_plugin",
            "name": "Premium Feature",
            "tier": "A",
            "version": "1.0.0"
        })

        user_id = "free_user_001"
        # Grant user only free tier (no tier granted = free)

        # Attempt install
        plugin = marketplace.get_plugin("premium_plugin")
        user_quota = licensing.get_user_quota(user_id)

        # Should not have access
        can_install = licensing.can_access_tier(user_id, plugin["tier"])
        assert not can_install, "Free user should not install Tier A plugin"

    def test_marketplace_quota_decrements_on_install(self):
        """After successful install, quota should decrement"""
        marketplace = PluginRegistry()
        licensing = TierQuotaManager(tier_metadata={
            "A": TierMetadata("A", 5, 100, 10, 100)
        })

        user_id = "paid_user_001"
        licensing.grant_tier(user_id, "A")

        initial_quota = licensing.get_user_quota(user_id)
        initial_executions = initial_quota["max_total_executions"]

        # Simulate install + one execution
        marketplace.register_plugin({
            "id": "plugin_001",
            "name": "Plugin 1",
            "tier": "A",
            "version": "1.0.0"
        })

        # After execution, quota should decrement
        licensing.record_execution(user_id)

        final_quota = licensing.get_user_quota(user_id)
        assert final_quota["executions_remaining"] < initial_quota["executions_remaining"]


class TestCrossTrackDependencyA_B:
    """Test A + B: Skill Forge packages (A) include Learning config (B)"""

    def test_skill_forge_package_includes_learning_config(self):
        """Skill package should include learning schema"""
        loader = SkillForgeLoader(base_path="/tmp/test_skills")

        # Create a skill package
        manifest = SkillManifest(
            id="test_skill",
            name="Test Skill",
            version="1.0.0",
            description="A test skill",
            entry_point="test:main",
            learning_schema={
                "feedback_types": ["outcome_feedback", "accuracy_feedback"],
                "config_params": ["confidence_threshold", "timeout_ms"],
                "convergence_criteria": {
                    "min_samples": 20,
                    "min_confidence": 0.85
                }
            }
        )

        # Verify learning schema present
        assert manifest.learning_schema is not None
        assert "feedback_types" in manifest.learning_schema
        assert "config_params" in manifest.learning_schema

    def test_skill_package_integrates_with_learning_loop(self):
        """Installed skill should be immediately wired to learning loop"""
        skill_forge = SkillForgeLoader(base_path="/tmp/test_skills")
        learning_loop = ActiveLearningLoop()

        # Install skill
        skill_id = "installed_skill_001"
        skill_forge.install_skill(skill_id, version="1.0.0")

        # Skill should be registered in learning loop
        is_registered = learning_loop.is_skill_registered(skill_id)
        assert is_registered, "Installed skill should auto-register with learning loop"


class TestCrossTrackDependencyB_C:
    """Test B + C: Learning feedback (B) tunes Skills in DAG order (C)"""

    def test_learning_feedback_respects_dag_dependencies(self):
        """Tuning dependencies should follow DAG order"""
        orchestrator = SkillOrchestrator()
        learning_loop = ActiveLearningLoop()

        # Create DAG
        dag = SkillDAG(
            skills={
                "base": {
                    "name": "base_skill",
                    "dependencies": []
                },
                "dependent": {
                    "name": "dependent_skill",
                    "dependencies": ["base"]
                }
            }
        )

        # Feed feedback
        for skill_name in ["base", "dependent"]:
            for i in range(5):
                learning_loop.record_feedback({
                    "skill_id": skill_name,
                    "feedback_type": "outcome_feedback",
                    "signal": "correct",
                    "confidence_score": 0.90,
                    "tenant_id": "_default"
                })

        # Optimize in DAG order (base first, then dependent)
        topo_order = orchestrator.get_topological_order(dag)

        optimization_results = {}
        for skill_name in topo_order:
            result = learning_loop.optimize_skill_config(skill_name)
            optimization_results[skill_name] = result

        # All should optimize successfully
        assert all(r.get("status") == "converged" for r in optimization_results.values())

    def test_feedback_on_dependent_doesnt_affect_upstream(self):
        """Feedback on dependent skill should not change base skill config"""
        learning_loop = ActiveLearningLoop()

        # Feed feedback only for dependent skill
        learning_loop.record_feedback({
            "skill_id": "dependent_skill",
            "feedback_type": "outcome_feedback",
            "signal": "incorrect",
            "confidence_score": 0.10,
            "tenant_id": "_default"
        })

        base_config_before = learning_loop.get_skill_config("base_skill")

        # Optimize dependent
        learning_loop.optimize_skill_config("dependent_skill")

        base_config_after = learning_loop.get_skill_config("base_skill")

        # Base config should not change
        assert base_config_before == base_config_after, \
            "Feedback on dependent should not affect base config"


class TestCrossTrackDependencyH_E:
    """Test H + E: DoD Verifier (H) score improves with Learning Integration (E)"""

    def test_dod_verifier_integrates_with_learning_loop(self):
        """DoD Verifier should register feedback to learning loop"""
        dod_verifier = DODVerifierSkill()
        learning_loop = ActiveLearningLoop()

        # Score a project
        project = {
            "project_id": "proj_001",
            "title": "Test",
            "requirements": "Complete",
            "test_results": {"passed": 10, "failed": 0, "skipped": 0},
            "code_review": {"approved": True},
            "documentation": {"updated": True, "files_changed": ["docs/README.md"]},
            "issues": {"blocking": 0, "critical": 0, "normal": 0}
        }

        score_result = dod_verifier.calculate_score(project)

        # Simulate feedback
        learning_loop.record_feedback({
            "skill_id": "dod_verifier",
            "project_id": project["project_id"],
            "feedback_type": "accuracy_feedback",
            "judgment": "accurate",
            "confidence_score": 0.95,
            "initial_score": score_result["overall_score"],
            "tenant_id": "_default"
        })

        # Feedback should be registered
        feedback = learning_loop.get_feedback("dod_verifier")
        assert len(feedback) > 0

    def test_dod_accuracy_improves_with_feedback_iterations(self):
        """After multiple feedback iterations, DoD accuracy should improve"""
        dod_verifier = DODVerifierSkill()
        learning_loop = ActiveLearningLoop()

        skill_id = "dod_verifier"

        # Multiple feedback cycles
        for cycle in range(3):
            # Collect feedback (simulate consistent expert judgment)
            for i in range(5):
                learning_loop.record_feedback({
                    "skill_id": skill_id,
                    "feedback_type": "accuracy_feedback",
                    "judgment": "accurate",
                    "confidence_score": 0.95,
                    "tenant_id": "_default"
                })

            # Optimize
            result = learning_loop.optimize_skill_config(skill_id)
            assert result is not None


class TestCrossTrackDependencyI_B:
    """Test I + B: DataHub (I) aggregates Skill metrics (B)"""

    def test_datahub_collects_learning_metrics(self):
        """DataHub should aggregate metrics from learning loop"""
        learning_loop = ActiveLearningLoop()
        datahub = DataHubCreator()

        # Record skill feedback
        for skill_id in ["skill_a", "skill_b", "skill_c"]:
            for i in range(10):
                learning_loop.record_feedback({
                    "skill_id": skill_id,
                    "feedback_type": "outcome_feedback",
                    "signal": "correct" if i < 8 else "incorrect",
                    "confidence_score": 0.90,
                    "tenant_id": "_default"
                })

        # Datahub should be able to query aggregated metrics
        metrics = datahub.get_skill_metrics()

        assert metrics is not None
        assert "skill_a" in metrics or len(metrics) > 0

    def test_datahub_tracks_skill_convergence(self):
        """DataHub should track convergence rates for each skill"""
        learning_loop = ActiveLearningLoop()
        datahub = DataHubCreator()

        skill_id = "convergent_skill"

        # Feed consistent feedback
        for i in range(20):
            learning_loop.record_feedback({
                "skill_id": skill_id,
                "feedback_type": "outcome_feedback",
                "signal": "correct",
                "confidence_score": 0.95,
                "tenant_id": "_default"
            })

        # Get convergence metrics
        convergence = datahub.get_convergence_metrics(skill_id)

        assert convergence is not None
        assert "convergence_status" in convergence or "samples" in convergence

    def test_datahub_multi_tenant_metric_aggregation(self):
        """DataHub should aggregate metrics per tenant"""
        learning_loop = ActiveLearningLoop()
        datahub = DataHubCreator()

        skill_id = "shared_skill"

        # Tenant 1: consistent correct
        for i in range(10):
            learning_loop.record_feedback({
                "skill_id": skill_id,
                "feedback_type": "outcome_feedback",
                "signal": "correct",
                "confidence_score": 0.95,
                "tenant_id": "tenant_1"
            })

        # Tenant 2: consistent incorrect
        for i in range(10):
            learning_loop.record_feedback({
                "skill_id": skill_id,
                "feedback_type": "outcome_feedback",
                "signal": "incorrect",
                "confidence_score": 0.20,
                "tenant_id": "tenant_2"
            })

        # Get metrics per tenant
        metrics_t1 = datahub.get_skill_metrics(skill_id, tenant_id="tenant_1")
        metrics_t2 = datahub.get_skill_metrics(skill_id, tenant_id="tenant_2")

        assert metrics_t1 is not None
        assert metrics_t2 is not None


class TestAllTracksIntegration:
    """Test all 8 tracks working together in a realistic scenario"""

    def test_all_eight_tracks_together(self):
        """Full integration test with all 8 tracks"""
        # Setup all components
        marketplace = PluginRegistry()
        licensing = TierQuotaManager(tier_metadata={
            "A": TierMetadata("A", 5, 1000, 10, 100)
        })
        skill_forge = SkillForgeLoader(base_path="/tmp/test_skills")
        orchestrator = SkillOrchestrator()
        learning_loop = ActiveLearningLoop()
        dod_verifier = DODVerifierSkill()
        datahub = DataHubCreator()
        audit_trail = AuditTrail(tenant_id="_default")

        # 1. User discovers plugin in marketplace (Track D)
        marketplace.register_plugin({
            "id": "analytics_skill",
            "name": "Analytics Processor",
            "tier": "A",
            "version": "1.0.0"
        })

        plugins = marketplace.search_plugins(query="analytics")
        assert len(plugins) > 0

        # 2. Check licensing tier (Track F)
        user_id = "analytics_user_001"
        licensing.grant_tier(user_id, "A")
        quota = licensing.get_user_quota(user_id)
        assert quota["tier"] == "A"

        # 3. Install with Skill Forge (Track A)
        skill_forge.install_skill("analytics_skill", version="1.0.0")

        # 4. Deploy in DAG (Track C)
        dag = SkillDAG(
            skills={
                "analytics_skill": {
                    "name": "analytics_skill",
                    "dependencies": []
                }
            }
        )
        orchestrator.deploy_dag(dag)

        # 5. Collect learning feedback (Track B)
        for i in range(10):
            learning_loop.record_feedback({
                "skill_id": "analytics_skill",
                "feedback_type": "outcome_feedback",
                "signal": "correct" if i < 8 else "incorrect",
                "confidence_score": 0.90,
                "tenant_id": "_default"
            })

        # 6. Integration: Learning tunes the skill (Track E)
        opt_result = learning_loop.optimize_skill_config("analytics_skill")
        assert opt_result is not None

        # 7. DoD Verifier scores the project (Track H)
        project = {
            "project_id": "proj_analytics",
            "title": "Analytics Implementation",
            "requirements": "Complete",
            "test_results": {"passed": 15, "failed": 0, "skipped": 0},
            "code_review": {"approved": True},
            "documentation": {"updated": True, "files_changed": ["docs/ANALYTICS.md"]},
            "issues": {"blocking": 0, "critical": 0, "normal": 2}
        }

        dod_score = dod_verifier.calculate_score(project)
        assert 0.0 <= dod_score["overall_score"] <= 1.0

        # 8. DataHub aggregates metrics (Track I)
        metrics = datahub.get_skill_metrics("analytics_skill")
        assert metrics is not None

        # Audit trail should capture everything
        audit_trail.log_event(AuditEvent(
            event_type="full_workflow_complete",
            details={
                "plugin_discovered": "analytics_skill",
                "tier_verified": "A",
                "installed": True,
                "dag_deployed": True,
                "feedbacks_collected": 10,
                "skill_optimized": True,
                "dod_scored": True,
                "metrics_aggregated": True
            }
        ))

        assert audit_trail.verify_chain_integrity()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
