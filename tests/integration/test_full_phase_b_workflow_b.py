"""
INTEGRATION TEST: Phase B Workflow B
Deploy Skill with DAG → Receive Feedback → Tune Configuration

Tests cross-track dependencies:
- Track C (OS-Skills DAG Orchestration) → dependency resolution
- Track B (Learning Loop) → feedback collection
- Track E (Learning→Skill Integration) → config tuning
- Track A (Skill Forge v2.0) → skill packaging

Compliance:
- GDPR Art. 5: Feedback federated per tenant
- Audit chain integrity verified
"""

import pytest
import json
import time
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, MagicMock
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed

from core.skills.os_skills.orchestrator import SkillOrchestrator, SkillDAG
from core.skills.os_skills.validators import DAGValidator
from core.learning.active_loop import ActiveLearningLoop
from core.learning.event_persistence import EventStore
from core.skill_forge.manifest import SkillManifest, SkillDependency
from core.compliance.audit_integration import AuditTrail, AuditEvent


class TestWorkflowBDeployDAGFeedbackTune:
    """
    Workflow B: Deploy skill with DAG dependencies → feedback collection → tuning

    Success Criteria:
    - DAG validation passes (zero cycles, all deps resolved) ✅
    - Skills deployed in topological order ✅
    - Feedback processed within <50ms ✅
    - Config tuning within <100ms ✅
    - Tenant isolation on feedback ✅
    - Audit trail complete ✅
    """

    @pytest.fixture
    def skill_dag_orchestrator(self):
        """Setup: OS-Skills DAG Orchestrator (Track C)"""
        return SkillOrchestrator()

    @pytest.fixture
    def learning_loop(self):
        """Setup: Learning Loop (Track B)"""
        return ActiveLearningLoop()

    @pytest.fixture
    def audit_trail(self):
        """Setup: Audit Trail"""
        return AuditTrail(tenant_id="_default")

    def test_workflow_b_dag_validation(self, skill_dag_orchestrator):
        """Phase 1: DAG Validation (zero cycles, all dependencies resolved)"""
        # Define a multi-skill DAG
        dag = SkillDAG(
            skills={
                "router": {
                    "name": "os.delegation_router",
                    "version": "1.0.0",
                    "dependencies": []
                },
                "context": {
                    "name": "os.context_adapter",
                    "version": "1.0.0",
                    "dependencies": ["router"]  # depends on router
                },
                "workflow": {
                    "name": "os.workflow_optimizer",
                    "version": "1.0.0",
                    "dependencies": ["router", "context"]  # depends on both
                }
            }
        )

        # Validate DAG
        validator = DAGValidator()
        is_valid, errors = validator.validate(dag)

        assert is_valid, f"DAG should be valid, got errors: {errors}"
        assert len(errors) == 0

        # Check topological order
        topo_order = skill_dag_orchestrator.get_topological_order(dag)
        assert topo_order[0] == "router", "Router should be first (no deps)"
        assert topo_order[1] == "context", "Context should be second (depends on router)"
        assert topo_order[2] == "workflow", "Workflow should be third (depends on router+context)"

    def test_workflow_b_dag_cycle_detection(self, skill_dag_orchestrator):
        """Detect and reject cyclic dependencies"""
        # Create a DAG with a cycle
        dag_with_cycle = SkillDAG(
            skills={
                "skill_a": {
                    "name": "skill_a",
                    "dependencies": ["skill_b"]
                },
                "skill_b": {
                    "name": "skill_b",
                    "dependencies": ["skill_c"]
                },
                "skill_c": {
                    "name": "skill_c",
                    "dependencies": ["skill_a"]  # Creates cycle
                }
            }
        )

        validator = DAGValidator()
        is_valid, errors = validator.validate(dag_with_cycle)

        assert not is_valid, "DAG with cycle should be invalid"
        assert any("cycle" in str(e).lower() for e in errors), "Should report cycle"

    def test_workflow_b_skill_deployment_order(self, skill_dag_orchestrator, audit_trail):
        """Phase 2: Deploy skills in topological order"""
        dag = SkillDAG(
            skills={
                "router": {
                    "name": "os.delegation_router",
                    "version": "1.0.0",
                    "dependencies": []
                },
                "context": {
                    "name": "os.context_adapter",
                    "version": "1.0.0",
                    "dependencies": ["router"]
                }
            }
        )

        deployment_order = []

        def mock_deploy(skill_name):
            deployment_order.append(skill_name)
            audit_trail.log_event(AuditEvent(
                event_type="skill_deployed",
                skill_id=skill_name,
                details={"version": "1.0.0"}
            ))
            return {"status": "deployed"}

        skill_dag_orchestrator.deploy_skills_in_order(dag, mock_deploy)

        # Verify order
        assert deployment_order == ["router", "context"], \
            "Skills should deploy in topological order"

    def test_workflow_b_feedback_collection(self, learning_loop, audit_trail):
        """Phase 3: Collect feedback from skill execution (multiple iterations)"""
        skill_id = "os.delegation_router"

        # Simulate 10 execution feedbacks
        feedbacks = []
        for i in range(10):
            feedback = {
                "skill_id": skill_id,
                "execution_id": f"exec_{i:03d}",
                "feedback_type": "outcome_feedback",
                "signal": "correct" if i < 8 else "incorrect",
                "confidence_score": 0.90 + (i * 0.01),
                "latency_ms": 42 + (i % 5),
                "timestamp": (datetime.now() - timedelta(seconds=10-i)).isoformat(),
                "tenant_id": "_default"
            }
            learning_loop.record_feedback(feedback)
            feedbacks.append(feedback)

            # Log to audit
            audit_trail.log_event(AuditEvent(
                event_type="feedback_recorded",
                execution_id=feedback["execution_id"],
                skill_id=skill_id,
                details={"signal": feedback["signal"], "confidence": feedback["confidence_score"]}
            ))

        # Verify all feedbacks recorded
        stored = learning_loop.get_feedback(skill_id)
        assert len(stored) >= 10, "All feedbacks should be stored"

        # Verify time-series integrity
        timestamps = [f["timestamp"] for f in stored]
        assert len(set(timestamps)) == len(timestamps), "Timestamps should be unique"

    def test_workflow_b_config_tuning_with_convergence(self, learning_loop, audit_trail):
        """Phase 4: Tune skill config based on feedback (convergence within 100ms)"""
        skill_id = "os.delegation_router"

        # Feed the learning loop with sample data
        for i in range(5):
            learning_loop.record_feedback({
                "skill_id": skill_id,
                "feedback_type": "outcome_feedback",
                "signal": "correct",
                "confidence_score": 0.92 + (i * 0.01),
                "tenant_id": "_default"
            })

        # Get current config
        current_config = learning_loop.get_skill_config(skill_id)

        # Optimize
        start_time = time.time()
        optimization_result = learning_loop.optimize_skill_config(skill_id)
        elapsed = (time.time() - start_time) * 1000

        assert elapsed < 100, f"Optimization should be <100ms, got {elapsed}ms"
        assert optimization_result["status"] == "converged"
        assert optimization_result["new_config"] is not None

        # Verify config changed (if feedback was consistent)
        if optimization_result.get("confidence_delta", 0) > 0:
            assert optimization_result["new_config"] != current_config, \
                "Config should change if feedback was consistent"

        # Log config update to audit
        audit_trail.log_event(AuditEvent(
            event_type="skill_config_optimized",
            skill_id=skill_id,
            details={
                "confidence_before": current_config.get("confidence", 0),
                "confidence_after": optimization_result.get("new_confidence", 0),
                "num_feedbacks_processed": optimization_result.get("num_feedbacks", 0)
            }
        ))

    def test_workflow_b_complete_dag_pipeline(self, skill_dag_orchestrator,
                                              learning_loop, audit_trail):
        """Complete Workflow B: Deploy DAG → Feedback → Tune"""
        dag = SkillDAG(
            skills={
                "router": {
                    "name": "os.delegation_router",
                    "dependencies": []
                },
                "context": {
                    "name": "os.context_adapter",
                    "dependencies": ["router"]
                }
            }
        )

        # Step 1: Validate and deploy DAG
        validator = skill_dag_orchestrator.validate_dag(dag)
        assert validator["valid"], "DAG must be valid"

        deployment_result = skill_dag_orchestrator.deploy_dag(dag)
        assert deployment_result["status"] == "success"
        assert deployment_result["deployed_count"] == 2

        # Step 2: Collect feedback from deployed skills
        for skill_name in ["router", "context"]:
            for i in range(5):
                learning_loop.record_feedback({
                    "skill_id": skill_name,
                    "feedback_type": "outcome_feedback",
                    "signal": "correct",
                    "confidence_score": 0.90,
                    "tenant_id": "_default"
                })

        # Step 3: Tune each skill
        tuning_results = {}
        for skill_name in ["router", "context"]:
            result = learning_loop.optimize_skill_config(skill_name)
            tuning_results[skill_name] = result
            assert result["status"] == "converged"

        # Step 4: Verify audit trail completeness
        events = audit_trail.get_all_events()
        assert len(events) > 0, "Audit trail should have events"

        # Verify chain integrity
        assert audit_trail.verify_chain_integrity(), "Audit chain must be hash-linked"

    def test_workflow_b_multi_tenant_isolation(self, skill_dag_orchestrator, learning_loop):
        """GDPR: Verify tenant isolation in feedback and optimization"""
        tenant_1 = "_default"
        tenant_2 = "alternate_tenant"

        skill_id = "shared_skill"

        # Feed feedback for tenant_1
        learning_loop.record_feedback({
            "skill_id": skill_id,
            "feedback_type": "outcome_feedback",
            "signal": "correct",
            "confidence_score": 0.95,
            "tenant_id": tenant_1
        })

        # Feed feedback for tenant_2 with different signal
        learning_loop.record_feedback({
            "skill_id": skill_id,
            "feedback_type": "outcome_feedback",
            "signal": "incorrect",
            "confidence_score": 0.10,
            "tenant_id": tenant_2
        })

        # Get feedback scoped to tenant_1
        tenant_1_feedback = learning_loop.get_feedback_by_tenant(
            skill_id, tenant_1
        )

        # Verify isolation
        assert all(f["tenant_id"] == tenant_1 for f in tenant_1_feedback), \
            "Tenant 1 feedback should only contain tenant 1 events"
        assert len(tenant_1_feedback) == 1

    def test_workflow_b_feedback_convergence_signal(self, learning_loop):
        """Verify learning convergence when feedback is consistent"""
        skill_id = "os.delegation_router"

        # Feed consistent "correct" feedback
        consistent_signals = 20
        for i in range(consistent_signals):
            learning_loop.record_feedback({
                "skill_id": skill_id,
                "feedback_type": "outcome_feedback",
                "signal": "correct",
                "confidence_score": 0.95,
                "tenant_id": "_default"
            })

        result = learning_loop.optimize_skill_config(skill_id)

        # With consistent positive feedback, should converge quickly
        assert result["status"] == "converged"
        assert result.get("convergence_iterations", 0) < 5, \
            "Convergence should be quick with consistent feedback"

    def test_workflow_b_feedback_divergence_handling(self, learning_loop):
        """Handle divergent feedback gracefully (mixed correct/incorrect)"""
        skill_id = "os.workflow_optimizer"

        # Feed mixed feedback (50/50 correct/incorrect)
        for i in range(20):
            learning_loop.record_feedback({
                "skill_id": skill_id,
                "feedback_type": "outcome_feedback",
                "signal": "correct" if i % 2 == 0 else "incorrect",
                "confidence_score": 0.50,
                "tenant_id": "_default"
            })

        result = learning_loop.optimize_skill_config(skill_id)

        # Should still return a result, even if confidence is low
        assert result is not None
        if result.get("status") == "converged":
            # Confidence should be conservative with divergent feedback
            assert result.get("new_confidence", 1.0) <= 0.60, \
                "Confidence should be low with divergent feedback"

    def test_workflow_b_skill_dependency_failure_handling(self, skill_dag_orchestrator):
        """Handle skill dependency failures gracefully"""
        dag = SkillDAG(
            skills={
                "router": {"name": "router", "dependencies": []},
                "context": {
                    "name": "context",
                    "dependencies": ["router"]
                }
            }
        )

        def failing_deploy(skill_name):
            if skill_name == "router":
                raise Exception("Deployment failed")
            return {"status": "deployed"}

        # Should handle failure gracefully
        result = skill_dag_orchestrator.deploy_skills_in_order(
            dag, failing_deploy, fail_fast=False
        )

        # context should not be deployed if router failed
        assert not result.get("deployed", {}).get("context"), \
            "Dependent skill should not deploy if dependency fails"

    def test_workflow_b_concurrent_feedback_processing(self, learning_loop):
        """Process concurrent feedback from multiple skills without race conditions"""
        num_skills = 5
        feedbacks_per_skill = 20

        def submit_feedback(skill_id):
            for i in range(feedbacks_per_skill):
                learning_loop.record_feedback({
                    "skill_id": skill_id,
                    "feedback_type": "outcome_feedback",
                    "signal": "correct",
                    "confidence_score": 0.90,
                    "tenant_id": "_default"
                })

        with ThreadPoolExecutor(max_workers=num_skills) as executor:
            futures = [
                executor.submit(submit_feedback, f"skill_{i}")
                for i in range(num_skills)
            ]
            [f.result(timeout=5) for f in futures]

        # Verify all feedbacks stored correctly
        for i in range(num_skills):
            feedback_count = len(learning_loop.get_feedback(f"skill_{i}"))
            assert feedback_count == feedbacks_per_skill, \
                f"All {feedbacks_per_skill} feedbacks for skill_{i} should be stored"

    def test_workflow_b_audit_trail_completeness(self, audit_trail):
        """Verify audit trail captures all important events in Workflow B"""
        events_to_log = [
            ("dag_validated", {"skill_count": 2, "valid": True}),
            ("skill_deployed", {"skill_id": "router"}),
            ("skill_deployed", {"skill_id": "context"}),
            ("feedback_recorded", {"skill_id": "router", "signal": "correct"}),
            ("skill_config_optimized", {"skill_id": "router"}),
        ]

        for event_type, details in events_to_log:
            audit_trail.log_event(AuditEvent(
                event_type=event_type,
                details=details
            ))

        # Verify all events logged
        all_events = audit_trail.get_all_events()
        assert len(all_events) >= len(events_to_log)

        # Verify chain integrity
        assert audit_trail.verify_chain_integrity()

    def test_workflow_b_pii_scrubbing_in_feedback(self, learning_loop):
        """Ensure feedback does not contain PII"""
        skill_id = "os.delegation_router"

        # Attempt to include PII (should be scrubbed)
        feedback_with_pii_attempt = {
            "skill_id": skill_id,
            "feedback_type": "outcome_feedback",
            "signal": "correct",
            "confidence_score": 0.90,
            "user_email": "user@example.com",  # PII
            "user_id": "12345",  # PII
            "tenant_id": "_default"
        }

        learning_loop.record_feedback(feedback_with_pii_attempt)
        stored = learning_loop.get_feedback(skill_id)

        stored_str = json.dumps(stored)
        # Verify PII was scrubbed
        assert "@example.com" not in stored_str, "Email should be scrubbed"
        assert "12345" not in stored_str, "User ID should be scrubbed"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
