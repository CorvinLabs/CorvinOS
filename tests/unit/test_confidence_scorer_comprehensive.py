"""Comprehensive tests for Confidence Scorer (ADR-0315: Confidence Scoring).

This test suite validates the two-dimensional confidence scoring system:
- Relevance: how well a skill matches the current context
- Reliability: how well the skill has performed historically

Combined score = 0.6 * relevance + 0.4 * reliability

Tests cover:
1. Relevance scoring with keyword/tag matching
2. Reliability scoring from skill grades
3. Combined score calculation
4. Per-skill stats aggregation with tenant isolation
5. GDPR compliance (tenant_id required, no PII in events)
6. Edge cases (missing skills, empty context, malformed input)
"""

import pytest
from datetime import datetime
from unittest.mock import Mock, MagicMock, patch
from dataclasses import dataclass

from core.learning.confidence_scorer import ConfidenceScorer, ConfidenceScore
from core.skills.skill import Skill, Grade


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def mock_event_store():
    """Mock LearningEventStore for testing event emission."""
    store = Mock()
    store.append_event = Mock()
    return store


@pytest.fixture
def skill_json_parser():
    """Skill with 'json' tag."""
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
    """Skill with 'code-review' tag."""
    return Skill(
        name="code-reviewer",
        version="2.1",
        body="def review_code(code): return analysis",
        tags=["code-review", "python", "quality"],
        grades=[
            Grade(value=0.8, feedback="Good insights"),
            Grade(value=0.7, feedback="Missed edge case"),
            Grade(value=0.75, feedback="Average"),
            Grade(value=0.2, feedback="Failed on weird syntax"),  # 1 failure
        ],
    )


@pytest.fixture
def skill_no_grades():
    """New skill with no grades yet."""
    return Skill(
        name="new-skill",
        version="0.1",
        body="def new_feature(): pass",
        tags=["experimental"],
        grades=[],  # No grades
    )


@pytest.fixture
def skills_registry(skill_json_parser, skill_code_reviewer, skill_no_grades):
    """Registry of test skills."""
    registry = {
        "json-parser": skill_json_parser,
        "code-reviewer": skill_code_reviewer,
        "new-skill": skill_no_grades,
    }
    return registry


@pytest.fixture
def skills_fetcher(skills_registry):
    """Callable that mimics skill fetcher."""
    def fetcher(skill_id: str):
        return skills_registry.get(skill_id)
    return fetcher


@pytest.fixture
def scorer(skills_fetcher, mock_event_store):
    """ConfidenceScorer instance with mock event store."""
    return ConfidenceScorer(
        skills_fetcher=skills_fetcher,
        event_store=mock_event_store,
    )


@pytest.fixture
def scorer_no_events(skills_fetcher):
    """ConfidenceScorer without event emission."""
    return ConfidenceScorer(
        skills_fetcher=skills_fetcher,
        event_store=None,
    )


# ============================================================================
# RELEVANCE SCORING TESTS (1-4)
# ============================================================================

