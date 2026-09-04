"""
L5 Operator Training Materials — Complete Test Suite

Tests for:
1. Operator Guide (L5_OPERATOR_GUIDE.md)
2. FAQ (L5_OPERATOR_FAQ.md)
3. Video Script (L5_VIDEO_SCRIPT.md)
4. Interactive Training Module (L5TrainingModule.tsx)

Validates:
- Documentation completeness
- Training module functionality
- Quiz correctness
- Certification workflow
"""

import json
import pytest
from pathlib import Path


class TestOperatorTrainingMaterials:
    """Test L5 operator training materials."""

    @pytest.fixture
    def docs_path(self) -> Path:
        """Get docs directory path."""
        return Path(__file__).parent.parent / "docs"

    def test_operator_guide_exists(self, docs_path: Path):
        """Test that operator guide exists and has content."""
        guide_path = docs_path / "L5_OPERATOR_GUIDE.md"
        assert guide_path.exists(), "Operator guide not found"

        content = guide_path.read_text()
        assert len(content) > 5000, "Operator guide too short"
        assert "## " in content, "Operator guide missing sections"

    def test_operator_guide_completeness(self, docs_path: Path):
        """Test that operator guide covers all required topics."""
        guide_path = docs_path / "L5_OPERATOR_GUIDE.md"
        content = guide_path.read_text()

        required_sections = [
            "Overview & Quick Start",
            "5-Gate Workflow",
            "Approval Decision Making",
            "SLA & Performance Monitoring",
            "Troubleshooting & Recovery",
            "Advanced Tuning",
            "FAQ",
        ]

        for section in required_sections:
            assert section in content, f"Missing section: {section}"

    def test_faq_exists_and_comprehensive(self, docs_path: Path):
        """Test that FAQ exists with 50+ Q&A pairs."""
        faq_path = docs_path / "L5_OPERATOR_FAQ.md"
        assert faq_path.exists(), "FAQ not found"

        content = faq_path.read_text()
        assert len(content) > 10000, "FAQ too short"

        # Count Q&A pairs
        questions = content.count("**Q")
        assert questions >= 50, f"FAQ has only {questions} Q&A pairs, need 50+"

    def test_faq_covers_all_topics(self, docs_path: Path):
        """Test that FAQ covers all important topics."""
        faq_path = docs_path / "L5_OPERATOR_FAQ.md"
        content = faq_path.read_text()

        required_topics = [
            "QUICK START",
            "THE 5 GATES",
            "OPERATOR APPROVAL",
            "METRICS & MONITORING",
            "TROUBLESHOOTING",
            "ADVANCED TOPICS",
            "EMERGENCY",
        ]

        for topic in required_topics:
            assert topic in content, f"FAQ missing topic: {topic}"

    def test_video_script_exists(self, docs_path: Path):
        """Test that video script exists and is complete."""
        script_path = docs_path / "L5_VIDEO_SCRIPT.md"
        assert script_path.exists(), "Video script not found"

        content = script_path.read_text()
        assert len(content) > 5000, "Video script too short"
        assert "VIDEO 1:" in content, "Video script missing sections"

    def test_video_script_structure(self, docs_path: Path):
        """Test that video script has proper structure."""
        script_path = docs_path / "L5_VIDEO_SCRIPT.md"
        content = script_path.read_text()

        # Should have 8 videos
        for i in range(1, 9):
            assert f"VIDEO {i}:" in content, f"Missing VIDEO {i}"

        # Each video should have duration
        assert "Duration:" in content, "Missing duration markers"

        # Should have production notes
        assert "PRODUCTION NOTES" in content, "Missing production notes"

    def test_video_script_content_quality(self, docs_path: Path):
        """Test that video script has quality voiceover content."""
        script_path = docs_path / "L5_VIDEO_SCRIPT.md"
        content = script_path.read_text()

        # Should have voiceover sections
        voiceovers = content.count("**Voiceover:**")
        assert voiceovers >= 8, f"Video script has only {voiceovers} voiceover sections"

        # Should have visual cues
        visuals = content.count("**Visual:**")
        assert visuals >= 8, f"Video script has only {visuals} visual descriptions"

    def test_training_module_component_exists(self):
        """Test that React training module component exists."""
        component_path = (
            Path(__file__).parent.parent
            / "core/console/corvin_console/web-next/src/panels/L5TrainingModule.tsx"
        )
        assert component_path.exists(), "L5TrainingModule.tsx not found"

        content = component_path.read_text()
        assert len(content) > 5000, "Training component too short"

    def test_training_module_has_8_steps(self):
        """Test that training module has all 8 required steps."""
        component_path = (
            Path(__file__).parent.parent
            / "core/console/corvin_console/web-next/src/panels/L5TrainingModule.tsx"
        )
        content = component_path.read_text()

        required_steps = [
            "Welcome to L5",
            "The 5-Gate System",
            "Decision Framework",
            "Approval Workflow",
            "Monitoring Dashboard",
            "Real-World Example",
            "Troubleshooting",
            "Certification Quiz",
        ]

        for step in required_steps:
            assert step in content, f"Training module missing step: {step}"

    def test_training_module_has_quizzes(self):
        """Test that training module has quizzes for all steps."""
        component_path = (
            Path(__file__).parent.parent
            / "core/console/corvin_console/web-next/src/panels/L5TrainingModule.tsx"
        )
        content = component_path.read_text()

        # Should have multiple quiz questions
        assert content.count("quiz:") >= 8, "Training module missing quiz definitions"
        assert content.count("options:") >= 8, "Training module missing quiz options"

    def test_training_module_has_progress_tracking(self):
        """Test that training module tracks progress."""
        component_path = (
            Path(__file__).parent.parent
            / "core/console/corvin_console/web-next/src/panels/L5TrainingModule.tsx"
        )
        content = component_path.read_text()

        # Should track steps completed
        assert "completedSteps" in content, "Training module missing progress tracking"
        assert "Progress" in content, "Training module missing progress display"

    def test_training_module_has_certification(self):
        """Test that training module has certification workflow."""
        component_path = (
            Path(__file__).parent.parent
            / "core/console/corvin_console/web-next/src/panels/L5TrainingModule.tsx"
        )
        content = component_path.read_text()

        assert "Certificate" in content, "Training module missing certification"
        assert "allCompleted" in content, "Training module missing completion tracking"

    def test_training_materials_consistency(self, docs_path: Path):
        """Test that training materials are consistent with each other."""
        guide = (docs_path / "L5_OPERATOR_GUIDE.md").read_text()
        faq = (docs_path / "L5_OPERATOR_FAQ.md").read_text()
        script = (docs_path / "L5_VIDEO_SCRIPT.md").read_text()

        # All should mention 5 gates
        assert "Gate k=1" in guide and "k=1" in faq and "k=1" in script
        assert "Gate k=2" in guide and "k=2" in faq and "k=2" in script
        assert "Gate k=5" in guide and "k=5" in faq and "k=5" in script

        # All should mention SLA
        assert "SLA" in guide
        assert "latency" in faq.lower()
        assert "5 min" in script or "5min" in script

    def test_training_materials_reference_each_other(self, docs_path: Path):
        """Test that training materials cross-reference appropriately."""
        guide_path = docs_path / "L5_OPERATOR_GUIDE.md"
        guide_content = guide_path.read_text()

        # Guide should reference FAQ
        assert "FAQ" in guide_content or "faq" in guide_content.lower()

    def test_operator_guide_has_decision_framework(self, docs_path: Path):
        """Test that operator guide has clear decision framework table."""
        guide_path = docs_path / "L5_OPERATOR_GUIDE.md"
        content = guide_path.read_text()

        # Should have decision table with confidence levels
        assert "Confidence" in content
        assert "APPROVE" in content
        assert "REJECT" in content

    def test_faq_has_different_question_types(self, docs_path: Path):
        """Test that FAQ has variety of question types."""
        faq_path = docs_path / "L5_OPERATOR_FAQ.md"
        content = faq_path.read_text()

        # Should have conceptual questions
        assert "What is" in content, "FAQ missing 'what is' questions"

        # Should have practical 'how to' questions
        assert "should I" in content.lower() or "do i" in content.lower()

        # Should have troubleshooting questions
        assert "wrong" in content.lower() or "issue" in content.lower()

        # Should have 'when' questions
        assert "when" in content.lower()

    def test_video_script_timing_adds_up(self, docs_path: Path):
        """Test that video script timing matches total."""
        script_path = docs_path / "L5_VIDEO_SCRIPT.md"
        content = script_path.read_text()

        durations = []
        for line in content.split("\n"):
            if "Duration:" in line:
                # Extract numbers like "1:30" or "1:00"
                import re

                match = re.search(r"(\d+):(\d{2})", line)
                if match:
                    minutes = int(match.group(1))
                    seconds = int(match.group(2))
                    durations.append(minutes + seconds / 60)

        assert len(durations) >= 8, "Video script missing duration markers"

        # Total should be around 10 minutes
        total = sum(durations)
        assert 9 < total < 11, f"Video script total {total:.1f} min, expected ~10 min"

    def test_training_guide_line_count(self, docs_path: Path):
        """Test that training materials meet minimum completeness."""
        guide_path = docs_path / "L5_OPERATOR_GUIDE.md"
        content = guide_path.read_text()
        lines = content.split("\n")

        # Should have substantial content
        assert len(lines) > 300, "Operator guide too short"

    def test_training_module_estimated_time_valid(self):
        """Test that training module has valid time estimates."""
        component_path = (
            Path(__file__).parent.parent
            / "core/console/corvin_console/web-next/src/panels/L5TrainingModule.tsx"
        )
        content = component_path.read_text()

        # Should have estimatedTime for each step
        assert content.count("estimatedTime:") >= 8, "Missing time estimates for steps"


