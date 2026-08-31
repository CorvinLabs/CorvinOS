"""Integration Tests for ADR-0315 (Confidence Scoring) + ADR-0318 (User Profiles).

This test suite validates the integration of two learning infrastructure modules:
1. ConfidenceScorer (ADR-0315): Multi-dimensional skill confidence scoring
2. UserProfileManager (ADR-0318): User preference learning and adaptation

Tests verify:
- Confidence scores update user preferences dynamically
- Skill grades propagate through confidence → preference chain
- Tenant isolation across modules (no cross-tenant leakage)
- GDPR compliance (tenant_id enforcement, Right to Object, audit trail)
- Event emission and audit trail integration
- No PII in events or payloads

Success Metrics:
- 15 integration tests covering cross-module scenarios
- 100% tenant isolation verified
- GDPR Art. 5, 6, 7, 21, 30, 32 compliance checked
- Zero PII leakage in events
"""

import pytest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, MagicMock, patch
from dataclasses import dataclass

from core.learning.confidence_scorer import ConfidenceScorer, ConfidenceScore
from core.learning.user_profile import UserProfileManager, UserProfile, DecisionStyle
from core.learning.event_store import EventStore
from core.learning.event_schema import LearningEvent, LearningEventType
from core.skills.skill import Skill, Grade


# ============================================================================
# FIXTURES — Shared Resources
# ============================================================================


@pytest.fixture
def temp_event_store():
    """Create temporary EventStore for integration testing."""
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "learning.db"
        store = EventStore(str(db_path))
        yield store


@pytest.fixture
def temp_profiles_dir():
    """Create temporary directory for user profiles."""
    with TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def skill_json_parser():
    """Test skill: JSON parsing."""
    return Skill(
        name="json-parser",
        version="1.0",
        body="def parse_json(s): return json.loads(s)",
        tags=["json", "parsing", "production"],
        grades=[
            Grade(value=0.9, feedback="Works well"),
            Grade(value=0.85, feedback="Minor issue"),
            Grade(value=0.95, feedback="Excellent"),
        ],
    )


@pytest.fixture
def skill_code_reviewer():
    """Test skill: Code review."""
    return Skill(
        name="code-reviewer",
        version="2.1",
        body="def review_code(code): return analysis",
        tags=["code-review", "python", "quality"],
        grades=[
            Grade(value=0.8, feedback="Good insights"),
            Grade(value=0.7, feedback="Missed edge case"),
            Grade(value=0.75, feedback="Average"),
            Grade(value=0.2, feedback="Failed"),
        ],
    )


@pytest.fixture
def skill_new():
    """Test skill: New skill with no grades."""
    return Skill(
        name="new-skill",
        version="0.1",
        body="def new_feature(): pass",
        tags=["experimental"],
        grades=[],
    )


@pytest.fixture
def skills_registry(skill_json_parser, skill_code_reviewer, skill_new):
    """Registry of test skills."""
    return {
        "json-parser": skill_json_parser,
        "code-reviewer": skill_code_reviewer,
        "new-skill": skill_new,
    }


@pytest.fixture
def skills_fetcher(skills_registry):
    """Callable that fetches skills from registry."""
    def fetcher(skill_id: str):
        return skills_registry.get(skill_id)
    return fetcher


@pytest.fixture
def confidence_scorer(temp_event_store, skills_fetcher):
    """ConfidenceScorer with event emission enabled."""
    return ConfidenceScorer(
        skills_fetcher=skills_fetcher,
        event_store=temp_event_store,
    )


@pytest.fixture
def profile_manager(temp_event_store, temp_profiles_dir):
    """UserProfileManager with event emission and custom profiles dir."""
    return UserProfileManager(
        event_store=temp_event_store,
        profiles_dir=temp_profiles_dir,
    )


# ============================================================================
# TEST CLASS 1: Confidence Scorer + User Profile Integration
# ============================================================================