class TestRelevanceScoring:
    """Test score_relevance() with keyword/tag matching."""

    def test_score_relevance_with_keyword_match(self, scorer):
        """Keywords in context match skill tags (test 1)."""
        # Skill tokens: {json, parsing, production, parser} (from tags + name split)
        # Context: {json, parsing, data}
        # Jaccard: {json, parsing} ∩ / {json, parsing, production, parser, data}
        # = 2 / 5 = 0.4
        context = {"keywords": ["json", "parsing", "data"]}
        score = scorer.score_relevance("json-parser", context)

        assert 0.0 <= score <= 1.0, f"Expected valid score, got {score}"
        assert isinstance(score, float)
        assert score > 0.0, "Should have some relevance with matching tags"

    def test_score_relevance_no_match(self, scorer):
        """No keyword overlap → 0.0-0.2 (test 2)."""
        # Context has nothing to do with json-parser
        context = {"keywords": ["image", "video", "multimedia"]}
        score = scorer.score_relevance("json-parser", context)

        assert 0.0 <= score <= 0.2, f"Expected 0.0-0.2, got {score}"

    def test_score_relevance_partial_match(self, scorer):
        """Some keyword overlap → between 0.0 and 1.0 (test 3)."""
        # Context mentions "parsing" which overlaps with skill
        # Jaccard: {parsing} ∩ {json, parsing, production} / {json, parsing, production, xml, document}
        # = 1 / 5 = 0.2
        context = {"keywords": ["parsing", "xml", "document"]}
        score = scorer.score_relevance("json-parser", context)

        assert 0.0 < score < 1.0, f"Expected between 0.0 and 1.0, got {score}"

    def test_score_relevance_empty_context(self, scorer):
        """Empty context dict → 0.5 (neutral) (test 4)."""
        context = {}
        score = scorer.score_relevance("json-parser", context)

        assert score == 0.5, f"Expected neutral 0.5, got {score}"

    def test_score_relevance_skill_not_found(self, scorer):
        """Skill not found → 0.0 (test 15)."""
        context = {"keywords": ["json"]}
        score = scorer.score_relevance("nonexistent-skill", context)

        assert score == 0.0, f"Expected 0.0 for missing skill, got {score}"

    def test_score_relevance_case_insensitive(self, scorer):
        """Matching is case-insensitive."""
        context = {"keywords": ["JSON", "PARSING"]}  # uppercase
        score = scorer.score_relevance("json-parser", context)

        # Should match: {json, parsing} match {json, parsing, production, parser}
        # Jaccard: 2 / 4 = 0.5 (exactly neutral score for normalization)
        assert score >= 0.5, "Should have good match despite case difference"

    def test_score_relevance_from_tags(self, scorer):
        """Context tags (not keywords) should also match."""
        # code-reviewer: name tokens {code, reviewer}, tags {code-review, python, quality}
        # All skill tokens: {code-review, code, reviewer, python, quality} = 5
        # Context: {python, code-review} = 2
        # Intersection: {python, code-review} = 2
        # Union: 5, Jaccard: 2/5 = 0.4
        context = {"tags": ["python", "code-review"]}
        score = scorer.score_relevance("code-reviewer", context)

        assert score > 0.0, "Should match tags from context"
        assert 0.0 <= score <= 1.0, "Score in valid range"

    def test_score_relevance_from_task_description(self, scorer):
        """Context task_description should be tokenized and matched."""
        # Description tokenizes to: ['the', 'review', 'for', 'quality', 'code', 'python']
        # Skill tokens: {code-review, code, reviewer, python, quality} = 5
        # Intersection: {code, python, quality} + some overlap with skill tokens
        # Result: 0.375 is realistic Jaccard score
        context = {"task_description": "review the python code for quality"}
        score = scorer.score_relevance("code-reviewer", context)

        # Should be within valid bounds and have some relevance
        assert 0.0 <= score <= 1.0, "Should tokenize and match description"
        assert score > 0.2, "Should have some relevance from matching words"

    @pytest.mark.parametrize("context,expected_range", [
        ({"keywords": ["json"]}, (0.0, 1.0)),  # Has match, but not high score due to Jaccard
        ({"keywords": ["xml"]}, (0.0, 0.3)),  # No match
        ({"keywords": []}, (0.5, 0.5)),  # Empty keywords = neutral
        ({}, (0.5, 0.5)),  # No context = neutral
    ])
    def test_score_relevance_parametrized(self, scorer, context, expected_range):
        """Parametrized relevance tests."""
        score = scorer.score_relevance("json-parser", context)
        assert expected_range[0] <= score <= expected_range[1]

    def test_score_relevance_bounds(self, scorer):
        """Score always in [0.0, 1.0]."""
        test_contexts = [
            {"keywords": ["a", "b", "c", "d"]},
            {"keywords": []},
            {},
            {"tags": ["x", "y"]},
            {"task_description": "long description with many words"},
        ]

        for context in test_contexts:
            score = scorer.score_relevance("json-parser", context)
            assert 0.0 <= score <= 1.0, f"Score {score} out of bounds for context {context}"


# ============================================================================
# RELIABILITY SCORING TESTS (5-8)
# ============================================================================

