"""
Unit tests for Discovery Relay k=1 (core relay, in-memory catalog).

Tests the following components:
- CatalogBackend (InMemoryCatalogBackend)
- Security (HMAC, AES-256-GCM encryption)
- DiscoveryRelay controller logic
"""

import pytest
import asyncio
import os
import json
import base64
from datetime import datetime, timezone, timedelta
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from corvin_operator.discovery_relay.catalog import (
    InMemoryCatalogBackend, DiscoveredInstance, CatalogBackend
)
from corvin_operator.discovery_relay.security import (
    validate_hmac, compute_hmac, encrypt_payload, decrypt_payload,
    derive_key_from_password, scrub_for_audit,
    AuthenticationError, EncryptionError
)


# ===== Fixtures =====

@pytest.fixture
def org_key() -> bytes:
    """Generate a test organization key (32 bytes)."""
    return os.urandom(32)


@pytest.fixture
def catalog() -> InMemoryCatalogBackend:
    """Create an in-memory catalog for testing."""
    return InMemoryCatalogBackend()


@pytest.fixture
def test_instance() -> DiscoveredInstance:
    """Create a test instance."""
    return DiscoveredInstance(
        org_id="org_test",
        instance_id="app_123",
        endpoint="https://app.example.com:443",
        tier_enc="<encrypted>",
        kid="kid_v1",
        latency_ms=42,
        last_heartbeat=datetime.now(timezone.utc),
        state="ACTIVE",
    )


# ===== Security Tests (25 unit tests) =====

class TestHMACValidation:
    """Test HMAC-SHA256 signature validation."""

    def test_valid_hmac_signature(self, org_key):
        """Verify a valid HMAC signature is accepted."""
        payload = b'{"instance_id":"app_123"}'
        sig = compute_hmac(payload, org_key)
        assert validate_hmac(payload, sig, org_key) is True

    def test_invalid_hmac_signature(self, org_key):
        """Verify an invalid HMAC signature is rejected."""
        payload = b'{"instance_id":"app_123"}'
        sig = base64.b64encode(os.urandom(32)).decode('utf-8')  # Random sig
        assert validate_hmac(payload, sig, org_key) is False

    def test_hmac_payload_tampering(self, org_key):
        """Verify HMAC detects payload tampering."""
        payload1 = b'{"instance_id":"app_123"}'
        payload2 = b'{"instance_id":"app_124"}'  # Different
        sig = compute_hmac(payload1, org_key)
        assert validate_hmac(payload2, sig, org_key) is False

    def test_hmac_key_tampering(self, org_key):
        """Verify HMAC detects key tampering."""
        payload = b'{"instance_id":"app_123"}'
        sig = compute_hmac(payload, org_key)
        wrong_key = os.urandom(32)
        assert validate_hmac(payload, sig, wrong_key) is False

    def test_compute_hmac_deterministic(self, org_key):
        """Verify HMAC computation is deterministic."""
        payload = b'{"instance_id":"app_123"}'
        sig1 = compute_hmac(payload, org_key)
        sig2 = compute_hmac(payload, org_key)
        assert sig1 == sig2


