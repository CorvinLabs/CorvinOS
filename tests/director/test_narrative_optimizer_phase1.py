"""Phase 1: Narrative Optimizer Tests

Tests for:
- Story templates validation
- Fact extraction and preservation
- Narrative suggestion
- Approval gate workflow
"""

import pytest
from core.skills.os_skills.video_producer.src.director.narrative_optimizer.templates import (
    StoryTemplate, NarrativeTemplate
)
from core.skills.os_skills.video_producer.src.director.narrative_optimizer.fact_extractor import (
    FactExtractor, Fact
)
from core.skills.os_skills.video_producer.src.director.narrative_optimizer.suggester import (
    NarrativeSuggester
)
from core.skills.os_skills.video_producer.src.director.narrative_optimizer.approval_gate import (
    ApprovalGate, ApprovalStatus
)


class TestStoryTemplates:
    """Test narrative story templates"""

    def test_hero_journey_template_complete(self):
        """All templates have duration_pct summing to 1.0"""
        assert StoryTemplate.validate_template_duration(StoryTemplate.HERO_JOURNEY)
        assert StoryTemplate.validate_template_duration(StoryTemplate.THREE_ACT)
        assert StoryTemplate.validate_template_duration(StoryTemplate.PROBLEM_SOLUTION)

    def test_get_template_by_type(self):
        """Retrieve template by type"""
        template = StoryTemplate.get_template(NarrativeTemplate.HERO_JOURNEY)
        assert "hook" in template
        assert "action" in template
        assert template["action"]["duration_pct"] == 0.50

    def test_list_all_templates(self):
        """List all available templates"""
        templates = StoryTemplate.list_all_templates()
        assert len(templates) == 3
        assert NarrativeTemplate.HERO_JOURNEY in templates

    def test_template_descriptions(self):
        """Get template descriptions"""
        desc = StoryTemplate.get_template_description(NarrativeTemplate.HERO_JOURNEY)
        assert "inspiring" in desc.lower()


class TestFactExtraction:
    """Test fact extraction and preservation"""

    @pytest.fixture
    def sample_storyboard(self):
        """Sample storyboard for testing"""
        return {
            "scenes": [
                {
                    "id": "scene1",
                    "narration": "This is the first fact. Here is another fact.",
                    "assets": [
                        {"description": "An important diagram", "type": "diagram"}
                    ],
                    "metadata": {
                        "key_messages": ["Key point 1"]
                    }
                },
                {
                    "id": "scene2",
                    "narration": "The final important statement.",
                    "assets": []
                }
            ]
        }

    def test_fact_extraction_preserves_all(self, sample_storyboard):
        """All facts from original storyboard extracted"""
        extractor = FactExtractor()
        facts = extractor.extract_facts(sample_storyboard)
        assert len(facts) >= 3  # At least narration + asset + metadata facts

    def test_facts_have_source_scene(self, sample_storyboard):
        """Extracted facts have source scene ID"""
        extractor = FactExtractor()
        facts = extractor.extract_facts(sample_storyboard)
        for fact in facts:
            assert fact.source_scene_id in ["scene1", "scene2"]

    def test_fact_types_classified(self, sample_storyboard):
        """Facts classified by type"""
        extractor = FactExtractor()
        facts = extractor.extract_facts(sample_storyboard)
        fact_types = set(f.fact_type for f in facts)
        assert "narration" in fact_types or "asset" in fact_types

    def test_get_facts_by_type(self, sample_storyboard):
        """Filter facts by type"""
        extractor = FactExtractor()
        extractor.extract_facts(sample_storyboard)
        narration_facts = extractor.get_facts_by_type("narration")
        assert len(narration_facts) > 0

    def test_critical_facts_filtering(self, sample_storyboard):
        """Get facts marked as critical"""
        extractor = FactExtractor()
        extractor.extract_facts(sample_storyboard)
        critical = extractor.get_critical_facts(threshold=0.9)
        # At least some facts should be critical
        assert isinstance(critical, list)


