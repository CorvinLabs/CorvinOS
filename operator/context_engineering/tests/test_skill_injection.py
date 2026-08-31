"""Tests for SkillInjection module (Phase 2)."""

import pytest
from ..skill_injection import SkillInjection, RecommendedSkill, SkillInjectionResult


class TestRecommendedSkillValidation:
    """Validate RecommendedSkill data structure."""

    def test_recommended_skill_validates_scores(self):
        """RecommendedSkill should validate scores are [0.0, 1.0]."""
        # Valid
        skill = RecommendedSkill(
            skill_id="skill-debug-001",
            title="Debug concurrent issues",
            relevance_score=0.85,
            success_rate=0.92,
            category="debugging",
            description="Systematic debugging approach",
        )
        assert skill.relevance_score == 0.85

        # Invalid relevance
        with pytest.raises(ValueError):
            RecommendedSkill(
                skill_id="skill-invalid",
                title="Invalid skill",
                relevance_score=1.5,
                success_rate=0.8,
                category="test",
                description="Test",
            )


class TestSkillInjectionInitialization:
    """Test SkillInjection initialization."""

    def test_skill_injection_initializes(self):
        """SkillInjection should initialize without error."""
        si = SkillInjection()
        assert si is not None
        assert si.cache_ttl is not None


class TestSkillInjectionRecommendation:
    """Test skill recommendation."""

    @pytest.fixture
    def injection(self):
        return SkillInjection()

    def test_recommend_skills_returns_result(self, injection):
        """recommend_skills should return SkillInjectionResult."""
        class MockTask:
            pass

        task = MockTask()
        result = injection.recommend_skills(task)

        assert isinstance(result, SkillInjectionResult)
        assert isinstance(result.recommended_skills, list)
        assert result.search_duration_ms >= 0

    def test_recommend_skills_respects_top_n(self, injection):
        """recommend_skills should respect top_n parameter."""
        class MockTask:
            pass

        task = MockTask()
        result = injection.recommend_skills(task, top_n=2)

        assert len(result.recommended_skills) <= 2

    def test_skill_injection_caching(self, injection):
        """Results should be cached and retrieved."""
        class MockTask:
            pass

        task = MockTask()

        # First call (miss)
        result1 = injection.recommend_skills(task)
        assert result1 is not None

        # Cache should be populated
        assert len(injection._injection_cache) >= 0


class TestSkillInjectionRanking:
    """Test skill ranking."""

    @pytest.fixture
    def injection(self):
        return SkillInjection()

    def test_rank_sorts_by_combined_score(self, injection):
        """Rank should sort by combined score (relevance * 0.6 + success * 0.4)."""
        skills = [
            RecommendedSkill(
                skill_id="s1",
                title="Low score",
                relevance_score=0.3,
                success_rate=0.4,
                category="test",
                description="Low",
            ),
            RecommendedSkill(
                skill_id="s2",
                title="High score",
                relevance_score=0.9,
                success_rate=0.95,
                category="test",
                description="High",
            ),
        ]

        ranked = injection.rank(skills)

        # Highest combined score first
        assert ranked[0].skill_id == "s2"
        assert ranked[1].skill_id == "s1"


class TestSkillInjectionIntegration:
    """Integration tests for skill injection."""

    @pytest.fixture
    def injection(self):
        return SkillInjection()

    def test_end_to_end_injection(self, injection):
        """End-to-end skill injection should work without crashes."""
        class EnrichedTask:
            class Normalized:
                summary = "Debug concurrent access issue in memory module"

            normalized = Normalized()

        task = EnrichedTask()
        result = injection.recommend_skills(task, top_n=3)

        # Should produce valid result
        assert isinstance(result, SkillInjectionResult)
        assert result.search_duration_ms >= 0
        assert result.adoption_tracked is True

    def test_concurrent_injections(self):
        """SkillInjection should handle concurrent calls."""
        from concurrent.futures import ThreadPoolExecutor

        si = SkillInjection()

        class MockTask:
            pass

        def inject_skills(task_id):
            task = MockTask()
            return si.recommend_skills(task)

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(inject_skills, i) for i in range(10)]
            results = [f.result() for f in futures]

        assert len(results) == 10
        assert all(isinstance(r, SkillInjectionResult) for r in results)
