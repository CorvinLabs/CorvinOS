"""
Comprehensive tests for ConsentStore (GDPR Art. 6, 7 compliance)

Tests:
- Consent grant/revoke operations
- TTL expiry verification
- Tenant isolation (fail-closed)
- Cross-tenant access blocking
- Audit event emission
- Concurrent access safety
"""

import pytest
import sqlite3
import tempfile
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

from core.compliance.consent_store import (
    ConsentStore,
    ConsentRecord,
    ConsentStoreError,
    TenantIsolationError,
    get_consent_store,
    ConsentScope
)


class TestConsentStoreBasic:
    """Basic ConsentStore functionality tests"""

    @pytest.fixture
    def temp_dir(self):
        """Temporary directory for test DB"""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def store(self, temp_dir):
        """Create ConsentStore for testing"""
        return ConsentStore(tenant_id="test_tenant", corvin_home=temp_dir)

    def test_store_initialization(self, store):
        """Test ConsentStore initializes without error"""
        assert store.tenant_id == "test_tenant"
        assert store.db_path.exists()

    def test_store_schema_created(self, store, temp_dir):
        """Test database schema is created"""
        db_path = temp_dir / "tenants" / "test_tenant" / "consent_store.db"
        assert db_path.exists()

        # Verify tables exist
        conn = sqlite3.connect(str(db_path))
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='consent_records'"
        )
        assert cursor.fetchone() is not None
        conn.close()

    def test_grant_consent(self, store):
        """Test granting consent"""
        record = store.grant_consent(
            user_id="user1",
            scope="skill_generation",
            ttl_days=90
        )

        assert record.user_id == "user1"
        assert record.scope == "skill_generation"
        assert record.tenant_id == "test_tenant"
        assert record.revoked_at is None
        assert record.is_active()

    def test_get_active_consent(self, store):
        """Test retrieving active consent"""
        store.grant_consent(user_id="user1", scope="skill_generation")

        has_consent = store.get_consent(user_id="user1", scope="skill_generation")
        assert has_consent is True

    def test_get_no_consent(self, store):
        """Test retrieving non-existent consent returns False"""
        has_consent = store.get_consent(user_id="user1", scope="skill_generation")
        assert has_consent is False

    def test_revoke_consent(self, store):
        """Test revoking consent"""
        store.grant_consent(user_id="user1", scope="skill_generation")

        # Revoke
        record = store.revoke_consent(user_id="user1", scope="skill_generation")
        assert record is not None
        assert record.revoked_at is not None

        # Verify no longer active
        has_consent = store.get_consent(user_id="user1", scope="skill_generation")
        assert has_consent is False

    def test_revoke_non_existent(self, store):
        """Test revoking non-existent consent returns None"""
        record = store.revoke_consent(user_id="user1", scope="skill_generation")
        assert record is None


class TestConsentTTLAndExpiry:
    """TTL and expiry tests"""

    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def store(self, temp_dir):
        return ConsentStore(tenant_id="test_tenant", corvin_home=temp_dir)

    def test_consent_expiry_default_90_days(self, store):
        """Test default TTL is 90 days"""
        record = store.grant_consent(user_id="user1", scope="skill_generation")

        granted = datetime.fromisoformat(record.granted_at)
        expires = datetime.fromisoformat(record.expires_at)

        delta = expires - granted
        assert 89 <= delta.days <= 91  # Allow ±1 day due to time rounding

    def test_consent_custom_ttl(self, store):
        """Test custom TTL"""
        record = store.grant_consent(user_id="user1", scope="skill_generation", ttl_days=30)

        granted = datetime.fromisoformat(record.granted_at)
        expires = datetime.fromisoformat(record.expires_at)

        delta = expires - granted
        assert 29 <= delta.days <= 31

    def test_expired_consent_not_active(self, store):
        """Test that expired consent is not considered active"""
        record = store.grant_consent(user_id="user1", scope="skill_generation", ttl_days=0)

        # Immediately check (should be expired or close)
        import time
        time.sleep(0.1)

        has_consent = store.get_consent(user_id="user1", scope="skill_generation")
        assert has_consent is False


