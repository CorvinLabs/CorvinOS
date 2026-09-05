"""ConfidenceScorer unit tests (ADR-0548 Phase 1, task 1.2).

Pure-function tests: no audit chain, no store, no I/O. ``now`` is injected so
recency decay is deterministic — a test that used wall-clock time would start
failing on its own N days after it was written.

Coverage targets from the implementation plan: N = 1..100, the full
success-rate range, recency from fresh to years stale, and the two invariants
that are load-bearing rather than incidental:
  * confidence never exceeds MAX_CONFIDENCE (0.95);
  * the product equals base x success x sample x recency exactly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.skills.os_skills.confidence_scorer import (
    AUTONOMOUS_THRESHOLD,
    DISCOVERY_THRESHOLD,
    MAX_CONFIDENCE,
    ConfidenceScorer,
)

NOW = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def scorer() -> ConfidenceScorer:
    return ConfidenceScorer(now=NOW)


def _score(scorer, *, seq=("/a", "/b", "/c"), rate=1.0, n=10, days=0):
    return scorer.score_parts(
        skill_sequence=seq,
        success_rate=rate,
        observation_count=n,
        last_observed=NOW - timedelta(days=days),
    )


# ── base_rate ───────────────────────────────────────────────────────────────


class TestBaseRate:
    @pytest.mark.parametrize(
        "length,expected",
        [(1, 0.40), (2, 0.65), (3, 0.75), (4, 0.85), (5, 0.88), (6, 0.90)],
    )
    def test_table_matches_adr_snippet(self, scorer, length, expected):
        """The executable ADR table, which this module follows over the prose."""
        seq = tuple(f"/s{i}" for i in range(length))
        assert scorer._base_rate_of_sequence(seq) == pytest.approx(expected)

    def test_empty_sequence_scores_zero_not_point_four(self, scorer):
        """A method with no steps is not a method; the ADR table's index 0 (0.4)
        would give it a real prior."""
        assert scorer._base_rate_of_sequence(()) == 0.0

    @pytest.mark.parametrize("length", [7, 8, 12, 50])
    def test_long_sequences_clamp_instead_of_indexerror(self, scorer, length):
        """The ADR snippet indexes a 7-element list unguarded and raises here."""
        seq = tuple(f"/s{i}" for i in range(length))
        assert scorer._base_rate_of_sequence(seq) == pytest.approx(0.90)

    def test_exotic_combo_boosts(self, scorer):
        plain = scorer._base_rate_of_sequence(("/a", "/b", "/c"))
        exotic = scorer._base_rate_of_sequence(("/dialectical-reasoning", "/b", "/c"))
        assert exotic > plain
        assert exotic == pytest.approx(plain * 1.10)

    def test_exotic_needs_min_length(self, scorer):
        """One exotic skill alone is a habit, not a method."""
        assert not scorer._is_exotic_combo(("/security-review",))
        assert not scorer._is_exotic_combo(("/security-review", "/b"))
        assert scorer._is_exotic_combo(("/security-review", "/b", "/c"))

    def test_base_rate_never_exceeds_cap(self, scorer):
        seq = ("/security-review",) + tuple(f"/s{i}" for i in range(10))
        assert scorer._base_rate_of_sequence(seq) <= MAX_CONFIDENCE


# ── success_boost ───────────────────────────────────────────────────────────


class TestSuccessBoost:
    @pytest.mark.parametrize("n", [0, 1, 2])
    def test_tiny_sample_is_capped_at_point_seven(self, scorer, n):
        assert scorer._success_boost(1.0, n) == pytest.approx(0.7)

    @pytest.mark.parametrize("n", [3, 5, 9])
    def test_small_sample_is_capped_at_point_nine(self, scorer, n):
        assert scorer._success_boost(1.0, n) == pytest.approx(0.9)

    @pytest.mark.parametrize("n", [10, 25, 100])
    def test_large_sample_is_capped_at_point_nine_five(self, scorer, n):
        assert scorer._success_boost(1.0, n) == pytest.approx(0.95)

    @pytest.mark.parametrize("rate", [0.0, 0.25, 0.5, 0.65, 0.9, 1.0])
    def test_monotonic_in_success_rate(self, scorer, rate):
        assert scorer._success_boost(rate, 20) == pytest.approx(0.7 + rate * 0.25)

    def test_perfect_small_sample_scores_below_good_large_sample(self, scorer):
        """3/3 is luck; 90% over 30 is a method. The ordering is the point."""
        assert scorer._success_boost(1.0, 2) < scorer._success_boost(0.9, 30)

    def test_zero_success_rate_still_positive(self, scorer):
        """A failing pattern is weak evidence, not negative evidence."""
        assert scorer._success_boost(0.0, 50) == pytest.approx(0.7)


# ── sample_size_boost ───────────────────────────────────────────────────────


class TestSampleSizeBoost:
    @pytest.mark.parametrize(
        "n,expected",
        [
            (0, 0.40), (1, 0.40), (2, 0.40),
            (3, 0.60), (4, 0.60),
            (5, 0.80), (9, 0.80),
            (10, 0.90), (19, 0.90),
            (20, 0.95), (29, 0.95),
            (30, 0.99), (100, 0.99),
        ],
    )
    def test_step_function_matches_adr(self, scorer, n, expected):
        assert scorer._sample_size_boost(n) == pytest.approx(expected)

    def test_monotonic_non_decreasing(self, scorer):
        values = [scorer._sample_size_boost(n) for n in range(0, 101)]
        assert values == sorted(values)


# ── recency_boost ───────────────────────────────────────────────────────────


class TestRecencyBoost:
    @pytest.mark.parametrize("days", [0, 1, 7])
    def test_fresh_is_full(self, scorer, days):
        assert scorer._recency_boost(NOW - timedelta(days=days)) == pytest.approx(1.0)

    def test_thirty_days_is_point_nine_five(self, scorer):
        assert scorer._recency_boost(NOW - timedelta(days=30)) == pytest.approx(0.95)

    def test_sixty_days_is_point_eight(self, scorer):
        assert scorer._recency_boost(NOW - timedelta(days=60)) == pytest.approx(0.80)

    def test_never_below_floor(self, scorer):
        assert scorer._recency_boost(NOW - timedelta(days=5000)) == pytest.approx(0.60)

    def test_monotonic_non_increasing_over_two_years(self, scorer):
        values = [scorer._recency_boost(NOW - timedelta(days=d)) for d in range(0, 730, 7)]
        assert values == sorted(values, reverse=True)

    def test_future_timestamp_cannot_raise_confidence(self, scorer):
        """Clock skew must not become a confidence boost."""
        assert scorer._recency_boost(NOW + timedelta(days=90)) == pytest.approx(1.0)

    def test_naive_datetime_is_read_as_utc(self, scorer):
        naive = (NOW - timedelta(days=45)).replace(tzinfo=None)
        assert scorer._recency_boost(naive) == pytest.approx(
            scorer._recency_boost(NOW - timedelta(days=45))
        )


# ── combined score ──────────────────────────────────────────────────────────


class TestCombinedScore:
    def test_product_is_exactly_the_four_factors(self, scorer):
        b = _score(scorer, seq=("/a", "/b", "/c", "/d"), rate=0.9, n=20, days=15)
        assert b.confidence == pytest.approx(
            b.base_rate * b.success_boost * b.sample_size_boost * b.recency_boost
        )

    @pytest.mark.parametrize("n", [1, 2, 3, 5, 10, 20, 30, 50, 100])
    @pytest.mark.parametrize("rate", [0.0, 0.5, 1.0])
    def test_never_exceeds_cap(self, scorer, n, rate):
        b = _score(scorer, seq=("/security-review", "/a", "/b", "/c", "/d", "/e"), rate=rate, n=n)
        assert b.confidence <= MAX_CONFIDENCE

    def test_single_observation_is_far_below_threshold(self, scorer):
        b = _score(scorer, rate=1.0, n=1)
        assert b.confidence < DISCOVERY_THRESHOLD
        assert b.confidence == pytest.approx(0.75 * 0.7 * 0.40)

    def test_adr_canonical_three_skill_sequence_cannot_clear_threshold(self, scorer):
        """ADR-0548's own worked example: [dialectical, loop, e2e] never reaches
        0.78 statistically, at any N, at 100% success. It is discoverable only
        via explicit user confirmation — which is exactly what the ADR's
        DIAGNOSIS paragraph concludes. Guards the finding against a future
        'fix' that quietly raises the base-rate table."""
        best = _score(scorer, seq=("/dialectical-reasoning", "/loop", "/e2e"), rate=1.0, n=100)
        assert best.confidence < DISCOVERY_THRESHOLD
        assert ConfidenceScorer.is_discoverable(best.confidence) is False
        assert ConfidenceScorer.is_discoverable(best.confidence, user_confirmed=True) is True

    def test_four_skill_sequence_can_clear_threshold(self, scorer):
        """The Phase-1 gate is reachable: 4 skills, perfect, N>=30."""
        b = _score(scorer, seq=("/a", "/b", "/c", "/d"), rate=1.0, n=30)
        assert b.confidence >= DISCOVERY_THRESHOLD

    def test_stale_pattern_falls_below_threshold(self, scorer):
        fresh = _score(scorer, seq=("/a", "/b", "/c", "/d"), rate=1.0, n=30, days=1)
        stale = _score(scorer, seq=("/a", "/b", "/c", "/d"), rate=1.0, n=30, days=400)
        assert fresh.confidence >= DISCOVERY_THRESHOLD
        assert stale.confidence < DISCOVERY_THRESHOLD

    def test_breakdown_is_frozen_and_hashable(self, scorer):
        b = _score(scorer)
        assert hash(b) is not None
        with pytest.raises(Exception):
            b.confidence = 0.99  # type: ignore[misc]

    def test_breakdown_payload_is_all_primitives(self, scorer):
        payload = _score(scorer).to_payload()
        assert all(isinstance(v, (int, float, bool, str)) for v in payload.values())

    def test_explain_mentions_every_factor(self, scorer):
        text = _score(scorer, seq=("/dialectical-reasoning", "/a", "/b")).explain()
        for token in ("base", "success", "sample", "recency", "exotic"):
            assert token in text


# ── input validation (fail-closed, not clamped) ─────────────────────────────


class TestValidation:
    @pytest.mark.parametrize("rate", [-0.01, 1.01, 2.0, -5.0])
    def test_out_of_range_success_rate_raises(self, scorer, rate):
        with pytest.raises(ValueError, match="success_rate"):
            _score(scorer, rate=rate)

    def test_negative_observation_count_raises(self, scorer):
        with pytest.raises(ValueError, match="observation_count"):
            _score(scorer, n=-1)

    def test_nan_success_rate_raises(self, scorer):
        with pytest.raises(ValueError):
            _score(scorer, rate=float("nan"))


# ── decision gates ──────────────────────────────────────────────────────────


class TestDecisionGates:
    def test_discoverable_at_threshold(self):
        assert ConfidenceScorer.is_discoverable(DISCOVERY_THRESHOLD) is True
        assert ConfidenceScorer.is_discoverable(DISCOVERY_THRESHOLD - 0.001) is False

    def test_user_confirmation_overrides_low_confidence(self):
        assert ConfidenceScorer.is_discoverable(0.10, user_confirmed=True) is True

    def test_autonomous_bar_is_higher_and_needs_confirmation(self):
        """Recommending is cheap; acting unasked is not. Confidence alone is
        never sufficient for autonomous application."""
        assert ConfidenceScorer.is_autonomously_applicable(0.94) is False
        assert ConfidenceScorer.is_autonomously_applicable(0.94, user_confirmed=True) is True
        assert ConfidenceScorer.is_autonomously_applicable(0.80, user_confirmed=True) is False

    def test_thresholds_are_ordered(self):
        assert DISCOVERY_THRESHOLD < AUTONOMOUS_THRESHOLD < MAX_CONFIDENCE
