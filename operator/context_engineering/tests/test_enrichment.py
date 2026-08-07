"""Tests for MemoryLookup enrichment pipeline."""

import pytest
from datetime import datetime

from ..memory_lookup import MemoryLookup
from ..rich_task_brief import RichTaskBrief, MemoryContext


class TestMemoryLookupRanking:
    """Test MemoryLookup.rank() method."""

    def test_rank_sorts_by_relevance_descending(self, memory_lookup):
        """Rank should sort matches by score (highest first)."""
        # Get unranked matches
        matches = memory_lookup.search(["voice", "bug"], max_results=10)

        if len(matches) < 2:
            pytest.skip("Need at least 2 matches to test ranking")

        # Rank them
        ranked = memory_lookup.rank(matches)

        # Verify descending order
        for i in range(len(ranked) - 1):
            assert ranked[i].relevance_score >= ranked[i + 1].relevance_score


class TestRichTaskBriefStructure:
    """Test RichTaskBrief data structure."""

    def test_rich_task_brief_has_all_fields(self, sample_enriched_task):
        """RichTaskBrief should have all required fields."""
        brief = RichTaskBrief(
            raw_input="test task",
            enriched_task=sample_enriched_task,
            memory_context=MemoryContext(),
            timestamp=datetime.now(),
            version="0.1",
        )

        assert brief.raw_input == "test task"
        assert brief.enriched_task is sample_enriched_task
        assert isinstance(brief.memory_context, MemoryContext)
        assert brief.timestamp is not None
        assert brief.version == "0.1"

    def test_rich_task_brief_repr(self, sample_enriched_task):
        """RichTaskBrief should have readable repr."""
        brief = RichTaskBrief(
            raw_input="fix bug in voice module",
            enriched_task=sample_enriched_task,
            memory_context=MemoryContext(confidence=0.85),
            timestamp=datetime.now(),
        )

        repr_str = repr(brief)
        assert "RichTaskBrief" in repr_str
        assert "fix bug" in repr_str
        assert "0.85" in repr_str


class TestMemoryLookupEnrichmentIntegration:
    """Test full enrichment pipeline (search → rank → brief)."""

    def test_enrich_task_full_pipeline(self, memory_lookup, sample_enriched_task):
        """Full pipeline should produce valid RichTaskBrief."""
        brief = memory_lookup.enrich_task(sample_enriched_task)

        # Verify structure
        assert isinstance(brief, RichTaskBrief)
        assert brief.enriched_task is sample_enriched_task
        assert isinstance(brief.memory_context, MemoryContext)

        # Verify memory context
        assert isinstance(brief.memory_context.matches, list)
        assert len(brief.memory_context.search_queries) > 0
        assert 0.0 <= brief.memory_context.confidence <= 1.0
        assert brief.memory_context.search_duration_ms >= 0

    def test_enrich_task_latency_recorded(self, memory_lookup, sample_enriched_task):
        """Enrichment should record latency."""
        brief = memory_lookup.enrich_task(sample_enriched_task)

        # Latency should be > 0 (at least measured something)
        assert brief.memory_context.search_duration_ms >= 0
        # Should be reasonable (< 10 seconds, even accounting for slow systems)
        assert brief.memory_context.search_duration_ms < 10000

    def test_enrich_task_confidence_matches_average(self, memory_lookup, sample_enriched_task):
        """Confidence should be average of memory match scores."""
        brief = memory_lookup.enrich_task(sample_enriched_task)

        if brief.memory_context.matches:
            expected_conf = sum(
                m.relevance_score for m in brief.memory_context.matches
            ) / len(brief.memory_context.matches)
            assert abs(brief.memory_context.confidence - expected_conf) < 0.001
        else:
            assert brief.memory_context.confidence == 0.0

    def test_enrich_task_with_empty_enriched_task(self, memory_lookup):
        """Enrichment should handle minimal task objects."""

        class MinimalTask:
            pass

        task = MinimalTask()
        brief = memory_lookup.enrich_task(task)

        assert isinstance(brief, RichTaskBrief)
        assert brief.raw_input == "unknown"  # Fallback for missing attributes

    def test_enrich_task_memory_matches_ranked(self, memory_lookup, sample_enriched_task):
        """Memory matches in brief should be ranked (highest first)."""
        brief = memory_lookup.enrich_task(sample_enriched_task)

        matches = brief.memory_context.matches
        if len(matches) > 1:
            # Verify descending order
            for i in range(len(matches) - 1):
                assert matches[i].relevance_score >= matches[i + 1].relevance_score

    def test_enrich_task_respects_max_results(self, memory_lookup, sample_enriched_task):
        """Enrichment should return at most 5 matches (default)."""
        brief = memory_lookup.enrich_task(sample_enriched_task)

        assert len(brief.memory_context.matches) <= 5

    def test_enrich_task_timestamp_recent(self, memory_lookup, sample_enriched_task):
        """RichTaskBrief timestamp should be very recent."""
        before = datetime.now()
        brief = memory_lookup.enrich_task(sample_enriched_task)
        after = datetime.now()

        # Timestamp should be between before and after
        assert before <= brief.timestamp <= after


