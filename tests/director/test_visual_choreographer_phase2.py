"""Phase 2: Visual Choreographer + Pacing Tests

Tests for:
- Visual language mapping
- Pacing intelligence
- Pacing optimization
"""

import pytest
from core.skills.os_skills.video_producer.src.director.visual_choreographer.visual_language import (
    VisualLanguageMapper, EmotionalStyle
)
from core.skills.os_skills.video_producer.src.director.pacing.pacing_intelligence import PacingIntelligence
from core.skills.os_skills.video_producer.src.director.pacing.optimizer import PacingOptimizer, NarrativePhase


class TestVisualLanguageMapping:
    """Test visual language mapping to emotional styles"""

    @pytest.fixture
    def sample_narrative(self):
        """Sample narrative with scenes"""
        return {
            "scenes": [
                {
                    "id": "s1",
                    "narration": "This is an inspiring story about transformation and success",
                    "metadata": {"emotion": "inspiring"}
                },
                {
                    "id": "s2",
                    "narration": "Let's implement the algorithm using Python code",
                    "metadata": {}
                },
                {
                    "id": "s3",
                    "narration": "This is so fun and exciting!",
                    "metadata": {}
                }
            ]
        }

    def test_visual_language_mapping(self, sample_narrative):
        """Map scenes to visual styles"""
        mapper = VisualLanguageMapper()
        visual_plan = mapper.map_scenes_to_visuals(sample_narrative)
        assert len(visual_plan) == 3
        assert visual_plan[0]["emotion"] == "inspiring"

    def test_emotional_style_detection(self, sample_narrative):
        """Detect emotional intent from scene content"""
        mapper = VisualLanguageMapper()
        visual_plan = mapper.map_scenes_to_visuals(sample_narrative)

        # Check emotional styles are detected
        emotions = [vp["emotion"] for vp in visual_plan]
        assert "inspiring" in emotions
        assert "technical" in emotions or "playful" in emotions

    def test_asset_selection(self, sample_narrative):
        """Select appropriate assets for scenes"""
        mapper = VisualLanguageMapper()
        visual_plan = mapper.map_scenes_to_visuals(
            sample_narrative,
            available_assets={
                "diagrams": ["/path/to/diagram.png"],
                "code_snippets": ["/path/to/code.txt"]
            }
        )

        # Each scene should have assets
        for scene_plan in visual_plan:
            assert "selected_assets" in scene_plan

    def test_animation_details_generation(self, sample_narrative):
        """Generate animation details for scenes"""
        mapper = VisualLanguageMapper()
        visual_plan = mapper.map_scenes_to_visuals(sample_narrative)

        for scene_plan in visual_plan:
            assert "animation_details" in scene_plan
            details = scene_plan["animation_details"]
            assert "type" in details
            assert "duration_ms" in details

    def test_color_palette_retrieval(self):
        """Get color palette for visual style"""
        mapper = VisualLanguageMapper()
        palette = mapper.get_color_palette("primary_accent")
        assert "primary" in palette
        assert "accent" in palette
        assert "background" in palette

    def test_visual_plan_summary(self, sample_narrative):
        """Get summary of visual plans"""
        mapper = VisualLanguageMapper()
        mapper.map_scenes_to_visuals(sample_narrative)
        summary = mapper.get_visual_plan_summary()
        assert summary["total_scenes"] == 3
        assert len(summary["emotions"]) > 0


