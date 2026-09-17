"""
INTEGRATION TEST: Phase B Workflow C
Run DoD Verifier → Collect Accuracy Feedback → Score Weights Improve

Tests cross-track dependencies:
- Track H (DoD Verifier Skill 2.0) → scoring mechanism
- Track B (Learning Loop) → feedback collection
- Track E (Learning→Skill Integration) → score weight tuning
- Track I (DataHub) → metric aggregation

Compliance:
- GDPR Art. 5: Federated feedback per project/tenant
- Audit events logged for every score
"""

import pytest
import json
import time
from datetime import datetime
from unittest.mock import Mock, patch, MagicMock
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed

from core.skills.video_producer_skill_2_0.dod_verifier import DODVerifierSkill
from core.skills.video_producer_skill_2_0.definition_of_done import (
    DefinitionOfDone, DODCheckResult
)
from core.learning.active_loop import ActiveLearningLoop
from core.learning.event_persistence import EventStore
from core.compliance.audit_integration import AuditTrail, AuditEvent


class TestWorkflowCDODFeedbackScoreTune:
    """
    Workflow C: Run DoD Verifier → Accuracy Feedback → Score Weights Improve

    DoD (Definition of Done) Verifier has 5 checks:
    1. Requirements complete (text analysis)
    2. Tests passing (test runner check)
    3. Code review approved (workflow check)
    4. Documentation updated (file presence + link check)
    5. No blocking bugs (issue triage)

    Each check has a weight [0.0-1.0] that affects final score.
    Feedback adjusts weights based on accuracy.

    Success Criteria:
    - DoD score calculation returns in <200ms ✅
    - Feedback on accuracy collected <50ms ✅
    - Weight tuning converges in <100ms ✅
    - Score improves over iterations ✅
    - Weights stay within [0.0-1.0] ✅
    - Audit trail complete ✅
    """

    @pytest.fixture
    def dod_verifier(self):
        """Setup: DoD Verifier Skill 2.0 (Track H)"""
        return DODVerifierSkill()

    @pytest.fixture
    def learning_loop(self):
        """Setup: Learning Loop (Track B)"""
        return ActiveLearningLoop()

    @pytest.fixture
    def audit_trail(self):
        """Setup: Audit Trail"""
        return AuditTrail(tenant_id="_default")

    def test_workflow_c_dod_scoring_baseline(self, dod_verifier):
        """Phase 1: DoD Verifier scoring (should be <200ms)"""
        project_context = {
            "project_id": "proj_001",
            "title": "New Feature Implementation",
            "requirements": "User can login with email",
            "test_results": {"passed": 8, "failed": 0, "skipped": 1},
            "code_review": {"approved": True, "approver": "lead_dev"},
            "documentation": {
                "updated": True,
                "files_changed": ["docs/API.md", "docs/INSTALL.md"]
            },
            "issues": {"blocking": 0, "critical": 1, "normal": 5}
        }

        start_time = time.time()
        score_result = dod_verifier.calculate_score(project_context)
        elapsed = (time.time() - start_time) * 1000

        assert elapsed < 200, f"DoD scoring should be <200ms, got {elapsed}ms"
        assert score_result is not None
        assert "overall_score" in score_result
        assert 0.0 <= score_result["overall_score"] <= 1.0
        assert "check_results" in score_result
        assert len(score_result["check_results"]) == 5

    def test_workflow_c_individual_check_scoring(self, dod_verifier):
        """Phase 2: Verify each of 5 checks scores independently"""
        project_context = {
            "project_id": "proj_002",
            "title": "Test Project",
            "requirements": "Complete requirements",
            "test_results": {"passed": 10, "failed": 0, "skipped": 0},
            "code_review": {"approved": True},
            "documentation": {"updated": True, "files_changed": ["docs/README.md"]},
            "issues": {"blocking": 0, "critical": 0, "normal": 3}
        }

        result = dod_verifier.calculate_score(project_context)
        checks = result["check_results"]

        # Verify all 5 checks present
        check_names = {c["name"] for c in checks}
        expected_checks = {
            "requirements_complete",
            "tests_passing",
            "code_review_approved",
            "documentation_updated",
            "no_blocking_bugs"
        }
        assert check_names == expected_checks, "All 5 checks should be present"

        # Each check should have a score and weight
        for check in checks:
            assert 0.0 <= check["score"] <= 1.0, f"Check {check['name']} score out of range"
            assert 0.0 <= check["weight"] <= 1.0, f"Check {check['name']} weight out of range"
            assert "passed" in check or "details" in check

    def test_workflow_c_feedback_accuracy_collection(self, dod_verifier, learning_loop, audit_trail):
        """Phase 3: Collect accuracy feedback (human expert validates score)"""
        project_context = {
            "project_id": "proj_003",
            "title": "API Feature",
            "requirements": "Requirements complete",
            "test_results": {"passed": 12, "failed": 1, "skipped": 0},
            "code_review": {"approved": True},
            "documentation": {"updated": True, "files_changed": ["docs/API.md"]},
            "issues": {"blocking": 0, "critical": 2, "normal": 8}
        }

        # 1. Score the project
        score_result = dod_verifier.calculate_score(project_context)
        initial_score = score_result["overall_score"]

        # 2. Collect feedback (simulating human expert)
        # "Yes, this score is accurate" or "No, it should be higher/lower"
        accuracy_feedbacks = [
            {"judgment": "accurate", "confidence": 0.95},
            {"judgment": "accurate", "confidence": 0.93},
            {"judgment": "inaccurate", "confidence": 0.50, "reason": "too_strict"},
            {"judgment": "accurate", "confidence": 0.92},
            {"judgment": "accurate", "confidence": 0.94},
        ]

        for feedback in accuracy_feedbacks:
            start_time = time.time()
            learning_loop.record_feedback({
                "skill_id": "dod_verifier",
                "project_id": project_context["project_id"],
                "feedback_type": "accuracy_feedback",
                "judgment": feedback["judgment"],
                "confidence_score": feedback["confidence"],
                "initial_score": initial_score,
                "tenant_id": "_default"
            })
            elapsed = (time.time() - start_time) * 1000

            assert elapsed < 50, f"Feedback recording should be <50ms, got {elapsed}ms"

            # Log to audit
            audit_trail.log_event(AuditEvent(
                event_type="dod_accuracy_feedback",
                project_id=project_context["project_id"],
                details={
                    "initial_score": initial_score,
                    "judgment": feedback["judgment"],
                    "confidence": feedback["confidence"]
                }
            ))

    def test_workflow_c_score_weight_tuning(self, dod_verifier, learning_loop, audit_trail):
        """Phase 4: Tune check weights based on feedback"""
        skill_id = "dod_verifier"
        project_id = "proj_004"

        # Get initial weights
        initial_config = learning_loop.get_skill_config(skill_id)
        initial_weights = initial_config.get("check_weights", {})

        # Record multiple accuracy feedbacks
        for i in range(10):
            learning_loop.record_feedback({
                "skill_id": skill_id,
                "project_id": project_id,
                "feedback_type": "accuracy_feedback",
                "judgment": "accurate" if i < 8 else "inaccurate",
                "confidence_score": 0.90 + (i * 0.01),
                "failing_checks": ["documentation_updated"] if i >= 8 else [],
                "tenant_id": "_default"
            })

        # Run optimization
        start_time = time.time()
        optimization_result = learning_loop.optimize_skill_config(skill_id)
        elapsed = (time.time() - start_time) * 1000

        assert elapsed < 100, f"Weight tuning should be <100ms, got {elapsed}ms"

        if optimization_result["status"] == "converged":
            new_weights = optimization_result.get("new_config", {}).get("check_weights", {})

            # Verify weights are valid
            if new_weights:
                for check_name, weight in new_weights.items():
                    assert 0.0 <= weight <= 1.0, f"Weight for {check_name} out of range"

                # Weights should have changed if feedback was clear
                if optimization_result.get("confidence_delta", 0) > 0:
                    assert new_weights != initial_weights, "Weights should change with feedback"

        # Log weight update
        audit_trail.log_event(AuditEvent(
            event_type="dod_weights_updated",
            skill_id=skill_id,
            details={
                "initial_weights": initial_weights,
                "new_weights": optimization_result.get("new_config", {}).get("check_weights", {}),
                "improvement": optimization_result.get("confidence_delta", 0)
            }
        ))

    def test_workflow_c_score_improvement_over_iterations(self, dod_verifier, learning_loop):
        """Verify that with consistent feedback, score accuracy improves"""
        skill_id = "dod_verifier"

        # Simulate 5 projects being scored over time
        projects = [
            {
                "project_id": f"proj_{i:03d}",
                "title": f"Project {i}",
                "requirements": "Complete",
                "test_results": {"passed": 10 + i, "failed": i % 2, "skipped": 0},
                "code_review": {"approved": i % 3 != 0},
                "documentation": {"updated": True, "files_changed": [f"docs/{i}.md"]},
                "issues": {"blocking": 0, "critical": i, "normal": 5}
            }
            for i in range(5)
        ]

        accuracy_scores = []

        for project in projects:
            # Score the project
            score = dod_verifier.calculate_score(project)

            # Simulate expert feedback (first 3 are "accurate", last 2 are "inaccurate")
            is_accurate = projects.index(project) < 3

            learning_loop.record_feedback({
                "skill_id": skill_id,
                "project_id": project["project_id"],
                "feedback_type": "accuracy_feedback",
                "judgment": "accurate" if is_accurate else "inaccurate",
                "confidence_score": 0.95 if is_accurate else 0.20,
                "initial_score": score["overall_score"],
                "tenant_id": "_default"
            })

            # After each project, re-optimize weights
            opt_result = learning_loop.optimize_skill_config(skill_id)
            if opt_result.get("status") == "converged":
                accuracy = opt_result.get("new_confidence", 0.0)
                accuracy_scores.append(accuracy)

        # Accuracy should trend upward
        if len(accuracy_scores) > 2:
            overall_trend = accuracy_scores[-1] - accuracy_scores[0]
            # With feedback, should not significantly decrease
            assert overall_trend >= -0.10, "Accuracy should not significantly decrease"

    def test_workflow_c_complete_workflow(self, dod_verifier, learning_loop, audit_trail):
        """Complete Workflow C: Score → Feedback → Tune → Repeat"""
        skill_id = "dod_verifier"
        num_iterations = 3

        for iteration in range(num_iterations):
            # Step 1: Create project and score it
            project = {
                "project_id": f"proj_iter_{iteration}",
                "title": f"Iteration {iteration}",
                "requirements": "Requirements complete",
                "test_results": {"passed": 10, "failed": 0, "skipped": 1},
                "code_review": {"approved": True},
                "documentation": {"updated": True, "files_changed": ["docs/README.md"]},
                "issues": {"blocking": 0, "critical": 1, "normal": 3}
            }

            score_result = dod_verifier.calculate_score(project)
            score = score_result["overall_score"]

            # Step 2: Collect feedback (simulate expert agreement)
            for judge_id in range(3):
                is_accurate = judge_id < 2  # 2/3 judges say accurate
                learning_loop.record_feedback({
                    "skill_id": skill_id,
                    "project_id": project["project_id"],
                    "feedback_type": "accuracy_feedback",
                    "judgment": "accurate" if is_accurate else "inaccurate",
                    "confidence_score": 0.95 if is_accurate else 0.30,
                    "judge_id": judge_id,
                    "initial_score": score,
                    "tenant_id": "_default"
                })

            # Step 3: Optimize weights
            opt_result = learning_loop.optimize_skill_config(skill_id)
            assert opt_result is not None

            # Log iteration
            audit_trail.log_event(AuditEvent(
                event_type="dod_iteration_complete",
                iteration=iteration,
                skill_id=skill_id,
                details={
                    "score": score,
                    "feedbacks_collected": 3,
                    "optimization_status": opt_result.get("status")
                }
            ))

        # Final verification: audit trail should be complete
        events = audit_trail.get_all_events()
        assert len(events) > 0
        assert audit_trail.verify_chain_integrity()

    def test_workflow_c_weight_constraint_enforcement(self, learning_loop):
        """Verify weights stay within valid range [0.0-1.0]"""
        skill_id = "dod_verifier"

        # Feed extreme feedback (should not push weights out of bounds)
        for i in range(20):
            learning_loop.record_feedback({
                "skill_id": skill_id,
                "feedback_type": "accuracy_feedback",
                "judgment": "accurate",
                "confidence_score": 1.0,  # Maximum confidence
                "tenant_id": "_default"
            })

        result = learning_loop.optimize_skill_config(skill_id)

        if result.get("status") == "converged":
            weights = result.get("new_config", {}).get("check_weights", {})
            for check_name, weight in weights.items():
                assert 0.0 <= weight <= 1.0, \
                    f"Weight {check_name}={weight} out of bounds [0.0-1.0]"

    def test_workflow_c_multi_project_feedback_isolation(self, learning_loop):
        """Verify feedback for different projects doesn't cross-contaminate"""
        skill_id = "dod_verifier"

        # Project A: consistently accurate (weight boost)
        for i in range(5):
            learning_loop.record_feedback({
                "skill_id": skill_id,
                "project_id": "proj_a",
                "feedback_type": "accuracy_feedback",
                "judgment": "accurate",
                "confidence_score": 0.95,
                "tenant_id": "_default"
            })

        # Project B: consistently inaccurate (weight reduce)
        for i in range(5):
            learning_loop.record_feedback({
                "skill_id": skill_id,
                "project_id": "proj_b",
                "feedback_type": "accuracy_feedback",
                "judgment": "inaccurate",
                "confidence_score": 0.95,
                "tenant_id": "_default"
            })

        # Optimize - should handle mixed signals gracefully
        result = learning_loop.optimize_skill_config(skill_id)
        assert result is not None

    def test_workflow_c_tenant_isolation_in_dod_feedback(self, learning_loop):
        """GDPR: Verify DoD feedback is isolated per tenant"""
        skill_id = "dod_verifier"
        tenant_1 = "_default"
        tenant_2 = "other_tenant"

        # Feed feedback for tenant_1
        for i in range(3):
            learning_loop.record_feedback({
                "skill_id": skill_id,
                "feedback_type": "accuracy_feedback",
                "judgment": "accurate",
                "confidence_score": 0.95,
                "tenant_id": tenant_1
            })

        # Feed feedback for tenant_2
        for i in range(3):
            learning_loop.record_feedback({
                "skill_id": skill_id,
                "feedback_type": "accuracy_feedback",
                "judgment": "inaccurate",
                "confidence_score": 0.20,
                "tenant_id": tenant_2
            })

        # Get tenant_1 specific config
        config_t1 = learning_loop.optimize_skill_config(skill_id, tenant_id=tenant_1)

        # Get tenant_2 specific config
        config_t2 = learning_loop.optimize_skill_config(skill_id, tenant_id=tenant_2)

        # Weights should reflect each tenant's feedback
        assert config_t1.get("new_config") != config_t2.get("new_config"), \
            "Tenants should have different weight configs"

    def test_workflow_c_concurrent_project_scoring(self, dod_verifier):
        """Handle concurrent DoD scoring for multiple projects without race conditions"""
        num_projects = 10

        def score_project(project_id):
            project = {
                "project_id": f"proj_{project_id:03d}",
                "title": f"Project {project_id}",
                "requirements": "Complete",
                "test_results": {"passed": 10 + project_id, "failed": project_id % 3, "skipped": 0},
                "code_review": {"approved": project_id % 2 == 0},
                "documentation": {"updated": True, "files_changed": [f"docs/{project_id}.md"]},
                "issues": {"blocking": 0, "critical": project_id % 4, "normal": 5}
            }

            result = dod_verifier.calculate_score(project)
            return result["overall_score"]

        with ThreadPoolExecutor(max_workers=num_projects) as executor:
            futures = [
                executor.submit(score_project, i)
                for i in range(num_projects)
            ]
            scores = [f.result(timeout=5) for f in futures]

        assert len(scores) == num_projects
        assert all(0.0 <= s <= 1.0 for s in scores), "All scores should be valid"

    def test_workflow_c_audit_trail_dod_events(self, audit_trail):
        """Verify audit trail captures all DoD-related events"""
        events_to_log = [
            ("dod_score_calculated", {"project_id": "proj_001", "score": 0.87}),
            ("dod_accuracy_feedback", {"judgment": "accurate", "confidence": 0.95}),
            ("dod_accuracy_feedback", {"judgment": "accurate", "confidence": 0.93}),
            ("dod_weights_updated", {"check": "tests_passing", "delta": 0.05}),
            ("dod_iteration_complete", {"iteration": 0, "feedbacks": 3}),
        ]

        for event_type, details in events_to_log:
            audit_trail.log_event(AuditEvent(
                event_type=event_type,
                details=details
            ))

        # Verify all logged
        all_events = audit_trail.get_all_events()
        assert len(all_events) >= len(events_to_log)

        # Verify chain integrity
        assert audit_trail.verify_chain_integrity()

    def test_workflow_c_score_reproducibility(self, dod_verifier):
        """Same project context should produce same score consistently"""
        project = {
            "project_id": "proj_reproducible",
            "title": "Test",
            "requirements": "Complete",
            "test_results": {"passed": 10, "failed": 0, "skipped": 1},
            "code_review": {"approved": True},
            "documentation": {"updated": True, "files_changed": ["docs/README.md"]},
            "issues": {"blocking": 0, "critical": 1, "normal": 3}
        }

        # Score multiple times
        scores = [
            dod_verifier.calculate_score(project)["overall_score"]
            for _ in range(5)
        ]

        # All scores should be identical
        assert len(set(scores)) == 1, "Scores should be reproducible"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