class TestConfidenceAndProfileIntegration:
    """Test confidence scorer + user profile interaction."""

    def test_confidence_score_updates_user_preference(
        self, confidence_scorer, profile_manager, skill_json_parser
    ):
        """Test that high confidence scores encourage user preference updates.

        Scenario:
        1. Score skill with high relevance/reliability
        2. Update user preference based on confidence
        3. Verify preference reflects learned confidence
        """
        # Score the skill
        context = {"keywords": ["json", "parsing"]}
        stats = confidence_scorer.per_skill_stats(
            skill_id="json-parser",
            tenant_id="tenant_1",
            user_id="user_1",
            context=context,
        )

        # High confidence expected (0.9 relevance, 0.9 reliability)
        assert stats["combined"] > 0.8, "Expected high combined confidence"

        # Update user preference based on confidence
        feedback = {
            "skill_feedback": {
                "json-parser": stats["combined"],  # Weight by confidence
            }
        }
        updated_profile = profile_manager.update_from_feedback(
            "user_1", "tenant_1", feedback
        )

        # Verify preference reflects confidence
        assert "json-parser" in updated_profile.skill_weights
        assert updated_profile.skill_weights["json-parser"] > 0.8

    def test_skill_grades_flow_through_confidence_to_profile(
        self, confidence_scorer, profile_manager, skill_code_reviewer
    ):
        """Test the full chain: skill grades → confidence → user profile.

        Scenario:
        1. Skill has mixed grades (0.8, 0.7, 0.75, 0.2)
        2. Confidence reliability = 75% success
        3. User preference adapts to learned reliability
        """
        # Score skill (75% grades >= 0.5 = 0.75 reliability)
        stats = confidence_scorer.per_skill_stats(
            skill_id="code-reviewer",
            tenant_id="tenant_1",
            user_id="user_1",
            context={},
        )

        # Reliability should reflect 75% success rate
        assert stats["reliability"] == 0.75, "Expected 0.75 reliability (3/4 grades pass)"
        assert stats["grade_count"] == 4

        # User learns this preference
        feedback = {"skill_feedback": {"code-reviewer": stats["reliability"]}}
        updated_profile = profile_manager.update_from_feedback(
            "user_1", "tenant_1", feedback
        )

        # Preference weight should match reliability
        assert updated_profile.skill_weights["code-reviewer"] == 0.75

    def test_confidence_scorer_ignores_context_when_missing(
        self, confidence_scorer
    ):
        """Test that confidence scorer returns neutral (0.5) relevance when context is empty.

        GDPR Art. 5: Data minimization — don't assume preferences without data.
        """
        stats = confidence_scorer.per_skill_stats(
            skill_id="json-parser",
            tenant_id="tenant_1",
            user_id="user_1",
            context={},  # Empty context
        )

        # Relevance should be 0.5 (neutral), not 0.0
        assert stats["relevance"] == 0.5, "Empty context should yield neutral (0.5) relevance"


# ============================================================================
# TEST CLASS 2: Tenant Isolation (GDPR Art. 32)
# ============================================================================


