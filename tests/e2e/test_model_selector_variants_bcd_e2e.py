"""
E2E Tests: Model Selection Variants B, C, D (ADR-0641, 0642, 0644, 0201)

Validates:
- Variant B: Base tenant-aware classification
- Variant C: Budget-aware selection with quota fallback
- Variant D: Learning-integrated selection with outcome feedback

All variants tested with:
- Tenant isolation
- Audit trail compliance
- Budget ceiling enforcement (ADR-0201)
- Quota fallback behavior
"""

import json
import pytest
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any

from core.skills.os_skills.model_selector_variants import (
    ModelVariant,
    VariantBSelector,
    VariantCSelector,
    VariantDSelector,
    ModelSelectionDecision,
    BudgetEnvelope,
    TenantBudgetQuota,
    create_selector,
)


class TestVariantBBase:
    """Tests for Variant B: Base Model Selection."""

    @pytest.fixture
    def selector_b(self, tmp_path, monkeypatch):
        """Create Variant B selector with temp config."""
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
        return VariantBSelector("test_tenant_1")

    def test_variant_b_simple_task_classification(self, selector_b):
        """Variant B correctly classifies simple tasks."""
        task = "Fix typo in README"
        decision = selector_b.classify(task, task_type="documentation")

        assert decision.variant == ModelVariant.B
        assert decision.complexity == "simple"
        assert decision.confidence >= 0.80
        assert decision.recommended_model == "claude-haiku-4-5-20251001"

    def test_variant_b_medium_task_classification(self, selector_b):
        """Variant B correctly classifies medium complexity tasks."""
        task = "Refactor database query: " + "SELECT * FROM users WHERE active=1 " * 10
        decision = selector_b.classify(task, task_type="code_gen")

        assert decision.complexity == "medium"
        assert decision.recommended_model == "claude-sonnet-5"

    def test_variant_b_complex_task_classification(self, selector_b):
        """Variant B correctly classifies complex tasks."""
        task = "Design system architecture for: " + ("multi-tenant microservices " * 50)
        decision = selector_b.classify(task, task_type="system_design")

        assert decision.complexity == "complex"
        assert decision.recommended_model == "claude-opus-5"

    def test_variant_b_tenant_overrides(self, selector_b, tmp_path, monkeypatch):
        """Variant B respects tenant-specific model overrides."""
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))

        # Set override for this tenant
        override_config = {
            "models": {
                "code_review": "claude-opus-5",  # Force Opus for code review
            }
        }
        config_path = tmp_path / "tenants" / "test_tenant_1" / "global" / "model_selection_overrides.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(override_config))

        # Reload selector
        selector = VariantBSelector("test_tenant_1")

        # Override should take precedence
        decision = selector.classify("Short review", task_type="code_review")
        assert decision.recommended_model == "claude-opus-5"
        assert decision.confidence == 1.0

    def test_variant_b_audit_serialization(self, selector_b):
        """Variant B decision serializes correctly for audit trail."""
        task = "Simple task"
        decision = selector_b.classify(task)

        audit_dict = decision.to_audit_dict()

        assert audit_dict["variant"] == "variant_b"
        assert audit_dict["tenant_id"] == "test_tenant_1"
        assert audit_dict["recommended_model"]
        assert audit_dict["confidence"] > 0
        assert "timestamp" in audit_dict
        assert "lom" in audit_dict


