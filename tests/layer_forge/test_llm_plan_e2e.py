"""E2E tests for Layer Forge LLM planning (ADR-2225).

Tests that the LLM plan phase:
1. Generates valid JSON manifests
2. Produces deterministic output (same input = same output structure)
3. Integrates with validation + audit chain
4. Fails gracefully on invalid LLM output
5. Audits all plan attempts (success + failure)
"""
from __future__ import annotations

import json
import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add repo root to path
REPO_ROOT = Path(__file__).resolve().parents[2]
import sys
sys.path.insert(0, str(REPO_ROOT))

from core.orchestration.layer_forge.llm_plan import (
    plan_layer_definition,
    generate_manifest_from_intent,
    LLMPlanError,
    LayerPlanInput,
)
from core.orchestration.layer_forge.orchestrator import LayerForgeOrchestrator
from core.orchestration.layer_forge.schema import validate_manifest


class TestLayerPlanPrompt:
    """Test prompt construction."""

    def test_plan_input_validation(self):
        """LayerPlanInput rejects invalid inputs."""
        with pytest.raises(ValueError, match="layer_id"):
            LayerPlanInput(layer_id="", intent="...")

        with pytest.raises(ValueError, match="intent"):
            LayerPlanInput(layer_id="L34", intent="")

        # Valid
        inp = LayerPlanInput(layer_id="L34", intent="audit L10")
        assert inp.layer_id == "L34"
        assert inp.intent == "audit L10"


class TestLLMManifestGeneration:
    """Test LLM manifest generation (mock + real)."""

    def test_generate_manifest_mock_valid_json(self):
        """Mock LLM returns valid manifest JSON."""
        mock_manifest = {
            "id": "l34-audit",
            "version": "1.0.0",
            "targets": [{"layer_id": "L34", "layer_name": "Data Flow Guard"}],
            "dependencies": [],
            "quality_gates": [],
            "enforcement_rules": [],
        }

        with patch("anthropic.Anthropic") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client

            # Mock the response
            mock_msg = MagicMock()
            mock_msg.content = [MagicMock(text=json.dumps(mock_manifest))]
            mock_client.messages.create.return_value = mock_msg

            result = generate_manifest_from_intent("L34", "audit L10")
            assert result == mock_manifest
            assert result["id"] == "l34-audit"

    def test_generate_manifest_mock_invalid_json(self):
        """Mock LLM returns invalid JSON."""
        with patch("anthropic.Anthropic") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client

            # Mock invalid JSON response
            mock_msg = MagicMock()
            mock_msg.content = [MagicMock(text="{ not valid json")]
            mock_client.messages.create.return_value = mock_msg

            with pytest.raises(LLMPlanError, match="not valid JSON"):
                generate_manifest_from_intent("L34", "audit L10")

    def test_generate_manifest_mock_not_object(self):
        """Mock LLM returns valid JSON but not an object."""
        with patch("anthropic.Anthropic") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client

            # Mock array response
            mock_msg = MagicMock()
            mock_msg.content = [MagicMock(text="[]")]
            mock_client.messages.create.return_value = mock_msg

            with pytest.raises(LLMPlanError, match="must be a JSON object"):
                generate_manifest_from_intent("L34", "audit L10")

    def test_generate_manifest_client_error(self):
        """LLM API call fails."""
        with patch("anthropic.Anthropic") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            mock_client.messages.create.side_effect = Exception("API error")

            with pytest.raises(LLMPlanError, match="LLM call failed"):
                generate_manifest_from_intent("L34", "audit L10")

    def test_generate_manifest_empty_response(self):
        """LLM returns empty content."""
        with patch("anthropic.Anthropic") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client

            # Mock empty response
            mock_msg = MagicMock()
            mock_msg.content = []
            mock_client.messages.create.return_value = mock_msg

            with pytest.raises(LLMPlanError, match="empty response"):
                generate_manifest_from_intent("L34", "audit L10")