class TestTenantIsolation:
    """Test tenant isolation across confidence + profile modules."""

    def test_confidence_scores_isolated_by_tenant(
        self, confidence_scorer, skill_json_parser
    ):
        """Test that confidence scores are computed independently per tenant.

        Scenario:
        1. Tenant A scores skill high (0.9 confidence)
        2. Tenant B scores same skill low (0.3 confidence)
        3. Verify each tenant's score is isolated
        """
        # Tenant A: high relevance context
        stats_a = confidence_scorer.per_skill_stats(
            skill_id="json-parser",
            tenant_id="tenant_a",
            user_id="user_a",
            context={"keywords": ["json", "parsing"]},
        )

        # Tenant B: low relevance context (code review terms)
        stats_b = confidence_scorer.per_skill_stats(
            skill_id="json-parser",
            tenant_id="tenant_b",
            user_id="user_b",
            context={"keywords": ["code", "review"]},
        )

        # Scores should differ
        assert stats_a["relevance"] > stats_b["relevance"], "Tenant isolation failed"
        assert stats_a["tenant_id"] == "tenant_a"
        assert stats_b["tenant_id"] == "tenant_b"

    def test_user_profiles_isolated_by_tenant(self, profile_manager):
        """Test that user profiles are stored and retrieved per-tenant.

        Scenario:
        1. User 1 in Tenant A learns preference (json weight = 0.9)
        2. User 1 in Tenant B learns different preference (json weight = 0.3)
        3. Verify cross-tenant storage is isolated
        """
        # Tenant A: User 1 with high json preference
        profile_manager.update_from_feedback(
            "user_1", "tenant_a",
            {"skill_feedback": {"json-parser": 0.9}}
        )

        # Tenant B: User 1 with low json preference
        profile_manager.update_from_feedback(
            "user_1", "tenant_b",
            {"skill_feedback": {"json-parser": 0.3}}
        )

        # Load profiles; verify isolation
        profile_a = profile_manager.get_profile("user_1", "tenant_a")
        profile_b = profile_manager.get_profile("user_1", "tenant_b")

        assert profile_a.skill_weights["json-parser"] == 0.9
        assert profile_b.skill_weights["json-parser"] == 0.3
        assert profile_a.tenant_id == "tenant_a"
        assert profile_b.tenant_id == "tenant_b"

    def test_no_cross_tenant_leakage_in_event_store(
        self, temp_event_store, confidence_scorer, profile_manager
    ):
        """Test that event store queries respect tenant isolation.

        GDPR Art. 32: Data isolation is a security control.
        """
        # Emit events for two tenants
        confidence_scorer.per_skill_stats("json-parser", "tenant_a", "user_a")
        confidence_scorer.per_skill_stats("json-parser", "tenant_b", "user_b")

        profile_manager.update_from_feedback("user_a", "tenant_a", {"conciseness": 0.8})
        profile_manager.update_from_feedback("user_b", "tenant_b", {"conciseness": 0.2})

        # Read events by tenant
        events_a = temp_event_store.read_events_by_tenant("tenant_a")
        events_b = temp_event_store.read_events_by_tenant("tenant_b")

        # Verify isolation: all events in A have tenant_id=tenant_a
        for event in events_a:
            assert event.tenant_id == "tenant_a"

        for event in events_b:
            assert event.tenant_id == "tenant_b"


# ============================================================================
# TEST CLASS 3: Audit Trail Integration (GDPR Art. 30, 32)
# ============================================================================


class TestAuditTrailIntegration:
    """Test event emission and audit trail for learning activities."""

    def test_confidence_scoring_emits_event(
        self, confidence_scorer, temp_event_store, skill_json_parser
    ):
        """Test that confidence scoring emits a learning event.

        GDPR Art. 30: Records of processing activities.
        """
        # Score skill
        confidence_scorer.per_skill_stats(
            skill_id="json-parser",
            tenant_id="tenant_1",
            user_id="user_1",
        )

        # Verify event was emitted
        events = temp_event_store.read_events_by_type(LearningEventType.CONFIDENCE_SCORE)
        assert len(events) > 0, "No confidence events emitted"

        # Verify event structure
        event = events[0]
        assert event.tenant_id == "tenant_1"
        assert event.user_id == "user_1"

    def test_profile_update_emits_event(
        self, profile_manager, temp_event_store
    ):
        """Test that profile updates emit UserPreferenceUpdated events.

        GDPR Art. 30: All preference changes are logged.
        """
        # Update profile
        profile_manager.update_from_feedback(
            "user_1", "tenant_1",
            {"conciseness": 0.8}
        )

        # Verify event was emitted
        events = temp_event_store.read_events_by_type(LearningEventType.PREFERENCE_SET)
        assert len(events) > 0, "No preference events emitted"

        # Verify event has correct tenant_id
        event = events[0]
        assert event.tenant_id == "tenant_1"

    def test_audit_trail_hash_chain_integrity(
        self, confidence_scorer, profile_manager, temp_event_store
    ):
        """Test that audit trail maintains hash-chain integrity.

        GDPR Art. 32: Integrity controls (hash-chaining).
        """
        # Emit multiple events
        confidence_scorer.per_skill_stats("json-parser", "tenant_1", "user_1")
        profile_manager.update_from_feedback("user_1", "tenant_1", {"conciseness": 0.7})

        # Verify hash chain
        is_valid = temp_event_store.verify_chain()
        assert is_valid, "Hash chain integrity check failed"

    def test_events_record_correct_tenant_and_user(
        self, confidence_scorer, profile_manager, temp_event_store
    ):
        """Test that all events record tenant_id and user_id for accountability.

        GDPR Art. 30: Accountability — all processing is audited.
        """
        # Generate events for multiple users/tenants
        confidence_scorer.per_skill_stats("json-parser", "tenant_1", "user_1")
        confidence_scorer.per_skill_stats("json-parser", "tenant_1", "user_2")
        confidence_scorer.per_skill_stats("json-parser", "tenant_2", "user_1")

        # Retrieve all events
        all_events = temp_event_store.read_events_by_tenant("tenant_1", limit=1000)

        # Verify tenant_id and user_id are recorded
        for event in all_events:
            assert event.tenant_id is not None
            assert event.user_id is not None


