"""Unit tests for QualityScorer."""

from core.skills.os_skills.data_hub.quality.scorer import QualityScorer


class TestQualityScorer:
    """Test quality scoring determinism and bounds."""

    def test_relevance_score_bounds(self):
        """Relevance scores are in [0, 1]."""
        scorer = QualityScorer()

        for distance in [0.0, 0.25, 0.5, 0.75, 1.0]:
            score = scorer.compute_relevance(distance, "test use case")
            assert 0 <= score <= 1, f"Score {score} out of bounds for distance {distance}"

    def test_relevance_distance_inverse(self):
        """Closer distance → higher relevance."""
        scorer = QualityScorer()

        score_close = scorer.compute_relevance(0.1, "use case")
        score_far = scorer.compute_relevance(0.9, "use case")
        assert score_close > score_far, "Closer should have higher relevance"

    def test_freshness_score_bounds(self):
        """Freshness scores are in [0, 1]."""
        scorer = QualityScorer()

        for hours in [0, 12, 24, 72, 168, 720]:
            score = scorer.compute_freshness(hours)
            assert 0 <= score <= 1, f"Score {score} out of bounds for {hours}h old"

    def test_freshness_decay(self):
        """Older data → lower freshness score."""
        scorer = QualityScorer()

        score_fresh = scorer.compute_freshness(1)
        score_old = scorer.compute_freshness(168)
        assert score_fresh > score_old, "Fresh data should score higher"

    def test_coverage_score_bounds(self):
        """Coverage scores are in [0, 1]."""
        scorer = QualityScorer()

        score = scorer.compute_coverage(50, 100)
        assert 0 <= score <= 1

    def test_coverage_exceeding_expected(self):
        """Coverage capped at 1.0 even if actual > expected."""
        scorer = QualityScorer()

        score = scorer.compute_coverage(150, 100)
        assert score == 1.0, "Coverage should be capped at 1.0"

    def test_completeness_score_bounds(self):
        """Completeness scores are in [0, 1]."""
        scorer = QualityScorer()

        score = scorer.compute_completeness(True, True, 0)
        assert 0 <= score <= 1

    def test_completeness_full(self):
        """Complete data scores higher."""
        scorer = QualityScorer()

        full = scorer.compute_completeness(has_metadata=True, has_examples=True, placeholder_count=0)
        incomplete = scorer.compute_completeness(has_metadata=False, has_examples=False, placeholder_count=5)
        assert full > incomplete

    def test_document_quality_reproducible(self):
        """Same inputs → same document quality score."""
        scorer = QualityScorer()

        inputs = (0.7, 0.8, 0.9, 0.6)
        score1 = scorer.compute_document_quality(*inputs)
        score2 = scorer.compute_document_quality(*inputs)
        assert score1 == score2, f"Quality score not deterministic: {score1} vs {score2}"

    def test_document_quality_weighted(self):
        """Document quality uses proper weights."""
        scorer = QualityScorer()

        # All components equal
        score_equal = scorer.compute_document_quality(0.5, 0.5, 0.5, 0.5)
        assert 0 <= score_equal <= 1

        # High relevance should dominate (40% weight)
        score_high_rel = scorer.compute_document_quality(1.0, 0.0, 0.0, 0.0)
        score_high_fresh = scorer.compute_document_quality(0.0, 1.0, 0.0, 0.0)
        assert score_high_rel > score_high_fresh, "Relevance has higher weight"

    def test_document_quality_bounds(self):
        """Document quality is always in [0, 1]."""
        scorer = QualityScorer()

        # Test corner cases
        assert scorer.compute_document_quality(0, 0, 0, 0) == 0
        assert scorer.compute_document_quality(1, 1, 1, 1) == 1

    def test_manifest_quality_empty(self):
        """Empty document list gives neutral quality."""
        scorer = QualityScorer()

        score = scorer.compute_manifest_quality([])
        assert score == 0.5  # Neutral

    def test_manifest_quality_average(self):
        """Manifest quality is average of document scores."""
        scorer = QualityScorer()

        docs = [0.6, 0.8, 0.7]
        score = scorer.compute_manifest_quality(docs)
        expected = (0.6 + 0.8 + 0.7) / 3
        assert score == expected

    def test_weights_sum_to_one(self):
        """Quality weights sum to 1.0."""
        scorer = QualityScorer()

        total = sum(scorer.weights.values())
        assert total == 1.0, f"Weights don't sum to 1.0: {total}"

    def test_invalid_input_bounds(self):
        """Invalid inputs (out of [0, 1]) raise error."""
        scorer = QualityScorer()

        # All inputs must be in [0, 1]
        try:
            scorer.compute_document_quality(1.5, 0.5, 0.5, 0.5)
            assert False, "Should raise error for out-of-bounds input"
        except ValueError:
            pass  # Expected
