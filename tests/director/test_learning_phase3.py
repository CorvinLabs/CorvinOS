"""Phase 3: Style Learning Loop Tests

Tests for:
- Feedback schema validation
- Style learning and preferences
- Profile persistence
"""

import pytest
import tempfile
from pathlib import Path
from core.skills.os_skills.video_producer.src.director.learning.feedback_schema import (
    FeedbackSchema, FeedbackType
)
from core.skills.os_skills.video_producer.src.director.learning.style_learner import StyleLearner, StyleProfile


class TestFeedbackSchema:
    """Test feedback schema validation"""

    def test_feedback_validation_valid(self):
        """Valid feedback is accepted"""
        assert FeedbackSchema.validate_feedback("more_animations")
        assert FeedbackSchema.validate_feedback("less_text")
        assert FeedbackSchema.validate_feedback("faster_pacing")

    def test_feedback_validation_invalid(self):
        """Invalid feedback is rejected"""
        assert not FeedbackSchema.validate_feedback("make_better")
        assert not FeedbackSchema.validate_feedback("improve")
        assert not FeedbackSchema.validate_feedback("random_feedback")

    def test_feedback_list_validation(self):
        """Validate lists of feedback"""
        valid, invalid = FeedbackSchema.validate_feedback_list([
            "more_animations",
            "less_text",
            "invalid_feedback"
        ])
        assert valid == False
        assert "invalid_feedback" in invalid

    def test_feedback_description(self):
        """Get feedback descriptions"""
        desc = FeedbackSchema.get_feedback_description("more_animations")
        assert "animation" in desc.lower()

    def test_list_valid_feedback(self):
        """List all valid feedback types"""
        valid_feedback = FeedbackSchema.list_valid_feedback()
        assert len(valid_feedback) > 10
        assert "more_animations" in valid_feedback

    def test_feedback_mapping(self):
        """Get parameter mapping for feedback"""
        mapping = FeedbackSchema.get_feedback_mapping("more_animations")
        assert mapping.target_parameter == "animation_level"
        assert mapping.delta > 0

    def test_feedback_for_scene_type(self):
        """Get applicable feedback for scene types"""
        technical_feedback = FeedbackSchema.get_feedback_for_scene_type("technical")
        assert len(technical_feedback) > 0
        assert "more_technical" in technical_feedback or "less_technical" in technical_feedback


class TestStyleLearner:
    """Test style learning and user preferences"""

    def test_profile_creation(self):
        """Create new style profile"""
        learner = StyleLearner()
        profile = learner.get_or_create_profile("user1")
        assert profile.user_id == "user1"
        assert profile.animation_level == 0.5  # Default

    def test_profile_persistence(self):
        """Profile persists across calls"""
        learner = StyleLearner()
        profile1 = learner.get_or_create_profile("user1")
        profile1.animation_level = 0.8

        profile2 = learner.get_or_create_profile("user1")
        assert profile2.animation_level == 0.8

    def test_feedback_updates_profile(self):
        """Feedback updates user preferences"""
        learner = StyleLearner()
        learner.record_feedback("user1", "video1", ["more_animations"])

        profile = learner.get_or_create_profile("user1")
        assert profile.animation_level > 0.5  # Should increase

    def test_multiple_feedback_accumulation(self):
        """Multiple feedback items accumulate"""
        learner = StyleLearner()
        learner.record_feedback("user1", "video1", ["more_animations", "faster_pacing"])

        profile = learner.get_or_create_profile("user1")
        assert profile.animation_level > 0.5
        assert profile.overall_speed > 1.0

    def test_feedback_validation(self):
        """Invalid feedback rejected"""
        learner = StyleLearner()
        result = learner.record_feedback("user1", "video1", ["invalid_feedback"])
        assert result == False

    def test_style_applied_to_narrative(self):
        """Learned style applied to new narratives"""
        learner = StyleLearner()
        # Apply multiple feedback to get text_density well below 0.3
        learner.record_feedback("user1", "video1", ["less_text"])
        learner.record_feedback("user1", "video2", ["less_text"])

        narrative = {
            "scenes": [
                {
                    "id": "s1",
                    "duration_seconds": 10,
                    "text_overlays": ["Text 1", "Text 2", "Text 3"]
                }
            ]
        }

        modified = learner.apply_learned_style("user1", narrative)
        # Text overlays should be reduced when text_density is low
        if modified["scenes"][0].get("text_overlays"):
            # After multiple less_text feedbacks, overlays should be minimal
            assert len(modified["scenes"][0]["text_overlays"]) <= 1

    def test_profile_summary(self):
        """Get profile summary"""
        learner = StyleLearner()
        learner.record_feedback("user1", "video1", ["more_animations"])

        summary = learner.get_profile_summary("user1")
        assert summary["user_id"] == "user1"
        assert summary["feedback_count"] == 1
        assert "preferences" in summary

    def test_parameter_clamping(self):
        """Parameters clamped to valid ranges"""
        learner = StyleLearner()
        profile = learner.get_or_create_profile("user1")

        # Apply many same-direction feedbacks
        for _ in range(10):
            learner.record_feedback("user1", f"v{_}", ["more_animations"])

        profile = learner.get_or_create_profile("user1")
        assert 0.0 <= profile.animation_level <= 1.0


class TestProfilePersistence:
    """Test profile storage and loading"""

    def test_profile_file_storage(self):
        """Profiles saved to files"""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage_path = Path(tmpdir)
            learner = StyleLearner(storage_path=storage_path)

            learner.record_feedback("user1", "video1", ["more_animations"])

            # Check file was created
            profile_file = storage_path / "user1.json"
            assert profile_file.exists()

    def test_profile_loading_from_storage(self):
        """Profiles loaded from storage"""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage_path = Path(tmpdir)

            # Save profile
            learner1 = StyleLearner(storage_path=storage_path)
            learner1.record_feedback("user1", "video1", ["more_animations"])

            # Load profile with new instance
            learner2 = StyleLearner(storage_path=storage_path)
            profile = learner2.get_or_create_profile("user1")
            assert profile.animation_level > 0.5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