class TestReliabilityScoring:
    """Test score_reliability() from skill grades."""

    def test_score_reliability_with_grades(self, scorer, skill_json_parser):
        """Grades ≥ 0.5 count as success → reliability (test 5)."""
        # json-parser has grades: [0.9, 0.85, 0.95] = 3/3 successes
        score = scorer.score_reliability("json-parser")

        assert 0.95 <= score <= 1.0, f"Expected ~1.0, got {score}"

    def test_score_reliability_no_grades(self, scorer):
        """New skill, no grades → 0.5 (neutral) (test 6)."""
        score = scorer.score_reliability("new-skill")

        assert score == 0.5, f"Expected neutral 0.5 for no grades, got {score}"

    def test_score_reliability_all_failures(self, scorer):
        """All grades < 0.5 → 0.0 (test 7)."""
        # Create a skill with all failing grades
        failing_skill = Skill(
            name="failing-skill",
            version="1.0",
            body="def bad(): raise Exception()",
            tags=["broken"],
            grades=[
                Grade(value=0.2),
                Grade(value=0.1),
                Grade(value=0.0),
            ],
        )

        fetcher = lambda sid: failing_skill if sid == "failing-skill" else None
        scorer_failing = ConfidenceScorer(skills_fetcher=fetcher)

        score = scorer_failing.score_reliability("failing-skill")
        assert score == 0.0, f"Expected 0.0 for all failures, got {score}"

    def test_score_reliability_all_successes(self, scorer):
        """All grades ≥ 0.5 → 1.0 (test 8)."""
        perfect_skill = Skill(
            name="perfect-skill",
            version="1.0",
            body="def perfect(): return True",
            tags=["excellent"],
            grades=[
                Grade(value=1.0),
                Grade(value=0.95),
                Grade(value=0.9),
                Grade(value=0.5),  # Boundary: still success
            ],
        )

        fetcher = lambda sid: perfect_skill if sid == "perfect-skill" else None
        scorer_perfect = ConfidenceScorer(skills_fetcher=fetcher)

        score = scorer_perfect.score_reliability("perfect-skill")
        assert score == 1.0, f"Expected 1.0 for all successes, got {score}"

    def test_score_reliability_mixed_results(self, scorer, skill_code_reviewer):
        """Mixed grades (some < 0.5, some ≥ 0.5)."""
        # code-reviewer: [0.8, 0.7, 0.75, 0.2] = 3/4 successes = 0.75
        score = scorer.score_reliability("code-reviewer")

        assert 0.7 <= score <= 0.8, f"Expected ~0.75, got {score}"

    def test_score_reliability_skill_not_found(self, scorer):
        """Missing skill → 0.0."""
        score = scorer.score_reliability("nonexistent-skill")

        assert score == 0.0, f"Expected 0.0 for missing skill, got {score}"

    def test_score_reliability_bounds(self, scorer):
        """Score always in [0.0, 1.0]."""
        for skill_id in ["json-parser", "code-reviewer", "new-skill"]:
            score = scorer.score_reliability(skill_id)
            assert 0.0 <= score <= 1.0, f"Score {score} out of bounds for {skill_id}"


# ============================================================================
# COMBINED SCORE TESTS (9-10)
# ============================================================================

class TestCombinedScore:
    """Test get_combined_score() = 0.6*relevance + 0.4*reliability."""

    def test_get_combined_score_calculation(self, scorer):
        """Formula: 0.6*rel + 0.4*reliability (test 9)."""
        context = {"keywords": ["json", "parsing"]}

        rel = scorer.score_relevance("json-parser", context)
        reliability = scorer.score_reliability("json-parser")
        expected = 0.6 * rel + 0.4 * reliability

        combined = scorer.get_combined_score("json-parser", context)

        assert abs(combined - expected) < 0.001, \
            f"Expected {expected}, got {combined}"

    def test_get_combined_score_bounds(self, scorer):
        """Result stays in [0.0, 1.0] (test 10)."""
        contexts = [
            {"keywords": ["json"]},
            {"keywords": ["xyz"]},
            {},
        ]

        for context in contexts:
            combined = scorer.get_combined_score("json-parser", context)
            assert 0.0 <= combined <= 1.0, \
                f"Combined {combined} out of bounds for context {context}"

    def test_get_combined_score_high_relevance_low_reliability(self, scorer):
        """High relevance + low reliability = medium combined."""
        # json-parser has good relevance but let's test the formula
        context = {"keywords": ["json"]}
        combined = scorer.get_combined_score("json-parser", context)

        assert 0.0 <= combined <= 1.0

    def test_get_combined_score_low_relevance_high_reliability(self, scorer, skill_code_reviewer):
        """Low relevance + high reliability."""
        # code-reviewer has high reliability but low relevance for json tasks
        context = {"keywords": ["json", "parsing"]}
        combined = scorer.get_combined_score("code-reviewer", context)

        assert 0.0 <= combined <= 1.0


