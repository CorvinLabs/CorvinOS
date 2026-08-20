"""Phase 2–3 Security & Integrity Tests (Rate Limiting, GDPR, Hash-Chain)."""

import pytest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from palace.rate_limiter import RateLimiter
from palace.redaction_engine import RedactionEngine
from palace.hash_chain import HashChain, HashChainEntry


class TestRateLimiter:
    """Test GH-002: Rate limiting on webhooks."""

    def test_rate_limiter_allows_within_limit(self):
        """Test rate limiter allows requests within limit."""
        limiter = RateLimiter(rate=10.0, capacity=10.0)

        # Should allow 10 requests in burst
        for i in range(10):
            assert limiter.allow("tenant_a")

        # 11th request should be denied
        assert not limiter.allow("tenant_a")

    def test_rate_limiter_refills_over_time(self):
        """Test rate limiter refills tokens over time."""
        limiter = RateLimiter(rate=10.0, capacity=10.0)

        # Burn initial tokens
        for i in range(10):
            limiter.allow("tenant_a")

        # Wait 0.2 seconds (should refill ~2 tokens at 10/sec rate)
        import time
        time.sleep(0.2)

        # Should allow ~2 more requests
        allowed_count = 0
        for i in range(3):
            if limiter.allow("tenant_a"):
                allowed_count += 1

        assert allowed_count >= 1  # At least 1 token refilled

    def test_rate_limiter_per_tenant_isolation(self):
        """Test rate limiter tracks per-tenant limits."""
        limiter = RateLimiter(rate=5.0, capacity=5.0)

        # Tenant A uses all 5 tokens
        for i in range(5):
            assert limiter.allow("tenant_a")

        # Tenant A is rate limited
        assert not limiter.allow("tenant_a")

        # Tenant B still has tokens
        assert limiter.allow("tenant_b")


class TestRedactionEngine:
    """Test MP-004: GDPR erasure and redaction."""

    def test_redact_email_pii(self):
        """Test email redaction."""
        engine = RedactionEngine(tenant_id="tenant_a")

        text = "Contact alice@example.com for details"
        redacted, hash_token = engine.redact_pii(text, pii_type='email')

        assert "alice@example.com" not in redacted
        assert "[REDACTED_EMAIL" in redacted
        assert hash_token  # Hash token computed

    def test_redact_phone_pii(self):
        """Test phone number redaction."""
        engine = RedactionEngine()

        text = "Call (555) 123-4567 for support"
        redacted, _ = engine.redact_pii(text, pii_type='phone')

        assert "(555) 123-4567" not in redacted
        assert "[REDACTED_PHONE" in redacted

    def test_redaction_audit_trail(self):
        """Test redaction audit trail is maintained."""
        engine = RedactionEngine(tenant_id="tenant_a")

        engine.redact_pii("test@example.com", pii_type='email')
        trail = engine.get_redaction_audit_trail()

        assert len(trail) == 1
        assert trail[0]["event_type"] == "pii_redacted"
        assert trail[0]["pii_type"] == "email"

    @pytest.mark.skip(reason="Requires real artifact file; placeholder for now")
    def test_redact_artifact(self):
        """Test redacting PII from artifact file (GDPR Art. 17)."""
        # TODO: Implement with real artifact file
        pass


class TestHashChain:
    """Test MP-008: Hash-chaining for Ideas/Concepts."""

    def test_hash_chain_entry_creation(self):
        """Test creating a hash chain entry."""
        entry = HashChainEntry(
            artifact_id="CONCEPT-0001",
            content_hash="abc123",
            previous_hash="0" * 64,  # Genesis
            timestamp="2026-08-20T12:00:00Z",
        )

        assert entry.artifact_id == "CONCEPT-0001"
        assert entry.chain_hash  # Computed

    def test_hash_chain_linkage(self):
        """Test hash chain linkage (each entry links to previous)."""
        chain = HashChain(tenant_id="tenant_a")

        # Add two entries
        hash1 = chain.append("CONCEPT-0001", "content1")
        hash2 = chain.append("CONCEPT-0002", "content2")

        assert hash1 != hash2
        assert len(chain.entries) == 2

    def test_hash_chain_integrity_verification(self):
        """Test integrity verification (detects tampering)."""
        chain = HashChain(tenant_id="tenant_a")

        # Add entries
        chain.append("CONCEPT-0001", "content1")
        chain.append("CONCEPT-0002", "content2")

        # Verify integrity (should pass)
        assert chain.verify_integrity()

        # Simulate tampering (modify content_hash)
        chain.entries[0].content_hash = "tampered"

        # Verify integrity (should fail)
        assert not chain.verify_integrity()

    def test_hash_chain_persistence(self):
        """Test hash chain persistence to disk."""
        with tempfile.TemporaryDirectory() as tmpdir:
            chain_file = Path(tmpdir) / "chain.jsonl"
            chain = HashChain(tenant_id="tenant_a", chain_file=chain_file)

            # Add entry
            hash1 = chain.append("CONCEPT-0001", "content1")

            # Verify persisted
            assert chain_file.exists()

            # Load in new chain instance
            chain2 = HashChain(tenant_id="tenant_a", chain_file=chain_file)
            assert len(chain2.entries) == 1
            assert chain2.last_hash == hash1

    def test_hash_chain_get_entry(self):
        """Test retrieving entry by artifact ID."""
        chain = HashChain()

        chain.append("CONCEPT-0001", "content1")
        chain.append("CONCEPT-0002", "content2")

        entry = chain.get_entry("CONCEPT-0001")
        assert entry is not None
        assert entry.artifact_id == "CONCEPT-0001"

        missing = chain.get_entry("CONCEPT-0999")
        assert missing is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
