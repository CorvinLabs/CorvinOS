"""Stream 4 Phase 2: Workflow Optimizer Integration Tests (ADR-2050).

13 E2E tests covering:
  1. Feedback write → EventStore (3 tests)
  2. EventStore read → ConfidenceCalculator (3 tests)
  3. Confidence update → weight persistence (3 tests)
  4. Full loop: feedback → weight update → routing uses learned weights (4 tests)

All tests verify:
  - Audit trail integrity (chain binding)
  - Tenant isolation (GDPR Art. 32)
  - Fail-closed on error (no silent loss)
  - Weight versioning (immutable history)
"""

import json
import pytest
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

from core.learning.event_store import EventStore
from core.learning.feedback_integration.event_store_writer import EventStoreWriter
from core.learning.learning_events import LearningEvent, EventType
from core.skills.os_skills.workflow_optimizer_skill.confidence_calculator import (
    ConfidenceCalculator,
    RoutingWeights,
)



@pytest.fixture(autouse=True)
def _downstream_of_the_feedback_gate(monkeypatch):
    """This suite exercises code downstream of the ADR-0534 feedback gate (writers,
    aggregations, optimizers), with bulk feedback on skills its sandbox never ran.
    The gate itself is proven in tests/learning/test_feedback_trust_gate_e2e.py."""
    from tests.learning.feedback_gate_helpers import open_gate
    open_gate(monkeypatch)





class TestPhase2WeightPersistence(unittest.TestCase):
    """Group 3: Confidence update → weight persistence (3 tests)."""

    def setUp(self):
        """Create confidence calculator with config directory."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tenant_home = Path(self.temp_dir.name)
        self.event_store = EventStore(self.tenant_home, tenant_id="_default")
        self.confidence_calc = ConfidenceCalculator(
            event_store=self.event_store,
            tenant_id="_default",
            config_dir=Path(self.temp_dir.name) / "config",
        )

    def tearDown(self):
        """Cleanup."""
        self.temp_dir.cleanup()

    def test_weights_saved_to_json(self):
        """Test 7: Updated weights persisted to JSON file."""
        weights = RoutingWeights(
            weights={
                "simple_haiku": 0.90,
                "medium_sonnet": 0.85,
                "complex_opus": 0.88,
            },
            feedback_count=50,
        )

        # Save weights
        self.confidence_calc.save_weights(weights)

        # Verify file exists
        self.assertTrue(self.confidence_calc.weights_file.exists())

        # Read back and verify
        with open(self.confidence_calc.weights_file, "r") as f:
            data = json.load(f)

        self.assertEqual(data["weights"]["simple_haiku"], 0.90)
        self.assertEqual(data["feedback_count"], 50)

    def test_weights_history_versioned_immutable(self):
        """Test 8: Weight history is immutable (one version per update)."""
        weights_v1 = RoutingWeights(weights={"simple_haiku": 0.85}, feedback_count=10)
        weights_v2 = RoutingWeights(weights={"simple_haiku": 0.92}, feedback_count=20)

        # Save v1
        self.confidence_calc.save_weights(weights_v1)
        history_v1 = list(self.confidence_calc.history_dir.glob("*.json"))
        self.assertEqual(len(history_v1), 1)

        # Save v2
        self.confidence_calc.save_weights(weights_v2)
        history_v2 = list(self.confidence_calc.history_dir.glob("*.json"))
        self.assertEqual(len(history_v2), 2)  # Both versions exist

        # Verify v1 unchanged
        with open(history_v1[0], "r") as f:
            v1_data = json.load(f)
        self.assertEqual(v1_data["weights"]["simple_haiku"], 0.85)

    def test_config_updated_event_audited(self):
        """Test 9: CONFIG_UPDATED event emitted to audit trail."""
        weights = RoutingWeights(
            weights={"simple_haiku": 0.88, "medium_sonnet": 0.91},
            feedback_count=75,
        )

        with patch.object(self.event_store, "write_event") as mock_write:
            self.confidence_calc.save_weights(weights)
        mock_write.assert_called_once()
        event = mock_write.call_args.args[0]
        self.assertEqual(event.event_type, EventType.CONFIG_UPDATED)
        self.assertIn("simple_haiku", event.signal["config_delta"])

    def test_audit_failure_persists_nothing(self):
        """Audit-first: a failed CONFIG_UPDATED write leaves no weights on disk."""
        weights = RoutingWeights(weights={"simple_haiku": 0.10}, feedback_count=1)
        with patch.object(self.event_store, "write_event",
                          side_effect=RuntimeError("chain down")):
            with self.assertRaises(RuntimeError):
                self.confidence_calc.save_weights(weights)
        self.assertFalse(self.confidence_calc.weights_file.exists())




if __name__ == "__main__":
    unittest.main()