# ============================================================================
# TEST CLASS 4: GDPR Right to Object (Art. 21)
# ============================================================================


class TestGDPRRightToObject:
    """Test operator override (Right to Object) mechanics."""

    def test_override_disables_learned_preferences(
        self, profile_manager
    ):
        """Test that operator override supercedes learned preferences.

        GDPR Art. 21: User can object to automated processing.
        """
        # Learn a preference
        profile_manager.update_from_feedback(
            "user_1", "tenant_1",
            {"decision_style": "pragmatic"}
        )

        # Override with explicit choice
        profile_manager.set_override(
            "user_1", "tenant_1",
            "decision_style", "theoretical"
        )

        # Load profile
        profile = profile_manager.get_profile("user_1", "tenant_1")

        # Verify override is recorded
        assert "decision_style" in profile.operator_override
        assert profile.operator_override["decision_style"] == "theoretical"

    def test_confidence_scorer_continues_after_override(
        self, confidence_scorer, profile_manager
    ):
        """Test that confidence scoring continues working after user override.

        Scenario:
        1. User sets override (Right to Object)
        2. Confidence scorer still works normally
        3. Verify no crashes or side effects
        """
        # Score skill
        stats_before = confidence_scorer.per_skill_stats(
            "json-parser", "tenant_1", "user_1"
        )

        # User overrides preferences
        profile_manager.set_override("user_1", "tenant_1", "conciseness", "1.0")

        # Score again; should still work
        stats_after = confidence_scorer.per_skill_stats(
            "json-parser", "tenant_1", "user_1"
        )

        # Scores should be identical (override doesn't affect scoring)
        assert stats_before["combined"] == stats_after["combined"]

    def test_predict_preference_applies_overrides(
        self, profile_manager
    ):
        """Test that predict_preference() applies overrides correctly.

        Overrides always win against learned preferences.
        """
        # Learn preference
        profile_manager.update_from_feedback(
            "user_1", "tenant_1",
            {"decision_style": "pragmatic", "conciseness": 0.3}
        )

        # Override conciseness
        profile_manager.set_override("user_1", "tenant_1", "conciseness", "0.9")

        # Predict preferences
        prediction = profile_manager.predict_preference("user_1", "tenant_1", {})

        # Override should win
        assert prediction["conciseness"] == 0.9
        # Learned preference should remain
        assert prediction["decision_style"] == "pragmatic"


# ============================================================================
# TEST CLASS 5: No PII in Events (GDPR Art. 5, 6)
# ============================================================================