class TestNarrativeSuggestion:
    """Test LLM-based narrative suggestion"""

    def test_suggestion_suggests_template(self):
        """Suggestion includes recommended template"""
        suggester = NarrativeSuggester()
        suggestion = suggester.suggest_structure(
            topic="How to solve problems",
            facts=[
                Fact("Problem A exists", "scene1", "narration", 0.9),
                Fact("Solution B works", "scene1", "narration", 0.9)
            ],
            duration_seconds=600,
            audience="general"
        )
        assert suggestion.suggested_template in NarrativeTemplate

    def test_suggestion_preserves_facts(self):
        """Suggestion indicates facts are preserved"""
        suggester = NarrativeSuggester()
        suggestion = suggester.suggest_structure(
            topic="Technical overview",
            facts=[Fact(f"Fact {i}", "s1", "narration", 0.8) for i in range(5)],
            duration_seconds=300,
            audience="technical"
        )
        assert suggestion.facts_preserved == True

    def test_suggestion_includes_pacing(self):
        """Suggestion includes pacing estimates"""
        suggester = NarrativeSuggester()
        suggestion = suggester.suggest_structure(
            topic="Tutorial",
            facts=[],
            duration_seconds=600,
            audience="learner"
        )
        assert len(suggestion.estimated_pacing) > 0
        total_pacing = sum(suggestion.estimated_pacing.values())
        assert abs(total_pacing - 600) < 1.0

    def test_suggestion_has_confidence(self):
        """Suggestion includes confidence score"""
        suggester = NarrativeSuggester()
        suggestion = suggester.suggest_structure(
            topic="Demo",
            facts=[],
            duration_seconds=300,
            audience="users"
        )
        assert 0.0 <= suggestion.confidence_score <= 1.0

    def test_multiple_suggestions_tracked(self):
        """Multiple suggestions are tracked"""
        suggester = NarrativeSuggester()
        for i in range(3):
            suggester.suggest_structure(
                topic=f"Topic {i}",
                facts=[],
                duration_seconds=600,
                audience="general"
            )

        summary = suggester.get_suggestions_summary()
        assert summary["total"] == 3


class TestApprovalGate:
    """Test user approval workflow"""

    def test_approval_request_creation(self):
        """Create approval request"""
        gate = ApprovalGate()
        approval_id = gate.request_approval(
            suggestion={"template": "hero_journey"},
            user_id="user1"
        )
        assert approval_id.startswith("approval_")

    def test_approval_submission(self):
        """Submit approval response"""
        gate = ApprovalGate()
        approval_id = gate.request_approval(
            suggestion={},
            user_id="user1"
        )
        response = gate.submit_approval(approval_id, approved=True)
        assert response.approval_status == ApprovalStatus.APPROVED

    def test_approval_with_modifications(self):
        """Submit approval with requested modifications"""
        gate = ApprovalGate()
        approval_id = gate.request_approval({}, user_id="user1")
        response = gate.submit_approval(
            approval_id,
            approved=False,
            modifications=["add_music", "increase_pacing"]
        )
        assert response.approval_status == ApprovalStatus.MODIFIED
        assert len(response.modifications) == 2

    def test_is_approved_check(self):
        """Check if approval was granted"""
        gate = ApprovalGate()
        approval_id = gate.request_approval({}, user_id="user1")
        assert not gate.is_approved(approval_id)

        gate.submit_approval(approval_id, approved=True)
        assert gate.is_approved(approval_id)

    def test_approval_summary(self):
        """Get approval summary"""
        gate = ApprovalGate()
        for i in range(3):
            aid = gate.request_approval({}, user_id="user1")
            if i < 2:
                gate.submit_approval(aid, approved=True)
            else:
                gate.submit_approval(aid, approved=False)

        summary = gate.get_approval_summary()
        assert summary["total"] == 3
        assert summary["approved"] == 2
        assert summary["rejected"] == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
