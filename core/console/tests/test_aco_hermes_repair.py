"""Tests for HermesHealthRepair — ACO L5 Tier LOCAL Hermes self-repair."""
from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from tempfile import TemporaryDirectory

from corvin_console.aco.repair_actions import (
    HermesHealthRepair,
    RepairContext,
    RepairOutcome,
    run_local_repairs,
)


@pytest.fixture
def repair_context():
    """Create a temporary CORVIN_HOME for testing."""
    with TemporaryDirectory() as tmpdir:
        home = Path(tmpdir) / "corvin_home"
        home.mkdir(parents=True)
        yield RepairContext(corvin_home=home, tenant_id="_default")


class TestHermesHealthRepairPrecondition:
    """Test fault detection."""

    def test_precondition_healthy(self, repair_context):
        """No faults when Hermes is healthy."""
        action = HermesHealthRepair()
        with patch.object(action, "_hermes_available", return_value=True):
            faults = action.precondition(repair_context)
            assert faults == []

    def test_precondition_not_reachable(self, repair_context):
        """Detect when Ollama is unreachable."""
        action = HermesHealthRepair()
        with patch("hermes_healing.get_health_status") as mock_status:
            mock_status.return_value = {
                "reachable": False,
                "has_model": False,
            }
            faults = action.precondition(repair_context)
            assert "not_reachable" in faults

    def test_precondition_model_missing(self, repair_context):
        """Detect when model is missing."""
        action = HermesHealthRepair()
        with patch("hermes_healing.get_health_status") as mock_status:
            mock_status.return_value = {
                "reachable": True,
                "has_model": False,
            }
            faults = action.precondition(repair_context)
            assert "model_missing" in faults

    def test_precondition_module_not_available(self, repair_context):
        """Gracefully handle when hermes_healing is not available."""
        action = HermesHealthRepair()
        with patch.object(action, "_import_hermes_healing", return_value=None):
            faults = action.precondition(repair_context)
            assert faults == []


class TestHermesHealthRepairApply:
    """Test repair application."""

    def test_apply_no_faults(self, repair_context):
        """No action taken when there are no faults."""
        action = HermesHealthRepair()
        fixed = action.apply(repair_context, [])
        assert fixed == 0

    def test_apply_server_startup_success(self, repair_context):
        """Successful server startup increments fixed count."""
        action = HermesHealthRepair()
        # The loss-gate re-checks reachability with a FRESH get_health_status, so
        # mock it healthy too (deterministic regardless of a real local Ollama).
        with patch("hermes_healing.repair_hermes") as mock_repair, \
             patch("hermes_healing.get_health_status",
                   return_value={"reachable": True, "has_model": True}):
            mock_repair.return_value = {
                "server_started": True,
                "model_pulled": False,
                "reachable": True,
                "error": None,
            }
            fixed = action.apply(repair_context, ["not_reachable"])
            assert fixed >= 1

    def test_apply_model_pull_success(self, repair_context):
        """Successful model pull increments fixed count."""
        action = HermesHealthRepair()
        with patch("hermes_healing.repair_hermes") as mock_repair, \
             patch("hermes_healing.get_health_status",
                   return_value={"reachable": True, "has_model": True}):
            mock_repair.return_value = {
                "server_started": False,
                "model_pulled": True,
                "reachable": True,
                "error": None,
            }
            fixed = action.apply(repair_context, ["model_missing"])
            assert fixed >= 1

    def test_apply_repair_fails(self, repair_context):
        """Repair returns 0 when the loss-gate recheck stays unreachable."""
        action = HermesHealthRepair()
        with patch("hermes_healing.repair_hermes") as mock_repair, \
             patch("hermes_healing.get_health_status",
                   return_value={"reachable": False, "has_model": False}):
            mock_repair.return_value = {
                "server_started": False,
                "model_pulled": False,
                "reachable": False,
                "error": "Ollama binary not found",
            }
            fixed = action.apply(repair_context, ["not_reachable"])
            assert fixed == 0


class TestHermesHealthRepairUndo:
    """Test repair reversal."""

    def test_undo_noop(self, repair_context):
        """Undo is a no-op (we want Ollama to stay running)."""
        action = HermesHealthRepair()
        # Should not raise, even if called twice
        action.undo(repair_context)
        action.undo(repair_context)


class TestHermesHealthIntegration:
    """Test full repair cycle with loss-gating."""

    def test_full_cycle_healthy_after_repair(self, repair_context):
        """Full repair cycle: detect -> apply -> verify reachability."""
        action = HermesHealthRepair()
        with patch("hermes_healing.get_health_status") as mock_status, \
             patch("hermes_healing.repair_hermes") as mock_repair:
            # Initially unhealthy
            mock_status.side_effect = [
                {"reachable": False, "has_model": False},  # precondition
                {"reachable": True, "has_model": True},    # reachability check after repair
            ]
            mock_repair.return_value = {
                "server_started": True,
                "model_pulled": False,
                "reachable": True,
                "error": None,
            }
            faults = action.precondition(repair_context)
            assert len(faults) > 0
            fixed = action.apply(repair_context, faults)
            assert fixed > 0

    def test_full_cycle_repair_fails(self, repair_context):
        """Repair returns 0 when system remains unhealthy."""
        action = HermesHealthRepair()
        with patch("hermes_healing.get_health_status") as mock_status:
            # Remains unhealthy even after repair attempt
            mock_status.return_value = {"reachable": False, "has_model": False}
            faults = action.precondition(repair_context)
            fixed = action.apply(repair_context, faults)
            # Loss-gate: no progress, so fixed=0 (will be rolled back)
            assert fixed == 0


class TestHermesHealingImport:
    """SH-7: the shared dir must resolve from the PACKAGE location (Path(__file__)),
    NOT from corvin_home — otherwise a pinned CORVIN_HOME=<repo>/.corvin walks two
    levels too high and this L5 repair becomes a dead no-op."""

    def test_import_resolves_from_package_not_corvin_home(self):
        action = HermesHealthRepair()
        # A bogus corvin_home must NOT affect resolution (the old bug read the
        # shared dir off corvin_home.parent.parent.parent).
        ctx = RepairContext(corvin_home=Path("/nonexistent/pinned/home"))
        mod = action._import_hermes_healing(ctx)
        assert mod is not None, "hermes_healing must import from the package location"
        assert hasattr(mod, "get_health_status")
        assert hasattr(mod, "repair_hermes")

    def test_shared_dirs_are_package_relative(self):
        dirs = HermesHealthRepair._shared_dirs()
        # None of the candidates are derived from a runtime CORVIN_HOME.
        assert any(d.name == "shared" for d in dirs)
        assert all("shared" in str(d) for d in dirs)


class TestHermesRepairRegistry:
    """Test that HermesHealthRepair is properly registered."""

    def test_action_registered(self):
        """HermesHealthRepair is registered in the repair registry."""
        from corvin_console.aco.repair_actions import registered_actions
        actions = registered_actions()
        assert "hermes_health" in actions

    def test_action_properties(self):
        """HermesHealthRepair has correct properties."""
        from corvin_console.aco.repair_actions import registered_actions, RISK_RISKY
        actions = registered_actions()
        action = actions["hermes_health"]
        assert action.risk == RISK_RISKY
        assert action.blast_radius == "home"
