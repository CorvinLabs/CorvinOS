"""Unit tests for ContextSelectorSkill (Phase 4, ACP pattern).

Tests verify:
- Heuristic layer (deterministic task-type rules)
- Learned layer (A/B converged modes)
- Real-time load adjustment
- User override escape hatch
- Audit event generation
"""

import pytest
from core.skills.os_skills.context_selector import (
    ContextSelectorSkill,
    QualityMode,
    get_context_selector,
)


class TestHeuristicLayer:
    """Test Layer 1: Deterministic heuristic rules."""

    def test_compliance_task_quality_max(self):
        """Compliance tasks mapped to QUALITY_MAX."""
        skill = ContextSelectorSkill()

        for task in ["compliance", "legal", "gdpr", "audit", "governance"]:
            decision = skill.execute(task_type=task, user_id="user-1")
            assert (
                decision.quality_mode == QualityMode.QUALITY_MAX
            ), f"Task '{task}' should map to QUALITY_MAX"

    def test_routing_task_balanced(self):
        """Routing tasks mapped to BALANCED."""
        skill = ContextSelectorSkill()

        for task in ["routing", "triage", "classification", "dispatch"]:
            decision = skill.execute(task_type=task, user_id="user-1")
            assert (
                decision.quality_mode == QualityMode.BALANCED
            ), f"Task '{task}' should map to BALANCED"

    def test_bulk_task_efficiency_max(self):
        """Bulk tasks mapped to EFFICIENCY_MAX."""
        skill = ContextSelectorSkill()

        for task in ["bulk", "batch", "background", "processing"]:
            decision = skill.execute(task_type=task, user_id="user-1")
            assert (
                decision.quality_mode == QualityMode.EFFICIENCY_MAX
            ), f"Task '{task}' should map to EFFICIENCY_MAX"

    def test_unknown_task_defaults_balanced(self):
        """Unknown task types default to BALANCED."""
        skill = ContextSelectorSkill()

        decision = skill.execute(task_type="unknown_xyz", user_id="user-1")
        assert decision.quality_mode == QualityMode.BALANCED


class TestLearnedLayer:
    """Test Layer 2: Learned preferences (A/B converged modes)."""

    def test_learned_mode_overrides_heuristic(self):
        """Learned mode (if available) overrides heuristic."""
        skill = ContextSelectorSkill()

        # Simulate learned mode (would be loaded from ADR-0314 in production)
        skill.learned_modes["routing"] = QualityMode.QUALITY_MAX

        decision = skill.execute(task_type="routing", user_id="user-1")

        # Should use learned mode (QUALITY_MAX) instead of heuristic (BALANCED)
        assert decision.quality_mode == QualityMode.QUALITY_MAX
        assert decision.confidence > 0.7  # Learned mode has higher confidence

    def test_no_learned_mode_uses_heuristic(self):
        """If no learned mode, fall back to heuristic."""
        skill = ContextSelectorSkill()
        # No learned modes set

        decision = skill.execute(task_type="compliance", user_id="user-1")

        assert decision.quality_mode == QualityMode.QUALITY_MAX  # Heuristic
        assert decision.confidence < 0.7  # Heuristic has lower confidence


class TestRealTimeLoadAdjustment:
    """Test Layer 3: Real-time load adjustment."""

    def test_load_adjustment_downgrades_mode(self):
        """High load (P99 > 1500ms) downgrades to EFFICIENCY_MAX."""
        skill = ContextSelectorSkill()

        # Without load: should be QUALITY_MAX for compliance task
        decision_no_load = skill.execute(
            task_type="compliance", user_id="user-1", system_load_p99_ms=500
        )
        assert decision_no_load.quality_mode == QualityMode.QUALITY_MAX

        # With high load: should downgrade to EFFICIENCY_MAX
        decision_high_load = skill.execute(
            task_type="compliance", user_id="user-1", system_load_p99_ms=2000
        )
        assert decision_high_load.quality_mode == QualityMode.EFFICIENCY_MAX

    def test_load_adjustment_reduces_confidence(self):
        """Load-triggered downgrade reduces confidence."""
        skill = ContextSelectorSkill()

        decision_no_load = skill.execute(
            task_type="compliance", user_id="user-1", system_load_p99_ms=500
        )
        decision_high_load = skill.execute(
            task_type="compliance", user_id="user-1", system_load_p99_ms=2000
        )

        # High-load decision should have lower confidence (downgraded)
        assert decision_high_load.confidence < decision_no_load.confidence


class TestUserOverride:
    """Test Layer 4: User override escape hatch."""

    def test_user_override_forces_mode(self):
        """User override bypasses heuristic and load adjustment."""
        skill = ContextSelectorSkill()

        # Bulk task (normally EFFICIENCY_MAX) + high load (would downgrade further)
        # but user override to QUALITY_MAX
        decision = skill.execute(
            task_type="bulk",
            user_id="user-1",
            system_load_p99_ms=2000,
            user_override="QUALITY_MAX",
        )

        assert decision.quality_mode == QualityMode.QUALITY_MAX
        assert decision.confidence >= 0.99  # User explicit, capped to 0.99 for safety

    def test_invalid_user_override_ignored(self):
        """Invalid override value is ignored, falls back to heuristic."""
        skill = ContextSelectorSkill()

        decision = skill.execute(
            task_type="compliance",
            user_id="user-1",
            user_override="INVALID_MODE",
        )

        # Should fall back to heuristic (QUALITY_MAX for compliance)
        assert decision.quality_mode == QualityMode.QUALITY_MAX


