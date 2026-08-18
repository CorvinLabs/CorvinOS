"""Test audit chain hash-chaining integrity (Phase 0 prerequisite).

Validates that:
1. Sequential learning events are properly hash-chained
2. Chain detects corruption (byte modification)
3. Chain verification fails on tampered events
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

import pytest


@dataclass(frozen=True)
class AuditChainEvent:
    """Immutable audit event for testing hash-chain integrity."""

    event_id: str
    event_type: str
    tenant_id: str
    timestamp: str
    payload: dict
    sequence: int

    def to_json(self) -> str:
        """Convert to JSON (deterministic for hashing)."""
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))


class HashChainValidator:
    """Validates hash-chained audit trail."""

    GENESIS_HASH = hashlib.sha256(b"genesis").hexdigest()

    def __init__(self):
        self.events: list[tuple[AuditChainEvent, str]] = []  # (event, hash) pairs
        self.last_hash = self.GENESIS_HASH

    def append_event(self, event: AuditChainEvent) -> str:
        """Append event and compute hash.

        Hash = sha256(prev_hash + event_json)
        """
        event_json = event.to_json().encode("utf-8")
        combined = (self.last_hash + event.to_json()).encode("utf-8")
        event_hash = hashlib.sha256(combined).hexdigest()

        self.events.append((event, event_hash))
        self.last_hash = event_hash
        return event_hash

    def verify_chain(self) -> bool:
        """Verify all events are properly hash-chained.

        Returns True if chain is valid, False if any hash is broken.
        """
        prev_hash = self.GENESIS_HASH
        for event, stored_hash in self.events:
            combined = (prev_hash + event.to_json()).encode("utf-8")
            computed_hash = hashlib.sha256(combined).hexdigest()
            if computed_hash != stored_hash:
                return False
            prev_hash = stored_hash
        return True

    def verify_from_json(self, chain_json: list[dict]) -> bool:
        """Verify a chain from serialized JSON.

        chain_json should be:
        [
            {event_id, event_type, tenant_id, timestamp, payload, sequence, hash}
        ]
        """
        prev_hash = self.GENESIS_HASH
        for entry in chain_json:
            # Reconstruct event (without hash)
            event_data = {
                "event_id": entry["event_id"],
                "event_type": entry["event_type"],
                "tenant_id": entry["tenant_id"],
                "timestamp": entry["timestamp"],
                "payload": entry["payload"],
                "sequence": entry["sequence"],
            }
            event_json = json.dumps(event_data, sort_keys=True, separators=(",", ":"))

            # Recompute hash
            combined = (prev_hash + event_json).encode("utf-8")
            computed_hash = hashlib.sha256(combined).hexdigest()

            stored_hash = entry["hash"]
            if computed_hash != stored_hash:
                return False
            prev_hash = stored_hash
        return True


class TestAuditChainIntegrity:
    """Test suite for audit chain hash-chaining."""

    def test_chain_creation_100_events(self):
        """Test: Generate 100 sequential learning events with proper hash-chain."""
        validator = HashChainValidator()

        for i in range(100):
            event = AuditChainEvent(
                event_id=str(uuid4()),
                event_type="learning.outcome.observed",
                tenant_id="_default",
                timestamp=datetime.now(timezone.utc).isoformat(),
                payload={
                    "decision_id": f"dec-{i}",
                    "outcome_type": "success",
                    "accuracy": 0.8 + (i * 0.001),
                    "latency_ms": 50 + (i % 20),
                },
                sequence=i,
            )
            validator.append_event(event)

        # Chain should be valid
        assert validator.verify_chain() is True
        assert len(validator.events) == 100

    def test_corruption_detection_single_byte(self):
        """Test: Modify one byte in event #50, chain should fail verification."""
        validator = HashChainValidator()

        # Build chain
        for i in range(100):
            event = AuditChainEvent(
                event_id=str(uuid4()),
                event_type="learning.outcome.observed",
                tenant_id="_default",
                timestamp=datetime.now(timezone.utc).isoformat(),
                payload={
                    "decision_id": f"dec-{i}",
                    "outcome_type": "success" if i != 50 else "failed",
                    "accuracy": 0.8 + (i * 0.001),
                },
                sequence=i,
            )
            validator.append_event(event)

        # Corrupt event #50 by changing its accuracy
        corrupted_event, _ = validator.events[50]
        new_payload = dict(corrupted_event.payload)
        new_payload["accuracy"] = 0.99  # Changed from original

        # Reconstruct event with corrupted payload
        corrupted = AuditChainEvent(
            event_id=corrupted_event.event_id,
            event_type=corrupted_event.event_type,
            tenant_id=corrupted_event.tenant_id,
            timestamp=corrupted_event.timestamp,
            payload=new_payload,
            sequence=corrupted_event.sequence,
        )

        # Replace event in chain
        validator.events[50] = (corrupted, validator.events[50][1])

        # Verification should fail
        assert validator.verify_chain() is False

    def test_corruption_detection_hash_tampering(self):
        """Test: Directly tamper with event hash, chain should fail verification."""
        validator = HashChainValidator()

        # Build chain
        for i in range(50):
            event = AuditChainEvent(
                event_id=str(uuid4()),
                event_type="learning.outcome.observed",
                tenant_id="_default",
                timestamp=datetime.now(timezone.utc).isoformat(),
                payload={"decision_id": f"dec-{i}"},
                sequence=i,
            )
            validator.append_event(event)

        # Tamper with event #25's hash
        event, hash_val = validator.events[25]
        tampered_hash = hashlib.sha256(b"tampered").hexdigest()
        validator.events[25] = (event, tampered_hash)

        # Verification should fail
        assert validator.verify_chain() is False

    def test_chain_serialization_and_verification(self):
        """Test: Serialize chain to JSON, deserialize, and verify."""
        validator = HashChainValidator()

        # Build chain
        for i in range(20):
            event = AuditChainEvent(
                event_id=f"event-{i}",
                event_type="learning.outcome.observed",
                tenant_id="_default",
                timestamp=datetime.now(timezone.utc).isoformat(),
                payload={
                    "decision_id": f"dec-{i}",
                    "accuracy": 0.8 + (i * 0.01),
                },
                sequence=i,
            )
            validator.append_event(event)

        # Serialize to JSON
        chain_json = [
            {
                "event_id": event.event_id,
                "event_type": event.event_type,
                "tenant_id": event.tenant_id,
                "timestamp": event.timestamp,
                "payload": event.payload,
                "sequence": event.sequence,
                "hash": hash_val,
            }
            for event, hash_val in validator.events
        ]

        # Verify from JSON
        assert validator.verify_from_json(chain_json) is True

    def test_insertion_attack_detection(self):
        """Test: Inserting a fake event breaks chain verification."""
        validator = HashChainValidator()

        # Build chain
        for i in range(10):
            event = AuditChainEvent(
                event_id=str(uuid4()),
                event_type="learning.outcome.observed",
                tenant_id="_default",
                timestamp=datetime.now(timezone.utc).isoformat(),
                payload={"decision_id": f"dec-{i}"},
                sequence=i,
            )
            validator.append_event(event)

        # Insert fake event at position 5
        fake_event = AuditChainEvent(
            event_id="fake-event",
            event_type="learning.outcome.observed",
            tenant_id="_default",
            timestamp=datetime.now(timezone.utc).isoformat(),
            payload={"decision_id": "fake-dec"},
            sequence=999,  # Wrong sequence
        )
        fake_hash = hashlib.sha256(b"fake").hexdigest()
        validator.events.insert(5, (fake_event, fake_hash))

        # Verification should fail
        assert validator.verify_chain() is False

    def test_deletion_attack_detection(self):
        """Test: Deleting an event breaks chain verification."""
        validator = HashChainValidator()

        # Build chain
        for i in range(10):
            event = AuditChainEvent(
                event_id=str(uuid4()),
                event_type="learning.outcome.observed",
                tenant_id="_default",
                timestamp=datetime.now(timezone.utc).isoformat(),
                payload={"decision_id": f"dec-{i}"},
                sequence=i,
            )
            validator.append_event(event)

        # Delete event at position 5
        del validator.events[5]

        # Verification should fail
        assert validator.verify_chain() is False

    def test_empty_chain_is_valid(self):
        """Test: Empty chain (just genesis) should be valid."""
        validator = HashChainValidator()
        assert validator.verify_chain() is True
        assert validator.last_hash == HashChainValidator.GENESIS_HASH

    def test_single_event_chain(self):
        """Test: Chain with single event should be valid."""
        validator = HashChainValidator()

        event = AuditChainEvent(
            event_id="single",
            event_type="learning.outcome.observed",
            tenant_id="_default",
            timestamp=datetime.now(timezone.utc).isoformat(),
            payload={"decision_id": "dec-0"},
            sequence=0,
        )
        validator.append_event(event)

        assert validator.verify_chain() is True
        assert len(validator.events) == 1

    def test_reorder_attack_detection(self):
        """Test: Reordering events breaks chain verification."""
        validator = HashChainValidator()

        # Build chain
        for i in range(5):
            event = AuditChainEvent(
                event_id=str(uuid4()),
                event_type="learning.outcome.observed",
                tenant_id="_default",
                timestamp=datetime.now(timezone.utc).isoformat(),
                payload={"decision_id": f"dec-{i}"},
                sequence=i,
            )
            validator.append_event(event)

        # Swap events at positions 2 and 3
        validator.events[2], validator.events[3] = validator.events[3], validator.events[2]

        # Verification should fail
        assert validator.verify_chain() is False

    def test_chain_convergence_with_many_events(self):
        """Test: Large chain (1000 events) maintains integrity."""
        validator = HashChainValidator()

        # Append 1000 events
        for i in range(1000):
            event = AuditChainEvent(
                event_id=f"event-{i}",
                event_type="learning.outcome.observed",
                tenant_id="_default",
                timestamp=datetime.now(timezone.utc).isoformat(),
                payload={
                    "decision_id": f"dec-{i}",
                    "accuracy": 0.7 + (i % 30) * 0.01,
                    "latency_ms": 50 + (i % 100),
                },
                sequence=i,
            )
            validator.append_event(event)

        # Verification should pass
        assert validator.verify_chain() is True
        assert len(validator.events) == 1000
        assert validator.last_hash != HashChainValidator.GENESIS_HASH