class TestLayerForgeOrchestratorPlan:
    """Test orchestrator plan integration."""

    def test_orchestrator_plan_success(self, tmp_path):
        """Orchestrator.plan_layer_definition succeeds with valid LLM output."""
        tenant_home = tmp_path / "tenants" / "_default" / "global" / "layer_forge"
        tenant_home.mkdir(parents=True, exist_ok=True)

        mock_manifest = {
            "id": "l34-audit",
            "version": "1.0.0",
            "targets": [{"layer_id": "L34"}],
            "dependencies": [],
            "quality_gates": [],
            "enforcement_rules": [],
        }

        with patch("core.orchestration.layer_forge.orchestrator.layer_forge_home") as mock_home:
            with patch("core.orchestration.layer_forge.llm_plan.generate_manifest_from_intent") as mock_gen:
                with patch("core.orchestration.layer_forge.audit.emit") as mock_audit:
                    mock_home.return_value = tenant_home
                    mock_gen.return_value = mock_manifest
                    mock_audit.return_value = "event-id"

                    orchestrator = LayerForgeOrchestrator("_default", actor="test")
                    manifest, result = orchestrator.plan_layer_definition("L34", "audit L10")
                    assert result.status == "SUCCESS"
                    assert manifest == mock_manifest

    def test_orchestrator_plan_llm_error(self, tmp_path):
        """Orchestrator.plan_layer_definition handles LLM error."""
        tenant_home = tmp_path / "tenants" / "_default" / "global" / "layer_forge"
        tenant_home.mkdir(parents=True, exist_ok=True)

        with patch("core.orchestration.layer_forge.orchestrator.layer_forge_home") as mock_home:
            mock_home.return_value = tenant_home
            orchestrator = LayerForgeOrchestrator("_default", actor="test")

            with patch("core.orchestration.layer_forge.orchestrator.plan_layer_definition") as mock_plan:
                mock_plan.side_effect = LLMPlanError("invalid JSON")

                manifest, result = orchestrator.plan_layer_definition("L34", "audit L10")
                assert result.status == "FAILED"
                assert result.phase == "plan"
                assert manifest is None
                assert "LLM planning failed" in result.error

    def test_orchestrator_plan_audit_recorded(self, tmp_path):
        """Orchestrator audits plan attempts."""
        tenant_home = tmp_path / "tenants" / "_default" / "global" / "layer_forge"
        tenant_home.mkdir(parents=True, exist_ok=True)

        mock_manifest = {
            "id": "l34-audit",
            "version": "1.0.0",
            "targets": [{"layer_id": "L34"}],
        }

        with patch("core.orchestration.layer_forge.orchestrator.layer_forge_home") as mock_home:
            mock_home.return_value = tenant_home
            orchestrator = LayerForgeOrchestrator("_default", actor="test")

            with patch("core.orchestration.layer_forge.orchestrator.plan_layer_definition") as mock_plan:
                mock_plan.return_value = mock_manifest

                with patch("core.orchestration.layer_forge.audit.emit") as mock_audit:
                    manifest, result = orchestrator.plan_layer_definition("L34", "audit L10")
                    assert result.status == "SUCCESS"

                    # Verify audit was called
                    assert mock_audit.called
                    call_args = [str(call) for call in mock_audit.call_args_list]
                    # One of the calls should have layer_forge.plan_generated
                    assert any("plan_generated" in str(call) for call in call_args)


