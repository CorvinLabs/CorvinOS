"""Tests for audit chain integrity validation (Autonomous Skill Forge).

Tests the AuditChainValidator's ability to detect hash-chain corruption,
file-level errors, and enforce fail-closed semantics.

Design: All tests use real JSON serialization and SHA256 hashing (no mocks).
Compliance: GDPR Art. 30, 32 (immutability, integrity).
"""

import hashlib
import json
import sys
from pathlib import Path

import pytest

# Add skill-forge to path (directory has hyphen, so can't import normally)
skill_forge_path = Path(__file__).parent.parent.parent / "corvin_operator" / "skill-forge"
if str(skill_forge_path) not in sys.path:
    sys.path.insert(0, str(skill_forge_path))

from autonomous.audit_chain_validator import (
    AuditChainValidator,
    ChainValidationResult,
)


@pytest.fixture
def validator() -> AuditChainValidator:
    """Create a validator instance for testing."""
    return AuditChainValidator()


@pytest.fixture
def temp_audit_dir(tmp_path: Path) -> Path:
    """Create a temporary directory for audit test files."""
    return tmp_path / "audit_test"


def _make_event(
    event_type: str,
    prev_hash: str,
    ts: float = 1234567890.0,
    **extra,
) -> dict:
    """Create a test audit event with hash fields.

    Computes the hash field based on prev_hash and canonical JSON.

    Args:
        event_type: Event type (e.g., "skill_executed")
        prev_hash: Hash of previous event (empty string for first event)
        ts: Timestamp (default: fixed value for determinism)
        **extra: Additional event fields

    Returns:
        dict: Complete event with hash and prev_hash fields
    """
    # Create canonical event (without hash fields)
    canonical = {
        "ts": ts,
        "event_type": event_type,
        "tenant_id": "_default",
        **extra,
    }

    # Compute hash: SHA256(prev_hash || json_bytes)
    json_bytes = json.dumps(canonical, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )
    hasher = hashlib.sha256()
    hasher.update(prev_hash.encode("utf-8"))
    hasher.update(json_bytes)
    event_hash = hasher.hexdigest()[:16]

    # Return event with hash fields
    return {
        "prev_hash": prev_hash,
        "hash": event_hash,
        **canonical,
    }


def _write_chain(audit_path: Path, events: list[dict]) -> None:
    """Write a chain of events to an audit file (one JSON per line)."""
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with open(audit_path, "w") as f:
        for event in events:
            f.write(json.dumps(event) + "\n")


# ============================================================================
# UNIT TEST 1: Valid chain (all hashes match)
# ============================================================================


def test_valid_chain(validator: AuditChainValidator, temp_audit_dir: Path) -> None:
    """Test validation of a valid, intact hash-chain."""
    audit_path = temp_audit_dir / "audit.jsonl"

    # Create a 5-event chain
    events = []
    prev_hash = ""
    for i in range(5):
        event = _make_event(
            event_type="skill_executed",
            prev_hash=prev_hash,
            ts=1234567890.0 + i,
            skill_id=f"skill_{i}",
        )
        events.append(event)
        prev_hash = event["hash"]

    _write_chain(audit_path, events)

    # Validate
    result = validator.validate_chain(audit_path)

    assert result.is_valid is True
    assert result.error is None
    assert result.event_count == 5
    assert result.last_verified_hash == events[-1]["hash"]
    assert result.error_line_num == 0


# ============================================================================
# UNIT TEST 2: Single hash mismatch → reject all
# ============================================================================


def test_hash_mismatch_rejects_chain(
    validator: AuditChainValidator, temp_audit_dir: Path
) -> None:
    """Test that a single hash mismatch invalidates the entire chain."""
    audit_path = temp_audit_dir / "audit.jsonl"

    # Create a valid 3-event chain
    events = []
    prev_hash = ""
    for i in range(3):
        event = _make_event(
            event_type="skill_executed",
            prev_hash=prev_hash,
            ts=1234567890.0 + i,
            skill_id=f"skill_{i}",
        )
        events.append(event)
        prev_hash = event["hash"]

    # Corrupt the hash of event 2 (middle event)
    events[1]["hash"] = "0000000000000000"  # Fake hash

    _write_chain(audit_path, events)

    # Validate
    result = validator.validate_chain(audit_path)

    assert result.is_valid is False
    assert result.error is not None
    assert "Hash mismatch" in result.error
    assert result.error_line_num == 2  # Line 2 is where the mismatch is detected


