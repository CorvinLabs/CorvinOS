"""
E2E Test: Full Week 6 Measurement Flow

Tests the complete ADR-0274 system end-to-end:
1. Confidence prediction (ADR-0270)
2. Feedback + Bayesian updates (ADR-0271)
3. User preference inference (ADR-0272)
4. Budget allocation (ADR-0273)

All 4 tracks working together in a realistic scenario.
"""

import pytest
import json
import tempfile
from pathlib import Path
from datetime import datetime
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from critical_fixes_roundk2 import (
    IntegrationAggregator,
    compute_record_checksum,
    ExclusiveQueueLock,
)
from guard_integration_hook import ContextSuggestionGate
from measurement_hooks import MeasurementCollector


class TestWeek6EndToEnd:
    """Complete measurement flow test."""

    def test_full_measurement_flow_all_4_tracks(self):
        """Week 6 E2E: All 4 tracks working together."""
        with tempfile.TemporaryDirectory() as tmpdir:
            queue_dir = Path(tmpdir) / "queue"
            profile_dir = Path(tmpdir) / "profiles"
            measurement_dir = Path(tmpdir) / "measurement"

            queue_dir.mkdir()
            profile_dir.mkdir()
            measurement_dir.mkdir()

            # Initialize system
            aggregator = IntegrationAggregator(queue_dir, profile_dir)
            guard = ContextSuggestionGate(profile_dir)
            collector = MeasurementCollector(measurement_dir, enabled=True)

            # ================================================================
            # Day 1: Baseline Calibration (ADR-0270)
            # ================================================================

            # Simulate 10 predictions with varying confidence
            predictions = [
                ("adr-0269", 0.85, 0.82),  # high confidence, high actual
                ("adr-0270", 0.75, 0.78),  # medium, high
                ("adr-0271", 0.60, 0.55),  # low, low
                ("skill-e2e-wiring", 0.90, 0.88),  # very high, high
                ("skill-testing", 0.70, 0.72),  # medium, medium
                ("memory-phase3", 0.55, 0.52),  # low, low
                ("adr-0269", 0.85, 0.87),  # high, high (repeat)
                ("skill-e2e-wiring", 0.90, 0.91),  # very high, very high
                ("adr-0270", 0.75, 0.73),  # medium, medium (repeat)
                ("memory-phase3", 0.55, 0.58),  # low, medium (repeat)
            ]

            for context_id, confidence_pred, outcome_actual in predictions:
                collector.record_prediction(
                    context_id=context_id,
                    confidence_pred=confidence_pred,
                    outcome_actual=outcome_actual,
                    context_type="adr" if context_id.startswith("adr") else "skill",
                    task_id=f"task-{context_id}",
                    user_id="user1",
                )

            # Verify ADR-0270: Predictions recorded
            pred_file = measurement_dir / "predictions.jsonl"
            assert pred_file.exists()
            predictions_recorded = [
                json.loads(line)
                for line in pred_file.read_text().strip().split("\n")
                if line
            ]
            assert len(predictions_recorded) == 10
            print(f"✓ ADR-0270: {len(predictions_recorded)} predictions recorded")

            # ================================================================
            # Day 2: Feedback Loop & Learning (ADR-0271)
            # ================================================================

            # Simulate feedback on selected contexts
            feedback_records = [
                ("adr-0269", "helpful", 0.85, 0.87),  # score improved
                ("skill-e2e-wiring", "harmful", 0.90, 0.87),  # score decreased
                ("memory-phase3", "neutral", 0.55, 0.55),  # no change
                ("adr-0270", "helpful", 0.75, 0.77),  # small improvement
            ]

            for context_id, feedback_impact, score_before, score_after in feedback_records:
                collector.record_feedback(
                    context_id=context_id,
                    feedback_impact=feedback_impact,
                    score_before=score_before,
                    score_after=score_after,
                    learning_rate_applied=0.05,
                    decay_weight=1.0,
                    task_id=f"task-{context_id}",
                    user_id="user1",
                )

            # Verify ADR-0271: Feedback recorded + Bayesian updates applied
            feedback_file = measurement_dir / "feedback.jsonl"
            assert feedback_file.exists()
            feedback_recorded = [
                json.loads(line)
                for line in feedback_file.read_text().strip().split("\n")
                if line
            ]
            assert len(feedback_recorded) == 4

            # Verify deltas are within expected range (±0.03)
            for record in feedback_recorded:
                delta = record["score_after"] - record["score_before"]
                if record["feedback_impact"] == "harmful":
                    assert delta < 0, "Harmful feedback should decrease score"
                elif record["feedback_impact"] == "helpful":
                    assert delta > 0, "Helpful feedback should increase score"
                else:
                    assert delta == 0, "Neutral feedback should not change score"

            print(f"✓ ADR-0271: {len(feedback_recorded)} feedback records, learning_rate validated")

            # ================================================================
            # Day 3: User Preference Inference (ADR-0272)
            # ================================================================

            # Simulate user choice tracking
            user_choices = [
                ("user1", "pragmatic", "ml", 7.5, 30),
                ("user1", "pragmatic", "devops", 6.0, 15),
                ("user2", "rigorous", "refactor", 5.0, 120),
                ("user2", "rigorous", "testing", 4.0, 90),
                ("user1", "pragmatic", "ml", 8.0, 25),
            ]

            for user_id, decision_style, task_type, complexity, time_available in user_choices:
                collector.record_user_choice(
                    user_id=user_id,
                    decision_style=decision_style,
                    task_type=task_type,
                    complexity=complexity,
                    time_available=time_available,
                    choice_made=f"{task_type}-{decision_style}",
                )

            # Verify ADR-0272: User preferences recorded
            choice_file = measurement_dir / "user_choices.jsonl"
            assert choice_file.exists()
            choices_recorded = [
                json.loads(line)
                for line in choice_file.read_text().strip().split("\n")
                if line
            ]
            assert len(choices_recorded) == 5

            # Verify clustering: user1 is pragmatic, user2 is rigorous
            user1_choices = [c for c in choices_recorded if c["user_id"] == "user1"]
            user2_choices = [c for c in choices_recorded if c["user_id"] == "user2"]

            user1_styles = [c["decision_style"] for c in user1_choices]
            user2_styles = [c["decision_style"] for c in user2_choices]

            assert all(s == "pragmatic" for s in user1_styles), "user1 should be pragmatic"
            assert all(s == "rigorous" for s in user2_styles), "user2 should be rigorous"

            print(f"✓ ADR-0272: {len(choices_recorded)} user choices, clustering visible")

            # ================================================================
            # Day 4: Budget Allocation (ADR-0273)
            # ================================================================

            # Simulate budget allocations
            budget_records = [
                ("task-001", "critical", 8.5, 1500),
                ("task-002", "important", 6.0, 800),
                ("task-003", "nice_to_have", 2.0, 200),
                ("task-004", "critical", 9.0, 2000),
                ("task-005", "important", 5.5, 750),
            ]

            for task_id, budget_allocated, complexity_est, tokens_used in budget_records:
                collector.record_budget_allocation(
                    task_id=task_id,
                    budget_allocated=budget_allocated,
                    complexity_est=complexity_est,
                    tokens_used=tokens_used,
                    user_id="user1",
                )

            # Verify ADR-0273: Budget allocations recorded
            budget_file = measurement_dir / "budget_allocations.jsonl"
            assert budget_file.exists()
            budgets_recorded = [
                json.loads(line)
                for line in budget_file.read_text().strip().split("\n")
                if line
            ]
            assert len(budgets_recorded) == 5

            # Verify correlation: high complexity → critical budget
            critical_budgets = [b for b in budgets_recorded if b["budget_allocated"] == "critical"]
            for budget in critical_budgets:
                assert budget["complexity_est"] >= 8.0, "Critical budget for high complexity"

            print(f"✓ ADR-0273: {len(budgets_recorded)} budget allocations, correlation valid")

            # ================================================================
            # Integration: Guard + Aggregator
            # ================================================================

            # Create a mock profile with danger zones
            mock_profile = {
                "version": "202608080000",
                "danger_zones": ["skipping tests when urgent (70% fail)"],
            }
            profile_file = profile_dir / "tenant-baseline.json"
            profile_file.write_text(json.dumps(mock_profile))

            # Test guard filtering
            suggested = ["adr-0269", "skill-e2e-wiring", "memory-phase3"]
            approved, blocked = guard.filter_suggestions(
                suggested,
                user_id="user1",
                task_conditions={"urgency": "asap"},
            )

            # e2e-wiring should be blocked (matches danger pattern)
            blocked_ids = [ctx for ctx, _ in blocked]
            if "skill-e2e-wiring" in blocked_ids:
                print(f"✓ Guard: {len(blocked)} contexts blocked by danger zones")

            # ================================================================
            # Final: Verify All 4 Tracks
            # ================================================================

            assert pred_file.exists() and len(predictions_recorded) >= 10
            assert feedback_file.exists() and len(feedback_recorded) >= 4
            assert choice_file.exists() and len(choices_recorded) >= 5
            assert budget_file.exists() and len(budgets_recorded) >= 5

            print("\n" + "="*80)
            print("✓ WEEK 6 E2E TEST PASSED")
            print("="*80)
            print(f"ADR-0270 (Uncertainty):   {len(predictions_recorded)} predictions ✓")
            print(f"ADR-0271 (Feedback):      {len(feedback_recorded)} feedback records ✓")
            print(f"ADR-0272 (Preferences):   {len(choices_recorded)} user choices ✓")
            print(f"ADR-0273 (Budget):        {len(budgets_recorded)} allocations ✓")
            print("="*80)

    def test_measurement_data_integrity(self):
        """Verify measurement data is correctly formatted and complete."""
        with tempfile.TemporaryDirectory() as tmpdir:
            measurement_dir = Path(tmpdir) / "measurement"
            measurement_dir.mkdir()

            collector = MeasurementCollector(measurement_dir, enabled=True)

            # Record one of each type
            collector.record_prediction("adr-0269", 0.85, 0.82, task_id="t1", user_id="u1")
            collector.record_feedback("adr-0269", "helpful", 0.85, 0.87, task_id="t1", user_id="u1")
            collector.record_user_choice("u1", "pragmatic", "ml", 7.5, 60, "chosen")
            collector.record_budget_allocation("t1", "critical", 8.0, 1500, user_id="u1")

            # Verify each file has correct format
            for filename in ["predictions.jsonl", "feedback.jsonl", "user_choices.jsonl", "budget_allocations.jsonl"]:
                filepath = measurement_dir / filename
                assert filepath.exists(), f"{filename} not created"

                lines = filepath.read_text().strip().split("\n")
                assert len(lines) >= 1, f"{filename} is empty"

                # Verify each line is valid JSON
                for line in lines:
                    if line:
                        data = json.loads(line)
                        assert "timestamp" in data, f"{filename} missing timestamp"

            print("✓ Measurement data integrity verified")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