class TestAuditChainPerformance:
    """Performance tests for audit chain operations."""

    def test_chain_append_performance(self):
        """Test: Appending 100 events should be fast (<1s total)."""
        import time

        validator = HashChainValidator()
        start = time.time()

        for i in range(100):
            event = AuditChainEvent(
                event_id=f"event-{i}",
                event_type="learning.outcome.observed",
                tenant_id="_default",
                timestamp=datetime.now(timezone.utc).isoformat(),
                payload={"decision_id": f"dec-{i}", "accuracy": 0.8},
                sequence=i,
            )
            validator.append_event(event)

        elapsed = time.time() - start
        assert elapsed < 1.0, f"Appending 100 events took {elapsed}s (target <1s)"

    def test_chain_verification_performance(self):
        """Test: Verifying 1000-event chain should be fast (<1s total)."""
        import time

        validator = HashChainValidator()

        # Build chain
        for i in range(1000):
            event = AuditChainEvent(
                event_id=f"event-{i}",
                event_type="learning.outcome.observed",
                tenant_id="_default",
                timestamp=datetime.now(timezone.utc).isoformat(),
                payload={"decision_id": f"dec-{i}"},
                sequence=i,
            )
            validator.append_event(event)

        # Verify chain
        start = time.time()
        result = validator.verify_chain()
        elapsed = time.time() - start

        assert result is True
        assert elapsed < 1.0, f"Verifying 1000 events took {elapsed}s (target <1s)"