# ============================================================================
# UNIT TEST 3: Missing event → chain break
# ============================================================================


def test_missing_event_breaks_chain(
    validator: AuditChainValidator, temp_audit_dir: Path
) -> None:
    """Test that a gap in prev_hash references breaks the chain."""
    audit_path = temp_audit_dir / "audit.jsonl"

    # Create first event
    event1 = _make_event(
        event_type="skill_executed",
        prev_hash="",
        ts=1234567890.0,
        skill_id="skill_0",
    )

    # Create a second event, but claim its prev_hash is something else
    # (simulating a missing middle event)
    event2 = {
        "prev_hash": "fake0000fake0000",  # Wrong prev_hash
        "hash": "fake1111fake1111",
        "ts": 1234567891.0,
        "event_type": "skill_executed",
        "tenant_id": "_default",
        "skill_id": "skill_2",
    }

    _write_chain(audit_path, [event1, event2])

    # Validate
    result = validator.validate_chain(audit_path)

    assert result.is_valid is False
    assert "Hash chain broken" in result.error
    assert result.error_line_num == 2


# ============================================================================
# UNIT TEST 4: Empty chain → pass (no events to validate)
# ============================================================================


def test_empty_chain_valid(
    validator: AuditChainValidator, temp_audit_dir: Path
) -> None:
    """Test that an empty chain is considered valid."""
    audit_path = temp_audit_dir / "audit.jsonl"

    # Create empty file
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text("")

    # Validate
    result = validator.validate_chain(audit_path)

    assert result.is_valid is True
    assert result.event_count == 0
    assert result.last_verified_hash == ""
    assert result.error is None


# ============================================================================
# UNIT TEST 5: Chain file missing → FileNotFoundError
# ============================================================================


def test_chain_file_missing(
    validator: AuditChainValidator, temp_audit_dir: Path
) -> None:
    """Test that a missing chain file raises FileNotFoundError."""
    audit_path = temp_audit_dir / "nonexistent" / "audit.jsonl"

    # Validate
    with pytest.raises(FileNotFoundError):
        validator.validate_chain(audit_path)


# ============================================================================
# UNIT TEST 6: Permission error on chain file → PermissionError
# ============================================================================


def test_chain_file_permission_error(
    validator: AuditChainValidator, temp_audit_dir: Path
) -> None:
    """Test that a permission error is detected (chain marked invalid, fail-closed)."""
    audit_path = temp_audit_dir / "audit.jsonl"

    # Create a file and make it unreadable
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text("test")
    audit_path.chmod(0o000)

    try:
        # Validate (should return invalid result, not raise - fail-closed design)
        result = validator.validate_chain(audit_path)
        assert result.is_valid is False
        assert "Failed to read audit chain" in result.error
    finally:
        # Cleanup: restore permissions so pytest can clean up
        audit_path.chmod(0o644)


# ============================================================================
# UNIT TEST 7: Invalid JSON line → fail-closed, reject chain
# ============================================================================


def test_invalid_json_line_rejects_chain(
    validator: AuditChainValidator, temp_audit_dir: Path
) -> None:
    """Test that invalid JSON in the chain causes validation failure."""
    audit_path = temp_audit_dir / "audit.jsonl"

    # Create first valid event
    event1 = _make_event(
        event_type="skill_executed",
        prev_hash="",
        ts=1234567890.0,
        skill_id="skill_0",
    )

    # Write first event + invalid JSON line
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with open(audit_path, "w") as f:
        f.write(json.dumps(event1) + "\n")
        f.write('{"invalid": json line with no closing brace\n')

    # Validate
    result = validator.validate_chain(audit_path)

    assert result.is_valid is False
    assert "Invalid JSON" in result.error
    assert result.error_line_num == 2


# ============================================================================
# UNIT TEST 8: Missing hash fields → fail-closed
# ============================================================================


def test_missing_hash_fields_rejects_chain(
    validator: AuditChainValidator, temp_audit_dir: Path
) -> None:
    """Test that events missing hash/prev_hash fields are rejected."""
    audit_path = temp_audit_dir / "audit.jsonl"

    # Create first valid event
    event1 = _make_event(
        event_type="skill_executed",
        prev_hash="",
        ts=1234567890.0,
        skill_id="skill_0",
    )

    # Create second event WITHOUT hash fields
    event2 = {
        "ts": 1234567891.0,
        "event_type": "skill_executed",
        "tenant_id": "_default",
        "skill_id": "skill_1",
        # Missing: prev_hash, hash
    }

    _write_chain(audit_path, [event1, event2])

    # Validate
    result = validator.validate_chain(audit_path)

    assert result.is_valid is False
    assert "Missing hash fields" in result.error
    assert result.error_line_num == 2


