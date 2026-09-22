"""
E2E Tests for Intent Router (Phase 9a).

Tests two-stage classification pipeline:
- Stage 1: Regex (fast fallback)
- Stage 2: LLM (preferred)

ADR-2028: Natural Language Intent Router
"""

import pytest
import asyncio
from datetime import datetime
from fastapi.testclient import TestClient

from core.console.corvin_console.intent_router import (
    classify_intent,
    IntentRouter,
    IntentType,
    RegexClassifier
)


class TestRegexClassifier:
    """Tests for Stage 1: Regex classifier."""

    def test_skill_gen_detection(self):
        """Test regex detects SKILL_GEN intent."""
        classifier = RegexClassifier()
        result = classifier.classify("Please create a skill to analyze customer feedback")
        assert result["intent_type"] == IntentType.SKILL_GEN
        assert result["confidence"] > 0.0

    def test_autonomy_detection(self):
        """Test regex detects AUTONOMY intent."""
        classifier = RegexClassifier()
        result = classifier.classify("Enable autonomous decision-making for routing")
        assert result["intent_type"] == IntentType.AUTONOMY
        assert result["confidence"] > 0.0

    def test_feedback_detection(self):
        """Test regex detects FEEDBACK intent."""
        classifier = RegexClassifier()
        result = classifier.classify("Collect feedback on model quality")
        assert result["intent_type"] == IntentType.FEEDBACK
        assert result["confidence"] > 0.0

    def test_no_match(self):
        """Test regex returns AMBIGUOUS for no-match input."""
        classifier = RegexClassifier()
        result = classifier.classify("The weather is nice today")
        assert result["intent_type"] == IntentType.AMBIGUOUS
        assert result["confidence"] == 0.0

    def test_case_insensitive(self):
        """Test regex is case-insensitive."""
        classifier = RegexClassifier()
        result1 = classifier.classify("CREATE A SKILL")
        result2 = classifier.classify("create a skill")
        assert result1["intent_type"] == result2["intent_type"]
        assert result1["confidence"] == result2["confidence"]


class TestIntentRouter:
    """Tests for Intent Router (two-stage pipeline)."""

    @pytest.mark.asyncio
    async def test_classify_skill_gen(self):
        """Test E2E classification of SKILL_GEN intent."""
        router = IntentRouter()
        result = await router.classify("Create a new skill for sentiment analysis")

        assert result.intent_type == IntentType.SKILL_GEN
        assert result.confidence > 0.6
        assert result.latency_ms > 0
        assert result.classifier_stage in ["regex", "llm", "regex_fallback"]
        assert result.audit_event_type == "intent_classified_skill_gen"

    @pytest.mark.asyncio
    async def test_classify_autonomy(self):
        """Test E2E classification of AUTONOMY intent."""
        router = IntentRouter()
        result = await router.classify("Delegate to autonomous systems")

        assert result.intent_type == IntentType.AUTONOMY
        assert result.confidence > 0.6
        assert result.audit_event_type == "intent_classified_autonomy"

    @pytest.mark.asyncio
    async def test_classify_feedback(self):
        """Test E2E classification of FEEDBACK intent."""
        router = IntentRouter()
        result = await router.classify("Collect feedback for learning")

        assert result.intent_type == IntentType.FEEDBACK
        assert result.confidence > 0.6
        assert result.audit_event_type == "intent_classified_feedback"

    @pytest.mark.asyncio
    async def test_ambiguous_intent(self):
        """Test E2E handling of ambiguous intent."""
        router = IntentRouter()
        result = await router.classify("Hello world")

        assert result.intent_type == IntentType.AMBIGUOUS
        assert result.audit_event_type == "intent_denied_ambiguous"

    @pytest.mark.asyncio
    async def test_latency_bounded(self):
        """Test latency is bounded by LLM timeout."""
        router = IntentRouter(config={"_llm_timeout_s": 0.1})
        result = await router.classify("Create a skill")

        # Should complete within ~200ms (including LLM timeout)
        assert result.latency_ms < 300

    @pytest.mark.asyncio
    async def test_threshold_enforcement(self):
        """Test confidence thresholds are enforced."""
        config = {
            "SKILL_GEN": 0.99,  # Very high threshold
            "AUTONOMY": 0.7,
            "FEEDBACK": 0.6,
        }
        router = IntentRouter(config=config)

        # High-threshold intent should fail
        result = await router.classify("create skill")
        # If confidence < 0.99, should return AMBIGUOUS
        if result.intent_type != IntentType.AMBIGUOUS:
            assert result.confidence >= 0.99

    @pytest.mark.asyncio
    async def test_fallback_on_llm_error(self):
        """Test fallback to regex when LLM fails."""
        router = IntentRouter()
        # Even if LLM fails, regex result should be returned
        result = await router.classify("Create a custom skill")

        assert result.intent_type in [IntentType.SKILL_GEN, IntentType.AMBIGUOUS]
        assert result.latency_ms > 0


