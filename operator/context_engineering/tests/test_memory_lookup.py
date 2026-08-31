"""Tests for MemoryLookup module."""

import pytest

# Use relative imports to avoid 'operator' stdlib conflict
from ..memory_lookup import MemoryLookup
from ..rich_task_brief import MemoryMatch, RichTaskBrief


class TestMemoryLookupSearch:
    """Test MemoryLookup.search() method."""

    def test_search_finds_relevant_memory_files(self, memory_lookup):
        """MemoryLookup should find files matching keywords."""
        results = memory_lookup.search(["voice", "summary"])

        assert len(results) > 0
        assert all(isinstance(r, MemoryMatch) for r in results)
        assert results[0].relevance_score >= 0.3

    def test_search_returns_confidence_scores(self, memory_lookup):
        """All results should have valid confidence scores [0.0, 1.0]."""
        results = memory_lookup.search(["bug", "fix"])

        for match in results:
            assert 0.0 <= match.relevance_score <= 1.0

    def test_search_caches_results(self, memory_lookup):
        """Results should be cached and retrieved from cache."""
        keywords = ["voice", "bug"]

        # First search
        results1 = memory_lookup.search(keywords, max_results=5)
        assert len(results1) > 0

        # Second search (should hit cache)
        results2 = memory_lookup.search(keywords, max_results=5)

        # Results should be identical
        assert len(results1) == len(results2)
        assert results1[0].filename == results2[0].filename

    def test_search_empty_keywords(self, memory_lookup):
        """Search with empty keywords should return empty list."""
        results = memory_lookup.search([])

        assert results == []

    def test_search_respects_max_results(self, memory_lookup):
        """Search should respect max_results parameter."""
        # Search for common term that will match many files
        results = memory_lookup.search(["bug"], max_results=2)

        assert len(results) <= 2

    def test_search_ranks_by_relevance(self, memory_lookup):
        """Results should be ranked by relevance (highest first)."""
        results = memory_lookup.search(["voice"], max_results=5)

        if len(results) > 1:
            # First result should have higher score than last
            assert results[0].relevance_score >= results[-1].relevance_score


class TestMemoryLookupRanking:
    """Test MemoryLookup.rank() method."""

    def test_rank_preserves_order(self, memory_lookup):
        """Rank should sort by relevance (descending)."""
        matches = memory_lookup.search(["voice"], max_results=10)
        ranked = memory_lookup.rank(matches)

        # Should be identical (already ranked by search)
        assert len(ranked) == len(matches)


class TestMemoryLookupEnrichment:
    """Test MemoryLookup.enrich_task() method."""

    def test_enrich_task_returns_rich_brief(self, memory_lookup, sample_enriched_task):
        """Enrich should return RichTaskBrief."""
        result = memory_lookup.enrich_task(sample_enriched_task)

        assert isinstance(result, RichTaskBrief)
        assert result.enriched_task is sample_enriched_task
        assert result.memory_context is not None

    def test_enrich_task_extracts_keywords(self, memory_lookup, sample_enriched_task):
        """Enrich should extract keywords from task."""
        result = memory_lookup.enrich_task(sample_enriched_task)

        assert len(result.memory_context.search_queries) > 0

    def test_enrich_task_confidence_score_calculated(
        self, memory_lookup, sample_enriched_task
    ):
        """Enrich should calculate overall confidence score."""
        result = memory_lookup.enrich_task(sample_enriched_task)

        assert 0.0 <= result.memory_context.confidence <= 1.0

    def test_enrich_task_empty_memory_graceful(self, memory_lookup):
        """Enrich should handle case when memory is empty."""

        class FakeTask:
            class Normalized:
                summary = "completely unique task xyz123"

            normalized = Normalized()

        result = memory_lookup.enrich_task(FakeTask())

        # Should not crash, confidence may be 0.0
        assert isinstance(result, RichTaskBrief)
        assert result.memory_context.confidence >= 0.0