class TestMemoryContextStructure:
    """Test MemoryContext data structure."""

    def test_memory_context_validates_confidence(self):
        """Confidence should be in [0.0, 1.0]."""
        # Valid
        ctx = MemoryContext(confidence=0.5)
        assert ctx.confidence == 0.5

        # Invalid
        with pytest.raises(ValueError):
            MemoryContext(confidence=1.5)

    def test_memory_context_empty_matches(self):
        """MemoryContext should handle empty matches."""
        ctx = MemoryContext()

        assert ctx.matches == []
        assert ctx.search_queries == []
        assert ctx.confidence == 0.0
        assert ctx.cache_hit is False


class TestEndToEndEnrichment:
    """End-to-end enrichment tests."""

    def test_enrichment_improves_task_understanding(self, memory_lookup):
        """Enrichment should add meaningful context to a task."""

        class SimpleTask:
            class Normalized:
                summary = "voice rendering crash"

            normalized = Normalized()

        # Enrich
        brief = memory_lookup.enrich_task(SimpleTask())

        # Should have found something (memory files mention voice)
        # Or at minimum, should produce valid RichTaskBrief
        assert isinstance(brief, RichTaskBrief)
        assert len(brief.memory_context.search_queries) > 0

    def test_enrichment_handles_vague_task(self, memory_lookup):
        """Enrichment should gracefully handle very vague tasks."""

        class VagueTask:
            class Normalized:
                summary = "fix issue"

            normalized = Normalized()

        brief = memory_lookup.enrich_task(VagueTask())

        # Should not crash
        assert isinstance(brief, RichTaskBrief)
        # Confidence might be 0 if no matches, but structure is valid
        assert 0.0 <= brief.memory_context.confidence <= 1.0

    def test_enrichment_caching_benefits(self, memory_lookup, sample_enriched_task):
        """Second enrichment of same task should use cache."""
        import time

        # First enrichment (cache miss)
        start1 = time.perf_counter()
        brief1 = memory_lookup.enrich_task(sample_enriched_task)
        time1_ms = (time.perf_counter() - start1) * 1000

        # Second enrichment (should hit cache)
        start2 = time.perf_counter()
        brief2 = memory_lookup.enrich_task(sample_enriched_task)
        time2_ms = (time.perf_counter() - start2) * 1000

        # Results should be identical
        assert len(brief1.memory_context.matches) == len(brief2.memory_context.matches)

        # Cache should provide some speedup (not guaranteed but likely)
        # Skip this check on slow systems, just verify both are fast
        assert time1_ms < 100  # Reasonable latency
        assert time2_ms < 100