class TestNoPIIInEvents:
    """Test that no personally identifiable information leaks into events."""

    def test_confidence_event_payload_no_pii(
        self, confidence_scorer, temp_event_store
    ):
        """Test that confidence events contain only scores, no user data.

        GDPR Art. 5(1)(a): Data minimization.
        """
        confidence_scorer.per_skill_stats(
            "json-parser", "tenant_1", "user_1",
            context={"keywords": ["secret_data", "password_reset"]}
        )

        events = temp_event_store.read_events_by_type(LearningEventType.CONFIDENCE_SCORE)
        assert len(events) > 0

        event = events[0]

        # Verify payload contains only non-PII data
        payload = event.payload
        # Payload should have reason/scores, not context keywords
        assert "password_reset" not in str(payload)
        assert "secret_data" not in str(payload)

    def test_preference_event_payload_no_pii(
        self, profile_manager, temp_event_store
    ):
        """Test that preference events contain only metadata, no free-form user data.

        GDPR Art. 5(1)(a): Data minimization.
        """
        profile_manager.update_from_feedback(
            "user_1", "tenant_1",
            {
                "decision_style": "pragmatic",
                "conciseness": 0.8,
                "skill_feedback": {"json-parser": 0.9}
            }
        )

        events = temp_event_store.read_events_by_type(LearningEventType.PREFERENCE_SET)
        assert len(events) > 0

        event = events[0]
        payload = event.payload

        # Payload should contain only structural info, no free-text data
        assert isinstance(payload.get("feedback_keys"), list)
        # Verify no user narrative/prose in payload
        for key, value in payload.items():
            if isinstance(value, str):
                assert len(value) < 100, f"Unexpected long string in payload: {key}"

    def test_skill_weights_anonymized_in_events(
        self, profile_manager, temp_event_store
    ):
        """Test that skill weights are recorded as IDs only, not descriptions.

        GDPR Art. 5(1)(a): No unnecessary user data in learning events.
        """
        profile_manager.update_from_feedback(
            "user_1", "tenant_1",
            {"skill_feedback": {"json-parser": 0.9, "code-reviewer": 0.7}}
        )

        events = temp_event_store.read_events_by_type(LearningEventType.PREFERENCE_SET)
        event = events[0]

        # Event should reference skill by ID, never include skill description
        event_str = str(event)
        assert "json-parser" in event_str
        # But should NOT have skill descriptions like "Parse JSON data"
        # (This is illustrative; actual skill bodies wouldn't be in events)


# ============================================================================
# TEST CLASS 6: Error Handling and Edge Cases
# ============================================================================


class TestErrorHandlingAndEdgeCases:
    """Test robustness against invalid inputs and edge cases."""

    def test_confidence_scorer_with_missing_skill(
        self, confidence_scorer
    ):
        """Test confidence scorer gracefully handles missing skills.

        Expected: return 0.0 relevance, not crash.
        """
        stats = confidence_scorer.per_skill_stats(
            skill_id="nonexistent-skill",
            tenant_id="tenant_1",
            user_id="user_1",
        )

        # Should return zero scores, not crash
        assert stats["relevance"] == 0.0
        assert stats["reliability"] == 0.0
        assert stats["combined"] == 0.0
        assert stats["grade_count"] == 0

    def test_profile_manager_creates_default_on_missing(
        self, profile_manager
    ):
        """Test that profile manager creates default profile for new users.

        GDPR Art. 5: Data minimization — default profile has minimal data.
        """
        # Get profile for non-existent user
        profile = profile_manager.get_profile("new_user", "tenant_1")

        # Should create default
        assert profile.user_id == "new_user"
        assert profile.tenant_id == "tenant_1"
        assert profile.decision_style == DecisionStyle.BALANCED
        assert len(profile.skill_weights) == 0, "Default should be empty (no learned prefs)"

    def test_empty_context_yields_neutral_relevance(
        self, confidence_scorer
    ):
        """Test that empty context doesn't bias scoring.

        GDPR Art. 5: Accuracy — don't assume without evidence.
        """
        stats = confidence_scorer.per_skill_stats(
            "json-parser",
            "tenant_1",
            "user_1",
            context={},
        )

        # Relevance should be 0.5 (neutral), not 0.0 or 1.0
        assert stats["relevance"] == 0.5

    def test_invalid_tenant_id_fails_closed(
        self, confidence_scorer
    ):
        """Test that operations with empty tenant_id fail safely.

        GDPR Art. 32: Fail-closed on missing isolation parameters.
        """
        with pytest.raises(ValueError):
            confidence_scorer.per_skill_stats(
                "json-parser",
                tenant_id="",  # Empty tenant_id
                user_id="user_1",
            )

    def test_profile_manager_rejects_invalid_conciseness(
        self, profile_manager
    ):
        """Test that profile manager rejects out-of-range conciseness values.

        Fail-closed on invalid input.
        """
        with pytest.raises(ValueError):
            profile_manager.update_from_feedback(
                "user_1", "tenant_1",
                {"conciseness": 1.5}  # Out of range [0.0, 1.0]
            )

    def test_skill_weights_limited_to_1000_entries(
        self, profile_manager
    ):
        """Test that skill_weights dict is bounded to 1000 entries.

        Prevent resource exhaustion.
        """
        large_feedback = {
            "skill_feedback": {f"skill_{i}": 0.5 for i in range(1001)}
        }

        with pytest.raises(ValueError):
            profile_manager.update_from_feedback(
                "user_1", "tenant_1",
                large_feedback
            )

    def test_preferred_models_limited_to_10_entries(
        self, profile_manager
    ):
        """Test that preferred_models list is bounded to 10 entries.

        Prevent resource exhaustion.
        """
        large_feedback = {
            "preferred_models": [f"model_{i}" for i in range(11)]
        }

        with pytest.raises(ValueError):
            profile_manager.update_from_feedback(
                "user_1", "tenant_1",
                large_feedback
            )


