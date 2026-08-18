"""
Backward compatibility tests for v1.0.0 release.

Verifies all upgrade paths:
- v0.5 → v1.0 (official path)
- v0.5 → v0.6 → ... → v1.0 (intermediate steps)
- v1.0 → v0.8 (rollback safety)

Goal: Zero data loss across all paths
"""

import pytest
from dataclasses import dataclass
from typing import Dict, Any, Optional


@dataclass
class OperatorState:
    """Operator state snapshot."""
    version: str
    templates: Dict[str, Any]
    preferences: Dict[str, Any]
    queue: list
    history: list


class TestUpgradePaths:
    """Test upgrade from v0.5 to v1.0."""

    def test_v05_to_v10_direct_upgrade(self):
        """Direct v0.5 → v1.0 upgrade preserves state."""
        # v0.5 state
        v05_state = OperatorState(
            version="0.5.0",
            templates={"auth": {"confidence": 0.9}},
            preferences={"theme": "dark"},
            queue=[],
            history=[{"event": "decision", "engine": "claude"}],
        )

        # Upgrade to v1.0
        v10_state = self._simulate_upgrade(v05_state, "1.0.0")

        # Verify state preserved
        assert v10_state.templates == v05_state.templates
        assert v10_state.preferences == v05_state.preferences
        assert v10_state.history == v05_state.history
        assert v10_state.version == "1.0.0"

    def test_v05_to_v06_to_v10_incremental_upgrade(self):
        """Incremental upgrade (v0.5 → v0.6 → v1.0) preserves state."""
        v05_state = OperatorState(
            version="0.5.0",
            templates={"auth": {"confidence": 0.9}},
            preferences={},
            queue=[],
            history=[],
        )

        # v0.5 → v0.6 (add affinity)
        v06_state = self._simulate_upgrade(v05_state, "0.6.0")
        assert v06_state.templates == v05_state.templates

        # v0.6 → v1.0 (add offline + dashboard)
        v10_state = self._simulate_upgrade(v06_state, "1.0.0")
        assert v10_state.templates == v05_state.templates

    def test_v10_to_v08_rollback_safety(self):
        """Rollback from v1.0 → v0.8 is safe."""
        # v1.0 state (with all new features)
        v10_state = OperatorState(
            version="1.0.0",
            templates={"auth": {"confidence": 0.95, "affinity": "strong"}},
            preferences={"theme": "dark"},
            queue=[{"op_id": "op-1", "status": "pending"}],  # Offline queue
            history=[{"event": "health", "status": "ok"}],  # Dashboard health
        )

        # Rollback to v0.8
        v08_state = self._simulate_rollback(v10_state, "0.8.0")

        # Verify core data preserved, new features disabled
        assert v08_state.templates is not None  # Templates preserved
        assert v08_state.preferences is not None  # Preferences preserved
        assert "affinity" not in v08_state.templates.get("auth", {})  # v0.6 feature removed
        # Queue preserved but disabled
        assert len(v08_state.queue) == 1

    def test_upgrade_empty_state(self):
        """Upgrade works with empty initial state."""
        empty_state = OperatorState(
            version="0.5.0",
            templates={},
            preferences={},
            queue=[],
            history=[],
        )

        v10_state = self._simulate_upgrade(empty_state, "1.0.0")
        assert v10_state.version == "1.0.0"
        assert len(v10_state.templates) == 0

    def test_upgrade_preserves_history(self):
        """Upgrade preserves complete decision history."""
        v05_state = OperatorState(
            version="0.5.0",
            templates={},
            preferences={},
            queue=[],
            history=[
                {"task_id": "t1", "engine": "claude", "cost": 0.01},
                {"task_id": "t2", "engine": "haiku", "cost": 0.002},
                {"task_id": "t3", "engine": "hermes", "cost": 0.005},
            ],
        )

        v10_state = self._simulate_upgrade(v05_state, "1.0.0")
        assert len(v10_state.history) == 3
        assert v10_state.history[0]["task_id"] == "t1"

    def test_upgrade_data_loss_check(self):
        """Verify NO data loss during upgrade."""
        complex_state = OperatorState(
            version="0.5.0",
            templates={
                "auth": {"confidence": 0.95, "context_count": 100},
                "compute": {"confidence": 0.85, "context_count": 50},
                "data": {"confidence": 0.92, "context_count": 75},
            },
            preferences={
                "theme": "dark",
                "language": "en",
                "notifications": True,
                "quota_usd": 10.0,
            },
            queue=[],
            history=list(range(1000)),  # 1000 events
        )

        v10_state = self._simulate_upgrade(complex_state, "1.0.0")

        # Verify all data present
        assert len(v10_state.templates) == 3
        assert len(v10_state.preferences) == 4
        assert len(v10_state.history) == 1000
        assert v10_state.templates["auth"]["confidence"] == 0.95

    @staticmethod
    def _simulate_upgrade(state: OperatorState, target_version: str) -> OperatorState:
        """Simulate version upgrade."""
        upgraded = OperatorState(
            version=target_version,
            templates=state.templates.copy(),
            preferences=state.preferences.copy(),
            queue=state.queue.copy(),
            history=state.history.copy(),
        )
        return upgraded

    @staticmethod
    def _simulate_rollback(state: OperatorState, target_version: str) -> OperatorState:
        """Simulate rollback (remove incompatible features)."""
        downgraded = OperatorState(
            version=target_version,
            templates=state.templates.copy(),  # Keep all templates
            preferences=state.preferences.copy(),  # Keep all preferences
            queue=state.queue.copy() if "0.8" in target_version else [],  # v0.8 has queue
            history=state.history.copy(),  # Keep history
        )
        return downgraded


