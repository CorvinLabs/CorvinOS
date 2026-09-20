"""Tests for SkillLossTriggerDetector.

Unit tests for:
  - detect_loss_signals() with low, threshold, and high confidence
  - Tenant isolation (cross-tenant reads blocked)
  - Lookback window parameter
  - Fail-closed behavior on invalid audit data
"""

from __future__ import annotations

import json
import logging
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add parent directories to path to import modules with dashes
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

# Import via importlib to handle the dash in the module name
import importlib.util
skill_forge_path = REPO / "corvin_operator" / "skill-forge" / "autonomous"
spec = importlib.util.spec_from_file_location(
    "trigger_detector",
    skill_forge_path / "trigger_detector.py"
)
trigger_detector_module = importlib.util.module_from_spec(spec)

# Mock the core modules before importing
sys.modules["core"] = MagicMock()
sys.modules["core.paths"] = MagicMock()
sys.modules["core.tenants"] = MagicMock()

spec.loader.exec_module(trigger_detector_module)

SkillLossTriggerDetector = trigger_detector_module.SkillLossTriggerDetector
LossTrigger = trigger_detector_module.LossTrigger


# Test helper functions
PASS = 0
FAIL = 0


def test(label, ok, *, detail=""):
    """Record a test result."""
    global PASS, FAIL
    status = "✅ PASS" if ok else "❌ FAIL"
    print(f"{status}  {label}{(' — ' + detail) if detail else ''}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


def create_mock_audit_file(
    audit_path: Path,
    tenant_id: str,
    events: list[dict],
) -> None:
    """Write mock audit events to a JSON-lines file."""
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with open(audit_path, "w") as f:
        for event in events:
            f.write(json.dumps(event) + "\n")


def create_skill_event(
    skill_id: str,
    version: str,
    correct: bool,
    ts: float,
    tenant_id: str = "_default",
) -> dict:
    """Create a mock skill_executed audit event."""
    return {
        "ts": ts,
        "event_type": "skill_executed",
        "skill_id": skill_id,
        "version": version,
        "tenant_id": tenant_id,
        "outcome_feedback": {
            "correct": correct,
            "reason": "test event",
        },
        "hash": "mock_hash",
        "prev_hash": "mock_prev_hash",
    }


def test_detect_loss_when_confidence_below_threshold():
    """Test that a trigger is emitted when confidence < 0.70."""
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_audit_dir = Path(tmpdir)
        detector = SkillLossTriggerDetector()
        tenant_id = "_default"
        now = datetime.utcnow()
        base_ts = now.timestamp()

        # Create 10 events: 3 correct, 7 incorrect → confidence = 0.30
        events = []
        for i in range(10):
            correct = i < 3  # First 3 are correct
            event = create_skill_event(
                skill_id="os.delegation_router",
                version="1.2.3",
                correct=correct,
                ts=base_ts - (10 - i) * 3600,  # Spread over 10 hours
                tenant_id=tenant_id,
            )
            events.append(event)

        audit_path = temp_audit_dir / "tenants" / tenant_id / "global" / "forge" / "audit.jsonl"
        create_mock_audit_file(audit_path, tenant_id, events)

        # Mock tenant_audit_chain to return our test file
        with patch.object(trigger_detector_module, "tenant_audit_chain") as mock_chain:
            mock_chain.return_value = audit_path

            # Also mock validate_tenant_id to pass through
            with patch.object(trigger_detector_module, "validate_tenant_id") as mock_validate:
                mock_validate.return_value = None

                triggers = detector.detect_loss_signals(tenant_id, lookback_hours=24)

        # Verify results
        ok = (
            len(triggers) == 1 and
            triggers[0].skill_id == "os.delegation_router" and
            triggers[0].version == "1.2.3" and
            abs(triggers[0].confidence - 0.30) < 0.01 and
            triggers[0].event_count == 10 and
            triggers[0].lookback_hours == 24
        )
        test("detect_loss_when_confidence_below_threshold", ok)


def test_no_trigger_when_confident():
    """Test that no trigger is emitted when confidence >= 0.70."""
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_audit_dir = Path(tmpdir)
        detector = SkillLossTriggerDetector()
        tenant_id = "_default"
        now = datetime.utcnow()
        base_ts = now.timestamp()

        # Create 10 events: 8 correct, 2 incorrect → confidence = 0.80
        events = []
        for i in range(10):
            correct = i < 8  # First 8 are correct
            event = create_skill_event(
                skill_id="os.context_adapter",
                version="2.0.0",
                correct=correct,
                ts=base_ts - (10 - i) * 3600,
                tenant_id=tenant_id,
            )
            events.append(event)

        audit_path = temp_audit_dir / "tenants" / tenant_id / "global" / "forge" / "audit.jsonl"
        create_mock_audit_file(audit_path, tenant_id, events)

        with patch.object(trigger_detector_module, "tenant_audit_chain") as mock_chain:
            mock_chain.return_value = audit_path
            with patch.object(trigger_detector_module, "validate_tenant_id") as mock_validate:
                mock_validate.return_value = None

                triggers = detector.detect_loss_signals(tenant_id, lookback_hours=24)

        # Expect no triggers (confidence 0.80 >= 0.70)
        ok = len(triggers) == 0
        test("no_trigger_when_confident", ok)


def test_no_trigger_at_exact_threshold():
    """Test boundary: confidence == 0.70 should NOT trigger (>= threshold)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_audit_dir = Path(tmpdir)
        detector = SkillLossTriggerDetector()
        tenant_id = "_default"
        now = datetime.utcnow()
        base_ts = now.timestamp()

        # Create 10 events: 7 correct, 3 incorrect → confidence = 0.70
        events = []
        for i in range(10):
            correct = i < 7  # First 7 are correct
            event = create_skill_event(
                skill_id="os.flow_guard",
                version="1.0.0",
                correct=correct,
                ts=base_ts - (10 - i) * 3600,
                tenant_id=tenant_id,
            )
            events.append(event)

        audit_path = temp_audit_dir / "tenants" / tenant_id / "global" / "forge" / "audit.jsonl"
        create_mock_audit_file(audit_path, tenant_id, events)

        with patch.object(trigger_detector_module, "tenant_audit_chain") as mock_chain:
            mock_chain.return_value = audit_path
            with patch.object(trigger_detector_module, "validate_tenant_id") as mock_validate:
                mock_validate.return_value = None

                triggers = detector.detect_loss_signals(tenant_id, lookback_hours=24)

        # Expect no triggers (confidence == 0.70, which is NOT < 0.70)
        ok = len(triggers) == 0
        test("no_trigger_at_exact_threshold", ok)


def test_tenant_isolation():
    """Test that only tenant-scoped events are read."""
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_audit_dir = Path(tmpdir)
        detector = SkillLossTriggerDetector()
        now = datetime.utcnow()
        base_ts = now.timestamp()

        # Create events for two tenants
        events_default = []
        events_other = []

        for i in range(5):
            # Tenant _default: 1 correct, 4 incorrect → 0.20 confidence → triggers
            event = create_skill_event(
                skill_id="os.delegation_router",
                version="1.0.0",
                correct=i == 0,
                ts=base_ts - (5 - i) * 3600,
                tenant_id="_default",
            )
            events_default.append(event)

            # Tenant "other_tenant": 4 correct, 1 incorrect → 0.80 confidence → no trigger
            event = create_skill_event(
                skill_id="os.delegation_router",
                version="1.0.0",
                correct=i < 4,
                ts=base_ts - (5 - i) * 3600,
                tenant_id="other_tenant",
            )
            events_other.append(event)

        # Write both to the same file (simulating a shared audit log)
        audit_path = temp_audit_dir / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
        all_events = events_default + events_other
        create_mock_audit_file(audit_path, "_default", all_events)

        with patch.object(trigger_detector_module, "tenant_audit_chain") as mock_chain:
            mock_chain.return_value = audit_path
            with patch.object(trigger_detector_module, "validate_tenant_id") as mock_validate:
                mock_validate.return_value = None

                # Query _default tenant
                triggers = detector.detect_loss_signals("_default", lookback_hours=24)

        # Should only see _default's trigger (confidence 0.20)
        ok = (
            len(triggers) == 1 and
            triggers[0].skill_id == "os.delegation_router" and
            abs(triggers[0].confidence - 0.20) < 0.01 and
            triggers[0].event_count == 5  # Only _default's 5 events
        )
        test("tenant_isolation", ok)


def test_lookback_window_parameter():
    """Test that lookback_hours parameter filters events correctly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_audit_dir = Path(tmpdir)
        detector = SkillLossTriggerDetector()
        tenant_id = "_default"
        now = datetime.utcnow()
        base_ts = now.timestamp()

        # Create 10 events spread over 25 hours
        events = []
        for i in range(10):
            correct = i < 3
            # Events at: 0, 2.5, 5, 7.5, 10, 12.5, 15, 17.5, 20, 22.5 hours ago
            ts = base_ts - i * 2.5 * 3600
            event = create_skill_event(
                skill_id="os.test_skill",
                version="1.0.0",
                correct=correct,
                ts=ts,
                tenant_id=tenant_id,
            )
            events.append(event)

        audit_path = temp_audit_dir / "tenants" / tenant_id / "global" / "forge" / "audit.jsonl"
        create_mock_audit_file(audit_path, tenant_id, events)

        with patch.object(trigger_detector_module, "tenant_audit_chain") as mock_chain:
            mock_chain.return_value = audit_path
            with patch.object(trigger_detector_module, "validate_tenant_id") as mock_validate:
                mock_validate.return_value = None

                # Query with 10-hour lookback
                triggers = detector.detect_loss_signals(tenant_id, lookback_hours=10)

        # Should include events from last 10 hours: 0, 2.5, 5, 7.5 hours ago = 4 events
        # Of these, 3 are correct, 1 is incorrect → confidence = 0.75 (no trigger)
        ok_1 = len(triggers) == 0

        with patch.object(trigger_detector_module, "tenant_audit_chain") as mock_chain:
            mock_chain.return_value = audit_path
            with patch.object(trigger_detector_module, "validate_tenant_id") as mock_validate:
                mock_validate.return_value = None

                # Query with 20-hour lookback
                triggers = detector.detect_loss_signals(tenant_id, lookback_hours=20)

        # Should include: 0, 2.5, 5, 7.5, 10, 12.5, 15, 17.5 hours ago = 8 events
        # Of these, 3 are correct, 5 are incorrect → confidence = 0.375 (triggers!)
        ok_2 = (
            len(triggers) == 1 and
            abs(triggers[0].confidence - 0.375) < 0.01
        )
        test("lookback_window_parameter", ok_1 and ok_2)


def test_missing_audit_file():
    """Test fail-closed behavior: missing audit.jsonl returns empty list."""
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_audit_dir = Path(tmpdir)
        detector = SkillLossTriggerDetector()
        tenant_id = "_nonexistent"

        # Mock tenant_audit_chain to return a non-existent path
        nonexistent_path = temp_audit_dir / "nonexistent.jsonl"
        with patch.object(trigger_detector_module, "tenant_audit_chain") as mock_chain:
            mock_chain.return_value = nonexistent_path
            with patch.object(trigger_detector_module, "validate_tenant_id") as mock_validate:
                mock_validate.return_value = None

                # Should return empty list, not crash
                triggers = detector.detect_loss_signals(tenant_id, lookback_hours=24)

        # Verify empty list
        ok = triggers == []
        test("missing_audit_file", ok)


def test_invalid_json_in_audit_file():
    """Test fail-closed behavior: invalid JSON raises exception."""
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_audit_dir = Path(tmpdir)
        detector = SkillLossTriggerDetector()
        tenant_id = "_default"

        # Create an audit file with invalid JSON
        audit_path = temp_audit_dir / "tenants" / tenant_id / "global" / "forge" / "audit.jsonl"
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with open(audit_path, "w") as f:
            f.write('{"valid": "json"}\n')
            f.write('{"invalid json\n')  # Invalid!

        with patch.object(trigger_detector_module, "tenant_audit_chain") as mock_chain:
            mock_chain.return_value = audit_path
            with patch.object(trigger_detector_module, "validate_tenant_id") as mock_validate:
                mock_validate.return_value = None

                try:
                    detector.detect_loss_signals(tenant_id, lookback_hours=24)
                    ok = False  # Should have raised
                except json.JSONDecodeError:
                    ok = True  # Expected

        test("invalid_json_in_audit_file", ok)


def test_events_without_outcome_feedback():
    """Test events without outcome_feedback are skipped."""
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_audit_dir = Path(tmpdir)
        detector = SkillLossTriggerDetector()
        tenant_id = "_default"
        now = datetime.utcnow()
        base_ts = now.timestamp()

        # Create 5 events: 3 with feedback, 2 without
        events = []
        for i in range(5):
            event = create_skill_event(
                skill_id="os.test_skill",
                version="1.0.0",
                correct=i < 2,
                ts=base_ts - (5 - i) * 3600,
                tenant_id=tenant_id,
            )
            if i >= 3:
                # Remove outcome_feedback from last 2 events
                del event["outcome_feedback"]
            events.append(event)

        audit_path = temp_audit_dir / "tenants" / tenant_id / "global" / "forge" / "audit.jsonl"
        create_mock_audit_file(audit_path, tenant_id, events)

        with patch.object(trigger_detector_module, "tenant_audit_chain") as mock_chain:
            mock_chain.return_value = audit_path
            with patch.object(trigger_detector_module, "validate_tenant_id") as mock_validate:
                mock_validate.return_value = None

                triggers = detector.detect_loss_signals(tenant_id, lookback_hours=24)

        # Should include 3 events with feedback: 2 correct, 1 incorrect → confidence = 0.67
        # event_count is the total number of events in the group (5), but only 3 have feedback
        ok = (
            len(triggers) == 1 and
            abs(triggers[0].confidence - (2/3)) < 0.01 and
            triggers[0].event_count == 5  # Total events in group
        )
        test("events_without_outcome_feedback", ok)


def test_multiple_skills_with_mixed_confidence():
    """Test multiple skills with different confidence levels."""
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_audit_dir = Path(tmpdir)
        detector = SkillLossTriggerDetector()
        tenant_id = "_default"
        now = datetime.utcnow()
        base_ts = now.timestamp()

        events = []

        # Skill 1: 2 correct, 8 incorrect → 0.20 (triggers)
        for i in range(10):
            event = create_skill_event(
                skill_id="skill1",
                version="1.0.0",
                correct=i < 2,
                ts=base_ts - (10 - i) * 360,
                tenant_id=tenant_id,
            )
            events.append(event)

        # Skill 2: 8 correct, 2 incorrect → 0.80 (no trigger)
        for i in range(10):
            event = create_skill_event(
                skill_id="skill2",
                version="1.0.0",
                correct=i < 8,
                ts=base_ts - (10 - i) * 360,
                tenant_id=tenant_id,
            )
            events.append(event)

        # Skill 3: 0 correct, 5 incorrect → 0.00 (triggers, worst)
        for i in range(5):
            event = create_skill_event(
                skill_id="skill3",
                version="2.0.0",
                correct=False,
                ts=base_ts - (5 - i) * 360,
                tenant_id=tenant_id,
            )
            events.append(event)

        audit_path = temp_audit_dir / "tenants" / tenant_id / "global" / "forge" / "audit.jsonl"
        create_mock_audit_file(audit_path, tenant_id, events)

        with patch.object(trigger_detector_module, "tenant_audit_chain") as mock_chain:
            mock_chain.return_value = audit_path
            with patch.object(trigger_detector_module, "validate_tenant_id") as mock_validate:
                mock_validate.return_value = None

                triggers = detector.detect_loss_signals(tenant_id, lookback_hours=24)

        # Should have 2 triggers (skill1 and skill3), sorted by confidence (worst first)
        ok = (
            len(triggers) == 2 and
            triggers[0].skill_id == "skill3" and
            triggers[0].confidence == 0.0 and
            triggers[1].skill_id == "skill1" and
            abs(triggers[1].confidence - 0.20) < 0.01
        )
        test("multiple_skills_with_mixed_confidence", ok)


def test_loss_trigger_immutability():
    """Test that LossTrigger dataclass is frozen (immutable)."""
    trigger = LossTrigger(
        skill_id="test",
        version="1.0.0",
        confidence=0.5,
        trigger_time=datetime.utcnow(),
        event_count=10,
        lookback_hours=24,
    )

    # Attempting to modify should raise AttributeError
    try:
        trigger.confidence = 0.8
        ok = False  # Should have raised
    except (AttributeError, TypeError):
        ok = True  # Expected

    test("loss_trigger_immutability", ok)


def test_empty_audit_file():
    """Test empty audit.jsonl returns no triggers."""
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_audit_dir = Path(tmpdir)
        detector = SkillLossTriggerDetector()
        tenant_id = "_default"

        # Create empty audit file
        audit_path = temp_audit_dir / "tenants" / tenant_id / "global" / "forge" / "audit.jsonl"
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        audit_path.write_text("")

        with patch.object(trigger_detector_module, "tenant_audit_chain") as mock_chain:
            mock_chain.return_value = audit_path
            with patch.object(trigger_detector_module, "validate_tenant_id") as mock_validate:
                mock_validate.return_value = None

                triggers = detector.detect_loss_signals(tenant_id, lookback_hours=24)

        ok = triggers == []
        test("empty_audit_file", ok)


if __name__ == "__main__":
    print("Running SkillLossTriggerDetector tests...\n")

    test_detect_loss_when_confidence_below_threshold()
    test_no_trigger_when_confident()
    test_no_trigger_at_exact_threshold()
    test_tenant_isolation()
    test_lookback_window_parameter()
    test_missing_audit_file()
    test_invalid_json_in_audit_file()
    test_events_without_outcome_feedback()
    test_multiple_skills_with_mixed_confidence()
    test_loss_trigger_immutability()
    test_empty_audit_file()

    print(f"\n{'='*60}")
    print(f"Results: {PASS} passed, {FAIL} failed")
    print(f"{'='*60}")
    sys.exit(0 if FAIL == 0 else 1)