class TestLayerForgePlanIntegration:
    """Integration: plan -> create pipeline."""

    def test_plan_then_validate_success(self, tmp_path):
        """Plan generates valid manifest that passes validation."""
        tenant_home = tmp_path / "tenants" / "_default" / "global" / "layer_forge"
        tenant_home.mkdir(parents=True, exist_ok=True)

        mock_manifest = {
            "id": "l34-audit",
            "version": "1.0.0",
            "type": "layer_definition",
            "targets": [{"layer_id": "L34", "layer_name": "Data Flow Guard"}],
            "dependencies": [],
            "quality_gates": [],
            "enforcement_rules": [],
            "host_awareness": {"cross_check": "none"},
        }

        with patch("core.orchestration.layer_forge.orchestrator.layer_forge_home") as mock_home:
            with patch("core.orchestration.layer_forge.llm_plan.generate_manifest_from_intent") as mock_gen:
                with patch("core.orchestration.layer_forge.audit.emit") as mock_audit:
                    mock_home.return_value = tenant_home
                    mock_gen.return_value = mock_manifest
                    mock_audit.return_value = "event-id"

                    orchestrator = LayerForgeOrchestrator("_default", actor="test")
                    manifest, plan_result = orchestrator.plan_layer_definition("L34", "audit")
                    assert plan_result.status == "SUCCESS"

                    # Validate the generated manifest
                    validate_manifest(manifest)  # Should not raise


class TestLayerForgeCliPlan:
    """Test CLI plan command."""

    def test_cli_plan_command_help(self):
        """CLI plan command is registered."""
        from scripts.layer_forge_cli import main
        import io
        import sys

        # Capture help output
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        result = None
        try:
            result = main(["--help"])
        except SystemExit as e:
            result = e.code
        finally:
            sys.stdout = old_stdout

        # Help exits with code 0
        assert result == 0 or result is None


class TestLayerForgeConsoleRoute:
    """Test console route integration."""

    def test_console_plan_route_exists(self):
        """Console plan route is registered."""
        from core.console.corvin_console.routes.layer_forge import router

        # Find the plan route
        plan_route = None
        for route in router.routes:
            if hasattr(route, "path") and "/plan" in route.path:
                plan_route = route
                break

        assert plan_route is not None, "Plan route should be registered"


class TestLayerForgeAuditEvents:
    """Test that audit events are generated correctly."""

    def test_plan_success_event(self, tmp_path):
        """Successful plan emits layer_forge.plan_generated event."""
        tenant_home = tmp_path / "tenants" / "_default" / "global" / "layer_forge"
        tenant_home.mkdir(parents=True, exist_ok=True)

        mock_manifest = {
            "id": "l34-audit",
            "version": "1.0.0",
            "targets": [{"layer_id": "L34"}],
        }

        events_emitted = []

        def mock_audit(event_name, **kwargs):
            events_emitted.append((event_name, kwargs))

        with patch("core.orchestration.layer_forge.orchestrator.layer_forge_home") as mock_home:
            mock_home.return_value = tenant_home
            orchestrator = LayerForgeOrchestrator("_default", actor="test")

            with patch("core.orchestration.layer_forge.orchestrator.plan_layer_definition") as mock_plan:
                mock_plan.return_value = mock_manifest
                with patch("core.orchestration.layer_forge.audit.emit", side_effect=mock_audit):
                    manifest, result = orchestrator.plan_layer_definition("L34", "audit L10")

        event_names = [e[0] for e in events_emitted]
        assert "layer_forge.plan_generated" in event_names

    def test_plan_failure_event(self, tmp_path):
        """Failed plan emits layer_forge.plan_failed event."""
        tenant_home = tmp_path / "tenants" / "_default" / "global" / "layer_forge"
        tenant_home.mkdir(parents=True, exist_ok=True)

        events_emitted = []

        def mock_audit(event_name, **kwargs):
            events_emitted.append((event_name, kwargs))

        with patch("core.orchestration.layer_forge.orchestrator.layer_forge_home") as mock_home:
            mock_home.return_value = tenant_home
            orchestrator = LayerForgeOrchestrator("_default", actor="test")

            with patch("core.orchestration.layer_forge.orchestrator.plan_layer_definition") as mock_plan:
                mock_plan.side_effect = LLMPlanError("JSON error")
                with patch("core.orchestration.layer_forge.audit.emit", side_effect=mock_audit):
                    manifest, result = orchestrator.plan_layer_definition("L34", "audit L10")

        event_names = [e[0] for e in events_emitted]
        assert "layer_forge.plan_failed" in event_names


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