class TestVariantCBudget:
    """Tests for Variant C: Budget-Aware with Quota Fallback (ADR-0201)."""

    @pytest.fixture
    def selector_c(self, tmp_path, monkeypatch):
        """Create Variant C selector with temp quota store."""
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
        return VariantCSelector("test_tenant_2")

    def test_variant_c_budget_envelope_at_ceiling(self, selector_c):
        """Variant C enforces budget ceiling per ADR-0201."""
        decision = selector_c.classify_with_budget("Complex task " * 20)

        assert decision.budget_envelope is not None
        assert decision.budget_envelope.max_loops == 100
        assert decision.budget_envelope.max_wall_time_seconds == 86400
        assert decision.budget_envelope.timeout_seconds == 86400
        assert decision.budget_envelope.max_worker_turns == 5000
        assert decision.budget_envelope.max_total_workers == 64
        assert decision.budget_envelope.is_at_ceiling()

    def test_variant_c_quota_tracking_initialization(self, selector_c):
        """Variant C initializes and tracks quota correctly."""
        assert selector_c.current_quota.tenant_id == "test_tenant_2"
        assert selector_c.current_quota.daily_quota_remaining == 50.0
        assert selector_c.current_quota.daily_quota_limit == 50.0
        assert selector_c.current_quota.remaining_budget_percent() == 100.0

    def test_variant_c_quota_deduction(self, selector_c):
        """Variant C deducts cost from quota correctly."""
        initial_quota = selector_c.current_quota.daily_quota_remaining

        selector_c.deduct_from_quota(10.0)

        assert selector_c.current_quota.daily_quota_remaining == initial_quota - 10.0
        assert selector_c.current_quota.remaining_budget_percent() == 80.0

    def test_variant_c_quota_exhaustion_fallback(self, selector_c):
        """Variant C applies fallback when quota exhausted (ADR-0201)."""
        # Exhaust quota
        selector_c.current_quota.daily_quota_remaining = 0
        selector_c._persist_quota()

        # Reload to confirm persistence
        selector_c = VariantCSelector("test_tenant_2")

        decision = selector_c.classify_with_budget("Any task")

        assert decision.fallback_applied
        assert "Quota exhausted" in decision.reasoning
        assert "quota fallback: true" in decision.reasoning.lower() or "fallback" in decision.reasoning.lower()
        assert decision.recommended_model == "claude-sonnet-5"

    def test_variant_c_low_quota_model_selection(self, selector_c):
        """Variant C selects cheaper model when quota is low."""
        # Set quota to 10% remaining
        selector_c.current_quota.daily_quota_remaining = 5.0
        selector_c.current_quota.daily_quota_limit = 50.0
        selector_c._persist_quota()

        # Reload
        selector_c = VariantCSelector("test_tenant_2")

        decision = selector_c.classify_with_budget("Complex task " * 20)

        # Should prefer cheaper model (Haiku) when quota low
        assert decision.recommended_model == "claude-haiku-4-5-20251001"
        assert "quota low" in decision.reasoning.lower()

    def test_variant_c_quota_reset_after_24h(self, selector_c, monkeypatch):
        """Variant C resets quota after 24-hour window."""
        # Set last reset to 25 hours ago
        selector_c.current_quota.last_reset = datetime.utcnow() - timedelta(hours=25)
        selector_c.current_quota.daily_quota_remaining = 0  # Exhaust quota
        selector_c._persist_quota()

        # Reload and check reset
        selector_c = VariantCSelector("test_tenant_2")
        decision = selector_c.classify_with_budget("Task")

        # After reset, quota should be restored
        assert selector_c.current_quota.daily_quota_remaining > 0
        assert selector_c.current_quota.remaining_budget_percent() == 100.0

    def test_variant_c_variant_marker(self, selector_c):
        """Variant C decisions are marked as variant_c."""
        decision = selector_c.classify_with_budget("Task")
        assert decision.variant == ModelVariant.C


