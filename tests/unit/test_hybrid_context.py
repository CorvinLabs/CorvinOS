"""Unit tests for Hybrid Context Model (Phase 4, k=2 E2E Design).

Tests verify:
- Immutable base snapshots (never modified)
- Layer injection + chain validation
- Fail-closed merge (drop failing layers)
- GDPR cascade delete
"""

import pytest
import json
from core.learning.hybrid_context import (
    HybridContextModel,
    ImmutableContextBase,
    InjectedLayer,
    CascadeDeleteResult,
)


@pytest.fixture(autouse=True)
def _as_tenant_1(monkeypatch):
    """HybridContextModel writes audit-first (core chain); the chain admits the
    PROCESS tenant only (ADR-0007), so these tests run as ``tenant-1``."""
    monkeypatch.setenv("CORVIN_TENANT_ID", "tenant-1")


class TestImmutableBase:
    """Test immutable base context snapshots."""

    def test_snapshot_base_context(self):
        """Snapshot immutable base from Phase 3 data."""
        model = HybridContextModel("tenant-1")

        decisions = [{"decision_id": "d1", "choice": "a"}]
        profile = {"style": "verbose", "confidence_threshold": 0.8}
        success_rate = 0.75
        attention_budget = 1000

        base_hash = model.snapshot_base_context(
            user_id="user-1",
            session_id="s1",
            decisions=decisions,
            profile=profile,
            success_rate=success_rate,
            attention_budget=attention_budget,
        )

        assert base_hash is not None
        assert len(base_hash) == 64  # sha256

        # Verify base stored
        key = "user-1:s1"
        assert key in model.base_snapshots
        base = model.base_snapshots[key]
        assert base.user_id == "user-1"
        assert base.session_id == "s1"
        assert base.success_rate == 0.75
        assert base.attention_budget_remaining == 1000

    def test_base_immutable_frozen(self):
        """Base snapshot is frozen (immutable)."""
        model = HybridContextModel("tenant-1")
        model.snapshot_base_context(
            user_id="user-1",
            session_id="s1",
            decisions=[],
            profile={},
            success_rate=0.5,
            attention_budget=1000,
        )

        base = model.base_snapshots["user-1:s1"]

        # Attempt to modify frozen dataclass
        with pytest.raises((AttributeError, TypeError)):
            base.success_rate = 0.9  # Should fail


class TestLayerInjection:
    """Test injected layer creation + chain validation."""

    def test_inject_layer_simple(self):
        """Inject a single context layer."""
        model = HybridContextModel("tenant-1")

        layer_hash = model.inject_layer(
            user_id="user-1",
            layer_name="user_style",
            data={"preference": "concise", "tone": "professional"},
            lom="test.py:L42:test_inject",
        )

        assert layer_hash is not None
        assert len(layer_hash) == 64  # sha256

        # Verify layer stored
        assert "user-1" in model.injected_layers
        layers = model.injected_layers["user-1"]
        assert len(layers) == 1
        assert layers[0].layer_name == "user_style"
        assert layers[0].status == "injected"

    def test_layer_chain_validation(self):
        """Layers form a hash chain (prev_hash links)."""
        model = HybridContextModel("tenant-1")

        # Inject first layer
        hash1 = model.inject_layer(
            user_id="user-1",
            layer_name="user_style",
            data={"tone": "verbose"},
            lom="test.py:L1",
        )

        # Inject second layer
        hash2 = model.inject_layer(
            user_id="user-1",
            layer_name="session_context",
            data={"conversation_depth": 5},
            lom="test.py:L2",
        )

        layers = model.injected_layers["user-1"]
        assert len(layers) == 2

        # Verify chain links
        assert layers[0].prev_hash == ""  # First layer has no prior
        assert layers[1].prev_hash == hash1  # Second links to first

    def test_inject_layer_pii_validation(self):
        """PII detection blocks injection (fail-closed)."""
        model = HybridContextModel("tenant-1")

        # Attempt to inject email (PII)
        with pytest.raises(ValueError, match="Potential PII"):
            model.inject_layer(
                user_id="user-1",
                layer_name="bad_layer",
                data={"user_email": "user@example.com"},
                lom="test.py:L1",
            )

        # Layer should not be stored
        assert "user-1" not in model.injected_layers or len(model.injected_layers.get("user-1", [])) == 0


