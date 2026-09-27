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


class TestSQLiteStoreFailsClosed:
    """The persistent store is NOT implemented (2026-09-27).

    It used to accept writes and keep them in memory while presenting itself as
    persistent storage — data a caller believed durable vanished on restart.
    These tests pinned that behaviour; they now pin the refusal.
    """

    def test_sqlite_store_refuses_construction(self):
        with pytest.raises(NotImplementedError):
            SQLiteStore()

    def test_factory_sqlite_mode_refuses(self):
        import core.console.corvin_console.services.voice_session_store as m

        m._store = None
        with pytest.raises(NotImplementedError):
            get_voice_session_store(store_type="sqlite")

    def test_list_limit_applies_after_tenant_filter(self):
        store = InMemoryStore()
        for i in range(3):
            store.create_session(f"o{i}", "tenant-2")
        store.create_session("mine", "tenant-1")
        assert [x["session_id"] for x in store.list_sessions("tenant-1", limit=1)] == ["mine"]


class TestStoreFactory:
    """Phase 3a: Store factory (pluggable)"""

    def test_factory_memory_mode(self):
        """Test: Factory creates in-memory store"""
        import core.console.corvin_console.services.voice_session_store as m

        m._store = None
        store = get_voice_session_store(store_type="memory")
        assert isinstance(store, InMemoryStore)
        assert store.is_available() is True

    def test_factory_auto_mode(self):
        """Test: Factory auto mode uses the (non-persistent) in-memory store"""
        import core.console.corvin_console.services.voice_session_store as m

        m._store = None
        store = get_voice_session_store(store_type="auto")
        assert isinstance(store, InMemoryStore)
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
