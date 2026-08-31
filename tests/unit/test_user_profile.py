"""Tests for User Profile (ADR-0318)."""

import pytest
from core.learning.user_profile import (
    UserProfile,
    UserProfileBuilder,
    DecisionStyle,
    VerbosityLevel,
    ExplanationDepth,
)


class TestUserProfile:
    """Test user profile model."""

    def test_create_profile_defaults(self):
        """Create profile with defaults."""
        profile = UserProfile(user_id="user-1", tenant_id="_default")

        assert profile.decision_style == DecisionStyle.THOROUGH
        assert profile.verbosity == VerbosityLevel.NORMAL
        assert profile.explanation_depth == ExplanationDepth.MEDIUM
        assert profile.language == "en"
        assert profile.allow_telemetry is True

    def test_create_profile_custom(self):
        """Create profile with custom values."""
        profile = UserProfile(
            user_id="user-1",
            tenant_id="_default",
            decision_style=DecisionStyle.PRAGMATIC,
            verbosity=VerbosityLevel.TERSE,
            explanation_depth=ExplanationDepth.LOW,
            language="de",
            timezone="Europe/Berlin",
            allow_telemetry=False,
        )

        assert profile.decision_style == DecisionStyle.PRAGMATIC
        assert profile.verbosity == VerbosityLevel.TERSE
        assert profile.explanation_depth == ExplanationDepth.LOW
        assert profile.language == "de"
        assert profile.timezone == "Europe/Berlin"
        assert profile.allow_telemetry is False

    def test_profile_immutability(self):
        """User profile is immutable."""
        profile = UserProfile(user_id="user-1", tenant_id="_default")

        with pytest.raises(AttributeError):
            profile.decision_style = DecisionStyle.PRAGMATIC

    def test_profile_to_dict(self):
        """Convert profile to dict."""
        profile = UserProfile(
            user_id="user-1",
            tenant_id="_default",
            decision_style=DecisionStyle.PRAGMATIC,
            language="de",
        )

        data = profile.to_dict()

        assert data["user_id"] == "user-1"
        assert data["decision_style"] == "pragmatic"
        assert data["language"] == "de"


class TestUserProfileBuilder:
    """Test profile builder."""

    def test_builder_defaults(self):
        """Builder uses sensible defaults."""
        profile = (
            UserProfileBuilder("user-1", "_default")
            .build()
        )

        assert profile.decision_style == DecisionStyle.THOROUGH
        assert profile.verbosity == VerbosityLevel.NORMAL

    def test_builder_fluent(self):
        """Builder supports fluent API."""
        profile = (
            UserProfileBuilder("user-1", "_default")
            .with_decision_style(DecisionStyle.PRAGMATIC)
            .with_verbosity(VerbosityLevel.DETAILED)
            .with_language("es")
            .build()
        )

        assert profile.decision_style == DecisionStyle.PRAGMATIC
        assert profile.verbosity == VerbosityLevel.DETAILED
        assert profile.language == "es"

    def test_builder_with_timezone(self):
        """Builder accepts timezone."""
        profile = (
            UserProfileBuilder("user-1", "_default")
            .with_timezone("America/New_York")
            .build()
        )

        assert profile.timezone == "America/New_York"

    def test_builder_with_telemetry(self):
        """Builder accepts telemetry preference."""
        profile = (
            UserProfileBuilder("user-1", "_default")
            .with_telemetry(False)
            .build()
        )

        assert profile.allow_telemetry is False

    def test_builder_invalid_language(self):
        """Reject invalid language code."""
        with pytest.raises(ValueError, match="Invalid language code"):
            (
                UserProfileBuilder("user-1", "_default")
                .with_language("12345")  # Numeric, not valid BCP 47
                .build()
            )

    def test_builder_from_dict(self):
        """Load profile from dict."""
        data = {
            "user_id": "user-1",
            "tenant_id": "_default",
            "decision_style": "pragmatic",
            "verbosity": "terse",
            "explanation_depth": "high",
            "language": "de",
            "timezone": "Europe/Berlin",
            "allow_telemetry": False,
        }

        profile = UserProfileBuilder.from_dict(data)

        assert profile.decision_style == DecisionStyle.PRAGMATIC
        assert profile.verbosity == VerbosityLevel.TERSE
        assert profile.explanation_depth == ExplanationDepth.HIGH
        assert profile.language == "de"
        assert profile.timezone == "Europe/Berlin"
        assert profile.allow_telemetry is False

    def test_builder_from_dict_missing_user_id(self):
        """Reject dict without user_id."""
        with pytest.raises(ValueError, match="user_id and tenant_id required"):
            UserProfileBuilder.from_dict({"tenant_id": "_default"})

    def test_all_decision_styles(self):
        """Support all decision styles."""
        styles = [
            DecisionStyle.PRAGMATIC,
            DecisionStyle.THOROUGH,
            DecisionStyle.EXPERIMENTAL,
        ]

        for style in styles:
            profile = (
                UserProfileBuilder("user-1", "_default")
                .with_decision_style(style)
                .build()
            )
            assert profile.decision_style == style

    def test_all_verbosity_levels(self):
        """Support all verbosity levels."""
        levels = [
            VerbosityLevel.TERSE,
            VerbosityLevel.NORMAL,
            VerbosityLevel.DETAILED,
        ]

        for level in levels:
            profile = (
                UserProfileBuilder("user-1", "_default")
                .with_verbosity(level)
                .build()
            )
            assert profile.verbosity == level

    def test_all_explanation_depths(self):
        """Support all explanation depths."""
        depths = [
            ExplanationDepth.LOW,
            ExplanationDepth.MEDIUM,
            ExplanationDepth.HIGH,
        ]

        for depth in depths:
            profile = (
                UserProfileBuilder("user-1", "_default")
                .with_explanation_depth(depth)
                .build()
            )
            assert profile.explanation_depth == depth