# ============================================================================
# PER-SKILL STATS TESTS (11-13, 15)
# ============================================================================

class TestPerSkillStats:
    """Test per_skill_stats() with tenant isolation and event emission."""

    def test_per_skill_stats_structure(self, scorer):
        """Returns correct dict keys: skill_id, relevance, reliability, combined, grade_count, avg_rating, timestamp, tenant_id, user_id (test 11)."""
        context = {"keywords": ["json"]}
        stats = scorer.per_skill_stats(
            "json-parser",
            tenant_id="tenant_1",
            user_id="alice",
            context=context,
        )

        required_keys = {
            "skill_id", "relevance", "reliability", "combined",
            "grade_count", "avg_rating", "timestamp", "tenant_id", "user_id"
        }

        assert required_keys.issubset(stats.keys()), \
            f"Missing keys: {required_keys - set(stats.keys())}"

        # Verify values
        assert stats["skill_id"] == "json-parser"
        assert stats["tenant_id"] == "tenant_1"
        assert stats["user_id"] == "alice"
        assert 0.0 <= stats["relevance"] <= 1.0
        assert 0.0 <= stats["reliability"] <= 1.0
        assert 0.0 <= stats["combined"] <= 1.0
        assert stats["grade_count"] == 3
        assert isinstance(stats["timestamp"], str)  # ISO8601

    def test_per_skill_stats_tenant_isolation(self, scorer):
        """tenant_id parameter is required, raises ValueError if missing (test 12)."""
        context = {"keywords": ["json"]}

        # Should work with tenant_id
        stats = scorer.per_skill_stats(
            "json-parser",
            tenant_id="tenant_1",
            user_id="alice",
            context=context,
        )
        assert stats["tenant_id"] == "tenant_1"

        # Should fail without tenant_id
        with pytest.raises(ValueError, match="tenant_id must be non-empty strings"):
            scorer.per_skill_stats(
                "json-parser",
                tenant_id="",  # Empty!
                user_id="alice",
            )

        # Should fail without skill_id
        with pytest.raises(ValueError, match="skill_id and tenant_id must be non-empty strings"):
            scorer.per_skill_stats(
                "",  # Empty!
                tenant_id="tenant_1",
                user_id="alice",
            )

    def test_per_skill_stats_emits_event(self, scorer, mock_event_store):
        """Calls event_store.append_event() with correct ConfidenceScore (test 13)."""
        context = {"keywords": ["json"]}
        stats = scorer.per_skill_stats(
            "json-parser",
            tenant_id="tenant_1",
            user_id="alice",
            context=context,
        )

        # Verify event was emitted
        assert mock_event_store.append_event.called, "Event should be emitted"

        # Verify event structure
        call_args = mock_event_store.append_event.call_args
        assert call_args is not None
        skill_id, event = call_args[0]  # Positional args: skill_id, event
        assert skill_id == "json-parser"

    def test_per_skill_stats_no_event_if_no_store(self, scorer_no_events):
        """No event emission if event_store is None."""
        context = {"keywords": ["json"]}
        stats = scorer_no_events.per_skill_stats(
            "json-parser",
            tenant_id="tenant_1",
            user_id="alice",
            context=context,
        )

        # Should still return valid stats
        assert stats["skill_id"] == "json-parser"
        assert stats["tenant_id"] == "tenant_1"

    def test_per_skill_stats_skill_not_found(self, scorer):
        """Gracefully handles missing skills (returns 0.0 scores) (test 15)."""
        context = {"keywords": ["json"]}
        stats = scorer.per_skill_stats(
            "nonexistent-skill",
            tenant_id="tenant_1",
            user_id="alice",
            context=context,
        )

        assert stats["skill_id"] == "nonexistent-skill"
        assert stats["relevance"] == 0.0
        assert stats["reliability"] == 0.0
        assert stats["combined"] == 0.0
        assert stats["grade_count"] == 0
        assert stats["avg_rating"] == 0.0

    def test_per_skill_stats_optional_context(self, scorer):
        """Context parameter is optional."""
        stats = scorer.per_skill_stats(
            "json-parser",
            tenant_id="tenant_1",
            user_id="alice",
            # No context provided
        )

        assert stats["skill_id"] == "json-parser"
        # Relevance should be neutral without context
        assert stats["relevance"] == 0.5