class TestMergeWithFallback:
    """Test fail-closed merge of layers."""

    def test_merge_healthy_layers(self):
        """Merge succeeds when all layers are healthy."""
        model = HybridContextModel("tenant-1")

        # Create base
        model.snapshot_base_context(
            user_id="user-1",
            session_id="s1",
            decisions=[{"d1": "a"}],
            profile={"style": "verbose"},
            success_rate=0.8,
            attention_budget=1000,
        )

        base = model.base_snapshots["user-1:s1"]

        # Create layers (all healthy) through the model, so they carry the
        # hash chain merge_with_fallback verifies (L-04): a hand-built layer
        # without hash/prev_hash is indistinguishable from a forged one and
        # is dropped by design.
        from dataclasses import asdict
        model.inject_layer("user-1", "user_style", {"tone": "professional"}, lom="test:1")
        model.inject_layer("user-1", "session_context", {"depth": 3}, lom="test:2")
        layers = [asdict(l) for l in model.injected_layers["user-1"]]

        # Merge
        merged = model.merge_with_fallback(base, layers)

        # Verify base fields present
        assert merged["success_rate"] == 0.8
        assert merged["attention_budget_remaining"] == 1000

        # Verify layers merged
        assert merged["user_style"]["tone"] == "professional"
        assert merged["session_context"]["depth"] == 3

    def test_merge_drop_failing_layer(self):
        """Merge drops failing layers, continues with healthy ones."""
        model = HybridContextModel("tenant-1")

        # Create base
        model.snapshot_base_context(
            user_id="user-1",
            session_id="s1",
            decisions=[],
            profile={},
            success_rate=0.5,
            attention_budget=500,
        )

        base = model.base_snapshots["user-1:s1"]

        # One good layer via the model (hash-chained), then a PII layer that
        # is chain-consistent (so it is dropped for CONTENT, not as a forgery).
        from dataclasses import asdict
        good_hash = model.inject_layer("user-1", "user_style", {"tone": "verbose"}, lom="test:1")
        bad_data = {"email": "user@example.com"}  # PII — will fail
        layers = [asdict(l) for l in model.injected_layers["user-1"]] + [
            {
                "layer_name": "bad_layer",
                "data": bad_data,
                "status": "injected",
                "version": "1.0",
                "prev_hash": good_hash,
                "hash": model._compute_hash("1.0", bad_data, good_hash),
            },
        ]

        # Merge — should drop bad_layer, continue with good_layer
        merged = model.merge_with_fallback(base, layers)

        # Verify good layer merged
        assert merged["user_style"]["tone"] == "verbose"

        # Verify bad layer NOT in merged (dropped)
        assert "bad_layer" not in merged or merged.get("bad_layer") is None

        # Verify base still present
        assert merged["attention_budget_remaining"] == 500


class TestGetContext:
    """Test full hybrid context retrieval."""

    def test_get_context_complete(self):
        """Get complete hybrid context (base + layers + merged)."""
        model = HybridContextModel("tenant-1")

        # Snapshot base
        model.snapshot_base_context(
            user_id="user-1",
            session_id="s1",
            decisions=[{"d1": "chosen_a"}],
            profile={"learned_preference": "concise"},
            success_rate=0.85,
            attention_budget=2000,
        )

        # Inject layer
        model.inject_layer(
            user_id="user-1",
            layer_name="user_style",
            data={"tone": "professional"},
            lom="test.py:L1",
        )

        # Get context
        context = model.get_context(user_id="user-1", session_id="s1")

        # Verify structure
        assert "base" in context
        assert "layers" in context
        assert "merged" in context

        # Verify base fields
        assert context["base"]["success_rate"] == 0.85
        assert context["base"]["attention_budget_remaining"] == 2000

        # Verify layers
        assert len(context["layers"]) == 1
        assert context["layers"][0]["layer_name"] == "user_style"

        # Verify merge
        assert context["merged"]["success_rate"] == 0.85
        assert context["merged"]["user_style"]["tone"] == "professional"


