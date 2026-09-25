"""
Catalog backend abstraction for instance discovery.

Supports both in-memory (k=1) and Redis (k=2+) storage with unified interface.
All operations are tenant-scoped and audit-logged.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
import asyncio
import logging

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiscoveredInstance:
    """An instance registered in the catalog."""
    org_id: str
    instance_id: str
    endpoint: str  # e.g., "https://app.host.com:443"
    tier_enc: str  # AES-256-GCM encrypted {tier, kid}
    kid: str  # Key ID from org_jwt claims
    latency_ms: int  # Measured in milliseconds
    last_heartbeat: datetime  # ISO 8601, UTC
    state: str  # ACTIVE, RETIRED
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class CatalogBackend(ABC):
    """Abstract interface for instance catalog storage."""

    @abstractmethod
    async def register(
        self, org_id: str, instance_id: str, payload: DiscoveredInstance
    ) -> None:
        """Register or update an instance."""
        pass

    @abstractmethod
    async def query(self, org_id: str) -> List[DiscoveredInstance]:
        """Query instances by org_id, sorted by latency (ascending)."""
        pass

    @abstractmethod
    async def heartbeat(self, org_id: str, instance_id: str) -> Optional[DiscoveredInstance]:
        """Refresh heartbeat for an instance. Returns updated instance or None if not found."""
        pass

    @abstractmethod
    async def retire_stale(self, ttl_seconds: int = 300) -> int:
        """Mark instances without heartbeat >ttl_seconds as RETIRED. Returns count marked."""
        pass

    @abstractmethod
    async def delete_old_retired(self, age_seconds: int = 3600) -> int:
        """Delete RETIRED instances older than age_seconds. Returns count deleted."""
        pass

    @abstractmethod
    async def get_instance(
        self, org_id: str, instance_id: str
    ) -> Optional[DiscoveredInstance]:
        """Get a single instance by ID."""
        pass

    @abstractmethod
    async def get_stats(self) -> Dict[str, int]:
        """Return catalog statistics: {active_instances, retired_instances, orgs_count}."""
        pass


class InMemoryCatalogBackend(CatalogBackend):
    """In-memory catalog implementation (k=1: single relay, no replication)."""

    def __init__(self):
        # {org_id: {instance_id: DiscoveredInstance}}
        self._catalog: Dict[str, Dict[str, DiscoveredInstance]] = {}
        self._lock = asyncio.Lock()

    async def register(
        self, org_id: str, instance_id: str, payload: DiscoveredInstance
    ) -> None:
        """Register or update an instance."""
        async with self._lock:
            if org_id not in self._catalog:
                self._catalog[org_id] = {}
            self._catalog[org_id][instance_id] = payload
            logger.info(
                f"Registered instance: org_id={org_id}, instance_id={instance_id}",
                extra={"org_id": org_id, "instance_id": instance_id}
            )

    async def query(self, org_id: str) -> List[DiscoveredInstance]:
        """Query instances by org_id, sorted by latency (ascending)."""
        async with self._lock:
            if org_id not in self._catalog:
                return []
            instances = list(self._catalog[org_id].values())
            # Filter ACTIVE only
            instances = [i for i in instances if i.state == "ACTIVE"]
            # Sort by latency
            instances.sort(key=lambda x: x.latency_ms)
            return instances

    async def heartbeat(self, org_id: str, instance_id: str) -> Optional[DiscoveredInstance]:
        """Refresh heartbeat for an instance."""
        async with self._lock:
            if org_id not in self._catalog or instance_id not in self._catalog[org_id]:
                return None
            old_instance = self._catalog[org_id][instance_id]
            # Replace with updated heartbeat time, but keep state ACTIVE
            updated = DiscoveredInstance(
                org_id=old_instance.org_id,
                instance_id=old_instance.instance_id,
                endpoint=old_instance.endpoint,
                tier_enc=old_instance.tier_enc,
                kid=old_instance.kid,
                latency_ms=old_instance.latency_ms,
                last_heartbeat=datetime.now(timezone.utc),
                state="ACTIVE",
                created_at=old_instance.created_at,
            )
            self._catalog[org_id][instance_id] = updated
            return updated

    async def retire_stale(self, ttl_seconds: int = 300) -> int:
        """Mark instances without heartbeat >ttl_seconds as RETIRED."""
        async with self._lock:
            count = 0
            now = datetime.now(timezone.utc)
            for org_id in self._catalog:
                for instance_id in self._catalog[org_id]:
                    instance = self._catalog[org_id][instance_id]
                    if instance.state == "ACTIVE":
                        age = (now - instance.last_heartbeat).total_seconds()
                        if age > ttl_seconds:
                            # Mark as RETIRED
                            retired = DiscoveredInstance(
                                org_id=instance.org_id,
                                instance_id=instance.instance_id,
                                endpoint=instance.endpoint,
                                tier_enc=instance.tier_enc,
                                kid=instance.kid,
                                latency_ms=instance.latency_ms,
                                last_heartbeat=instance.last_heartbeat,
                                state="RETIRED",
                                created_at=instance.created_at,
                            )
                            self._catalog[org_id][instance_id] = retired
                            count += 1
                            logger.warning(
                                f"Retired stale instance: org_id={org_id}, instance_id={instance_id}, age_sec={age}",
                                extra={"org_id": org_id, "instance_id": instance_id, "age_sec": int(age)}
                            )
            return count

    async def delete_old_retired(self, age_seconds: int = 3600) -> int:
        """Delete RETIRED instances older than age_seconds."""
        async with self._lock:
            count = 0
            now = datetime.now(timezone.utc)
            for org_id in list(self._catalog.keys()):
                for instance_id in list(self._catalog[org_id].keys()):
                    instance = self._catalog[org_id][instance_id]
                    if instance.state == "RETIRED":
                        age = (now - instance.last_heartbeat).total_seconds()
                        if age > age_seconds:
                            del self._catalog[org_id][instance_id]
                            count += 1
                            logger.info(
                                f"Deleted old retired instance: org_id={org_id}, instance_id={instance_id}, age_sec={age}",
                                extra={"org_id": org_id, "instance_id": instance_id}
                            )
                # Clean up empty org dicts
                if not self._catalog[org_id]:
                    del self._catalog[org_id]
            return count

    async def get_instance(
        self, org_id: str, instance_id: str
    ) -> Optional[DiscoveredInstance]:
        """Get a single instance by ID."""
        async with self._lock:
            if org_id not in self._catalog or instance_id not in self._catalog[org_id]:
                return None
            return self._catalog[org_id][instance_id]

    async def get_stats(self) -> Dict[str, int]:
        """Return catalog statistics."""
        async with self._lock:
            active = 0
            retired = 0
            for org_id in self._catalog:
                for instance in self._catalog[org_id].values():
                    if instance.state == "ACTIVE":
                        active += 1
                    else:
                        retired += 1
            return {
                "active_instances": active,
                "retired_instances": retired,
                "orgs_count": len(self._catalog),
            }