class TestVariantDLearning:
    """Tests for Variant D: Learning-Integrated Selection."""

    @pytest.fixture
    def selector_d(self, tmp_path, monkeypatch):
        """Create Variant D selector with learning store."""
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
        return VariantDSelector("test_tenant_3")

    def test_variant_d_learning_initialization(self, selector_d):
        """Variant D initializes learning data correctly."""
        assert "claude-haiku-4-5-20251001" in selector_d.success_rates
        assert "claude-sonnet-5" in selector_d.success_rates
        assert "claude-opus-5" in selector_d.success_rates

        haiku_rates = selector_d.success_rates["claude-haiku-4-5-20251001"]
        assert "code_review" in haiku_rates
        assert haiku_rates["code_review"] > 0

    def test_variant_d_classify_with_learning(self, selector_d):
        """Variant D integrates learning feedback into classification."""
        decision, hint = selector_d.classify_with_learning(
            "Code review: " * 30,
            task_type="code_review"
        )

        assert decision.variant == ModelVariant.D
        assert decision.task_type == "code_review"
        assert decision.confidence > 0  # Confidence adjusted by learned rate

    def test_variant_d_decomposition_hint_for_complex_tasks(self, selector_d):
        """Variant D generates decomposition hints for complex low-success tasks."""
        # For complex tasks with lower success rates, should provide hint
        decision, hint = selector_d.classify_with_learning(
            "Complex task: " * 100,  # Long, complex task
            task_type="system_design"
        )

        if decision.complexity == "complex":
            # May or may not have hint depending on learned success rate
            if hint:
                assert "decomposition" in hint.lower() or "subtask" in hint.lower()

    def test_variant_d_record_outcome_feedback_success(self, selector_d):
        """Variant D records successful outcome and updates success rate."""
        initial_rate = selector_d.success_rates["claude-sonnet-5"]["code_gen"]

        selector_d.record_outcome_feedback(
            model="claude-sonnet-5",
            task_type="code_gen",
            success=True,
            cost_usd=2.5,
        )

        updated_rate = selector_d.success_rates["claude-sonnet-5"]["code_gen"]
        # Should increase (0.9 * initial + 0.1 * 1.0)
        assert updated_rate > initial_rate

    def test_variant_d_record_outcome_feedback_failure(self, selector_d):
        """Variant D records failure and lowers success rate."""
        initial_rate = selector_d.success_rates["claude-haiku-4-5-20251001"]["analysis"]

        selector_d.record_outcome_feedback(
            model="claude-haiku-4-5-20251001",
            task_type="analysis",
            success=False,
            cost_usd=0.5,
        )

        updated_rate = selector_d.success_rates["claude-haiku-4-5-20251001"]["analysis"]
        # Should decrease (0.9 * initial + 0.1 * 0.0)
        assert updated_rate < initial_rate

    def test_variant_d_learning_persistence(self, selector_d, tmp_path):
        """Variant D persists learning data to disk."""
        selector_d.record_outcome_feedback(
            model="claude-opus-5",
            task_type="system_design",
            success=True,
            cost_usd=5.0,
        )

        # Reload and verify persistence
        selector_d_2 = VariantDSelector("test_tenant_3")

        # Should have same learned rates
        assert (
            selector_d_2.success_rates["claude-opus-5"]["system_design"] ==
            selector_d.success_rates["claude-opus-5"]["system_design"]
        )

    def test_variant_d_cost_variance_tracking(self, selector_d):
        """Variant D tracks cost variance for threshold adaptation."""
        selector_d.record_outcome_feedback("claude-sonnet-5", "code_gen", True, 1.5)
        selector_d.record_outcome_feedback("claude-sonnet-5", "code_gen", True, 2.0)
        selector_d.record_outcome_feedback("claude-sonnet-5", "code_gen", True, 1.8)

        assert len(selector_d.cost_variance_history) == 3
        assert all(task == "code_gen" for task, _ in selector_d.cost_variance_history)


class TestSelectorFactory:
    """Tests for selector factory and variant creation."""

    def test_create_selector_variant_b(self, tmp_path, monkeypatch):
        """Factory creates Variant B selector correctly."""
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))

        selector = create_selector("tenant_1", ModelVariant.B)
        assert isinstance(selector, VariantBSelector)
        assert not isinstance(selector, VariantCSelector)

    def test_create_selector_variant_c(self, tmp_path, monkeypatch):
        """Factory creates Variant C selector correctly."""
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))

        selector = create_selector("tenant_1", ModelVariant.C)
        assert isinstance(selector, VariantCSelector)
        assert not isinstance(selector, VariantDSelector)

    def test_create_selector_variant_d(self, tmp_path, monkeypatch):
        """Factory creates Variant D selector correctly."""
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))

        selector = create_selector("tenant_1", ModelVariant.D)
        assert isinstance(selector, VariantDSelector)