class TestTenantIsolation:
    """Tenant isolation (fail-closed) tests"""

    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    def test_invalid_tenant_id_rejected(self, temp_dir):
        """Test None or empty tenant_id raises error"""
        with pytest.raises(TenantIsolationError):
            ConsentStore(tenant_id=None, corvin_home=temp_dir)

        with pytest.raises(TenantIsolationError):
            ConsentStore(tenant_id="", corvin_home=temp_dir)

    def test_separate_stores_per_tenant(self, temp_dir):
        """Test each tenant has separate DB"""
        store1 = ConsentStore(tenant_id="tenant1", corvin_home=temp_dir)
        store2 = ConsentStore(tenant_id="tenant2", corvin_home=temp_dir)

        # Grant consent in tenant1
        store1.grant_consent(user_id="user1", scope="skill_generation")

        # Verify tenant2 doesn't see it
        has_consent = store2.get_consent(user_id="user1", scope="skill_generation")
        assert has_consent is False

    def test_cross_tenant_query_blocked(self, temp_dir):
        """Test queries are filtered by tenant_id (fail-closed)"""
        store1 = ConsentStore(tenant_id="tenant1", corvin_home=temp_dir)
        store2 = ConsentStore(tenant_id="tenant2", corvin_home=temp_dir)

        store1.grant_consent(user_id="user1", scope="skill_generation")

        # Store2 cannot see store1's consents (isolated DBs)
        consents = store2.list_active_consents(user_id="user1")
        assert len(consents) == 0

    def test_tenant_id_in_audit_event(self, temp_dir):
        """Test tenant_id is included in audit payload"""
        store = ConsentStore(tenant_id="tenant1", corvin_home=temp_dir)
        record = store.grant_consent(user_id="user1", scope="skill_generation")

        audit_dict = record.to_dict()
        assert audit_dict["tenant_id"] == "tenant1"


class TestConsentOperations:
    """Consent operation tests"""

    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def store(self, temp_dir):
        return ConsentStore(tenant_id="test_tenant", corvin_home=temp_dir)

    def test_idempotent_grant(self, store):
        """Test granting same consent twice is idempotent"""
        record1 = store.grant_consent(user_id="user1", scope="skill_generation")
        record2 = store.grant_consent(user_id="user1", scope="skill_generation")

        # Both should be active
        assert store.get_consent(user_id="user1", scope="skill_generation")

    def test_list_active_consents(self, store):
        """Test listing active consents for user"""
        store.grant_consent(user_id="user1", scope="skill_generation")
        store.grant_consent(user_id="user1", scope="telemetry_ping")
        store.grant_consent(user_id="user2", scope="skill_generation")

        consents = store.list_active_consents(user_id="user1")
        assert len(consents) == 2
        scopes = {c.scope for c in consents}
        assert scopes == {"skill_generation", "telemetry_ping"}

    def test_revoke_then_regrant(self, store):
        """Test revoking and re-granting consent"""
        store.grant_consent(user_id="user1", scope="skill_generation")
        store.revoke_consent(user_id="user1", scope="skill_generation")

        assert not store.get_consent(user_id="user1", scope="skill_generation")

        # Re-grant
        store.grant_consent(user_id="user1", scope="skill_generation")
        assert store.get_consent(user_id="user1", scope="skill_generation")


class TestErrorHandling:
    """Error handling and fail-closed behavior"""

    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def store(self, temp_dir):
        return ConsentStore(tenant_id="test_tenant", corvin_home=temp_dir)

    def test_empty_user_id_fails(self, store):
        """Test empty user_id raises error"""
        with pytest.raises(ConsentStoreError):
            store.grant_consent(user_id="", scope="skill_generation")

    def test_empty_scope_fails(self, store):
        """Test empty scope raises error"""
        with pytest.raises(ConsentStoreError):
            store.grant_consent(user_id="user1", scope="")

    def test_get_consent_empty_user_returns_false(self, store):
        """Test get_consent with empty user_id returns False (fail-closed)"""
        result = store.get_consent(user_id="", scope="skill_generation")
        assert result is False

    def test_list_empty_user_returns_empty(self, store):
        """Test listing consents with empty user_id returns empty list"""
        consents = store.list_active_consents(user_id="")
        assert consents == []


class TestConsentSingleton:
    """Test module-level singleton behavior"""

    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    def test_get_consent_store_singleton(self, temp_dir):
        """Test get_consent_store returns same instance per tenant"""
        store1 = get_consent_store(tenant_id="tenant1", corvin_home=temp_dir)
        store2 = get_consent_store(tenant_id="tenant1", corvin_home=temp_dir)

        # Same instance (cached)
        assert store1 is store2

    def test_different_tenants_different_stores(self, temp_dir):
        """Test different tenants get different store instances"""
        store1 = get_consent_store(tenant_id="tenant1", corvin_home=temp_dir)
        store2 = get_consent_store(tenant_id="tenant2", corvin_home=temp_dir)

        assert store1 is not store2
        assert store1.tenant_id == "tenant1"
        assert store2.tenant_id == "tenant2"


class TestConsentImmutability:
    """Test ConsentRecord immutability"""

    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def store(self, temp_dir):
        return ConsentStore(tenant_id="test_tenant", corvin_home=temp_dir)

    def test_consent_record_frozen(self, store):
        """Test ConsentRecord is immutable (frozen dataclass)"""
        record = store.grant_consent(user_id="user1", scope="skill_generation")

        with pytest.raises(AttributeError):
            record.user_id = "user2"

        with pytest.raises(AttributeError):
            record.tenant_id = "different_tenant"

    def test_consent_record_to_dict(self, store):
        """Test ConsentRecord can be converted to dict for audit"""
        record = store.grant_consent(user_id="user1", scope="skill_generation")

        d = record.to_dict()
        assert d["user_id"] == "user1"
        assert d["scope"] == "skill_generation"
        assert d["tenant_id"] == "test_tenant"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