# ============================================================================
# UNIT TEST 9: Chain with 1000+ events (performance test)
# ============================================================================


def test_large_chain_performance(
    validator: AuditChainValidator, temp_audit_dir: Path
) -> None:
    """Test that validation of a large chain (1000+ events) is efficient."""
    audit_path = temp_audit_dir / "audit.jsonl"

    # Create a 1000-event chain
    events = []
    prev_hash = ""
    for i in range(1000):
        event = _make_event(
            event_type="skill_executed",
            prev_hash=prev_hash,
            ts=1234567890.0 + i,
            skill_id=f"skill_{i % 10}",
        )
        events.append(event)
        prev_hash = event["hash"]

    _write_chain(audit_path, events)

    # Validate (should complete quickly)
    result = validator.validate_chain(audit_path)

    assert result.is_valid is True
    assert result.event_count == 1000
    assert result.last_verified_hash == events[-1]["hash"]


# ============================================================================
# UNIT TEST 10: Blank lines in chain (skipped, benign)
# ============================================================================


def test_blank_lines_skipped(
    validator: AuditChainValidator, temp_audit_dir: Path
) -> None:
    """Test that blank lines in the chain are skipped (benign)."""
    audit_path = temp_audit_dir / "audit.jsonl"

    # Create events with blank lines interspersed
    event1 = _make_event(
        event_type="skill_executed",
        prev_hash="",
        ts=1234567890.0,
        skill_id="skill_0",
    )

    event2 = _make_event(
        event_type="skill_executed",
        prev_hash=event1["hash"],
        ts=1234567891.0,
        skill_id="skill_1",
    )

    # Write with blank lines
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with open(audit_path, "w") as f:
        f.write(json.dumps(event1) + "\n")
        f.write("\n")  # Blank line
        f.write("\n")  # Another blank line
        f.write(json.dumps(event2) + "\n")

    # Validate
    result = validator.validate_chain(audit_path)

    assert result.is_valid is True
    assert result.event_count == 2
    assert result.last_verified_hash == event2["hash"]


# ============================================================================
# E2E TEST: Trigger detector blocks on corrupted chain
# ============================================================================


def test_trigger_detector_blocks_on_corrupted_chain(
    temp_audit_dir: Path,
) -> None:
    """E2E test: TriggerDetector detects corrupted chain and blocks.

    Creates a valid chain, corrupts an event hash, then verifies
    TriggerDetector raises RuntimeError on detect_loss_signals().
    """
    from autonomous.trigger_detector import (
        SkillLossTriggerDetector,
    )
    from unittest.mock import patch
    from datetime import datetime, timedelta
    import time

    # Create audit directory
    audit_path = temp_audit_dir / "audit.jsonl"

    # Create a valid 3-event chain with skill_executed events
    # Use recent timestamps so they're within the lookback window
    now_ts = datetime.utcnow().timestamp()
    events = []
    prev_hash = ""
    for i in range(3):
        event = _make_event(
            event_type="skill_executed",
            prev_hash=prev_hash,
            ts=now_ts - (100 - i * 10),  # Recent timestamps within 24h window
            skill_id="os.delegation_router",
            version="1.2.3",
            outcome_feedback={"correct": True if i < 2 else False},
        )
        events.append(event)
        prev_hash = event["hash"]

    # Write the chain
    _write_chain(audit_path, events)

    # Verify: detector can read valid chain
    detector = SkillLossTriggerDetector()
    with patch("autonomous.trigger_detector.tenant_audit_chain", return_value=audit_path):
        triggers = detector.detect_loss_signals("_default", lookback_hours=24)
        # Chain is valid, and confidence is 2/3 ≈ 0.667 < 0.70, so trigger should fire
        assert len(triggers) > 0, f"Expected at least 1 trigger, got {len(triggers)}"

    # Corrupt the chain: change hash of event 1
    events[1]["hash"] = "corruptedcorrupted"
    _write_chain(audit_path, events)

    # Verify: detector blocks on corrupted chain
    detector2 = SkillLossTriggerDetector()
    with patch("autonomous.trigger_detector.tenant_audit_chain", return_value=audit_path):
        with pytest.raises(RuntimeError, match="Audit chain integrity check failed"):
            detector2.detect_loss_signals("_default", lookback_hours=24)