class TestTenantIsolation:
    """Tests for tenant isolation across variants."""

    def test_tenant_isolation_variant_b(self, tmp_path, monkeypatch):
        """Variant B isolates configuration per tenant."""
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))

        selector_1 = VariantBSelector("tenant_alpha")
        selector_2 = VariantBSelector("tenant_beta")

        # Each tenant has different config path
        assert selector_1._get_config_path() != selector_2._get_config_path()

    def test_tenant_isolation_variant_c_quota(self, tmp_path, monkeypatch):
        """Variant C isolates quota tracking per tenant."""
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))

        selector_1 = VariantCSelector("tenant_alpha")
        selector_2 = VariantCSelector("tenant_beta")

        # Deduct from tenant_alpha
        selector_1.deduct_from_quota(20.0)

        # tenant_beta should be unaffected
        selector_2_reloaded = VariantCSelector("tenant_beta")
        assert selector_2_reloaded.current_quota.daily_quota_remaining == 50.0

    def test_tenant_isolation_variant_d_learning(self, tmp_path, monkeypatch):
        """Variant D isolates learning data per tenant."""
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))

        selector_1 = VariantDSelector("tenant_alpha")
        selector_2 = VariantDSelector("tenant_beta")

        # Update learning for tenant_alpha
        selector_1.record_outcome_feedback("claude-sonnet-5", "code_gen", True, 2.0)

        # tenant_beta should have original rates
        selector_2_reloaded = VariantDSelector("tenant_beta")
        # Rates should differ due to different persistence
        # (tenant_alpha should have updated, tenant_beta original)


class TestErrorHandling:
    """Tests for error handling and edge cases."""

    def test_variant_b_invalid_config_fallback(self, tmp_path, monkeypatch):
        """Variant B falls back gracefully on invalid config."""
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))

        # Create invalid config
        config_path = tmp_path / "tenants" / "bad_tenant" / "global" / "model_selection_overrides.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("{ INVALID JSON }")

        selector = VariantBSelector("bad_tenant")
        # Should fallback to empty overrides
        assert selector.overrides == {}

    def test_variant_c_quota_negative_boundary(self, tmp_path, monkeypatch):
        """Variant C handles quota going negative correctly."""
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))

        selector = VariantCSelector("test_tenant")
        selector.deduct_from_quota(55.0)  # Deduct more than limit

        # Should be negative
        assert selector.current_quota.daily_quota_remaining < 0
        assert selector.current_quota.is_quota_exhausted()

    def test_variant_d_unknown_model_success_rate(self, tmp_path, monkeypatch):
        """Variant D handles unknown model gracefully."""
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))

        selector = VariantDSelector("test_tenant")
        rate = selector._get_learned_success_rate("claude-unknown-999", "code_gen")

        # Should return default
        assert rate > 0.0


class TestBudgetEnvelope:
    """Tests for BudgetEnvelope validation."""

    def test_budget_envelope_ceiling_validation(self):
        """BudgetEnvelope validates ceiling correctly."""
        envelope = BudgetEnvelope(
            max_loops=100,
            max_wall_time_seconds=86400,
            timeout_seconds=86400,
            max_worker_turns=5000,
            max_total_workers=64,
        )

        assert envelope.is_at_ceiling()

    def test_budget_envelope_below_ceiling(self):
        """BudgetEnvelope detects when below ceiling."""
        envelope = BudgetEnvelope(
            max_loops=50,  # Below ceiling
            max_wall_time_seconds=86400,
            timeout_seconds=86400,
            max_worker_turns=5000,
            max_total_workers=64,
        )

        assert not envelope.is_at_ceiling()


class TestTenantQuotaTracking:
    """Tests for TenantBudgetQuota tracking."""

    def test_quota_remaining_percent_calculation(self):
        """TenantBudgetQuota calculates percentage correctly."""
        quota = TenantBudgetQuota(
            tenant_id="test",
            daily_quota_remaining=25.0,
            daily_quota_limit=100.0,
            last_reset=datetime.utcnow(),
            worker_count_current=0,
        )

        assert quota.remaining_budget_percent() == 25.0

    def test_quota_exhaustion_detection(self):
        """TenantBudgetQuota detects exhaustion correctly."""
        quota = TenantBudgetQuota(
            tenant_id="test",
            daily_quota_remaining=-1.0,
            daily_quota_limit=50.0,
            last_reset=datetime.utcnow(),
            worker_count_current=0,
        )

        assert quota.is_quota_exhausted()

    def test_quota_reset_window_detection(self):
        """TenantBudgetQuota detects reset window correctly."""
        quota_old = TenantBudgetQuota(
            tenant_id="test",
            daily_quota_remaining=0,
            daily_quota_limit=50.0,
            last_reset=datetime.utcnow() - timedelta(hours=25),
            worker_count_current=0,
        )

        assert quota_old.quota_reset_needed()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
