"""
Tests for Your Talent Score Calculator (CONCEPT-0003)
"""

import pytest
from pathlib import Path
from datetime import datetime
import json
import tempfile

# Import from parent directory
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from talent_score import TalentScoreCalculator


@pytest.fixture
def temp_measurement_dir():
    """Create temporary measurement directory with test data."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)

        # Create today's directory
        today = datetime.utcnow().strftime("%Y-%m-%d")
        date_dir = tmp_path / today
        date_dir.mkdir(parents=True)

        # Create predictions.jsonl
        predictions = [
            {"confidence_pred": 0.75, "outcome_actual": 0.78},
            {"confidence_pred": 0.82, "outcome_actual": 0.80},
            {"confidence_pred": 0.88, "outcome_actual": 0.90},
        ]
        with open(date_dir / "predictions.jsonl", "w") as f:
            for p in predictions:
                f.write(json.dumps(p) + "\n")

        # Create feedback.jsonl
        feedback = [
            {"feedback_impact": "helpful"},
            {"feedback_impact": "helpful"},
            {"feedback_impact": "neutral"},
        ]
        with open(date_dir / "feedback.jsonl", "w") as f:
            for fb in feedback:
                f.write(json.dumps(fb) + "\n")

        # Create user_choices.jsonl
        choices = [
            {"task_type": "ml"},
            {"task_type": "devops"},
            {"task_type": "refactor"},
        ]
        with open(date_dir / "user_choices.jsonl", "w") as f:
            for c in choices:
                f.write(json.dumps(c) + "\n")

        # Create budget_allocations.jsonl
        budget = [
            {"match_score": 0.95},
            {"match_score": 0.85},
            {"match_score": 0.90},
        ]
        with open(date_dir / "budget_allocations.jsonl", "w") as f:
            for b in budget:
                f.write(json.dumps(b) + "\n")

        yield tmp_path


class TestTalentScoreCalculator:
    """Test the TalentScoreCalculator."""

    def test_compute_accuracy(self, temp_measurement_dir):
        """Test accuracy calculation (ADR-0270)."""
        calc = TalentScoreCalculator(temp_measurement_dir)
        predictions = [
            {"confidence_pred": 0.75, "outcome_actual": 0.78},
            {"confidence_pred": 0.82, "outcome_actual": 0.80},
        ]

        accuracy = calc.compute_accuracy(predictions)
        # Accuracy = 1.0 - avg(|pred - actual|)
        # = 1.0 - ((0.03 + 0.02) / 2) = 1.0 - 0.025 = 0.975
        assert 0.97 < accuracy <= 1.0

    def test_compute_learning_rate(self, temp_measurement_dir):
        """Test learning rate calculation (ADR-0271)."""
        calc = TalentScoreCalculator(temp_measurement_dir)
        feedback = [
            {"feedback_impact": "helpful"},
            {"feedback_impact": "helpful"},
            {"feedback_impact": "neutral"},
        ]

        learning = calc.compute_learning_rate(feedback)
        # Learning = helpful % = 2/3 ≈ 0.667
        assert 0.65 < learning < 0.70

    def test_compute_variety(self, temp_measurement_dir):
        """Test variety calculation (ADR-0272)."""
        calc = TalentScoreCalculator(temp_measurement_dir)
        choices = [
            {"task_type": "ml"},
            {"task_type": "devops"},
            {"task_type": "refactor"},
        ]

        variety = calc.compute_variety(choices)
        # Variety = unique task types / 10 = 3/10 = 0.3
        assert variety == 0.3

    def test_compute_efficiency(self, temp_measurement_dir):
        """Test efficiency calculation (ADR-0273)."""
        calc = TalentScoreCalculator(temp_measurement_dir)
        budget = [
            {"match_score": 0.95},
            {"match_score": 0.85},
            {"match_score": 0.90},
        ]

        efficiency = calc.compute_efficiency(budget)
        # Efficiency = avg(match_score) = (0.95 + 0.85 + 0.90) / 3 ≈ 0.9
        assert 0.89 < efficiency < 0.91

    def test_compute_talent_score(self, temp_measurement_dir):
        """Test full talent score calculation."""
        calc = TalentScoreCalculator(temp_measurement_dir)
        records = calc.get_recent_records(days=7)

        score, components = calc.compute_talent_score(records)

        # Score should be 0-10
        assert 0 <= score <= 10

        # Components should exist
        assert "accuracy" in components
        assert "learning_rate" in components
        assert "variety" in components
        assert "efficiency" in components

    def test_context_ranking(self, temp_measurement_dir):
        """Test context ranking."""
        calc = TalentScoreCalculator(temp_measurement_dir)
        records = calc.get_recent_records(days=7)

        ranking = calc.compute_context_ranking(records)

        # Should have at least one context
        assert len(ranking) > 0

        # Top context should have rank 1
        assert ranking[0]["rank"] == 1
        assert ranking[0]["medal"] == "🏆"

        # Should have status
        assert "status" in ranking[0]

    def test_learning_events(self, temp_measurement_dir):
        """Test learning events extraction."""
        calc = TalentScoreCalculator(temp_measurement_dir)
        records = calc.get_recent_records(days=7)

        events = calc.compute_learning_events(records)

        # Should have events
        assert isinstance(events, list)

        # Each event should have required fields
        for event in events:
            assert "timestamp" in event
            assert "type" in event
            assert "title" in event
            assert "description" in event

    def test_generate_talent_report(self, temp_measurement_dir):
        """Test complete talent report generation."""
        calc = TalentScoreCalculator(temp_measurement_dir)

        report = calc.generate_talent_report(days=7)

        # Should have all sections
        assert "timestamp" in report
        assert "talent_score" in report
        assert "trend" in report
        assert "components" in report
        assert "ranking" in report
        assert "events" in report
        assert "record_counts" in report

        # Score should be 0-10
        assert 0 <= report["talent_score"] <= 10

    def test_empty_data(self, temp_measurement_dir):
        """Test with empty data files."""
        calc = TalentScoreCalculator(temp_measurement_dir)

        # Create empty files
        today = datetime.utcnow().strftime("%Y-%m-%d")
        date_dir = temp_measurement_dir / today

        for f in ["predictions.jsonl", "feedback.jsonl", "user_choices.jsonl", "budget_allocations.jsonl"]:
            Path(date_dir / f).touch()

        records = calc.get_recent_records(days=7)
        score, components = calc.compute_talent_score(records)

        # Should return default scores
        assert score > 0  # Not zero
        assert score <= 10

    def test_single_record_each_type(self, temp_measurement_dir):
        """Test with one record of each type."""
        calc = TalentScoreCalculator(temp_measurement_dir)

        records = {
            "predictions": [{"confidence_pred": 0.8, "outcome_actual": 0.8}],
            "feedback": [{"feedback_impact": "helpful"}],
            "choices": [{"task_type": "test"}],
            "budget": [{"match_score": 0.8}],
        }

        score, components = calc.compute_talent_score(records)

        # With perfect predictions and helpful feedback, score should be high
        assert score > 7.0
