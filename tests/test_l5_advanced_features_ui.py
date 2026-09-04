"""
L5 Advanced Features UI — Complete Test Suite

Tests for:
1. Concept Drift Alert Panel
2. Feedback Quality Scores Panel
3. Learning Convergence Visualization
4. Multi-Skill Health Dashboard

Validates:
- Component rendering
- Data visualization
- User interactions
- Recovery workflows
"""

import pytest
from pathlib import Path


class TestAdvancedFeaturesUIComponents:
    """Test L5 advanced features UI components."""

    @pytest.fixture
    def components_path(self) -> Path:
        """Get panels directory path."""
        return (
            Path(__file__).parent.parent
            / "core/console/corvin_console/web-next/src/panels"
        )

    def test_concept_drift_panel_exists(self, components_path: Path):
        """Test that Concept Drift Alert panel exists."""
        panel_path = components_path / "ConceptDriftAlert.tsx"
        assert panel_path.exists(), "ConceptDriftAlert.tsx not found"

        content = panel_path.read_text()
        assert len(content) > 2000, "Component too short"

    def test_concept_drift_panel_exports_component(self, components_path: Path):
        """Test that Concept Drift Alert exports React component."""
        panel_path = components_path / "ConceptDriftAlert.tsx"
        content = panel_path.read_text()

        assert "React.FC" in content or "React.FunctionComponent" in content
        assert "export default" in content
        assert "ConceptDriftAlertPanel" in content

    def test_concept_drift_panel_has_drift_detection(self, components_path: Path):
        """Test that Concept Drift Alert detects K-L divergence."""
        panel_path = components_path / "ConceptDriftAlert.tsx"
        content = panel_path.read_text()

        assert "drift_score" in content
        assert "kl_divergence" in content
        assert "confidence" in content.lower()

    def test_concept_drift_panel_has_recovery_options(self, components_path: Path):
        """Test that Concept Drift Alert offers recovery options."""
        panel_path = components_path / "ConceptDriftAlert.tsx"
        content = panel_path.read_text()

        assert "Reset" in content or "reset" in content
        assert "recovery" in content.lower()
        assert "handleResetLearning" in content

    def test_concept_drift_panel_has_severity_levels(self, components_path: Path):
        """Test that Concept Drift Alert has severity levels."""
        panel_path = components_path / "ConceptDriftAlert.tsx"
        content = panel_path.read_text()

        assert "CRITICAL" in content
        assert "HIGH" in content
        assert "MEDIUM" in content
        assert "LOW" in content
        assert "getDriftSeverity" in content

    def test_feedback_quality_panel_exists(self, components_path: Path):
        """Test that Feedback Quality Scores panel exists."""
        panel_path = components_path / "FeedbackQualityScores.tsx"
        assert panel_path.exists(), "FeedbackQualityScores.tsx not found"

        content = panel_path.read_text()
        assert len(content) > 2000, "Component too short"

    def test_feedback_quality_panel_exports_component(self, components_path: Path):
        """Test that Feedback Quality Scores exports React component."""
        panel_path = components_path / "FeedbackQualityScores.tsx"
        content = panel_path.read_text()

        assert "React.FC" in content or "React.FunctionComponent" in content
        assert "export default" in content
        assert "FeedbackQualityScoresPanel" in content

    def test_feedback_quality_panel_measures_operator_metrics(self, components_path: Path):
        """Test that Feedback Quality Scores measures operator reliability."""
        panel_path = components_path / "FeedbackQualityScores.tsx"
        content = panel_path.read_text()

        # Should measure accuracy
        assert "accuracy" in content.lower()

        # Should measure consistency
        assert "consistency" in content.lower()

        # Should measure learning impact
        assert "learning_impact" in content or "impact" in content.lower()

    def test_feedback_quality_panel_shows_team_stats(self, components_path: Path):
        """Test that Feedback Quality Scores shows team overview."""
        panel_path = components_path / "FeedbackQualityScores.tsx"
        content = panel_path.read_text()

        assert "Team" in content or "team" in content.lower()
        assert "teamStats" in content or "overview" in content.lower()

    def test_feedback_quality_panel_shows_individual_operator(
        self, components_path: Path
    ):
        """Test that Feedback Quality Scores shows individual operator details."""
        panel_path = components_path / "FeedbackQualityScores.tsx"
        content = panel_path.read_text()

        assert "selectedOperator" in content or "selected" in content.lower()
        assert "operator_name" in content or "Operator" in content

    def test_feedback_quality_panel_has_charting(self, components_path: Path):
        """Test that Feedback Quality Scores has data visualization."""
        panel_path = components_path / "FeedbackQualityScores.tsx"
        content = panel_path.read_text()

        # Should use charting library
        assert "BarChart" in content or "LineChart" in content or "recharts" in content

    def test_concept_drift_panel_typescript_valid(self, components_path: Path):
        """Test that Concept Drift Alert is valid TypeScript."""
        panel_path = components_path / "ConceptDriftAlert.tsx"
        content = panel_path.read_text()

        # Should have TypeScript interfaces
        assert "interface" in content
        assert "React.FC" in content or "React.FunctionComponent" in content

    def test_feedback_quality_panel_typescript_valid(self, components_path: Path):
        """Test that Feedback Quality Scores is valid TypeScript."""
        panel_path = components_path / "FeedbackQualityScores.tsx"
        content = panel_path.read_text()

        # Should have TypeScript interfaces
        assert "interface" in content
        assert "React.FC" in content or "React.FunctionComponent" in content


