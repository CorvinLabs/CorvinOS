"""
Marketplace Index Cache Manager (Task #3, Phase 1).

Implements 1-hour TTL caching for marketplace index.json.
Supports stale-while-revalidate fallback on network errors.
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List
import logging
import hashlib

logger = logging.getLogger(__name__)


class MarketplaceCacheManager:
    """File-based cache for marketplace index with 1h TTL."""

    def __init__(self, cache_dir: Optional[str] = None):
        """
        Initialize cache manager.

        Args:
            cache_dir: directory for cache files. Default: ~/.corvin/cache/
        """
        if cache_dir is None:
            cache_dir = os.path.expanduser("~/.corvin/cache/")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.cache_file = self.cache_dir / "marketplace_index.json"
        self.meta_file = self.cache_dir / "marketplace_index.meta.json"
        self.ttl_seconds = 3600  # 1 hour

    def _read_cache_file(self) -> Optional[List[Dict[str, Any]]]:
        """Read cached index data from disk."""
        try:
            if not self.cache_file.exists():
                return None
            with open(self.cache_file, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to read cache file: {e}")
            return None

    def _read_metadata(self) -> Optional[Dict[str, Any]]:
        """Read cache metadata (timestamps, hash)."""
        try:
            if not self.meta_file.exists():
                return None
            with open(self.meta_file, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to read metadata: {e}")
            return None

    def _write_cache_file(self, data: List[Dict[str, Any]]) -> bool:
        """Atomically write cache data to disk."""
        try:
            # Write to temp file first (atomic)
            temp_file = self.cache_file.with_suffix(".tmp")
            with open(temp_file, "w") as f:
                json.dump(data, f)
            # Atomic rename
            temp_file.replace(self.cache_file)
            return True
        except Exception as e:
            logger.error(f"Failed to write cache file: {e}")
            return False

    def _write_metadata(self, meta: Dict[str, Any]) -> bool:
        """Atomically write metadata."""
        try:
            temp_file = self.meta_file.with_suffix(".tmp")
            with open(temp_file, "w") as f:
                json.dump(meta, f)
            temp_file.replace(self.meta_file)
            return True
        except Exception as e:
            logger.error(f"Failed to write metadata: {e}")
            return False

    def _compute_data_hash(self, data: List[Dict[str, Any]]) -> str:
        """Compute SHA256 hash of data for integrity check."""
        data_json = json.dumps(data, sort_keys=True)
        return hashlib.sha256(data_json.encode()).hexdigest()

    def _is_cache_stale(self) -> bool:
        """Check if cache has expired (TTL passed)."""
        meta = self._read_metadata()
        if not meta or "cached_at" not in meta:
            return True  # No metadata = stale

        try:
            cached_at = datetime.fromisoformat(meta["cached_at"])
            expires_at = cached_at + timedelta(seconds=self.ttl_seconds)
            is_stale = datetime.utcnow() > expires_at
            return is_stale
        except Exception as e:
            logger.warning(f"Failed to check staleness: {e}")
            return True  # On error, treat as stale

    def get(self) -> Optional[List[Dict[str, Any]]]:
        """
        Get marketplace index from cache.

        Returns:
            Cached extension list if fresh, else None.
            On network failure: returns stale data if available.
        """
        if self._is_cache_stale():
            return None  # Cache expired

        cached_data = self._read_cache_file()
        if not cached_data:
            return None  # No cache

        # Verify integrity
        meta = self._read_metadata()
        if meta and "data_hash" in meta:
            computed_hash = self._compute_data_hash(cached_data)
            if computed_hash != meta["data_hash"]:
                logger.warning("Cache integrity check failed")
                return None

        logger.debug("Cache hit")
        return cached_data

    def get_stale(self) -> Optional[List[Dict[str, Any]]]:
        """
        Get cached data even if stale (for fallback on network error).

        Returns:
            Any cached data, regardless of TTL.
        """
        cached_data = self._read_cache_file()
        if cached_data:
            logger.debug("Cache hit (stale fallback)")
        return cached_data

    def set(self, data: List[Dict[str, Any]]) -> bool:
        """
        Store marketplace index in cache.

        Args:
            data: list of extension metadata

        Returns:
            True if successfully cached, else False
        """
        if not data:
            logger.warning("Refusing to cache empty data")
            return False

        # Write cache file
        if not self._write_cache_file(data):
            return False

        # Write metadata
        now = datetime.utcnow()
        meta = {
            "cached_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=self.ttl_seconds)).isoformat(),
            "data_hash": self._compute_data_hash(data),
            "item_count": len(data),
        }
        return self._write_metadata(meta)

    def invalidate(self) -> bool:
        """
        Manually invalidate cache (force refresh on next request).

        Returns:
            True if successfully invalidated
        """
        try:
            if self.cache_file.exists():
                self.cache_file.unlink()
            if self.meta_file.exists():
                self.meta_file.unlink()
            logger.info("Cache invalidated")
            return True
        except Exception as e:
            logger.error(f"Failed to invalidate cache: {e}")
            return False

    def status(self) -> Dict[str, Any]:
        """
        Get cache status (for diagnostics).

        Returns:
            {
              "cached": bool (data exists),
              "fresh": bool (not stale),
              "size_bytes": int,
              "cached_at": str (ISO),
              "expires_at": str (ISO),
              "item_count": int
            }
        """
        meta = self._read_metadata()
        if not meta:
            return {"cached": False, "fresh": False}

        try:
            cached_at = datetime.fromisoformat(meta.get("cached_at", ""))
            expires_at = datetime.fromisoformat(meta.get("expires_at", ""))
            is_fresh = datetime.utcnow() < expires_at

            size = 0
            if self.cache_file.exists():
                size = self.cache_file.stat().st_size

            return {
                "cached": True,
                "fresh": is_fresh,
                "size_bytes": size,
                "cached_at": meta.get("cached_at"),
                "expires_at": meta.get("expires_at"),
                "item_count": meta.get("item_count", 0),
            }
        except Exception as e:
            logger.error(f"Failed to get cache status: {e}")
            return {"cached": True, "fresh": False, "error": str(e)}