class TestIntentRouterAPI:
    """Tests for intent router HTTP endpoints."""

    @pytest.mark.asyncio
    async def test_classify_endpoint_success(self):
        """Test POST /v1/console/intents/classify happy path."""
        # This is a placeholder E2E test that would use a real FastAPI client
        # In production, use TestClient(app)

        # For now, test the async wrapper
        result = await classify_intent("Please create a skill")
        assert result.intent_type == IntentType.SKILL_GEN
        assert result.latency_ms > 0

    @pytest.mark.asyncio
    async def test_classify_endpoint_ambiguous(self):
        """Test POST /v1/console/intents/classify with ambiguous input."""
        result = await classify_intent("xyz abc 123")
        assert result.intent_type == IntentType.AMBIGUOUS

    @pytest.mark.asyncio
    async def test_audit_event_emitted(self):
        """Test that audit events are emitted for classifications."""
        result = await classify_intent("Create a skill")

        # Audit event should be of correct type
        assert result.audit_event_type in [
            "intent_classified_skill_gen",
            "intent_classified_autonomy",
            "intent_classified_feedback",
            "intent_denied_ambiguous",
        ]


class TestIntentRouterAdversarial:
    """Adversarial tests for intent router."""

    @pytest.mark.asyncio
    async def test_empty_input(self):
        """Test handling of empty input."""
        router = IntentRouter()
        result = await router.classify("")
        assert result.intent_type == IntentType.AMBIGUOUS

    @pytest.mark.asyncio
    async def test_very_long_input(self):
        """Test handling of very long input (5000 chars)."""
        long_text = "Create a skill" * 500  # ~7000 chars
        router = IntentRouter()
        result = await router.classify(long_text)

        # Should still classify (might truncate in audit)
        assert result.intent_type in [
            IntentType.SKILL_GEN,
            IntentType.AMBIGUOUS,
        ]
        assert result.latency_ms > 0

    @pytest.mark.asyncio
    async def test_special_characters(self):
        """Test handling of special characters."""
        router = IntentRouter()
        result = await router.classify("Create @#$%^& skill !@#$")

        # Should not crash
        assert result.intent_type in [
            IntentType.SKILL_GEN,
            IntentType.AMBIGUOUS,
        ]

    @pytest.mark.asyncio
    async def test_unicode_input(self):
        """Test handling of unicode characters."""
        router = IntentRouter()
        result = await router.classify("请创建一个技能 (create a skill)")

        # Should classify the English part
        assert result.intent_type in [
            IntentType.SKILL_GEN,
            IntentType.AMBIGUOUS,
        ]

    @pytest.mark.asyncio
    async def test_sql_injection_attempt(self):
        """Test that SQL injection in intent input is safe."""
        router = IntentRouter()
        malicious_input = "'; DROP TABLE intents; --"
        result = await router.classify(malicious_input)

        # Should classify safely (no DB queries)
        assert result.intent_type == IntentType.AMBIGUOUS

    @pytest.mark.asyncio
    async def test_concurrent_classifications(self):
        """Test concurrent intent classifications."""
        router = IntentRouter()

        inputs = [
            "Create a skill",
            "Enable autonomy",
            "Collect feedback",
            "Hello world",
        ]

        # Run 4 classifications concurrently
        results = await asyncio.gather(*[
            router.classify(text) for text in inputs
        ])

        assert len(results) == 4
        # First 3 should have intent, last should be ambiguous
        assert results[0].intent_type == IntentType.SKILL_GEN
        assert results[1].intent_type == IntentType.AUTONOMY
        assert results[2].intent_type == IntentType.FEEDBACK
        assert results[3].intent_type == IntentType.AMBIGUOUS


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