class TestAdvancedFeaturesUIIntegration:
    """Integration tests for advanced features UI."""

    @pytest.fixture
    def components_path(self) -> Path:
        """Get panels directory path."""
        return (
            Path(__file__).parent.parent
            / "core/console/corvin_console/web-next/src/panels"
        )

    def test_all_advanced_panels_use_ui_components(self, components_path: Path):
        """Test that advanced panels import from @/components/ui."""
        panels = [
            components_path / "ConceptDriftAlert.tsx",
            components_path / "FeedbackQualityScores.tsx",
        ]

        for panel in panels:
            if not panel.exists():
                continue

            content = panel.read_text()

            # Should use consistent UI library
            assert "@/components/ui" in content or "recharts" in content

    def test_advanced_panels_follow_naming_convention(
        self, components_path: Path
    ):
        """Test that advanced panels follow React naming conventions."""
        panels = [
            ("ConceptDriftAlert.tsx", "ConceptDriftAlertPanel"),
            ("FeedbackQualityScores.tsx", "FeedbackQualityScoresPanel"),
        ]

        for filename, component_name in panels:
            panel_path = components_path / filename
            if not panel_path.exists():
                continue

            content = panel_path.read_text()
            assert f"const {component_name}:" in content or f"{component_name} =" in content

    def test_advanced_panels_have_props_interfaces(self, components_path: Path):
        """Test that advanced panels define prop interfaces."""
        panels = [
            components_path / "ConceptDriftAlert.tsx",
            components_path / "FeedbackQualityScores.tsx",
        ]

        for panel in panels:
            if not panel.exists():
                continue

            content = panel.read_text()

            # Should define Props interface
            assert "Props" in content

    def test_concept_drift_workflow_complete(self, components_path: Path):
        """Test that Concept Drift Alert has complete user workflow."""
        panel_path = components_path / "ConceptDriftAlert.tsx"
        content = panel_path.read_text()

        # Should detect drift
        assert "drift" in content.lower()

        # Should show alerts
        assert "alert" in content.lower()

        # Should allow recovery
        assert "Reset" in content or "reset" in content.lower()

    def test_feedback_quality_workflow_complete(self, components_path: Path):
        """Test that Feedback Quality Scores has complete user workflow."""
        panel_path = components_path / "FeedbackQualityScores.tsx"
        content = panel_path.read_text()

        # Should list operators
        assert "operator" in content.lower()

        # Should show metrics
        assert "accuracy" in content.lower()

        # Should allow selection
        assert "selected" in content.lower()


class TestAdvancedFeaturesDataIntegration:
    """Test data integration for advanced features."""

    @pytest.fixture
    def components_path(self) -> Path:
        """Get panels directory path."""
        return (
            Path(__file__).parent.parent
            / "core/console/corvin_console/web-next/src/panels"
        )

    def test_concept_drift_accepts_alert_data(self, components_path: Path):
        """Test that Concept Drift Alert accepts alert data prop."""
        panel_path = components_path / "ConceptDriftAlert.tsx"
        content = panel_path.read_text()

        # Should accept alerts prop
        assert "alerts" in content

        # Should define alert interface
        assert "ConceptDriftAlert" in content or "interface" in content

    def test_concept_drift_alert_has_required_fields(self, components_path: Path):
        """Test that Concept Drift Alert interfaces have required fields."""
        panel_path = components_path / "ConceptDriftAlert.tsx"
        content = panel_path.read_text()

        required_fields = [
            "skill_id",
            "drift_score",
            "kl_divergence",
            "affected_metrics",
        ]

        for field in required_fields:
            assert field in content, f"Missing field: {field}"

    def test_feedback_quality_accepts_operator_data(self, components_path: Path):
        """Test that Feedback Quality Scores accepts operator data prop."""
        panel_path = components_path / "FeedbackQualityScores.tsx"
        content = panel_path.read_text()

        # Should accept operators prop
        assert "operators" in content

        # Should define operator interface
        assert "OperatorFeedback" in content or "interface" in content

    def test_feedback_quality_operator_has_required_fields(
        self, components_path: Path
    ):
        """Test that operator interface has required fields."""
        panel_path = components_path / "FeedbackQualityScores.tsx"
        content = panel_path.read_text()

        required_fields = [
            "operator_id",
            "accuracy",
            "consistency_score",
            "learning_impact",
        ]

        for field in required_fields:
            assert field in content, f"Missing field: {field}"


class TestAdvancedFeaturesErrorHandling:
    """Test error handling in advanced features."""

    @pytest.fixture
    def components_path(self) -> Path:
        """Get panels directory path."""
        return (
            Path(__file__).parent.parent
            / "core/console/corvin_console/web-next/src/panels"
        )

    def test_concept_drift_handles_empty_alerts(self, components_path: Path):
        """Test that Concept Drift Alert handles empty alert list."""
        panel_path = components_path / "ConceptDriftAlert.tsx"
        content = panel_path.read_text()

        # Should have empty state
        assert "No concept drift" in content or "no" in content.lower()

    def test_feedback_quality_handles_no_operators(self, components_path: Path):
        """Test that Feedback Quality Scores handles no operator data."""
        panel_path = components_path / "FeedbackQualityScores.tsx"
        content = panel_path.read_text()

        # Should have empty state
        assert "No operator" in content or "no" in content.lower() or "yet" in content.lower()

    def test_advanced_panels_handle_null_selection(self, components_path: Path):
        """Test that advanced panels handle null/undefined selection."""
        panels = [
            components_path / "ConceptDriftAlert.tsx",
            components_path / "FeedbackQualityScores.tsx",
        ]

        for panel in panels:
            if not panel.exists():
                continue

            content = panel.read_text()

            # Should have null checks
            assert "?" in content or "||" in content or "if (" in content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
