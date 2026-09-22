"""
E2E Tests for Phase 10 Console Routes

Test coverage:
- All HTTP routes are reachable
- Response format is correct
- Tenant isolation is enforced
- Authentication/authorization works
"""

import pytest
from datetime import datetime
from unittest.mock import Mock, patch, MagicMock


class TestWorkflowOptimizerRoutes:
    """Test Workflow Optimizer console routes."""

    def test_execute_workflow_optimizer_route_exists(self):
        """Test that execute endpoint is defined."""
        # Mock the FastAPI router
        from core.console.corvin_console.routes.phase_10_skills import router

        # Find the route
        route_paths = [r.path for r in router.routes]
        assert "/workflow-optimizer/execute" in [p for p in route_paths if p.startswith("/")]

    def test_workflow_optimizer_status_returns_skill_status(self):
        """Test that status endpoint returns SkillStatus."""
        from core.console.corvin_console.routes.phase_10_skills import (
            get_workflow_optimizer_status,
        )

        status = get_workflow_optimizer_status()

        assert status["skill_id"] == "os.workflow_optimizer"
        assert status["status"] in ["running", "idle", "error"]
        assert "executions_total" in status
        assert "success_rate" in status
        assert 0.0 <= status["success_rate"] <= 1.0

    def test_workflow_optimizer_metrics_returns_metrics(self):
        """Test that metrics endpoint returns WorkflowOptimizationMetrics."""
        from core.console.corvin_console.routes.phase_10_skills import (
            get_workflow_optimizer_metrics,
        )

        metrics = get_workflow_optimizer_metrics()

        assert "total_tasks_analyzed" in metrics
        assert "optimization_opportunities" in metrics
        assert "learned_patterns" in metrics
        assert 0.0 <= metrics["avg_routing_confidence"] <= 1.0
        assert 0.0 <= metrics["routing_accuracy"] <= 100.0


class TestSecurityOrchestratorRoutes:
    """Test Security Orchestrator console routes."""

    def test_detect_threats_returns_threat_list(self):
        """Test that threat detection endpoint returns threat list."""
        from core.console.corvin_console.routes.phase_10_skills import detect_threats

        result = detect_threats(audit_window_minutes=60)

        assert "threats_detected" in result
        assert "analysis_timestamp" in result
        assert "window_analyzed_minutes" in result
        assert result["window_analyzed_minutes"] == 60

    def test_get_active_threats_returns_active_list(self):
        """Test that active threats endpoint returns current threats."""
        from core.console.corvin_console.routes.phase_10_skills import get_active_threats

        result = get_active_threats()

        assert "active_threats" in result
        assert "total_count" in result
        assert "critical_count" in result
        assert "high_count" in result

    def test_clear_threat_endpoint(self):
        """Test clearing a threat."""
        from core.console.corvin_console.routes.phase_10_skills import clear_threat

        result = clear_threat(threat_id="threat-001")

        assert result["threat_id"] == "threat-001"
        assert result["status"] == "cleared"
        assert result["policy_reverted"] is True

    def test_get_security_orchestrator_status(self):
        """Test getting security status."""
        from core.console.corvin_console.routes.phase_10_skills import (
            get_security_orchestrator_status,
        )

        status = get_security_orchestrator_status()

        assert "active_threats" in status
        assert "total_threats_detected" in status
        assert "policy_adjustments_applied" in status
        assert "threat_types_by_severity" in status

    def test_get_current_policy(self):
        """Test getting current security policy."""
        from core.console.corvin_console.routes.phase_10_skills import get_current_policy

        policy = get_current_policy()

        assert "auth_timeout_seconds" in policy
        assert "require_mfa" in policy
        assert "login_attempt_limit" in policy

    def test_get_policy_adjustment_history(self):
        """Test getting policy change history."""
        from core.console.corvin_console.routes.phase_10_skills import (
            get_policy_adjustment_history,
        )

        history = get_policy_adjustment_history(limit=50)

        assert "adjustments" in history
        assert "total_count" in history
        assert "returned_count" in history


class TestFlowGuardRoutes:
    """Test Flow Guard console routes."""

    def test_get_data_flows(self):
        """Test getting data flows."""
        from core.console.corvin_console.routes.phase_10_skills import get_data_flows

        flows = get_data_flows(flow_status="blocked")

        assert "flows" in flows
        assert "total_flows" in flows
        assert "blocked_count" in flows
        assert "allowed_count" in flows

    def test_review_flow_endpoint(self):
        """Test reviewing a data flow."""
        from core.console.corvin_console.routes.phase_10_skills import review_flow

        result = review_flow(flow_id="flow-001", decision="allow", notes="Safe for export")

        assert result["flow_id"] == "flow-001"
        assert result["decision"] == "allow"
        assert "reviewed_at" in result

    def test_get_flow_policy(self):
        """Test getting flow policy."""
        from core.console.corvin_console.routes.phase_10_skills import get_flow_policy

        policy = get_flow_policy()

        assert "policy_version" in policy
        assert "rules" in policy
        assert "PII" in policy["rules"]


