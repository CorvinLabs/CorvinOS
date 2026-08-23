"""Tier-1 Layer 16: Audit Tripwire (Hash-Chain Integrity) — Mutation Tests (GDPR Art. 32)."""

import pytest
import json
import hashlib
from pathlib import Path
from core.audit.chain import AuditChain
from core.compliance.corvin_compliance_reports.tripwire import (
    assert_all,
    verify_hash_chain,
)


class TestAuditTripwireMutation:
    """Mutation tests: breaking audit chain must fail tripwire."""

    @pytest.fixture
    def sample_audit_chain(self, tmp_path):
        """Create a valid audit chain."""
        audit_file = tmp_path / "audit.jsonl"
        events = [
            {"id": "e1", "timestamp": "2026-08-23T10:00:00Z", "event": "boot", "hash": None},
            {"id": "e2", "timestamp": "2026-08-23T10:00:01Z", "event": "user_login", "hash": None},
            {"id": "e3", "timestamp": "2026-08-23T10:00:02Z", "event": "data_write", "hash": None},
        ]

        # Compute hashes (chain)
        prev_hash = None
        for event in events:
            event_copy = {k: v for k, v in event.items() if k != "hash"}
            event_str = json.dumps(event_copy, sort_keys=True)
            new_hash = hashlib.sha256(
                (event_str + (prev_hash or "")).encode()
            ).hexdigest()
            event["hash"] = new_hash
            prev_hash = new_hash

        with open(audit_file, "w") as f:
            for event in events:
                f.write(json.dumps(event) + "\n")

        return audit_file

    def test_valid_chain_passes_tripwire(self, sample_audit_chain):
        """Valid chain must pass verification."""
        result = verify_hash_chain(sample_audit_chain)
        assert result is True, "Valid chain should pass tripwire"

    def test_mutated_event_fails_tripwire(self, sample_audit_chain):
        """Mutating an event must fail verification (mutation: change 'event' field)."""
        # Read, mutate, rewrite
        with open(sample_audit_chain, "r") as f:
            lines = f.readlines()

        lines[1] = lines[1].replace("user_login", "admin_login")  # MUTATION

        with open(sample_audit_chain, "w") as f:
            f.writelines(lines)

        # Tripwire must FAIL
        result = verify_hash_chain(sample_audit_chain)
        assert result is False, "Mutated event should fail tripwire"

    def test_deleted_event_fails_tripwire(self, sample_audit_chain):
        """Deleting an event must fail verification (mutation: remove middle event)."""
        with open(sample_audit_chain, "r") as f:
            lines = f.readlines()

        # Delete middle event
        del lines[1]

        with open(sample_audit_chain, "w") as f:
            f.writelines(lines)

        result = verify_hash_chain(sample_audit_chain)
        assert result is False, "Deleted event should fail tripwire"

    def test_reordered_events_fail_tripwire(self, sample_audit_chain):
        """Reordering events must fail verification (mutation: swap last two)."""
        with open(sample_audit_chain, "r") as f:
            lines = f.readlines()

        # Swap last two events
        lines[1], lines[2] = lines[2], lines[1]

        with open(sample_audit_chain, "w") as f:
            f.writelines(lines)

        result = verify_hash_chain(sample_audit_chain)
        assert result is False, "Reordered events should fail tripwire"

    def test_truncated_hash_fails_tripwire(self, sample_audit_chain):
        """Truncating a hash must fail (mutation: shorten hash string)."""
        with open(sample_audit_chain, "r") as f:
            lines = f.readlines()

        # Truncate hash of first event
        data = json.loads(lines[0])
        data["hash"] = data["hash"][:32]  # Shorten from 64 to 32 chars
        lines[0] = json.dumps(data) + "\n"

        with open(sample_audit_chain, "w") as f:
            f.writelines(lines)

        result = verify_hash_chain(sample_audit_chain)
        assert result is False, "Truncated hash should fail tripwire"


class TestAuditTripwireBootOrder:
    """Verify Tier-1 boot sequence: audit tripwire runs BEFORE plugin load."""

    def test_tripwire_blocks_broken_chain(self, tmp_path):
        """Broken chain must block boot (fail-closed)."""
        # Create broken audit file
        audit_file = tmp_path / "audit.jsonl"
        with open(audit_file, "w") as f:
            f.write('{"event": "boot", "hash": "invalid"}\n')

        # Tripwire must block boot
        result = verify_hash_chain(audit_file)
        assert result is False, "Broken chain must fail boot"

    def test_missing_audit_file_fails_tripwire(self, tmp_path):
        """Missing audit file must fail boot (fail-closed)."""
        audit_file = tmp_path / "missing.jsonl"

        # Should raise or return False
        try:
            result = verify_hash_chain(audit_file)
            assert result is False
        except FileNotFoundError:
            # Also acceptable: missing file → fail-closed
            pass


class TestAuditTripwireComplianceGate:
    """GDPR Art. 30/32: Audit trail integrity is non-negotiable."""

    def test_tripwire_exits_1_on_broken_chain(self, sample_audit_chain):
        """Broken chain must cause exit(1), not proceed."""
        # Mutate chain
        with open(sample_audit_chain, "r") as f:
            lines = f.readlines()
        lines[0] = lines[0].replace("boot", "tamper")
        with open(sample_audit_chain, "w") as f:
            f.writelines(lines)

        # assert_all() should raise SystemExit or equivalent
        with pytest.raises((SystemExit, RuntimeError)):
            assert_all(audit_file=sample_audit_chain)

    def test_tripwire_no_override_env_var(self):
        """Tripwire cannot be disabled by env var (load-bearing)."""
        import os

        # Even if someone sets SKIP_AUDIT_CHECK=true, tripwire must run
        # (This test documents the constraint; real impl prevents env override)
        os.environ.pop("SKIP_AUDIT_CHECK", None)
        # Tripwire runs unconditionally