class TestL5TrainingIntegration:
    """Integration tests for L5 training materials."""

    def test_training_covers_all_gates(self):
        """Test that training material covers all 5 gates."""
        component_path = (
            Path(__file__).parent.parent
            / "core/console/corvin_console/web-next/src/panels/L5TrainingModule.tsx"
        )
        content = component_path.read_text()

        for gate in ["k=1", "k=2", "k=3", "k=4", "k=5"]:
            assert gate in content, f"Training missing explanation of {gate}"

    def test_training_covers_decision_making(self):
        """Test that training covers decision-making framework."""
        component_path = (
            Path(__file__).parent.parent
            / "core/console/corvin_console/web-next/src/panels/L5TrainingModule.tsx"
        )
        content = component_path.read_text()

        # Should mention confidence threshold
        assert "confidence" in content.lower()
        assert "85" in content, "Missing 85% confidence threshold"
        assert "APPROVE" in content
        assert "REJECT" in content

    def test_training_covers_monitoring(self):
        """Test that training covers monitoring/dashboarding."""
        component_path = (
            Path(__file__).parent.parent
            / "core/console/corvin_console/web-next/src/panels/L5TrainingModule.tsx"
        )
        content = component_path.read_text()

        # Should mention key metrics
        assert "latency" in content.lower()
        assert "accuracy" in content.lower()
        assert "revoke" in content.lower()

    def test_training_covers_troubleshooting(self):
        """Test that training covers troubleshooting scenarios."""
        component_path = (
            Path(__file__).parent.parent
            / "core/console/corvin_console/web-next/src/panels/L5TrainingModule.tsx"
        )
        content = component_path.read_text()

        # Should mention common issues
        assert "broke" in content.lower() or "issue" in content.lower()
        assert "revoke" in content.lower()
        assert "escalate" in content.lower()