class TestContextItemSelection:
    """Test ADR + memory item selection."""

    def test_quality_max_selects_all_adrs(self):
        """QUALITY_MAX selects all relevant ADRs."""
        skill = ContextSelectorSkill()

        decision = skill.execute(
            task_type="compliance", user_id="user-1", user_override="QUALITY_MAX"
        )

        assert len(decision.selected_adr_ids) > 0
        # For compliance: should include multiple ADRs
        assert len(decision.selected_adr_ids) >= 3

    def test_balanced_selects_subset_adrs(self):
        """BALANCED selects subset of ADRs."""
        skill = ContextSelectorSkill()

        decision_quality = skill.execute(
            task_type="routing", user_id="user-1", user_override="QUALITY_MAX"
        )
        decision_balanced = skill.execute(
            task_type="routing", user_id="user-1", user_override="BALANCED"
        )

        # BALANCED should have fewer ADRs than QUALITY_MAX
        assert len(decision_balanced.selected_adr_ids) < len(decision_quality.selected_adr_ids)
        assert len(decision_balanced.selected_adr_ids) > 0

    def test_efficiency_max_selects_minimal_adrs(self):
        """EFFICIENCY_MAX selects minimal ADRs."""
        skill = ContextSelectorSkill()

        decision = skill.execute(
            task_type="routing", user_id="user-1", user_override="EFFICIENCY_MAX"
        )

        # EFFICIENCY_MAX should select only 1 ADR (most critical)
        assert len(decision.selected_adr_ids) == 1

    def test_quality_max_includes_memory(self):
        """QUALITY_MAX includes memory items."""
        skill = ContextSelectorSkill()

        decision = skill.execute(
            task_type="compliance", user_id="user-test-mem", user_override="QUALITY_MAX"
        )

        # QUALITY_MAX should include memory
        assert len(decision.selected_memory_ids) >= 1

    def test_efficiency_max_excludes_memory(self):
        """EFFICIENCY_MAX excludes memory items."""
        skill = ContextSelectorSkill()

        decision = skill.execute(
            task_type="compliance", user_id="user-test-mem", user_override="EFFICIENCY_MAX"
        )

        # EFFICIENCY_MAX should have no memory
        assert len(decision.selected_memory_ids) == 0


class TestAuditEvent:
    """Test audit event generation."""

    def test_decision_includes_audit_event(self):
        """Decision includes audit event for compliance."""
        skill = ContextSelectorSkill()

        decision = skill.execute(task_type="compliance", user_id="user-audit")

        assert decision.audit_event is not None
        assert decision.audit_event["event_type"] == "context_selection_made"
        assert decision.audit_event["skill_id"] == "os.context_selector"
        assert decision.audit_event["user_id"] == "user-audit"
        assert decision.audit_event["quality_mode"] == decision.quality_mode.value

    def test_audit_event_includes_selections(self):
        """Audit event includes selected ADRs and memory."""
        skill = ContextSelectorSkill()

        decision = skill.execute(task_type="compliance", user_id="user-1")

        assert decision.audit_event["selected_adrs"] == decision.selected_adr_ids
        assert decision.audit_event["selected_memory_items"] == decision.selected_memory_ids


class TestExecutionMetrics:
    """Test execution time tracking."""

    def test_decision_includes_execution_time(self):
        """Decision includes execution time."""
        skill = ContextSelectorSkill()

        decision = skill.execute(task_type="compliance", user_id="user-1")

        assert decision.execution_time_ms > 0
        assert decision.execution_time_ms < 100  # Should be fast

    def test_reasoning_includes_context(self):
        """Decision reasoning explains the choice."""
        skill = ContextSelectorSkill()

        decision = skill.execute(task_type="compliance", user_id="user-1")

        assert "compliance" in decision.reasoning.lower()
        assert "QUALITY_MAX" in decision.reasoning


class TestSkillSingleton:
    """Test global skill instance."""

    def test_get_context_selector_singleton(self):
        """get_context_selector() returns singleton."""
        skill1 = get_context_selector("tenant-1")
        skill2 = get_context_selector("tenant-1")

        assert skill1 is skill2  # Same instance


class TestIntegration:
    """Integration tests."""

    def test_execution_count_increments(self):
        """Skill tracks execution count."""
        skill = ContextSelectorSkill()

        assert skill.execution_count == 0

        skill.execute(task_type="compliance", user_id="user-1")
        assert skill.execution_count == 1

        skill.execute(task_type="routing", user_id="user-1")
        assert skill.execution_count == 2

    def test_multiple_task_types_in_sequence(self):
        """Skill handles multiple task types in sequence."""
        skill = ContextSelectorSkill()

        tasks = ["compliance", "routing", "bulk", "unknown"]
        decisions = []

        for task in tasks:
            decision = skill.execute(task_type=task, user_id="user-1")
            decisions.append(decision)

        # All should return valid decisions
        assert len(decisions) == len(tasks)
        assert all(d.quality_mode is not None for d in decisions)
        assert all(d.confidence > 0 for d in decisions)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
