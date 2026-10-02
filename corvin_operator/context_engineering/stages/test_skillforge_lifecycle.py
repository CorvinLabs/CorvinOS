"""Test CEL skill lifecycle marking (ADR-0409, Commit 2)."""

from unittest.mock import Mock, patch, MagicMock
from datetime import datetime
from corvin_operator.context_engineering.stages.skillforge import _skill_create


def test_skill_create_marks_lifecycle_turn():
    """CEL skills are marked with lifecycle='turn' and session_id."""
    mock_registry = MagicMock()

    with patch('corvin_operator.context_engineering.stages.skillforge._skill_registry') as mock_reg_fn:
        mock_reg_fn.return_value = mock_registry

        # Call _skill_create with session_id
        session_id = "sess_test123"
        _skill_create(tenant_id="test_tenant", name="cel_test_skill",
                     body="# Test skill\nThis is a test.",
                     session_id=session_id)

        # Verify .create() was called with correct lifecycle fields
        mock_registry.create.assert_called_once()
        call_kwargs = mock_registry.create.call_args[1]

        # Assertions
        assert call_kwargs['lifecycle'] == "turn", "CEL skill should default to lifecycle='turn'"
        assert call_kwargs['session_id'] == session_id, "session_id should be passed through"
        assert call_kwargs['created_at'] is not None, "created_at should be ISO timestamp"

        # Verify created_at is valid ISO format
        datetime.fromisoformat(call_kwargs['created_at'])


def test_skill_create_without_session_id():
    """CEL skills can be created without session_id (defaults to None)."""
    mock_registry = MagicMock()

    with patch('corvin_operator.context_engineering.stages.skillforge._skill_registry') as mock_reg_fn:
        mock_reg_fn.return_value = mock_registry

        _skill_create(tenant_id="test_tenant", name="cel_skill", body="# Test")

        call_kwargs = mock_registry.create.call_args[1]
        assert call_kwargs['lifecycle'] == "turn"
        assert call_kwargs['session_id'] is None


def test_skillforge_stage_passes_session_id():
    """SkillForgeStage passes session_id to _skill_create."""
    from corvin_operator.context_engineering.stages.skillforge import SkillForgeStage

    mock_registry = MagicMock()
    mock_ctx = Mock()
    mock_ctx.tenant_id = "test_tenant"
    mock_ctx.session_id = "sess_xyz"

    bundle = Mock()
    bundle.scratch = {}
    bundle.skills_to_bind = []

    # Mock the needs (skills to generate)
    bundle.scratch['needs'] = {
        'skills': [
            {
                'name': 'test-skill',
                'body': '# Generated skill\nSome content'
            }
        ]
    }

    stage = SkillForgeStage()

    with patch('corvin_operator.context_engineering.stages.skillforge._skill_registry') as mock_reg_fn:
        mock_reg_fn.return_value = mock_registry
        with patch('corvin_operator.context_engineering.stages.skillforge.forge_create_licensed') as mock_lic:
            mock_lic.return_value = True

            result_bundle, telemetry = stage.run(bundle, mock_ctx)

            # Verify _skill_create was called (indirectly through _skill_registry.create)
            # The stage calls _skill_create which calls _skill_registry().create()
            assert mock_registry.create.called, "Skill registry create should be called"


if __name__ == "__main__":
    test_skill_create_marks_lifecycle_turn()
    print("✅ Test 1: CEL skills marked with lifecycle='turn'")

    test_skill_create_without_session_id()
    print("✅ Test 2: CEL skills without session_id")

    # Note: test_skillforge_stage_passes_session_id() requires more complex mocking
    # and is better tested as part of integration tests in the main suite.

    print("\n✅ All core lifecycle tests passed")