class TestEncryption:
    """Test AES-256-GCM encryption/decryption."""

    def test_encrypt_decrypt_roundtrip(self, org_key):
        """Verify encryption and decryption are inverses."""
        plaintext = {"tier": "pro", "kid": "kid_v1", "instance_id": "app_123"}
        ciphertext, nonce = encrypt_payload(plaintext, org_key)
        decrypted = decrypt_payload(ciphertext, nonce, org_key)
        assert decrypted == plaintext

    def test_decrypt_with_wrong_key(self, org_key):
        """Verify decryption with wrong key fails gracefully (returns None)."""
        plaintext = {"tier": "pro", "kid": "kid_v1"}
        ciphertext, nonce = encrypt_payload(plaintext, org_key)
        wrong_key = os.urandom(32)
        decrypted = decrypt_payload(ciphertext, nonce, wrong_key)
        assert decrypted is None

    def test_decrypt_corrupted_ciphertext(self, org_key):
        """Verify decryption of corrupted ciphertext fails gracefully."""
        plaintext = {"tier": "pro"}
        ciphertext, nonce = encrypt_payload(plaintext, org_key)
        # Corrupt the ciphertext
        corrupt_ct = base64.b64encode(os.urandom(50)).decode('utf-8')
        decrypted = decrypt_payload(corrupt_ct, nonce, org_key)
        assert decrypted is None

    def test_encrypt_with_invalid_key_length(self):
        """Verify encryption with invalid key length raises EncryptionError."""
        plaintext = {"tier": "pro"}
        invalid_key = os.urandom(16)  # Should be 32
        with pytest.raises(EncryptionError):
            encrypt_payload(plaintext, invalid_key)

    def test_decrypt_with_invalid_nonce_length(self, org_key):
        """Verify decryption with invalid nonce length fails gracefully."""
        plaintext = {"tier": "pro"}
        ciphertext, _ = encrypt_payload(plaintext, org_key)
        invalid_nonce = base64.b64encode(os.urandom(8)).decode('utf-8')  # Should be 12
        decrypted = decrypt_payload(ciphertext, invalid_nonce, org_key)
        assert decrypted is None

    def test_encrypt_different_inputs_different_output(self, org_key):
        """Verify different inputs produce different ciphertexts."""
        plaintext1 = {"tier": "pro"}
        plaintext2 = {"tier": "free"}
        ct1, _ = encrypt_payload(plaintext1, org_key)
        ct2, _ = encrypt_payload(plaintext2, org_key)
        assert ct1 != ct2

    def test_encrypt_nonce_not_reused(self, org_key):
        """Verify different encryptions of same plaintext produce different ciphertexts (different nonces)."""
        plaintext = {"tier": "pro"}
        ct1, _ = encrypt_payload(plaintext, org_key)
        ct2, _ = encrypt_payload(plaintext, org_key)
        assert ct1 != ct2


class TestKeyDerivation:
    """Test PBKDF2 key derivation."""

    def test_derive_key_length(self):
        """Verify derived key is 32 bytes."""
        key, salt = derive_key_from_password("password123")
        assert len(key) == 32
        assert len(salt) == 16

    def test_derive_key_deterministic_same_salt(self):
        """Verify key derivation is deterministic with same salt."""
        password = "password123"
        salt = os.urandom(16)
        key1, _ = derive_key_from_password(password, salt)
        key2, _ = derive_key_from_password(password, salt)
        assert key1 == key2

    def test_derive_key_different_passwords(self):
        """Verify different passwords produce different keys."""
        salt = os.urandom(16)
        key1, _ = derive_key_from_password("password1", salt)
        key2, _ = derive_key_from_password("password2", salt)
        assert key1 != key2


class TestScrubbing:
    """Test sensitive data scrubbing."""

    def test_scrub_tier_enc(self):
        """Verify tier_enc is redacted."""
        payload = {"tier_enc": "encrypted_value", "instance_id": "app_123"}
        scrubbed = scrub_for_audit(payload)
        assert scrubbed["tier_enc"] == "<redacted>"
        assert scrubbed["instance_id"] == "app_123"

    def test_scrub_multiple_sensitive_fields(self):
        """Verify multiple sensitive fields are redacted."""
        payload = {
            "api_key": "secret_key",
            "token": "secret_token",
            "instance_id": "app_123",
            "endpoint": "https://example.com",
        }
        scrubbed = scrub_for_audit(payload)
        assert scrubbed["api_key"] == "<redacted>"
        assert scrubbed["token"] == "<redacted>"
        assert scrubbed["instance_id"] == "app_123"
        assert scrubbed["endpoint"] == "https://example.com"

    def test_scrub_case_insensitive(self):
        """Verify scrubbing is case-insensitive."""
        payload = {"API_KEY": "value", "Token": "value2"}
        scrubbed = scrub_for_audit(payload)
        assert scrubbed["API_KEY"] == "<redacted>"
        assert scrubbed["Token"] == "<redacted>"


# ===== Catalog Tests (15 unit tests) =====

