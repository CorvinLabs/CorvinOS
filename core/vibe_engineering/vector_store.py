"""
Vector Store with Graceful Fallback

Provides persistent storage for semantic vectors with in-memory LRU fallback
when the primary store is unavailable (DB down, permission denied, etc.).

This module implements Finding 3 fix: Vector-Semantic WRITE Path Error Handling.
"""

import logging
from typing import Optional, Dict, Any
from collections import OrderedDict
from dataclasses import dataclass

_log = logging.getLogger(__name__)


@dataclass
class Vector:
    """Represents a stored semantic vector."""
    vector_id: str
    embedding: list
    metadata: Dict[str, Any]


class VectorStore:
    """
    Vector store with in-memory LRU fallback.

    When persistence fails (DB down, permission denied), vectors are stored
    in a session-scoped in-memory cache. This allows graceful degradation
    instead of silent failure.
    """

    def __init__(self, db_writer=None, max_cache_size: int = 1000, tenant_id: str = "_default"):
        """
        Initialize vector store.

        Args:
            db_writer: Optional database writer for persistence (mocked in tests)
            max_cache_size: Maximum vectors to keep in in-memory cache
            tenant_id: Tenant identifier for audit logging
        """
        self.db_writer = db_writer
        self.max_cache_size = max_cache_size
        self.tenant_id = tenant_id
        self._fallback_cache: OrderedDict[str, Vector] = OrderedDict()
        self._cache_enabled = False

    def write(self, vector: Vector) -> bool:
        """
        Write vector to persistent store with fallback to in-memory cache.

        Args:
            vector: Vector object to persist

        Returns:
            bool: True if persisted to DB, False if fell back to in-memory cache

        Raises:
            None (fail-closed logging only)
        """
        try:
            # Attempt to write to persistent store
            if self.db_writer is not None:
                self.db_writer.write(vector)
            return True

        except Exception as e:
            # Fallback to in-memory cache
            _log.warning(
                f"Vector write failed for {vector.vector_id}: {e}; "
                f"falling back to in-memory cache (tenant={self.tenant_id})"
            )
            self._add_to_fallback_cache(vector)
            self._cache_enabled = True
            return False

    def read(self, vector_id: str) -> Optional[Vector]:
        """
        Read vector from persistent store, falling back to in-memory cache.

        Args:
            vector_id: ID of vector to retrieve

        Returns:
            Vector if found, None otherwise
        """
        # Try in-memory cache first if enabled
        if self._cache_enabled and vector_id in self._fallback_cache:
            return self._fallback_cache[vector_id]

        # Try persistent store
        if self.db_writer is not None:
            try:
                return self.db_writer.read(vector_id)
            except Exception as e:
                _log.warning(
                    f"Vector read failed for {vector_id}: {e}; "
                    f"checking in-memory cache (tenant={self.tenant_id})"
                )

        # Fall back to cache
        if vector_id in self._fallback_cache:
            return self._fallback_cache[vector_id]

        return None

    def _add_to_fallback_cache(self, vector: Vector) -> None:
        """
        Add vector to in-memory LRU cache.

        If cache is full, evict oldest entry (FIFO within session).

        Args:
            vector: Vector to cache
        """
        # Remove if already exists (update case)
        if vector.vector_id in self._fallback_cache:
            del self._fallback_cache[vector.vector_id]

        # Add to end of OrderedDict
        self._fallback_cache[vector.vector_id] = vector

        # Evict oldest if over capacity
        if len(self._fallback_cache) > self.max_cache_size:
            oldest_id, _ = self._fallback_cache.popitem(last=False)
            _log.debug(f"Evicted oldest vector from cache: {oldest_id}")

    def clear_fallback_cache(self) -> int:
        """
        Clear in-memory fallback cache.

        Used at session end or when primary store becomes available again.

        Returns:
            Number of vectors cleared
        """
        count = len(self._fallback_cache)
        self._fallback_cache.clear()
        self._cache_enabled = False
        _log.info(f"Cleared fallback cache ({count} vectors, tenant={self.tenant_id})")
        return count

    @property
    def cache_size(self) -> int:
        """Return current size of fallback cache."""
        return len(self._fallback_cache)

    @property
    def is_cache_enabled(self) -> bool:
        """Return whether fallback cache is active."""
        return self._cache_enabled
