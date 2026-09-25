"""
Voice Session Store Tests (Phase 3a Stream A)
Tests store abstraction with in-memory and SQLite variants
"""

import pytest
from core.console.corvin_console.services.voice_session_store import (
    InMemoryStore,
    SQLiteStore,
    get_voice_session_store,
    is_store_available,
)


class TestInMemoryStore:
    """Phase 3a: In-memory store (fallback)"""

    def test_create_session(self):
        """Test: Create session"""
        store = InMemoryStore()
        session = store.create_session("sess-001", "_default")

        assert session["session_id"] == "sess-001"
        assert session["tenant_id"] == "_default"
        assert session["status"] == "RECORDING"

    def test_get_session(self):
        """Test: Retrieve session"""
        store = InMemoryStore()
        store.create_session("sess-002", "_default")

        session = store.get_session("sess-002")
        assert session is not None
        assert session["session_id"] == "sess-002"

    def test_update_session(self):
        """Test: Update session"""
        store = InMemoryStore()
        store.create_session("sess-003", "_default")

        success = store.update_session("sess-003", {"status": "STOPPED"})
        assert success is True

        updated = store.get_session("sess-003")
        assert updated["status"] == "STOPPED"

    def test_delete_session(self):
        """Test: Delete session"""
        store = InMemoryStore()
        store.create_session("sess-004", "_default")

        success = store.delete_session("sess-004")
        assert success is True

        retrieved = store.get_session("sess-004")
        assert retrieved is None

    def test_list_sessions(self):
        """Test: List sessions for tenant"""
        store = InMemoryStore()

        store.create_session("sess-a", "tenant-1")
        store.create_session("sess-b", "tenant-1")
        store.create_session("sess-c", "tenant-2")

        tenant1_sessions = store.list_sessions("tenant-1")
        assert len(tenant1_sessions) == 2

    def test_always_available(self):
        """Test: In-memory store is always available"""
        store = InMemoryStore()
        assert store.is_available() is True


class TestSQLiteStoreFallback:
    """Phase 3a: SQLite store with fallback"""

    def test_sqlite_fallback_to_memory(self):
        """Test: SQLite falls back to in-memory when DB unavailable"""
        store = SQLiteStore()

        # Phase 3a: DB not implemented yet, so fallback is active
        assert store.is_available() is False

        # But fallback store works
        session = store.create_session("sess-fallback", "_default")
        assert session["session_id"] == "sess-fallback"

    def test_sqlite_get_from_fallback(self):
        """Test: Get session from fallback store"""
        store = SQLiteStore()

        store.create_session("sess-fb", "_default")
        retrieved = store.get_session("sess-fb")

        assert retrieved is not None
        assert retrieved["session_id"] == "sess-fb"


class TestStoreFactory:
    """Phase 3a: Store factory (pluggable)"""

    def test_factory_memory_mode(self):
        """Test: Factory creates in-memory store"""
        store = get_voice_session_store(store_type="memory")
        assert isinstance(store, InMemoryStore)
        assert store.is_available() is True

    def test_factory_auto_mode(self):
        """Test: Factory auto-detects DB, falls back to memory"""
        store = get_voice_session_store(store_type="auto")
        # Phase 3a: DB not available, so should get in-memory
        assert store.is_available() is True

    def test_store_singleton(self):
        """Test: Store factory returns singleton"""
        store1 = get_voice_session_store(store_type="memory")
        store2 = get_voice_session_store(store_type="memory")
        assert store1 is store2

    def test_is_store_available(self):
        """Test: Availability check"""
        available = is_store_available()
        assert available is True  # Fallback store always available


class TestMigrationStrategy:
    """Phase 3a: Zero-downtime migration plan"""

    def test_dual_write_simulation(self):
        """Test: Simulate dual-write during migration"""
        memory_store = InMemoryStore()
        sqlite_store = SQLiteStore()

        # Phase 3 migration: write to both stores
        session_id = "migration-test"
        tenant_id = "_default"

        mem_session = memory_store.create_session(session_id, tenant_id)
        db_session = sqlite_store.create_session(session_id, tenant_id)

        # Both stores have the data
        assert memory_store.get_session(session_id) is not None
        # SQLite falls back, so also has it
        assert sqlite_store.get_session(session_id) is not None

    def test_fallback_on_db_failure(self):
        """Test: System gracefully handles DB failure"""
        store = SQLiteStore()

        # Create in fallback
        store.create_session("fail-test", "_default")

        # Simulate DB failure
        store._available = False

        # Operations still work via fallback
        session = store.get_session("fail-test")
        assert session is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