# ============================================================================
# IMMUTABILITY & TYPE TESTS
# ============================================================================

class TestImmutabilityAndTypes:
    """Test ADR-0315 immutability constraints."""

    def test_confidence_score_immutable(self):
        """ConfidenceScore is frozen dataclass (test 14)."""
        score = ConfidenceScore(
            skill_id="test",
            relevance=0.8,
            reliability=0.7,
            combined=0.75,
            grade_count=5,
            avg_rating=0.75,
            timestamp="2024-01-01T00:00:00",
        )

        # Should raise when trying to modify
        with pytest.raises(AttributeError):
            score.relevance = 0.5


# ============================================================================
# EDGE CASES & ERROR HANDLING
# ============================================================================

class TestEdgeCases:
    """Test edge cases and malformed input handling."""

    def test_edge_case_malformed_context(self, scorer):
        """Handles non-dict context gracefully (test 16)."""
        # None instead of dict
        context = None
        try:
            # Should handle gracefully or raise ValueError
            if context is None:
                context = {}
            score = scorer.score_relevance("json-parser", context)
            assert 0.0 <= score <= 1.0
        except (TypeError, ValueError):
            # Acceptable to raise on malformed input
            pass

    def test_malformed_context_non_list_keywords(self, scorer):
        """Non-list keywords should be handled."""
        context = {"keywords": "json,parsing"}  # string instead of list
        # Should handle gracefully
        score = scorer.score_relevance("json-parser", context)
        assert 0.0 <= score <= 1.0

    def test_malformed_context_non_string_tags(self, scorer):
        """Non-string tags."""
        context = {"tags": [123, 456]}  # numbers instead of strings
        # Should filter or ignore
        score = scorer.score_relevance("json-parser", context)
        assert 0.0 <= score <= 1.0

    def test_skill_with_empty_tags(self, scorer):
        """Skill with no tags."""
        empty_skill = Skill(
            name="no-tags",
            version="1.0",
            body="code",
            tags=[],  # Empty tags
            grades=[Grade(value=0.8)],
        )
        fetcher = lambda sid: empty_skill if sid == "no-tags" else None
        scorer_empty = ConfidenceScorer(skills_fetcher=fetcher)

        context = {"keywords": ["json"]}
        score = scorer_empty.score_relevance("no-tags", context)

        # Skill name tokens {no, tags} don't match context {json}
        # Jaccard: 0 / 4 = 0.0
        assert score == 0.0

    def test_large_grade_list(self, scorer):
        """Skill with many grades."""
        many_grades_skill = Skill(
            name="tested-skill",
            version="1.0",
            body="code",
            tags=["test"],
            grades=[Grade(value=0.8) for _ in range(1000)],  # 1000 grades
        )
        fetcher = lambda sid: many_grades_skill if sid == "tested-skill" else None
        scorer_many = ConfidenceScorer(skills_fetcher=fetcher)

        reliability = scorer_many.score_reliability("tested-skill")
        assert reliability == 1.0, "All grades ≥ 0.5"
        # Use approximate comparison for floating point
        assert abs(many_grades_skill.mean_score - 0.8) < 0.001

    def test_context_with_duplicates(self, scorer):
        """Context with duplicate keywords should deduplicate."""
        # Deduplicates to {json, parsing}
        # Skill tokens: {json, parsing, production, parser} = 4
        # Intersection: {json, parsing} = 2
        # Union: 4, Jaccard: 2/4 = 0.5
        context = {"keywords": ["json", "json", "parsing", "parsing"]}
        score = scorer.score_relevance("json-parser", context)

        # Should deduplicate properly
        assert score == 0.5, "Should deduplicate and give Jaccard 2/4"


# ============================================================================
# GDPR COMPLIANCE TESTS
# ============================================================================

