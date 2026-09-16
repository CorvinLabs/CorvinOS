"""
Tier-2 Tests: Vector Store Fallback to In-Memory Cache

Tests for Finding 3 fix: Vector-Semantic WRITE Path Error Handling
- Test 1: Normal WRITE succeeds (mock DB, assert vector persisted)
- Test 2: DB down, WRITE falls back to in-memory (mock DB.write() raises error)
- Test 3: READ from fallback cache works (assert retrieved == stored)
"""

import pytest
from unittest.mock import Mock
from datetime import datetime
from core.vibe_engineering.vector_store import VectorStore, Vector


class TestVectorStoreFallback:
    """Tier-2 tests for vector store fallback mechanism."""

    # ========================================================================
    # Test 1: Normal WRITE Succeeds (Primary Store Available)
    # ========================================================================

    @pytest.mark.tier2
    def test_vector_write_success_to_db(self):
        """
        Test 1: Vector write succeeds when DB is available.

        Scenario:
        - DB writer is mocked (always succeeds)
        - Write a vector
        - Assert: write() returns True, cache not used

        Expected: Vector persisted to DB, fallback cache remains empty
        """
        # Setup
        mock_db = Mock()
        mock_db.write = Mock(return_value=None)  # Success

        store = VectorStore(db_writer=mock_db, tenant_id="test_tenant")

        # Create test vector
        vector = Vector(
            vector_id="vec_001",
            embedding=[0.1, 0.2, 0.3],
            metadata={"source": "test"}
        )

        # Act
        result = store.write(vector)

        # Assert
        assert result is True, "write() should return True for successful DB write"
        assert not store.is_cache_enabled, "Cache should not be enabled"
        assert store.cache_size == 0, "Cache should be empty after successful write"
        mock_db.write.assert_called_once_with(vector)

    # ========================================================================
    # Test 2: DB Down, WRITE Falls Back to In-Memory Cache
    # ========================================================================

    @pytest.mark.tier2
    def test_vector_write_fallback_to_cache(self):
        """
        Test 2: Vector write falls back to in-memory cache when DB is down.

        Scenario:
        - DB writer raises an exception (simulating DB down)
        - Write a vector
        - Assert: write() returns False, cache is enabled, vector in cache

        Expected: Vector stored in in-memory fallback cache, not in DB
        """
        # Setup
        mock_db = Mock()
        mock_db.write = Mock(side_effect=RuntimeError("DB connection lost"))

        store = VectorStore(db_writer=mock_db, tenant_id="test_tenant")

        # Create test vector
        vector = Vector(
            vector_id="vec_002",
            embedding=[0.4, 0.5, 0.6],
            metadata={"source": "test", "timestamp": datetime.utcnow().isoformat()}
        )

        # Act
        result = store.write(vector)

        # Assert
        assert result is False, "write() should return False for fallback to cache"
        assert store.is_cache_enabled, "Cache should be enabled after fallback"
        assert store.cache_size == 1, "Cache should contain 1 vector"
        assert vector.vector_id in store._fallback_cache, "Vector should be in fallback cache"
        mock_db.write.assert_called_once_with(vector)

    # ========================================================================
    # Test 3: READ from Fallback Cache Works
    # ========================================================================

    @pytest.mark.tier2
    def test_vector_read_from_fallback_cache(self):
        """
        Test 3: Vector can be read from fallback cache after write failed.

        Scenario:
        - DB writer fails
        - Write a vector (falls back to cache)
        - Read the same vector
        - Assert: retrieved vector == original vector, from cache not DB

        Expected: Vector retrieved from in-memory cache (DB never queried)
        """
        # Setup
        mock_db = Mock()
        mock_db.write = Mock(side_effect=RuntimeError("DB connection lost"))
        mock_db.read = Mock()  # Should not be called for cache hits

        store = VectorStore(db_writer=mock_db, tenant_id="test_tenant")

        # Create and write test vector (will fail and go to cache)
        vector = Vector(
            vector_id="vec_003",
            embedding=[0.7, 0.8, 0.9],
            metadata={"cached": True}
        )
        write_result = store.write(vector)
        assert write_result is False, "Write should fall back to cache"

        # Act: Read the vector back
        retrieved = store.read(vector.vector_id)

        # Assert
        assert retrieved is not None, "Vector should be retrieved from cache"
        assert retrieved.vector_id == vector.vector_id, "Vector ID should match"
        assert retrieved.embedding == vector.embedding, "Embedding should match"
        assert retrieved.metadata == vector.metadata, "Metadata should match"

        # DB read should NOT have been called (cache hit)
        mock_db.read.assert_not_called()

    # ========================================================================
    # Bonus Tests
    # ========================================================================

    @pytest.mark.tier2
    def test_vector_cache_lru_eviction(self):
        """
        Verify: LRU eviction works when cache exceeds max_cache_size.

        Expected: Oldest vector is evicted when cache is full
        """
        # Setup small cache
        mock_db = Mock()
        mock_db.write = Mock(side_effect=RuntimeError("DB down"))

        store = VectorStore(db_writer=mock_db, max_cache_size=3, tenant_id="test_tenant")

        # Write 4 vectors (exceeds max_cache_size=3)
        vectors = [
            Vector(f"vec_{i:03d}", [float(i)] * 3, {"index": i})
            for i in range(4)
        ]

        for vec in vectors:
            store.write(vec)

        # Assert: oldest vector (vec_000) was evicted
        assert store.cache_size == 3, "Cache size should not exceed max"
        assert "vec_000" not in store._fallback_cache, "Oldest vector should be evicted"
        assert "vec_003" in store._fallback_cache, "Newest vector should be in cache"

    @pytest.mark.tier2
    def test_vector_clear_fallback_cache(self):
        """
        Verify: clear_fallback_cache() empties the cache and disables it.

        Expected: Cache cleared and is_cache_enabled set to False
        """
        # Setup
        mock_db = Mock()
        mock_db.write = Mock(side_effect=RuntimeError("DB down"))

        store = VectorStore(db_writer=mock_db, tenant_id="test_tenant")

        # Write a vector (goes to cache)
        vector = Vector("vec_clear", [1.0, 2.0], {})
        store.write(vector)

        assert store.cache_size == 1, "Vector should be in cache"
        assert store.is_cache_enabled, "Cache should be enabled"

        # Act: Clear cache
        cleared_count = store.clear_fallback_cache()

        # Assert
        assert cleared_count == 1, "Should report 1 vector cleared"
        assert store.cache_size == 0, "Cache should be empty"
        assert not store.is_cache_enabled, "Cache should be disabled"