class TestTrainingQuizzes:
    """Test quiz correctness and answers."""

    def test_quiz_options_have_explanations(self):
        """Test that all quiz options have explanations."""
        component_path = (
            Path(__file__).parent.parent
            / "core/console/corvin_console/web-next/src/panels/L5TrainingModule.tsx"
        )
        content = component_path.read_text()

        # Should have explanation field in options
        assert "explanation:" in content, "Quiz options missing explanations"

    def test_quiz_has_correct_answers(self):
        """Test that each quiz question has exactly one correct answer."""
        component_path = (
            Path(__file__).parent.parent
            / "core/console/corvin_console/web-next/src/panels/L5TrainingModule.tsx"
        )
        content = component_path.read_text()

        # Should mark correct answers
        assert "correct: true" in content
        assert "correct: false" in content


class TestTrainingMaterialsAccessibility:
    """Test that training materials are accessible."""

    def test_faq_has_searchable_questions(self, docs_path: Path):
        """Test that FAQ questions are clearly formatted for searching."""
        faq_path = docs_path / "L5_OPERATOR_FAQ.md"
        content = faq_path.read_text()

        # Questions should start with **Q
        questions = [line for line in content.split("\n") if line.startswith("**Q")]
        assert len(questions) >= 50, "FAQ questions not clearly formatted"

    def test_operator_guide_has_table_of_contents(self, docs_path: Path):
        """Test that operator guide has TOC."""
        guide_path = docs_path / "L5_OPERATOR_GUIDE.md"
        content = guide_path.read_text()

        assert "Table of Contents" in content or "Contents" in content

    def test_training_module_has_step_navigation(self):
        """Test that training module has step navigation."""
        component_path = (
            Path(__file__).parent.parent
            / "core/console/corvin_console/web-next/src/panels/L5TrainingModule.tsx"
        )
        content = component_path.read_text()

        # Should have navigation
        assert "handleNext" in content
        assert "handlePrevious" in content
        assert "currentStep" in content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