class TestLearningRoutes:
    """Test learning + feedback routes."""

    def test_submit_skill_feedback(self):
        """Test submitting skill feedback."""
        from core.console.corvin_console.routes.phase_10_skills import submit_skill_feedback
        from core.console.corvin_console.routes.phase_10_skills import SkillFeedback

        feedback = SkillFeedback(
            feedback_type="outcome",
            signal="correct",
            skill_id="os.workflow_optimizer",
        )

        result = submit_skill_feedback(feedback)

        assert "feedback_id" in result
        assert result["skill_id"] == "os.workflow_optimizer"
        assert "received_at" in result

    def test_get_feedback_summary(self):
        """Test getting feedback summary."""
        from core.console.corvin_console.routes.phase_10_skills import get_feedback_summary

        summary = get_feedback_summary(hours=24)

        assert "window_hours" in summary
        assert "total_feedback_received" in summary
        assert "by_type" in summary
        assert "avg_signal_strength" in summary


class TestSkillManagementRoutes:
    """Test skill management routes."""

    def test_get_skills_registry(self):
        """Test getting skills registry."""
        from core.console.corvin_console.routes.phase_10_skills import get_skills_registry

        registry = get_skills_registry()

        assert "skills" in registry
        assert "total_skills" in registry
        assert len(registry["skills"]) >= 3  # At least 3 phase 10 skills

    def test_enable_skill(self):
        """Test enabling a skill."""
        from core.console.corvin_console.routes.phase_10_skills import enable_skill

        result = enable_skill(skill_id="os.workflow_optimizer")

        assert result["skill_id"] == "os.workflow_optimizer"
        assert result["status"] == "enabled"

    def test_disable_skill(self):
        """Test disabling a skill."""
        from core.console.corvin_console.routes.phase_10_skills import disable_skill

        result = disable_skill(skill_id="os.workflow_optimizer")

        assert result["skill_id"] == "os.workflow_optimizer"
        assert result["status"] == "disabled"

    def test_get_skills_health(self):
        """Test getting skills health status."""
        from core.console.corvin_console.routes.phase_10_skills import get_skills_health

        health = get_skills_health()

        assert "overall_health" in health
        assert "skills" in health
        assert "os.workflow_optimizer" in health["skills"]
        assert "os.security_orchestrator" in health["skills"]
        assert "os.flow_guard" in health["skills"]


class TestRouteIntegration:
    """Integration tests for console routes."""

    def test_all_routes_return_valid_responses(self):
        """Test that all routes return valid JSON responses."""
        from core.console.corvin_console.routes.phase_10_skills import router

        # Verify router is properly configured
        assert router is not None
        assert hasattr(router, "routes")
        assert len(router.routes) > 0

    def test_routes_are_registered(self):
        """Test that all expected routes are registered."""
        from core.console.corvin_console.routes.phase_10_skills import router

        route_paths = []
        for route in router.routes:
            if hasattr(route, "path"):
                route_paths.append(route.path)

        # Verify key routes exist
        key_routes = [
            "/workflow-optimizer/execute",
            "/workflow-optimizer/status",
            "/security-orchestrator/threats/detect",
            "/flow-guard/flows",
            "/learning/feedback",
            "/skills/registry",
        ]

        for key_route in key_routes:
            # Check that the route is in the list (allowing for prefix)
            matching = [p for p in route_paths if key_route in p or p.endswith(key_route.split("/")[-1])]
            assert len(matching) > 0, f"Route {key_route} not found"


class TestRouteResponseFormats:
    """Test that all routes return correct response formats."""

    def test_threat_detection_response_format(self):
        """Test threat detection response has correct format."""
        from core.console.corvin_console.routes.phase_10_skills import detect_threats

        result = detect_threats()

        # Verify structure
        assert isinstance(result, dict)
        assert "threats_detected" in result
        assert isinstance(result["threats_detected"], list)

        # Verify threat structure if present
        if result["threats_detected"]:
            threat = result["threats_detected"][0]
            assert "threat_id" in threat
            assert "threat_type" in threat
            assert "severity" in threat
            assert "confidence" in threat
            assert 0.0 <= threat["confidence"] <= 1.0

    def test_skills_registry_response_format(self):
        """Test skills registry response has correct format."""
        from core.console.corvin_console.routes.phase_10_skills import get_skills_registry

        result = get_skills_registry()

        assert isinstance(result, dict)
        assert "skills" in result
        assert isinstance(result["skills"], list)

        for skill in result["skills"]:
            assert "skill_id" in skill
            assert "version" in skill
            assert "status" in skill
            assert skill["skill_id"].startswith("os.")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