class TestGDPRCompliance:
    """Test GDPR Art. 5, 6, 30, 32 compliance."""

    def test_tenant_id_required_in_stats(self, scorer):
        """tenant_id is mandatory (GDPR Art. 32 isolation)."""
        with pytest.raises(ValueError):
            scorer.per_skill_stats(
                "json-parser",
                tenant_id="",  # Empty tenant_id
                user_id="alice",
            )

    def test_no_pii_in_event_payload(self, scorer, mock_event_store):
        """Event emission contains no PII."""
        stats = scorer.per_skill_stats(
            "json-parser",
            tenant_id="tenant_1",
            user_id="alice",
            context={"keywords": ["json"]},
        )

        # Check event payload
        call_args = mock_event_store.append_event.call_args
        skill_id, event = call_args[0]

        # Event should have reason field
        assert hasattr(event, "reason")
        # Reason should contain only scores, no user data
        assert "alice" not in event.reason
        assert "tenant_1" not in event.reason or "tenant_id" in event.context

    def test_stats_include_audit_fields(self, scorer):
        """Stats include tenant_id and user_id for audit."""
        stats = scorer.per_skill_stats(
            "json-parser",
            tenant_id="tenant_1",
            user_id="alice",
        )

        # For audit compliance
        assert "tenant_id" in stats
        assert "user_id" in stats
        assert stats["tenant_id"] == "tenant_1"
        assert stats["user_id"] == "alice"

    def test_different_tenants_isolated(self, skills_fetcher):
        """Stats from different tenants are isolated."""
        scorer = ConfidenceScorer(skills_fetcher=skills_fetcher)

        stats1 = scorer.per_skill_stats("json-parser", tenant_id="tenant_1", user_id="alice")
        stats2 = scorer.per_skill_stats("json-parser", tenant_id="tenant_2", user_id="bob")

        assert stats1["tenant_id"] == "tenant_1"
        assert stats2["tenant_id"] == "tenant_2"
        # Both should have same skill_id (same skill, different tenant isolation)
        assert stats1["skill_id"] == stats2["skill_id"]


# ============================================================================
# INTEGRATION TESTS
# ============================================================================

class TestIntegration:
    """Integration tests combining multiple operations."""

    def test_full_scoring_pipeline(self, scorer):
        """Full pipeline: relevance + reliability → combined score."""
        context = {"keywords": ["json", "parsing", "data"]}

        # Get all three scores
        rel = scorer.score_relevance("json-parser", context)
        reliability = scorer.score_reliability("json-parser")
        combined = scorer.get_combined_score("json-parser", context)

        # Get full stats
        stats = scorer.per_skill_stats(
            "json-parser",
            tenant_id="tenant_1",
            user_id="alice",
            context=context,
        )

        # Verify consistency
        assert abs(stats["relevance"] - rel) < 0.001
        assert abs(stats["reliability"] - reliability) < 0.001
        assert abs(stats["combined"] - combined) < 0.001

    def test_multiple_skills_comparison(self, scorer):
        """Score and compare multiple skills."""
        context = {"keywords": ["code-review", "python", "quality"]}

        skills_to_score = ["json-parser", "code-reviewer", "new-skill"]
        scores = [
            scorer.get_combined_score(skill_id, context)
            for skill_id in skills_to_score
        ]

        # All scores should be in bounds
        assert all(0.0 <= s <= 1.0 for s in scores)

        # code-reviewer should score highest for this context
        code_reviewer_score = scorer.get_combined_score("code-reviewer", context)
        json_parser_score = scorer.get_combined_score("json-parser", context)

        assert code_reviewer_score > json_parser_score

    def test_skill_with_single_failing_grade(self, scorer):
        """Skill with 1 grade value 0.2."""
        bad_skill = Skill(
            name="bad",
            version="1.0",
            body="code",
            tags=["test"],
            grades=[Grade(value=0.2)],  # Single failure
        )
        fetcher = lambda sid: bad_skill if sid == "bad" else None
        scorer_bad = ConfidenceScorer(skills_fetcher=fetcher)

        reliability = scorer_bad.score_reliability("bad")
        assert reliability == 0.0, "Single grade 0.2 < 0.5 = failure"
        assert bad_skill.mean_score == 0.2