class TestCascadeDelete:
    """Test GDPR Art. 17 cascade delete."""

    def test_delete_user_context_complete(self):
        """Cascade delete all context for user (base + layers)."""
        model = HybridContextModel("tenant-1")

        # Create two sessions with context
        model.snapshot_base_context(
            user_id="user-1",
            session_id="s1",
            decisions=[],
            profile={},
            success_rate=0.5,
            attention_budget=1000,
        )
        model.snapshot_base_context(
            user_id="user-1",
            session_id="s2",
            decisions=[],
            profile={},
            success_rate=0.6,
            attention_budget=1500,
        )

        # Inject layers for user-1
        model.inject_layer(
            user_id="user-1",
            layer_name="user_style",
            data={"tone": "verbose"},
            lom="test.py:L1",
        )
        model.inject_layer(
            user_id="user-1",
            layer_name="session_context",
            data={"depth": 5},
            lom="test.py:L2",
        )

        # Verify data exists
        assert len([k for k in model.base_snapshots if k.startswith("user-1")]) == 2
        assert len(model.injected_layers.get("user-1", [])) == 2

        # Delete
        result = model.delete_user_context("user-1")

        # Verify deletion counts and verification
        assert result.deleted_bases == 2
        assert result.deleted_layers == 2
        assert result.total == 4
        assert result.verification_complete is True
        assert len(result.errors) == 0

        # Verify data gone
        assert len([k for k in model.base_snapshots if k.startswith("user-1")]) == 0
        assert len(model.injected_layers.get("user-1", [])) == 0

    def test_delete_user_idempotent(self):
        """Cascade delete is idempotent (second call returns 0)."""
        model = HybridContextModel("tenant-1")

        # Create and delete
        model.snapshot_base_context(
            user_id="user-1",
            session_id="s1",
            decisions=[],
            profile={},
            success_rate=0.5,
            attention_budget=1000,
        )

        result1 = model.delete_user_context("user-1")
        assert result1.total > 0

        # Delete again (idempotent)
        result2 = model.delete_user_context("user-1")
        assert result2.total == 0
        assert result2.verification_complete is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class MockDecisionAdapter:
    """Mock Phase 3 DecisionHistoryStore adapter."""

    def __init__(self, decisions: list[dict]):
        self.decisions = decisions

    def get_recent_decisions(self, user_id: str, tenant_id: str, limit: int = 10) -> list[dict]:
        """Return mock decisions."""
        return self.decisions[:limit]


class MockOutcomeAdapter:
    """Mock Phase 3 OutcomeFeedbackStore adapter."""

    def __init__(self, success_rate: float):
        self.success_rate = success_rate

    def get_success_rate(self, user_id: str, tenant_id: str) -> float:
        """Return mock success rate."""
        return self.success_rate


class MockProfileAdapter:
    """Mock Phase 3 UserProfileManager adapter."""

    def __init__(self, profile: dict):
        self.profile = profile

    def get_profile(self, user_id: str, tenant_id: str) -> dict:
        """Return mock profile."""
        return self.profile


class TestPhase3Integration:
    """Integration tests with mocked Phase 3 adapters."""

    def test_snapshot_with_phase3_adapters(self):
        """Snapshot using Phase 3 adapters (k=4 integration)."""
        # Create mock adapters
        decisions_adapter = MockDecisionAdapter([{"d_id": "d1", "choice": "a"}])
        outcome_adapter = MockOutcomeAdapter(0.85)
        profile_adapter = MockProfileAdapter({"style": "verbose"})

        # Initialize model with adapters
        model = HybridContextModel(
            "tenant-1",
            decision_adapter=decisions_adapter,
            outcome_adapter=outcome_adapter,
            profile_adapter=profile_adapter,
        )

        # Snapshot should still work via direct calls
        base_hash = model.snapshot_base_context(
            user_id="user-1",
            session_id="s1",
            decisions=decisions_adapter.get_recent_decisions("user-1", "tenant-1"),
            profile=profile_adapter.get_profile("user-1", "tenant-1"),
            success_rate=outcome_adapter.get_success_rate("user-1", "tenant-1"),
            attention_budget=1000,
        )

        # Verify snapshot captured Phase 3 data
        assert base_hash is not None
        base = model.base_snapshots["user-1:s1"]
        assert base.success_rate == 0.85
        assert base.user_profile == {"style": "verbose"}
        assert len(base.recent_decisions) == 1

    def test_cascade_delete_with_adapters(self):
        """Cascade delete with adapters initialized (k=4 integration)."""
        model = HybridContextModel(
            "tenant-1",
            decision_adapter=MockDecisionAdapter([]),
            outcome_adapter=MockOutcomeAdapter(0.5),
            profile_adapter=MockProfileAdapter({}),
        )

        # Create context
        model.snapshot_base_context(
            user_id="user-1",
            session_id="s1",
            decisions=[],
            profile={},
            success_rate=0.5,
            attention_budget=1000,
        )
        model.inject_layer(
            user_id="user-1",
            layer_name="test_layer",
            data={"v": 1},
            lom="test.py:L1",
        )

        # Delete
        result = model.delete_user_context("user-1")

        # Verify
        assert result.verification_complete is True
        assert result.total > 0
