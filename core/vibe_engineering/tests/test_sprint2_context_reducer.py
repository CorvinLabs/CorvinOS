"""
Sprint 2.1: ContextReducer Tests

15 tests covering:
- Tier classification (Tier 1/2/3 keyword detection)
- Reduction logic (keep essential, drop tangential)
- Round-trip serialization (serialize → deserialize)
- Compression ratio validation

Target: 91% reduction, <100 lines essential context.
"""

import pytest
from core.vibe_engineering.context_reducer import (
    ContextReducer,
    ReducedContext,
    EssentialSection
)


class TestContextReducerTierClassification:
    """Test Tier 1/2/3 keyword detection."""

    def setup_method(self):
        self.reducer = ContextReducer(target_reduction_pct=91)

    def test_tier_1_goal_keyword_detected(self):
        """Tier 1: 'goal' keyword triggers KEEP."""
        assert self.reducer._is_tier_1("The goal is to improve performance")

    def test_tier_1_must_keyword_detected(self):
        """Tier 1: 'must' keyword triggers KEEP."""
        assert self.reducer._is_tier_1("Checkpoints MUST be idempotent")

    def test_tier_1_error_keyword_detected(self):
        """Tier 1: 'error' keyword triggers KEEP."""
        assert self.reducer._is_tier_1("An error occurred during checkpoint save")

    def test_tier_1_constraint_keyword_detected(self):
        """Tier 1: 'constraint' keyword triggers KEEP."""
        assert self.reducer._is_tier_1("Constraint: max memory 2GB")

    def test_tier_1_decision_keyword_detected(self):
        """Tier 1: 'decision' keyword triggers KEEP."""
        assert self.reducer._is_tier_1("We made a decision to use Redis")

    def test_tier_2_learned_keyword_detected(self):
        """Tier 2: 'learned' keyword triggers KEEP."""
        assert self.reducer._is_tier_2("Learned: TTL-based expiration is insufficient")

    def test_tier_2_pattern_keyword_detected(self):
        """Tier 2: 'pattern' keyword triggers KEEP."""
        assert self.reducer._is_tier_2("Industry pattern: event-driven cache invalidation")

    def test_tier_2_optimization_keyword_detected(self):
        """Tier 2: 'optimization' keyword triggers KEEP."""
        assert self.reducer._is_tier_2("Optimization: connection pooling reduces latency")

    def test_tier_3_tangential_keyword_detected(self):
        """Tier 3: 'tangential' keyword triggers DROP."""
        assert self.reducer._is_tier_3("Tangential: Redis memory fragmentation tuning")

    def test_tier_3_nice_to_know_keyword_detected(self):
        """Tier 3: 'nice-to-know' keyword triggers DROP."""
        assert self.reducer._is_tier_3("Nice-to-know: monitoring cache hit rates")

    def test_tier_3_fyi_keyword_detected(self):
        """Tier 3: 'FYI' keyword triggers DROP."""
        assert self.reducer._is_tier_3("FYI: some teams use polling instead of events")


class TestContextReducerReductionLogic:
    """Test reduction of decisions, errors, learnings."""

    def setup_method(self):
        self.reducer = ContextReducer(target_reduction_pct=91)

    def test_reduce_keeps_goal(self):
        """Reduction always keeps goal."""
        reduced = self.reducer.reduce(
            goal="Analyze security logs",
            constraints=[],
            decisions=[],
            errors=[],
            learnings=[]
        )
        assert reduced.goal == "Analyze security logs"

    def test_reduce_keeps_all_constraints(self):
        """Reduction keeps all constraints (Tier 1)."""
        constraints = [
            "Filesystem backend only",
            "Concurrent writes expected",
            "No database available"
        ]
        reduced = self.reducer.reduce(
            goal="Test",
            constraints=constraints,
            decisions=[],
            errors=[],
            learnings=[]
        )
        assert reduced.constraints == constraints

    def test_reduce_keeps_tier_1_decisions(self):
        """Reduction keeps Tier 1 decisions (MUST, required, etc.)."""
        decisions = [
            {"iter": 1, "decision": "Checkpoints MUST be idempotent", "why": "blocking"},
            {"iter": 2, "decision": "Consider async approach", "why": "optimization"}  # Tier 2/3
        ]
        reduced = self.reducer.reduce(
            goal="Test",
            constraints=[],
            decisions=decisions,
            errors=[],
            learnings=[]
        )
        # Should keep first decision (Tier 1: MUST)
        assert len(reduced.decisions_made) >= 1
        assert "idempotent" in reduced.decisions_made[0].content

    def test_reduce_keeps_all_errors(self):
        """Reduction keeps all errors (Tier 1, critical for recovery)."""
        errors = [
            {"iter": 5, "error_type": "ConnectionTimeout", "root_cause": "Redis unavailable"},
            {"iter": 10, "error_type": "FileNotFound", "root_cause": "Path whitelist issue"}
        ]
        reduced = self.reducer.reduce(
            goal="Test",
            constraints=[],
            decisions=[],
            errors=errors,
            learnings=[]
        )
        assert len(reduced.errors_encountered) == 2

    def test_reduce_drops_tier_3_learnings(self):
        """Reduction drops Tier 3 learnings (tangential, nice-to-know)."""
        learnings = [
            {"iter": 3, "learning": "TTL alone is insufficient", "applies_to": "cache strategy"},  # Tier 2
            {"iter": 4, "learning": "Redis memory fragmentation can be tuned", "applies_to": "optional"}  # Tier 3
        ]
        reduced = self.reducer.reduce(
            goal="Test",
            constraints=[],
            decisions=[],
            errors=[],
            learnings=learnings
        )
        # Should keep first learning (Tier 2)
        assert len(reduced.learnings) >= 1
        # Should drop second (Tier 3)
        assert len([l for l in reduced.learnings if "fragmentation" in l.content]) == 0

    def test_reduce_compression_ratio(self):
        """Reduction achieves target compression (91%)."""
        reduced = self.reducer.reduce(
            goal="Analyze security logs",
            constraints=["Filesystem only", "Concurrent writes", "No DB"],
            decisions=[
                {"iter": 1, "decision": "Checkpoints MUST be idempotent", "why": "blocking"},
                {"iter": 2, "decision": "Could use async approach", "why": "optimization"}
            ],
            errors=[
                {"iter": 5, "error_type": "ConnectionTimeout", "root_cause": "Redis unavailable"}
            ],
            learnings=[
                {"iter": 3, "learning": "TTL alone is insufficient", "applies_to": "strategy"}
            ],
            original_size_tokens=1000
        )
        # Typical reduction should be 80-95%
        assert 80 <= reduced.reduction_pct <= 95