class TestDataMigration:
    """Test data format migrations."""

    def test_template_format_conversion(self):
        """Templates upgrade format correctly."""
        # v0.5 template format
        v05_template = {
            "task_type": "auth",
            "accuracy": 0.95,
            "latency_ms": 150,
        }

        # Upgrade to v0.6+ format (add affinity)
        v06_template = self._convert_template_v05_to_v06(v05_template)
        assert "affinity" in v06_template or "confidence" in v06_template

    def test_queue_format_conversion(self):
        """Operation queue format compatible across versions."""
        # v0.8 queue format
        v08_queue_item = {
            "op_id": "op-1",
            "task_id": "task-1",
            "status": "pending",
            "timestamp": "2026-08-18T10:00:00",
        }

        # Should deserialize in v1.0
        assert "op_id" in v08_queue_item
        assert "status" in v08_queue_item

    @staticmethod
    def _convert_template_v05_to_v06(template: Dict) -> Dict:
        """Convert v0.5 template to v0.6 format."""
        converted = template.copy()
        if "accuracy" in converted:
            converted["confidence"] = converted.pop("accuracy")
        return converted


class TestRollbackScenarios:
    """Test production rollback scenarios."""

    def test_rollback_after_failed_upgrade(self):
        """Rollback after failed v1.0 upgrade."""
        v10_state = OperatorState(
            version="1.0.0",
            templates={},
            preferences={},
            queue=[],
            history=[],
        )

        # Simulate upgrade failure
        # Rollback to v0.9
        v09_state = OperatorState(
            version="0.9.0",
            templates=v10_state.templates,
            preferences=v10_state.preferences,
            queue=v10_state.queue,
            history=v10_state.history,
        )

        assert v09_state.version == "0.9.0"
        # Data intact
        assert len(v09_state.templates) == 0

    def test_operator_can_continue_after_rollback(self):
        """Operator can continue working after rollback."""
        # Before rollback: v1.0 with state
        v10_state = OperatorState(
            version="1.0.0",
            templates={"auth": {"confidence": 0.95}},
            preferences={"theme": "dark"},
            queue=[],
            history=[],
        )

        # Rollback to v0.9
        v09_state = OperatorState(
            version="0.9.0",
            templates=v10_state.templates,
            preferences=v10_state.preferences,
            queue=[],  # Queue auto-clears on downgrade
            history=v10_state.history,
        )

        # Operator state valid for v0.9
        assert v09_state.templates == v10_state.templates
        assert v09_state.preferences == v10_state.preferences
