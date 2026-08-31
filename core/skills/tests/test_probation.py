"""Unit tests for probation.py (ADR-0421 bootstrap grades)."""

import pytest
from datetime import datetime, timedelta, timezone
from core.skills.corvin_skills.probation import (
    is_in_probation,
    apply_bootstrap_grade,
    should_apply_probation_cap,
    exit_probation,
)


class TestIsInProbation:
    """Test probation window detection."""

    def test_newly_created_durable_skill_is_in_probation(self):
        """Skill created < 24h ago enters probation."""
        now = datetime.now(timezone.utc)
        created_at = now - timedelta(hours=12)

        manifest_entry = {
            "lifecycle": "durable",
            "created_at": created_at.isoformat(),
        }

        assert is_in_probation("skill.name", manifest_entry, now=now) is True

    def test_old_durable_skill_exits_probation(self):
        """Skill created > 24h ago exits probation."""
        now = datetime.now(timezone.utc)
        created_at = now - timedelta(hours=25)

        manifest_entry = {
            "lifecycle": "durable",
            "created_at": created_at.isoformat(),
        }

        assert is_in_probation("skill.name", manifest_entry, now=now) is False

    def test_session_scoped_skill_never_enters_probation(self):
        """Session/turn skills bypass probation."""
        now = datetime.now(timezone.utc)
        created_at = now - timedelta(hours=1)

        for lifecycle in ["session", "turn"]:
            manifest_entry = {
                "lifecycle": lifecycle,
                "created_at": created_at.isoformat(),
            }
            assert is_in_probation("skill.name", manifest_entry, now=now) is False

    def test_missing_created_at_returns_false(self):
        """Malformed entry (no created_at) is not in probation."""
        now = datetime.now(timezone.utc)
        manifest_entry = {"lifecycle": "durable"}

        assert is_in_probation("skill.name", manifest_entry, now=now) is False

    def test_exactly_24h_boundary(self):
        """Skill created exactly 24h ago exits probation."""
        now = datetime.now(timezone.utc)
        created_at = now - timedelta(hours=24)

        manifest_entry = {
            "lifecycle": "durable",
            "created_at": created_at.isoformat(),
        }

        # Just past 24h, should be false
        assert is_in_probation("skill.name", manifest_entry, now=now) is False
        # Just before 24h, should be true
        now_minus_1s = now - timedelta(seconds=1)
        assert is_in_probation("skill.name", manifest_entry, now=now_minus_1s) is True


class TestApplyBootstrapGrade:
    """Test score capping to 0.3."""

    def test_score_above_03_is_capped(self):
        """Scores > 0.3 are capped at 0.3."""
        assert apply_bootstrap_grade(0.9) == 0.3
        assert apply_bootstrap_grade(1.0) == 0.3

    def test_score_below_03_is_preserved(self):
        """Scores < 0.3 are preserved."""
        assert apply_bootstrap_grade(0.1) == 0.1
        assert apply_bootstrap_grade(0.29) == 0.29

    def test_exactly_03_is_preserved(self):
        """Score of 0.3 is not changed."""
        assert apply_bootstrap_grade(0.3) == 0.3

    def test_negative_score_clamped_to_zero(self):
        """Negative scores are clamped to 0.0."""
        assert apply_bootstrap_grade(-0.5) == 0.0

    def test_zero_score(self):
        """Score of 0.0 is preserved."""
        assert apply_bootstrap_grade(0.0) == 0.0


class TestShouldApplyProbationCap:
    """Test when probation cap should be applied."""

    def test_cap_applied_to_first_grade(self):
        """First grade of new skill is capped."""
        now = datetime.now(timezone.utc)
        created_at = now - timedelta(hours=1)

        manifest_entry = {
            "lifecycle": "durable",
            "created_at": created_at.isoformat(),
            "metadata": {},
        }

        assert should_apply_probation_cap("skill.name", manifest_entry) is True

    def test_cap_not_applied_after_bootstrap_seeded(self):
        """Once bootstrap grade is seeded, cap doesn't reapply."""
        now = datetime.now(timezone.utc)
        created_at = now - timedelta(hours=1)

        manifest_entry = {
            "lifecycle": "durable",
            "created_at": created_at.isoformat(),
            "metadata": {
                "bootstrap_score": 0.3,
                "bootstrap_grade_at": now.isoformat(),
            },
        }

        assert should_apply_probation_cap("skill.name", manifest_entry) is False

    def test_cap_not_applied_past_24h(self):
        """Probation expires after 24h."""
        now = datetime.now(timezone.utc)
        created_at = now - timedelta(hours=25)

        manifest_entry = {
            "lifecycle": "durable",
            "created_at": created_at.isoformat(),
            "metadata": {},
        }

        assert should_apply_probation_cap("skill.name", manifest_entry) is False


class TestExitProbation:
    """Test probation exit logic."""

    def test_clears_bootstrap_markers(self):
        """Exiting probation removes bootstrap metadata."""
        manifest_entry = {
            "name": "skill.name",
            "metadata": {
                "bootstrap_score": 0.3,
                "bootstrap_grade_at": "2026-08-23T10:15:00Z",
                "other_field": "preserved",
            },
        }

        result = exit_probation(manifest_entry)

        assert "bootstrap_score" not in result["metadata"]
        assert "bootstrap_grade_at" not in result["metadata"]
        assert result["metadata"]["other_field"] == "preserved"

    def test_handles_missing_metadata(self):
        """Exiting probation handles missing metadata gracefully."""
        manifest_entry = {"name": "skill.name"}

        result = exit_probation(manifest_entry)

        assert result["name"] == "skill.name"
        # No error; metadata dict created if needed
