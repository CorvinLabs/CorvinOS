"""E2E Tests: Hybrid Context Model with Real Claude API.

This test suite verifies end-to-end hybrid context injection into real LLM calls.
Tests three quality modes (QUALITY_MAX, BALANCED, EFFICIENCY_MAX) with real prompts.

Setup: Set ANTHROPIC_API_KEY environment variable.
Run: pytest tests/e2e/test_hybrid_context_llm_e2e.py -v -m e2e
"""

import pytest
import os
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.learning.hybrid_context import (
    HybridContextModel,
    ImmutableContextBase,
)

# Import Anthropic client (optional, for real LLM tests)
try:
    from anthropic import Anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False


TENANT = "_default"  # valid tenant id — a hyphen ("tenant-default") is rejected


@pytest.fixture(autouse=True)
def sandbox(tmp_path: Path, monkeypatch):
    """N-04: the model audits through the REAL core writer (fail-closed), so run
    against a temp chain (``VOICE_AUDIT_PATH``), the matching tenant context and a
    temp ``CORVIN_HOME`` — never the live install."""
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(tmp_path / "chain" / "audit.jsonl"))
    monkeypatch.setenv("CORVIN_TENANT_ID", TENANT)
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "home"))
    return tmp_path


# Metrics collector
class E2EMetricsCollector:
    """Collect E2E test metrics for analysis."""

    def __init__(self, output_path: str = "outputs/phase4_e2e_metrics.jsonl"):
        self.output_path = Path(output_path)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, metric: dict):
        """Record a metric (immutable append)."""
        metric["timestamp"] = datetime.utcnow().isoformat()
        with open(self.output_path, "a") as f:
            f.write(json.dumps(metric) + "\n")

    def aggregate(self) -> dict:
        """Aggregate collected metrics."""
        metrics = []
        if self.output_path.exists():
            with open(self.output_path) as f:
                metrics = [json.loads(line) for line in f if line.strip()]

        if not metrics:
            return {
                "total_runs": 0,
                "avg_context_tokens": 0,
                "avg_latency_ms": 0,
                "accuracy_rate": 0,
                "mode_distribution": {},
            }

        return {
            "total_runs": len(metrics),
            "avg_context_tokens": sum(
                m.get("context_tokens", 0) for m in metrics
            ) / len(metrics),
            "avg_latency_ms": sum(m.get("latency_ms", 0) for m in metrics) / len(metrics),
            "accuracy_rate": sum(
                1 for m in metrics if m.get("accuracy") == "PASSED"
            ) / len(metrics) if metrics else 0,
            "mode_distribution": self._count_modes(metrics),
        }

    @staticmethod
    def _count_modes(metrics):
        """Count mode distribution."""
        modes = {}
        for m in metrics:
            mode = m.get("quality_mode", "unknown")
            modes[mode] = modes.get(mode, 0) + 1
        return modes


@pytest.fixture
def metrics_collector(tmp_path):
    """Provide metrics collector (writes under the test's tmp dir, not the repo)."""
    return E2EMetricsCollector(str(tmp_path / "outputs" / "phase4_e2e_metrics.jsonl"))


@pytest.fixture
def hybrid_context():
    """Provide hybrid context model (valid, sandboxed tenant)."""
    return HybridContextModel(TENANT)