class TestInMemoryCatalog:
    """Test InMemoryCatalogBackend."""

    @pytest.mark.asyncio
    async def test_register_instance(self, catalog, test_instance):
        """Verify instance registration."""
        await catalog.register(test_instance.org_id, test_instance.instance_id, test_instance)
        retrieved = await catalog.get_instance(test_instance.org_id, test_instance.instance_id)
        assert retrieved is not None
        assert retrieved.instance_id == test_instance.instance_id
        assert retrieved.endpoint == test_instance.endpoint

    @pytest.mark.asyncio
    async def test_query_returns_active_only(self, catalog, test_instance):
        """Verify query returns only ACTIVE instances."""
        await catalog.register(test_instance.org_id, test_instance.instance_id, test_instance)
        instances = await catalog.query(test_instance.org_id)
        assert len(instances) == 1
        assert instances[0].state == "ACTIVE"

    @pytest.mark.asyncio
    async def test_query_sorted_by_latency(self, catalog):
        """Verify query results are sorted by latency."""
        instances = [
            DiscoveredInstance("org1", "app_1", "https://a.com", "enc1", "kid1", 100, datetime.now(timezone.utc), "ACTIVE"),
            DiscoveredInstance("org1", "app_2", "https://b.com", "enc2", "kid2", 10, datetime.now(timezone.utc), "ACTIVE"),
            DiscoveredInstance("org1", "app_3", "https://c.com", "enc3", "kid3", 50, datetime.now(timezone.utc), "ACTIVE"),
        ]
        for inst in instances:
            await catalog.register(inst.org_id, inst.instance_id, inst)

        results = await catalog.query("org1")
        latencies = [r.latency_ms for r in results]
        assert latencies == [10, 50, 100]

    @pytest.mark.asyncio
    async def test_heartbeat_updates_timestamp(self, catalog, test_instance):
        """Verify heartbeat updates last_heartbeat timestamp."""
        await catalog.register(test_instance.org_id, test_instance.instance_id, test_instance)
        old_time = test_instance.last_heartbeat

        # Wait a bit and send heartbeat
        await asyncio.sleep(0.1)
        updated = await catalog.heartbeat(test_instance.org_id, test_instance.instance_id)

        assert updated is not None
        assert updated.last_heartbeat > old_time

    @pytest.mark.asyncio
    async def test_heartbeat_nonexistent_instance(self, catalog):
        """Verify heartbeat on nonexistent instance returns None."""
        result = await catalog.heartbeat("org1", "nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_retire_stale_instances(self, catalog):
        """Verify retire_stale marks old instances as RETIRED."""
        old_time = datetime.now(timezone.utc) - timedelta(seconds=400)
        old_instance = DiscoveredInstance("org1", "app_old", "https://old.com", "enc", "kid", 0, old_time, "ACTIVE")
        await catalog.register("org1", "app_old", old_instance)

        count = await catalog.retire_stale(ttl_seconds=300)
        assert count == 1

        retrieved = await catalog.get_instance("org1", "app_old")
        assert retrieved.state == "RETIRED"

    @pytest.mark.asyncio
    async def test_delete_old_retired(self, catalog):
        """Verify delete_old_retired removes old RETIRED instances."""
        very_old_time = datetime.now(timezone.utc) - timedelta(seconds=7200)  # 2 hours
        retired_instance = DiscoveredInstance("org1", "app_old", "https://old.com", "enc", "kid", 0, very_old_time, "RETIRED")
        await catalog.register("org1", "app_old", retired_instance)

        count = await catalog.delete_old_retired(age_seconds=3600)  # Delete >1 hour
        assert count == 1

        retrieved = await catalog.get_instance("org1", "app_old")
        assert retrieved is None

    @pytest.mark.asyncio
    async def test_get_stats(self, catalog):
        """Verify get_stats reports correct counts."""
        instances = [
            DiscoveredInstance("org1", "app_1", "https://a.com", "enc", "kid", 0, datetime.now(timezone.utc), "ACTIVE"),
            DiscoveredInstance("org1", "app_2", "https://b.com", "enc", "kid", 0, datetime.now(timezone.utc), "RETIRED"),
        ]
        for inst in instances:
            await catalog.register(inst.org_id, inst.instance_id, inst)

        stats = await catalog.get_stats()
        assert stats["active_instances"] == 1
        assert stats["retired_instances"] == 1
        assert stats["orgs_count"] == 1

    @pytest.mark.asyncio
    async def test_query_empty_org(self, catalog):
        """Verify query on empty org returns empty list."""
        results = await catalog.query("nonexistent_org")
        assert results == []

    @pytest.mark.asyncio
    async def test_concurrent_register_and_query(self, catalog):
        """Verify concurrent register and query are safe."""
        async def register_many():
            for i in range(10):
                inst = DiscoveredInstance(
                    "org1", f"app_{i}", f"https://app{i}.com", "enc", "kid", i,
                    datetime.now(timezone.utc), "ACTIVE"
                )
                await catalog.register("org1", f"app_{i}", inst)

        async def query_many():
            for _ in range(10):
                await catalog.query("org1")

        # Run concurrently
        await asyncio.gather(register_many(), query_many())
        final_count = len(await catalog.query("org1"))
        assert final_count == 10
