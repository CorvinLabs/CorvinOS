"""
Marketplace Index v3 Caching

Manages cached marketplace_index_v3.json in tenant directory.
- Local caching with TTL (24 hours default)
- Fallback to stale cache on network errors
- Automatic refresh on first load or after TTL expiry
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class MarketplaceIndexCache:
    """Manage cached marketplace_index_v3.json in tenant directory."""

    DEFAULT_TTL = 24 * 3600  # 24 hours
    FALLBACK_TTL = 7 * 24 * 3600  # 7 days (stale cache)

    def __init__(self, tenant_id: str, cache_dir: Path):
        """
        Initialize cache manager.

        Args:
            tenant_id: Tenant ID (for scoping)
            cache_dir: Directory to store cached index (~/.corvin/tenants/<tid>/marketplace/)
        """
        self.tenant_id = tenant_id
        self.cache_dir = cache_dir
        self.cache_file = self.cache_dir / "index_v3.json"
        self.ttl = self.DEFAULT_TTL

    async def load(self, refresh_if_stale: bool = True) -> Dict[str, Any]:
        """
        Load index from cache or fetch fresh.

        Args:
            refresh_if_stale: If True, fetch fresh if cache is older than TTL

        Returns:
            Marketplace index v3 dict

        Raises:
            Exception: If both cache miss and fetch fail
        """
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Try cache first
        if self.cache_file.exists():
            age = datetime.now().timestamp() - self.cache_file.stat().st_mtime
            if age < self.ttl and not refresh_if_stale:
                logger.debug(f"Using cached index (age: {age:.0f}s)")
                return self._read_cache()
            elif age < self.ttl:
                logger.debug(f"Cache is fresh (age: {age:.0f}s), using it")
                return self._read_cache()

        # Fetch fresh
        logger.info("Fetching fresh marketplace index...")
        try:
            index = await self._fetch_fresh()
            self._write_cache(index)
            return index
        except Exception as e:
            logger.warning(f"Failed to fetch fresh index: {e}")

            # Fallback to stale cache if available
            if self.cache_file.exists():
                age = datetime.now().timestamp() - self.cache_file.stat().st_mtime
                if age < self.FALLBACK_TTL:
                    logger.warning(f"Using stale cache (age: {age/3600:.1f}h) due to fetch failure")
                    return self._read_cache()

            # No fallback available
            raise Exception(
                f"Failed to fetch marketplace index and no cache available"
            ) from e

    def search(self, artifact_type: str, query: str) -> list:
        """
        Client-side search over cached index.

        Args:
            artifact_type: Type to search ('plugins', 'skills', etc.)
            query: Search query (searched in name + description)

        Returns:
            List of matching artifacts
        """
        if not self.cache_file.exists():
            return []

        try:
            index = self._read_cache()
        except Exception as e:
            logger.error(f"Failed to read cache for search: {e}")
            return []

        artifacts = index.get("artifacts", {}).get(artifact_type, {}).get("items", [])
        query_lower = query.lower()

        return [
            item for item in artifacts
            if (query_lower in item.get("name", "").lower()
                or query_lower in item.get("description", "").lower())
        ]

    async def refresh(self) -> bool:
        """
        Manually refresh the cache.

        Returns:
            True if refresh succeeded, False otherwise
        """
        try:
            index = await self._fetch_fresh()
            self._write_cache(index)
            logger.info("Cache refreshed successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to refresh cache: {e}")
            return False

    # Private methods

    def _read_cache(self) -> Dict[str, Any]:
        """Read cache file."""
        with open(self.cache_file) as f:
            return json.load(f)

    def _write_cache(self, index: Dict[str, Any]) -> None:
        """Write cache file."""
        with open(self.cache_file, "w") as f:
            json.dump(index, f)

    async def _fetch_fresh(self) -> Dict[str, Any]:
        """
        Fetch fresh index from remote GitHub.

        Returns:
            Marketplace index v3 dict

        Raises:
            Exception: If fetch fails
        """
        import httpx

        url = (
            "https://raw.githubusercontent.com/"
            "CorvinLabs/Corvin-Marketplace/main/index/marketplace_index_v3.json"
        )

        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(url, timeout=10)
                resp.raise_for_status()
                return resp.json()
            except httpx.RequestError as e:
                raise Exception(f"Failed to fetch {url}: {e}") from e
            except json.JSONDecodeError as e:
                raise Exception(f"Invalid JSON from {url}: {e}") from e


# Singleton-like cache manager (one per tenant)
_caches: Dict[str, MarketplaceIndexCache] = {}


def get_marketplace_index_cache(tenant_id: str, cache_dir: Path) -> MarketplaceIndexCache:
    """Get or create cache manager for tenant."""
    key = str(cache_dir)
    if key not in _caches:
        _caches[key] = MarketplaceIndexCache(tenant_id, cache_dir)
    return _caches[key]