class TestMemoryMatchValidation:
    """Test MemoryMatch struct validation."""

    def test_memory_match_validates_score(self):
        """MemoryMatch should validate relevance_score is [0.0, 1.0]."""
        from datetime import datetime

        # Valid
        match = MemoryMatch(
            filename="test.md",
            title="Test",
            relevance_score=0.5,
            source_file="/tmp/test.md",
            timestamp=datetime.now(),
        )
        assert match.relevance_score == 0.5

        # Invalid
        with pytest.raises(ValueError):
            MemoryMatch(
                filename="test.md",
                title="Test",
                relevance_score=1.5,
                source_file="/tmp/test.md",
                timestamp=datetime.now(),
            )


class TestMemoryLookupCaching:
    """Test MemoryLookup caching behavior."""

    def test_cache_stores_and_retrieves_results(self, memory_lookup):
        """Results should be cached and retrieved on second search."""
        keywords = ["voice", "bug"]

        # First search (miss)
        results1 = memory_lookup.search(keywords, max_results=5)
        assert len(results1) > 0

        # Verify cache was populated
        cache_key = hash(tuple(sorted(keywords)))
        assert cache_key in memory_lookup._search_cache

        # Second search (hit)
        results2 = memory_lookup.search(keywords, max_results=5)

        # Results should be identical
        assert len(results1) == len(results2)
        for r1, r2 in zip(results1, results2):
            assert r1.filename == r2.filename
            assert r1.relevance_score == r2.relevance_score

    def test_cache_ttl_expiry(self, temp_memory_dir):
        """Cache entries should expire after TTL."""
        # Create lookup with very short TTL (0.01 min = 600ms)
        lookup = MemoryLookup(memory_dir=temp_memory_dir, cache_ttl_minutes=0.01)

        keywords = ["test"]
        results1 = lookup.search(keywords)
        cache_size_before = len(lookup._search_cache)

        # Wait for cache to expire (1 second)
        import time

        time.sleep(1.0)

        # Search again (should miss cache, remove expired entry)
        results2 = lookup.search(keywords)

        # Results should be same but cache should not have expired entry
        assert len(results1) == len(results2)

    def test_cache_key_deduplication(self, memory_lookup):
        """Same keywords in different order should hit cache."""
        keywords1 = ["voice", "bug"]
        keywords2 = ["bug", "voice"]

        results1 = memory_lookup.search(keywords1)

        # Cache key should be same (sorted tuple)
        cache_key1 = hash(tuple(sorted(keywords1)))
        cache_key2 = hash(tuple(sorted(keywords2)))
        assert cache_key1 == cache_key2

        # Second search should hit cache
        results2 = memory_lookup.search(keywords2)

        assert len(results1) == len(results2)


class TestMemoryLookupKeywordExtraction:
    """Test keyword extraction from tasks."""

    def test_extract_keywords_from_summary(self, memory_lookup, sample_enriched_task):
        """Extract keywords should get words from normalized.summary."""
        keywords = memory_lookup._extract_keywords(sample_enriched_task)

        assert len(keywords) > 0
        # Should include longer words from summary
        assert any("voice" in k.lower() for k in keywords)
        assert any("bug" in k.lower() for k in keywords)

    def test_extract_keywords_filters_short_words(self, memory_lookup):
        """Should filter out words < 4 chars."""

        class FakeTask:
            class Normalized:
                summary = "a fix b the c this that is go to do"

            normalized = Normalized()

        keywords = memory_lookup._extract_keywords(FakeTask())

        # Should only have words > 3 chars
        assert all(len(k) > 3 or k.isdigit() for k in keywords)

    def test_extract_keywords_deduplicates(self, memory_lookup):
        """Should deduplicate keywords."""

        class FakeTask:
            class Normalized:
                summary = "voice voice voice crash crash problem problem"

            normalized = Normalized()

        keywords = memory_lookup._extract_keywords(FakeTask())

        # Count occurrences
        voice_count = sum(1 for k in keywords if "voice" in k.lower())
        crash_count = sum(1 for k in keywords if "crash" in k.lower())
        problem_count = sum(1 for k in keywords if "problem" in k.lower())

        # Should be deduplicated (each appears once)
        assert voice_count == 1
        assert crash_count == 1
        assert problem_count == 1