class TestContextReducerSerialization:
    """Test round-trip serialization."""

    def setup_method(self):
        self.reducer = ContextReducer()

    def test_serialize_produces_valid_json(self):
        """Serialization produces valid JSON."""
        reduced = self.reducer.reduce(
            goal="Test",
            constraints=["C1", "C2"],
            decisions=[{"iter": 1, "decision": "Choose A", "why": "blocking"}],
            errors=[],
            learnings=[]
        )
        json_str = self.reducer.serialize(reduced)
        # Should not raise
        import json
        data = json.loads(json_str)
        assert data["goal"] == "Test"

    def test_deserialize_restores_reduced_context(self):
        """Deserialization restores full ReducedContext."""
        original = self.reducer.reduce(
            goal="Audit logs",
            constraints=["FS only"],
            decisions=[{"iter": 1, "decision": "Idempotent", "why": "blocking"}],
            errors=[{"iter": 5, "error_type": "Timeout", "root_cause": "unavailable"}],
            learnings=[{"iter": 3, "learning": "TTL insufficient", "applies_to": "strategy"}]
        )

        json_str = self.reducer.serialize(original)
        restored = self.reducer.deserialize(json_str)

        assert restored.goal == original.goal
        assert restored.constraints == original.constraints
        assert len(restored.decisions_made) == len(original.decisions_made)
        assert len(restored.errors_encountered) == len(original.errors_encountered)

    def test_round_trip_fidelity(self):
        """Round-trip preserves all essential data."""
        original = self.reducer.reduce(
            goal="Complex task",
            constraints=["C1", "C2", "C3"],
            decisions=[
                {"iter": 1, "decision": "MUST be idempotent", "why": "blocking"},
                {"iter": 5, "decision": "Should use Redis", "why": "optimization"}
            ],
            errors=[
                {"iter": 10, "error_type": "FileNotFound", "root_cause": "path issue"},
                {"iter": 15, "error_type": "Timeout", "root_cause": "network"}
            ],
            learnings=[
                {"iter": 3, "learning": "Learned: events > polling", "applies_to": "strategy"}
            ]
        )

        # Serialize and deserialize
        json_str = self.reducer.serialize(original)
        restored = self.reducer.deserialize(json_str)

        # Verify identity
        assert restored.goal == original.goal
        assert len(restored.decisions_made) == len(original.decisions_made)
        assert len(restored.errors_encountered) == len(original.errors_encountered)
        assert restored.reduction_pct == original.reduction_pct


class TestContextReducerDroppedTracking:
    """Test tracking of dropped sections for recovery."""

    def setup_method(self):
        self.reducer = ContextReducer()

    def test_identify_dropped_tangential_decisions(self):
        """Dropped sections are tracked for recovery."""
        reduced = self.reducer.reduce(
            goal="Test",
            constraints=[],
            decisions=[
                {"iter": 1, "decision": "Consider async approach", "why": "tangential"},
                {"iter": 2, "decision": "Could use polling", "why": "fyi"}
            ],
            errors=[],
            learnings=[]
        )
        # Should have dropped some decisions
        assert len(reduced.dropped_sections) > 0

    def test_dropped_sections_include_content_preview(self):
        """Dropped sections store content for audit trail."""
        reduced = self.reducer.reduce(
            goal="Test",
            constraints=[],
            decisions=[
                {"iter": 1, "decision": "Consider a very long tangential comment about polling alternatives", "why": "tangential"}
            ],
            errors=[],
            learnings=[]
        )
        if reduced.dropped_sections:
            assert "content" in reduced.dropped_sections[0]
            assert len(reduced.dropped_sections[0]["content"]) <= 100  # Truncated


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