class TestPacingIntelligence:
    """Test intelligent time allocation"""

    @pytest.fixture
    def sample_narrative(self):
        """Sample narrative with scene roles"""
        return {
            "scenes": [
                {
                    "id": "s1",
                    "role_in_narrative": "hook",
                    "narration": "Attention grabber!",
                    "metadata": {"key_messages": ["Main point"]}
                },
                {
                    "id": "s2",
                    "role_in_narrative": "action",
                    "narration": "This is the main content " * 20,
                    "metadata": {"key_messages": ["Point 1", "Point 2"]}
                },
                {
                    "id": "s3",
                    "role_in_narrative": "resolution",
                    "narration": "Conclusion",
                    "metadata": {}
                }
            ]
        }

    def test_time_allocation_sums_to_total(self, sample_narrative):
        """Time allocation across scenes equals total duration"""
        pacing = PacingIntelligence()
        allocation = pacing.allocate_time(sample_narrative, 600)

        total_allocated = sum(allocation.values())
        assert abs(total_allocated - 600) < 1.0

    def test_action_gets_more_time(self, sample_narrative):
        """Action scenes get more time than hook"""
        pacing = PacingIntelligence()
        allocation = pacing.allocate_time(sample_narrative, 600)

        assert allocation["s2"] > allocation["s1"]  # Action > Hook

    def test_importance_scoring(self, sample_narrative):
        """Scenes scored by importance"""
        pacing = PacingIntelligence()
        pacing.allocate_time(sample_narrative, 600)
        scores = pacing.importance_scores

        # Action should have highest importance
        assert scores["s2"].total_score > scores["s1"].total_score

    def test_allocation_summary(self, sample_narrative):
        """Get allocation summary"""
        pacing = PacingIntelligence()
        pacing.allocate_time(sample_narrative, 300)
        summary = pacing.get_allocation_summary()

        assert summary["total_duration"] == 300
        assert summary["scenes"] == 3
        assert "allocations" in summary


class TestPacingOptimizer:
    """Test cinematic pacing optimization"""

    @pytest.fixture
    def scenes_with_time(self):
        """Scenes with default time allocations"""
        return [
            {"id": "s1", "duration_seconds": 30, "role_in_narrative": "hook"},
            {"id": "s2", "duration_seconds": 60, "role_in_narrative": "action"},
            {"id": "s3", "duration_seconds": 60, "role_in_narrative": "action"},
            {"id": "s4", "duration_seconds": 40, "role_in_narrative": "resolution"},
            {"id": "s5", "duration_seconds": 30, "role_in_narrative": "closing"}
        ]

    def test_pacing_optimization(self, scenes_with_time):
        """Apply cinematic pacing rules"""
        optimizer = PacingOptimizer()
        optimized = optimizer.optimize(scenes_with_time)

        assert len(optimized) == 5
        # Check pacing multipliers applied
        assert optimized[0]["pacing_multiplier"] == 0.8  # Hook

    def test_climax_longer_than_hook(self, scenes_with_time):
        """Climax scenes longer than hook"""
        optimizer = PacingOptimizer()
        optimized = optimizer.optimize(scenes_with_time)

        hook_duration = optimized[0]["duration_seconds"]
        climax_duration = optimized[1]["duration_seconds"]
        assert climax_duration > hook_duration

    def test_transition_recommendations(self, scenes_with_time):
        """Each scene has transition recommendation"""
        optimizer = PacingOptimizer()
        optimized = optimizer.optimize(scenes_with_time)

        for scene in optimized:
            assert "transition_type" in scene
            assert scene["transition_type"] in ["cut", "fade", "dissolve"]

    def test_pacing_distribution(self, scenes_with_time):
        """Get pacing distribution across narrative"""
        optimizer = PacingOptimizer()
        optimizer.optimize(scenes_with_time)
        distribution = optimizer.get_pacing_distribution()

        assert "hook" in distribution or "build" in distribution
        total_pct = sum(distribution.values())
        assert abs(total_pct - 1.0) < 0.01  # Should sum to 100%

    def test_pacing_validation(self, scenes_with_time):
        """Validate pacing follows best practices"""
        optimizer = PacingOptimizer()
        optimizer.optimize(scenes_with_time)
        validation = optimizer.validate_pacing()

        assert validation["valid"] == True
        assert validation["total_duration"] > 0

    def test_visual_intensity_pacing(self, scenes_with_time):
        """Optimize with visual intensity considerations"""
        optimizer = PacingOptimizer()
        optimized = optimizer.optimize_with_visual_intensity(
            scenes_with_time,
            intensity_curve="exponential"
        )

        # Climax should have intensity multiplier
        assert any(s.get("intensity_multiplier", 1.0) > 1.0 for s in optimized)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
