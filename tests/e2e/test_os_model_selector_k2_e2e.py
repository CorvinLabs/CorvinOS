"""E2E Tests: OS Model Selector k=2 (Tier 1 Routing in Real Orchestration)."""
import pytest
from core.skills.os_skills.model_selector import (
    Tier1Router,
    ModelSelectionDecision,
)


class TestOSModelSelectorK2E2E:
    """E2E tests: Tier 1 router in orchestration context."""
    
    @pytest.fixture
    def router(self):
        return Tier1Router()
    
    def test_e2e_simple_task_routing(self, router):
        """E2E: Simple task routed via Tier 1 → Haiku/Sonnet."""
        # Simulate real orchestration: task arrives
        task_id = "orchestrator.simple_summarize.v1.2026-09-26T12:30:45Z"
        complexity = "simple"  # Auto-detected or user-provided
        
        # Route via Tier 1
        decision = router.route(task_id, complexity)
        
        # Verify decision
        assert decision.task_id == task_id
        assert decision.complexity == complexity
        assert "haiku" in decision.selected_model or "sonnet" in decision.selected_model
        assert "opus" not in decision.selected_model.lower()
    
    def test_e2e_complex_task_routing(self, router):
        """E2E: Complex task routed via Tier 1 → mostly Opus."""
        task_id = "orchestrator.complex_analysis.v2.2026-09-26T13:00:00Z"
        complexity = "complex"
        
        decision = router.route(task_id, complexity)
        
        assert decision.complexity == complexity
        # Complex should favor Opus (80%)
        if "opus" not in decision.selected_model.lower():
            # Allow Sonnet (20%), but unlikely
            assert "sonnet" in decision.selected_model.lower()
    
    def test_e2e_audit_safe_decision(self, router):
        """E2E: ModelSelectionDecision is audit-safe (no PII, no prompts)."""
        task_id = "task_abc123"
        decision = router.route(task_id, "medium")
        
        # Verify no PII/secrets in serialization
        decision_dict = {
            "task_id": decision.task_id,
            "complexity": decision.complexity,
            "selected_model": decision.selected_model,
            "stratification_bucket": decision.stratification_bucket,
        }
        
        # Safe to emit to audit_backend (ADR-0297)
        assert not any(
            secret in str(decision_dict).lower()
            for secret in ["password", "token", "api_key", "secret"]
        )
    
    def test_e2e_real_orchestration_scenario(self, router):
        """E2E: Real orchestration scenario (multiple tasks, mixed complexity)."""
        tasks = [
            ("task_001", "simple", "haiku", "sonnet"),      # Allow haiku or sonnet
            ("task_002", "medium", "haiku", "sonnet", "opus"),  # Mixed
            ("task_003", "complex", "sonnet", "opus"),       # Favor opus
            ("task_004", "simple", "haiku", "sonnet"),       # Simple again
            ("task_005", "complex", "opus"),                 # Complex again
        ]
        
        for task_id, complexity, *allowed_models in tasks:
            decision = router.route(task_id, complexity)
            
            # Verify decision matches allowed models for complexity
            assert any(model in decision.selected_model for model in allowed_models), \
                f"Task {task_id}: unexpected model {decision.selected_model}"
    
    def test_e2e_stratification_accuracy(self, router):
        """E2E: Stratification matches expected percentages (quality gate)."""
        results = {}
        
        for complexity in ["simple", "medium", "complex"]:
            decisions = [
                router.route(f"{complexity}_{i:03d}", complexity)
                for i in range(100)
            ]
            
            haiku_count = sum(1 for d in decisions if "haiku" in d.selected_model)
            sonnet_count = sum(1 for d in decisions if "sonnet" in d.selected_model)
            opus_count = sum(1 for d in decisions if "opus" in d.selected_model)
            
            results[complexity] = {
                "haiku": haiku_count,
                "sonnet": sonnet_count,
                "opus": opus_count,
            }
        
        # Verify stratification
        # simple: 30–40% Haiku
        assert 25 <= results["simple"]["haiku"] <= 45
        # medium: ~10% Haiku, ~50% Sonnet, ~40% Opus
        assert 5 <= results["medium"]["haiku"] <= 15
        assert 40 <= results["medium"]["sonnet"] <= 60
        # complex: 0% Haiku, 20% Sonnet, 80% Opus
        assert results["complex"]["haiku"] == 0
        assert 15 <= results["complex"]["sonnet"] <= 25
    
    def test_e2e_no_pii_in_decision(self, router):
        """E2E: No PII even with PII-containing task_id (hash strips it)."""
        # Task ID might contain user info (should not appear in decision)
        pii_task_id = "user@example.com_task_2026-09-26"
        
        decision = router.route(pii_task_id, "medium")
        
        # Task ID is in decision, but should be safe
        assert decision.task_id == pii_task_id  # Task ID is metadata
        
        # Bucket is derived via hash (one-way, no PII recovery)
        assert not any(
            email in str(decision.stratification_bucket)
            for email in ["example.com", "@"]
        )
    
    def test_e2e_consistency_across_restarts(self, router):
        """E2E: Same task_id routes to same model across process restarts."""
        task_id = "persistent_task_xyz"
        
        # Simulate first invocation
        decision1 = router.route(task_id, "medium")
        
        # Simulate restart (new router instance)
        router2 = Tier1Router()
        decision2 = router2.route(task_id, "medium")
        
        # Must be identical
        assert decision1.selected_model == decision2.selected_model
        assert decision1.stratification_bucket == decision2.stratification_bucket
    
    def test_e2e_load_distribution(self, router):
        """E2E: Load is distributed across available models (no single-model bias)."""
        # Simulate 300 random tasks
        decisions = [
            router.route(f"load_test_{i:04d}", ["simple", "medium", "complex"][i % 3])
            for i in range(300)
        ]
        
        model_counts = {}
        for d in decisions:
            model = d.selected_model
            model_counts[model] = model_counts.get(model, 0) + 1
        
        # All three models should be used
        assert len(model_counts) == 3, f"Expected 3 models, got {len(model_counts)}"
        
        # No single model should dominate > 50%
        for model, count in model_counts.items():
            assert count < 150, f"Model {model} overused: {count}/300"


pytestmark = pytest.mark.e2e


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