@pytest.fixture
def anthropic_client():
    """Provide Anthropic client if API key is available."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        pytest.skip("ANTHROPIC_API_KEY not set")
    return Anthropic(api_key=api_key)


class TestHybridContextQualityMax:
    """E2E Tests: QUALITY_MAX mode (comprehensive context)."""

    @pytest.mark.e2e
    @pytest.mark.skipif(not HAS_ANTHROPIC, reason="Anthropic SDK not installed")
    def test_compliance_task_quality_max(
        self, hybrid_context, anthropic_client, metrics_collector
    ):
        """E2E: Compliance task with QUALITY_MAX mode (all context injected)."""
        # Step 1: Build hybrid context
        decisions = [
            {"decision_id": "d1", "choice": "enforce_gdpr", "outcome": "positive"},
            {"decision_id": "d2", "choice": "audit_enabled", "outcome": "positive"},
        ]
        profile = {"style": "verbose", "confidence_threshold": 0.95}
        success_rate = 0.92
        attention_budget = 8000

        base_hash = hybrid_context.snapshot_base_context(
            user_id="user-compliance",
            session_id="sess-compliance-1",
            decisions=decisions,
            profile=profile,
            success_rate=success_rate,
            attention_budget=attention_budget,
        )

        # Step 2: Inject context layers
        hybrid_context.inject_layer(
            user_id="user-compliance",
            layer_name="user_style",
            data={
                "preference": "legal_accuracy",
                "tone": "formal",
                "detail_level": 5,
            },
            lom="test_hybrid_context_llm_e2e.py:L88:test_compliance_quality_max",
        )

        hybrid_context.inject_layer(
            user_id="user-compliance",
            layer_name="session_context",
            data={
                "conversation_depth": 3,
                "context_focus": "GDPR compliance",
                "recent_topics": ["audit_trails", "consent", "erasure"],
            },
            lom="test_hybrid_context_llm_e2e.py:L100:test_compliance_quality_max",
        )

        # Step 3: Get merged context
        context = hybrid_context.get_context(
            user_id="user-compliance", session_id="sess-compliance-1"
        )

        # Step 4: Prepare prompt with context
        context_str = self._serialize_context(context)
        system_prompt = (
            f"You are a compliance expert. Answer the following based on "
            f"your knowledge of EU GDPR and privacy law.\n\n{context_str}"
        )

        user_prompt = (
            "Is ADR-0555 (Hybrid Context Model) compliant with GDPR Art. 5 (data minimization)? "
            "Explain your reasoning in 3-5 sentences."
        )

        # Step 5: Call real Claude API
        start_time = time.time()
        response = anthropic_client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=500,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        latency_ms = (time.time() - start_time) * 1000

        # Step 6: Validate response
        response_text = response.content[0].text
        assert len(response_text) > 100, "Response too short"
        assert (
            "GDPR" in response_text or "minimization" in response_text.lower()
        ), "Response should mention GDPR/minimization"

        # Step 7: Record metrics
        metric = {
            "test_name": "compliance_quality_max",
            "quality_mode": "QUALITY_MAX",
            "task_type": "compliance",
            "context_tokens": len(context_str.split()),
            "latency_ms": latency_ms,
            "response_length": len(response_text),
            "accuracy": "PASSED",
            "model": "claude-3-5-sonnet-20241022",
        }
        metrics_collector.record(metric)

    @pytest.mark.e2e
    @pytest.mark.skipif(not HAS_ANTHROPIC, reason="Anthropic SDK not installed")
    def test_governance_task_quality_max(
        self, hybrid_context, anthropic_client, metrics_collector
    ):
        """E2E: Governance task with QUALITY_MAX mode."""
        # Build context
        decisions = [
            {"decision_id": "g1", "choice": "multi_tier_review", "outcome": "positive"},
        ]
        profile = {"style": "detail_oriented", "confidence_threshold": 0.90}

        hybrid_context.snapshot_base_context(
            user_id="user-governance",
            session_id="sess-gov-1",
            decisions=decisions,
            profile=profile,
            success_rate=0.88,
            attention_budget=7000,
        )

        hybrid_context.inject_layer(
            user_id="user-governance",
            layer_name="user_style",
            data={"tone": "analytical", "evidence_required": True},
            lom="test_hybrid_context_llm_e2e.py:L160:test_governance_quality_max",
        )

        context = hybrid_context.get_context(
            user_id="user-governance", session_id="sess-gov-1"
        )

        context_str = self._serialize_context(context)
        system_prompt = (
            f"You are a governance expert. Provide structured analysis.\n\n{context_str}"
        )

        user_prompt = (
            "What are 3 key governance checkpoints for Phase 4 deployment? "
            "Use bullet points."
        )

        start_time = time.time()
        response = anthropic_client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=500,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        latency_ms = (time.time() - start_time) * 1000

        response_text = response.content[0].text
        assert "•" in response_text or "-" in response_text, "Should use bullet points"

        metric = {
            "test_name": "governance_quality_max",
            "quality_mode": "QUALITY_MAX",
            "task_type": "governance",
            "context_tokens": len(context_str.split()),
            "latency_ms": latency_ms,
            "response_length": len(response_text),
            "accuracy": "PASSED",
            "model": "claude-3-5-sonnet-20241022",
        }
        metrics_collector.record(metric)

    @staticmethod
    def _serialize_context(context: dict) -> str:
        """Convert hybrid context to prompt string."""
        lines = []
        lines.append("## Hybrid Context (Phase 4)")

        base = context.get("base", {})
        if base:
            lines.append(f"Base decisions: {base.get('recent_decisions', [])}")
            lines.append(f"Success rate: {base.get('success_rate', 0.5):.1%}")
            lines.append(f"Attention budget: {base.get('attention_budget_remaining', 0)} tokens")

        for layer in context.get("layers", []):
            layer_name = layer.get("layer_name", "unknown")
            data = layer.get("data", {})
            lines.append(f"{layer_name}: {json.dumps(data)}")

        return "\n".join(lines)


class TestHybridContextBalanced:
    """E2E Tests: BALANCED mode (moderate context)."""

    @pytest.mark.e2e
    @pytest.mark.skipif(not HAS_ANTHROPIC, reason="Anthropic SDK not installed")
    def test_routing_task_balanced(
        self, hybrid_context, anthropic_client, metrics_collector
    ):
        """E2E: Routing task with BALANCED mode."""
        decisions = [
            {"decision_id": "r1", "choice": "route_to_compliance", "outcome": "neutral"},
        ]
        profile = {"style": "concise", "confidence_threshold": 0.75}

        hybrid_context.snapshot_base_context(
            user_id="user-routing",
            session_id="sess-routing-1",
            decisions=decisions,
            profile=profile,
            success_rate=0.80,
            attention_budget=4000,  # Smaller budget for BALANCED
        )

        hybrid_context.inject_layer(
            user_id="user-routing",
            layer_name="user_style",
            data={"tone": "neutral", "routing_preference": "deterministic"},
            lom="test_hybrid_context_llm_e2e.py:L240:test_routing_balanced",
        )

        context = hybrid_context.get_context(
            user_id="user-routing", session_id="sess-routing-1"
        )

        context_str = TestHybridContextQualityMax._serialize_context(context)
        system_prompt = f"You are a request router. Route to the appropriate team.\n\n{context_str}"
        user_prompt = (
            "A user reports a GDPR data deletion request. Which team should handle this?"
        )

        start_time = time.time()
        response = anthropic_client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=300,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        latency_ms = (time.time() - start_time) * 1000

        response_text = response.content[0].text
        assert any(
            team in response_text.lower()
            for team in ["legal", "privacy", "compliance", "data"]
        ), "Should mention relevant team"

        metric = {
            "test_name": "routing_balanced",
            "quality_mode": "BALANCED",
            "task_type": "routing",
            "context_tokens": len(context_str.split()),
            "latency_ms": latency_ms,
            "response_length": len(response_text),
            "accuracy": "PASSED",
            "model": "claude-3-5-sonnet-20241022",
        }
        metrics_collector.record(metric)


class TestHybridContextEfficiencyMax:
    """E2E Tests: EFFICIENCY_MAX mode (minimal context)."""

    @pytest.mark.e2e
    @pytest.mark.skipif(not HAS_ANTHROPIC, reason="Anthropic SDK not installed")
    def test_bulk_classification_efficiency_max(
        self, hybrid_context, anthropic_client, metrics_collector
    ):
        """E2E: Bulk classification task with EFFICIENCY_MAX mode (minimal context)."""
        decisions = []  # Minimal decisions
        profile = {}  # Minimal profile

        hybrid_context.snapshot_base_context(
            user_id="user-bulk",
            session_id="sess-bulk-1",
            decisions=decisions,
            profile=profile,
            success_rate=0.50,
            attention_budget=1000,  # Very small budget
        )

        # For EFFICIENCY_MAX, skip extra layers
        context = hybrid_context.get_context(
            user_id="user-bulk", session_id="sess-bulk-1"
        )

        context_str = TestHybridContextQualityMax._serialize_context(context)

        # Minimal system prompt (efficiency)
        system_prompt = (
            f"You are a text classifier. Classify the input.\n\n{context_str}"
        )
        user_prompt = "Classify: 'The data protection law requires consent.' (compliance/technical/other)"

        start_time = time.time()
        response = anthropic_client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=100,  # Very small
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        latency_ms = (time.time() - start_time) * 1000

        response_text = response.content[0].text
        assert "compliance" in response_text.lower(), "Should classify as compliance"

        metric = {
            "test_name": "bulk_classification_efficiency",
            "quality_mode": "EFFICIENCY_MAX",
            "task_type": "bulk_classification",
            "context_tokens": len(context_str.split()),
            "latency_ms": latency_ms,
            "response_length": len(response_text),
            "accuracy": "PASSED",
            "model": "claude-3-5-sonnet-20241022",
        }
        metrics_collector.record(metric)


class TestContextMergeEdgeCases:
    """E2E Tests: Edge cases in context merge."""

    @pytest.mark.e2e
    def test_merge_with_pii_layer_dropped(self, hybrid_context, metrics_collector):
        """E2E: Merge drops PII layer, continues with clean context."""
        hybrid_context.snapshot_base_context(
            user_id="user-pii",
            session_id="sess-pii-1",
            decisions=[{"d": "test"}],
            profile={},
            success_rate=0.5,
            attention_budget=1000,
        )

        # Inject good layer
        hybrid_context.inject_layer(
            user_id="user-pii",
            layer_name="good_layer",
            data={"status": "ok"},
            lom="test_hybrid_context_llm_e2e.py:L360:test_merge_pii_dropped",
        )

        # Try to inject PII (will fail silently in merge)
        try:
            hybrid_context.inject_layer(
                user_id="user-pii",
                layer_name="bad_layer",
                data={"email": "user@example.com"},
                lom="test_hybrid_context_llm_e2e.py:L367:test_merge_pii_dropped",
            )
        except ValueError:
            # Expected — injection fails
            pass

        context = hybrid_context.get_context(
            user_id="user-pii", session_id="sess-pii-1"
        )

        # Verify good layer present, bad not present
        assert "good_layer" in context["merged"]
        assert "bad_layer" not in context["merged"]

        metric = {
            "test_name": "merge_pii_layer_dropped",
            "quality_mode": "MIXED",
            "task_type": "edge_case",
            "context_tokens": 100,
            "latency_ms": 10,
            "response_length": 0,
            "accuracy": "PASSED",
        }
        metrics_collector.record(metric)

    @pytest.mark.e2e
    def test_cascade_delete_e2e(self, hybrid_context, metrics_collector):
        """E2E: GDPR cascade delete removes all context."""
        user_id = "user-delete-test"
        hybrid_context.snapshot_base_context(
            user_id=user_id,
            session_id="sess-1",
            decisions=[],
            profile={},
            success_rate=0.5,
            attention_budget=1000,
        )

        hybrid_context.inject_layer(
            user_id=user_id,
            layer_name="layer1",
            data={"v": 1},
            lom="test_hybrid_context_llm_e2e.py:L410:test_cascade_delete_e2e",
        )

        # Delete
        result = hybrid_context.delete_user_context(user_id)

        assert result.verification_complete is True
        assert result.deleted_bases > 0
        assert result.deleted_layers > 0

        # Verify deletion
        with pytest.raises(ValueError):
            hybrid_context.get_context(user_id=user_id, session_id="sess-1")

        metric = {
            "test_name": "cascade_delete_e2e",
            "quality_mode": "GDPR",
            "task_type": "deletion",
            "context_tokens": 0,
            "latency_ms": 5,
            "response_length": 0,
            "accuracy": "PASSED",
        }
        metrics_collector.record(metric)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-m", "e2e"])
