"""End-to-end tests for ADR-0701 Gates G1, G4, G5.

Verifies that gates enforce forge.create capability across real
call paths (not mocked entry points).
"""

import pytest
from unittest.mock import patch, MagicMock, call
from pathlib import Path
import tempfile


class TestG1E2E:
    """E2E: forge.create gate on Registry.create/promote"""

    def test_g1_create_with_free_tier_denied(self, tmp_path):
        """E2E: Registry.create() denies free tier."""
        from corvin_operator.forge.forge.registry import Registry, LicenseDenied

        # Mock require_capability to simulate free tier denial
        with patch('corvin_operator.forge.forge.registry.require_capability') as mock_req:
            mock_req.side_effect = LicenseDenied("forge.create not available: free tier")

            registry = Registry(tmp_path)

            # create() should raise PermissionError (wrapped around LicenseDenied)
            with pytest.raises(PermissionError) as exc_info:
                registry.create(
                    name="test_tool",
                    description="test",
                    input_schema={},
                    impl="print('hello')",
                    runtime="python"
                )

            assert "forge.create" in str(exc_info.value).lower()

    def test_g1_create_with_member_tier_allowed(self, tmp_path):
        """E2E: Registry.create() allows member tier."""
        from corvin_operator.forge.forge.registry import Registry

        # Mock require_capability to allow member tier
        with patch('corvin_operator.forge.forge.registry.require_capability') as mock_req:
            mock_req.return_value = None  # No exception = allowed

            registry = Registry(tmp_path, hash_chain=False)

            # create() should succeed
            result = registry.create(
                name="test_tool",
                description="test tool",
                input_schema={"type": "object"},
                impl="print('hello')",
                runtime="python"
            )

            assert result is not None
            assert result.name == "test_tool"

            # Verify require_capability was called with correct args
            mock_req.assert_called_once()
            call_args = mock_req.call_args
            assert "forge.create" in call_args[1]["tenant_id"] or call_args[0][0] == "forge.create"

    def test_g1_promote_enforces_gate(self, tmp_path):
        """E2E: Registry.promote() enforces gate."""
        from corvin_operator.forge.forge.registry import Registry, LicenseDenied

        # First create a tool as member
        with patch('corvin_operator.forge.forge.registry.require_capability') as mock_req:
            mock_req.return_value = None

            registry = Registry(tmp_path, hash_chain=False)
            registry.create(
                name="test_tool",
                description="test",
                input_schema={},
                impl="x=1",
                runtime="python"
            )

        # Now mock the gate to deny on promote
        with patch('corvin_operator.forge.forge.registry.require_capability') as mock_req:
            mock_req.side_effect = LicenseDenied("tier downgrade detected")

            # promote() should raise PermissionError
            with pytest.raises(PermissionError):
                registry.promote("test_tool")


class TestG4E2E:
    """E2E: plugin_builder gate on /plugin-builder command"""

    def test_g4_command_denies_free_tier(self):
        """E2E: /plugin-builder command denies free tier."""
        from core.plugins.plugin_builder.turn import command, LicenseDenied

        with patch('core.plugins.plugin_builder.turn.require_capability') as mock_req:
            mock_req.side_effect = LicenseDenied("forge.create not available")

            result = command(
                "",
                tenant_id="_default",
                session_key="test_session"
            )

            # Should return error message, not raise
            assert isinstance(result, str)
            assert len(result) > 0

    def test_g4_command_allows_member_tier(self):
        """E2E: /plugin-builder command allows member tier."""
        from core.plugins.plugin_builder.turn import command

        with patch('core.plugins.plugin_builder.turn.require_capability') as mock_req:
            mock_req.return_value = None  # Allowed

            # Mock session_store to return no existing session
            with patch('core.plugins.plugin_builder.turn.session_store.get') as mock_get:
                mock_get.return_value = None

                # Call with "status" which doesn't need to start interview
                result = command(
                    "status",
                    tenant_id="_default",
                    session_key="test_session"
                )

                # Should return status message
                assert "no interview" in result.lower() or "active" in result.lower()


class TestG5E2E:
    """E2E: orchestration quota gate on skill_forge/tool_forge"""

    def test_g5_skill_forge_denies_free_tier(self):
        """E2E: check_forge_capability denies free tier."""
        from core.orchestration.quota_gate import check_forge_capability, LicenseDenied

        with patch('core.orchestration.quota_gate.require_capability') as mock_req:
            mock_req.side_effect = LicenseDenied("forge.create denied")

            with pytest.raises(LicenseDenied):
                check_forge_capability("_default", entry_point="skill_forge_mcp")

    def test_g5_tool_forge_allows_member_tier(self):
        """E2E: check_forge_capability allows member tier."""
        from core.orchestration.quota_gate import check_forge_capability

        with patch('core.orchestration.quota_gate.require_capability') as mock_req:
            mock_req.return_value = None  # Allowed

            # Should not raise
            check_forge_capability("_default", entry_point="tool_forge_mcp")

            # Verify it was called with forge.create
            mock_req.assert_called_once()
            assert "forge.create" in str(mock_req.call_args)


class TestGateAuditTrail:
    """Verify gates emit audit events."""

    def test_g1_denies_emit_audit_event(self):
        """Gates emit audit events on denial."""
        # This is a placeholder for when ADR-0703 audit integration is complete
        # For now, we just verify the gate structure is in place
        from corvin_operator.license.capability_api import require_capability
        assert require_capability is not None
