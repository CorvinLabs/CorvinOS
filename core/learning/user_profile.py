"""User Profile — style preferences (ADR-0318)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class DecisionStyle(str, Enum):
    """User's decision-making preference."""

    PRAGMATIC = "pragmatic"  # Fast, good-enough decisions
    THOROUGH = "thorough"    # Exhaustive analysis
    EXPERIMENTAL = "experimental"  # Try novel approaches


class VerbosityLevel(str, Enum):
    """User's preference for output length."""

    TERSE = "terse"          # Minimal, key points only
    NORMAL = "normal"        # Balanced
    DETAILED = "detailed"    # Comprehensive


class ExplanationDepth(str, Enum):
    """User's preference for explanation detail."""

    LOW = "low"              # Bare results
    MEDIUM = "medium"        # Standard explanations
    HIGH = "high"            # Deep reasoning


@dataclass(frozen=True)
class UserProfile:
    """Immutable user style preferences."""

    user_id: str
    tenant_id: str
    decision_style: DecisionStyle = DecisionStyle.THOROUGH
    verbosity: VerbosityLevel = VerbosityLevel.NORMAL
    explanation_depth: ExplanationDepth = ExplanationDepth.MEDIUM
    language: str = "en"
    timezone: Optional[str] = None
    allow_telemetry: bool = True

    def to_dict(self) -> dict:
        """Convert to dict for persistence."""
        return {
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "decision_style": self.decision_style.value,
            "verbosity": self.verbosity.value,
            "explanation_depth": self.explanation_depth.value,
            "language": self.language,
            "timezone": self.timezone,
            "allow_telemetry": self.allow_telemetry,
        }


class UserProfileBuilder:
    """Build and validate user profiles."""

    def __init__(self, user_id: str, tenant_id: str):
        """Initialize builder.

        Args:
            user_id: User ID
            tenant_id: Tenant ID (for isolation)
        """
        self.user_id = user_id
        self.tenant_id = tenant_id
        self._decision_style = DecisionStyle.THOROUGH
        self._verbosity = VerbosityLevel.NORMAL
        self._explanation_depth = ExplanationDepth.MEDIUM
        self._language = "en"
        self._timezone: Optional[str] = None
        self._allow_telemetry = True

    def with_decision_style(self, style: DecisionStyle) -> UserProfileBuilder:
        """Set decision style."""
        self._decision_style = style
        return self

    def with_verbosity(self, verbosity: VerbosityLevel) -> UserProfileBuilder:
        """Set verbosity level."""
        self._verbosity = verbosity
        return self

    def with_explanation_depth(self, depth: ExplanationDepth) -> UserProfileBuilder:
        """Set explanation depth."""
        self._explanation_depth = depth
        return self

    def with_language(self, language: str) -> UserProfileBuilder:
        """Set language code."""
        if not (1 <= len(language) <= 5):
            raise ValueError(f"Invalid language code: {language}")
        self._language = language
        return self

    def with_timezone(self, timezone: Optional[str]) -> UserProfileBuilder:
        """Set timezone."""
        self._timezone = timezone
        return self

    def with_telemetry(self, allow: bool) -> UserProfileBuilder:
        """Set telemetry preference."""
        self._allow_telemetry = allow
        return self

    def build(self) -> UserProfile:
        """Build the profile."""
        return UserProfile(
            user_id=self.user_id,
            tenant_id=self.tenant_id,
            decision_style=self._decision_style,
            verbosity=self._verbosity,
            explanation_depth=self._explanation_depth,
            language=self._language,
            timezone=self._timezone,
            allow_telemetry=self._allow_telemetry,
        )

    @classmethod
    def from_dict(cls, data: dict) -> UserProfile:
        """Load profile from dict."""
        user_id = data.get("user_id")
        tenant_id = data.get("tenant_id")

        if not user_id or not tenant_id:
            raise ValueError("user_id and tenant_id required")

        builder = cls(user_id, tenant_id)

        if "decision_style" in data:
            builder._decision_style = DecisionStyle(data["decision_style"])
        if "verbosity" in data:
            builder._verbosity = VerbosityLevel(data["verbosity"])
        if "explanation_depth" in data:
            builder._explanation_depth = ExplanationDepth(data["explanation_depth"])
        if "language" in data:
            builder._language = data["language"]
        if "timezone" in data:
            builder._timezone = data["timezone"]
        if "allow_telemetry" in data:
            builder._allow_telemetry = data["allow_telemetry"]

        return builder.build()
