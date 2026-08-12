"""E2E tests for User Profile Preferences (ADR-0318)."""

import pytest
from pathlib import Path
from tempfile import TemporaryDirectory

from core.learning.event_emitter import EventEmitter
from core.learning.user_profile import (
    UserProfileBuilder,
    DecisionStyle,
    VerbosityLevel,
)


@pytest.fixture
def temp_tenant_home():
    """Create a temporary tenant home directory."""
    with TemporaryDirectory() as tmpdir:
        tenant_home = Path(tmpdir) / "tenants" / "_default"
        tenant_home.mkdir(parents=True, exist_ok=True)
        yield tenant_home


class TestPreferenceE2E:
    """End-to-end tests for user preferences."""

    @pytest.mark.asyncio
    async def test_emit_preference_change(self, temp_tenant_home):
        """Emit a preference change event."""
        emitter = EventEmitter(temp_tenant_home, "_default")

        await emitter.start()

        # Emit preference change
        await emitter.emit_preference(
            preference_type="decision_style",
            preference_value="pragmatic",
        )

        await emitter.flush()
        await emitter.stop()

        # Read back
        preferences = await emitter.store.read_preferences(
            tenant_id="_default"
        )
        assert len(preferences) == 1
        assert preferences[0]["preference_type"] == "decision_style"
        assert preferences[0]["preference_value"] == "pragmatic"

    @pytest.mark.asyncio
    async def test_profile_builder_to_events(self, temp_tenant_home):
        """Build profile and emit preference changes."""
        emitter = EventEmitter(temp_tenant_home, "_default")

        await emitter.start()

        # Build profile
        profile = (
            UserProfileBuilder("user-1", "_default")
            .with_decision_style(DecisionStyle.PRAGMATIC)
            .with_verbosity(VerbosityLevel.DETAILED)
            .build()
        )

        # Emit preference changes
        await emitter.emit_preference(
            preference_type="decision_style",
            preference_value=profile.decision_style.value,
        )

        await emitter.emit_preference(
            preference_type="verbosity",
            preference_value=profile.verbosity.value,
        )

        await emitter.flush()
        await emitter.stop()

        # Read all preferences
        preferences = await emitter.store.read_preferences(
            tenant_id="_default"
        )
        assert len(preferences) == 2

        # Verify individual preferences
        decision_prefs = [p for p in preferences if p["preference_type"] == "decision_style"]
        assert len(decision_prefs) == 1
        assert decision_prefs[0]["preference_value"] == "pragmatic"

    @pytest.mark.asyncio
    async def test_multiple_users_preferences(self, temp_tenant_home):
        """Emit preferences for multiple users."""
        emitter = EventEmitter(temp_tenant_home, "_default")

        await emitter.start()

        # Preferences
        await emitter.emit_preference(
            preference_type="verbosity",
            preference_value="terse",
            session_id="s1",
        )

        await emitter.emit_preference(
            preference_type="verbosity",
            preference_value="detailed",
            session_id="s2",
        )

        await emitter.flush()
        await emitter.stop()

        # Read all
        all_prefs = await emitter.store.read_preferences(
            tenant_id="_default"
        )
        assert len(all_prefs) == 2
        assert any(p["preference_value"] == "terse" for p in all_prefs)
        assert any(p["preference_value"] == "detailed" for p in all_prefs)

    @pytest.mark.asyncio
    async def test_filter_by_preference_type(self, temp_tenant_home):
        """Filter preferences by type."""
        emitter = EventEmitter(temp_tenant_home, "_default")

        await emitter.start()

        # Multiple preference types
        await emitter.emit_preference(
            preference_type="decision_style",
            preference_value="pragmatic",
        )

        await emitter.emit_preference(
            preference_type="verbosity",
            preference_value="detailed",
        )

        await emitter.emit_preference(
            preference_type="explanation_depth",
            preference_value="high",
        )

        await emitter.flush()
        await emitter.stop()

        # Filter by preference type
        style_prefs = await emitter.store.read_preferences(
            tenant_id="_default",
            preference_type="decision_style"
        )
        assert len(style_prefs) == 1
        assert style_prefs[0]["preference_value"] == "pragmatic"