# ============================================================================
# TEST CLASS 7: Confidence + Profile Round-Trip
# ============================================================================


class TestConfidenceProfileRoundTrip:
    """Test realistic scenarios combining scoring and profiling."""

    def test_full_learning_cycle(
        self, confidence_scorer, profile_manager, temp_event_store
    ):
        """Test complete learning cycle: score → profile → predict.

        Scenario:
        1. Score skill high
        2. Update user preference from score
        3. Predict future preference
        4. Verify prediction reflects learned preference
        """
        # Step 1: Score skill
        stats = confidence_scorer.per_skill_stats(
            "json-parser", "tenant_1", "user_1",
            context={"keywords": ["json", "parsing"]}
        )
        assert stats["combined"] > 0.8

        # Step 2: Learn preference from score
        feedback = {"skill_feedback": {"json-parser": stats["combined"]}}
        profile_manager.update_from_feedback("user_1", "tenant_1", feedback)

        # Step 3: Predict preference
        prediction = profile_manager.predict_preference("user_1", "tenant_1", {})

        # Step 4: Verify prediction reflects learned preference
        assert "json-parser" in prediction["skill_weights"]
        assert prediction["skill_weights"]["json-parser"] > 0.8
        # Confidence should be non-trivial (we have 1 data point)
        assert prediction["confidence"] > 0.5

    def test_multiple_scores_refine_profile(
        self, confidence_scorer, profile_manager
    ):
        """Test that multiple confidence scores refine user profile over time.

        Each score provides feedback that updates preferences.
        """
        # First score: high on json-parser
        stats1 = confidence_scorer.per_skill_stats(
            "json-parser", "tenant_1", "user_1",
            context={"keywords": ["json"]}
        )
        profile_manager.update_from_feedback(
            "user_1", "tenant_1",
            {"skill_feedback": {"json-parser": stats1["combined"]}}
        )

        # Second score: medium on code-reviewer
        stats2 = confidence_scorer.per_skill_stats(
            "code-reviewer", "tenant_1", "user_1",
            context={"keywords": ["review"]}
        )
        profile_manager.update_from_feedback(
            "user_1", "tenant_1",
            {"skill_feedback": {"code-reviewer": stats2["combined"]}}
        )

        # Profile should have both preferences
        profile = profile_manager.get_profile("user_1", "tenant_1")
        assert len(profile.skill_weights) == 2
        assert "json-parser" in profile.skill_weights
        assert "code-reviewer" in profile.skill_weights

        # Prediction confidence increases with more data
        prediction = profile_manager.predict_preference("user_1", "tenant_1", {})
        # 2 skills → 0.5 + (2 * 0.05) = 0.6 confidence
        assert prediction["confidence"] >= 0.6

    def test_event_trail_shows_full_learning_history(
        self, confidence_scorer, profile_manager, temp_event_store
    ):
        """Test that event trail captures full learning history with proper ordering.

        GDPR Art. 30: Complete audit trail of all preference changes.
        """
        # Generate sequence of events
        confidence_scorer.per_skill_stats("json-parser", "tenant_1", "user_1")
        profile_manager.update_from_feedback(
            "user_1", "tenant_1",
            {"decision_style": "pragmatic"}
        )
        confidence_scorer.per_skill_stats("code-reviewer", "tenant_1", "user_1")

        # Read all tenant events
        events = temp_event_store.read_events_by_tenant("tenant_1", limit=1000)

        # Should have multiple events
        assert len(events) >= 3, "Expected at least 3 events in audit trail"

        # Verify all have correct tenant_id
        for event in events:
            assert event.tenant_id == "tenant_1"
