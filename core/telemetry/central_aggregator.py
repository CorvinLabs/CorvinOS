"""Central Telemetry Aggregation Service (ADR-0365)

Receives telemetry from distributed CorvinOS instances and maintains
a unified view of cluster statistics with real-time updates.

Key responsibilities:
- Accept telemetry submissions from instances via REST API
- Aggregate metrics across all instances
- Track instance health and uptime
- Provide real-time stream API (SSE / WebSocket)
- Handle instance registration/deregistration
- Tenant isolation for multi-tenant deployments
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional, Dict, List, Set
from uuid import uuid4
import hashlib

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InstanceLocation:
    """Geographic location of an instance."""
    latitude: float
    longitude: float
    city: Optional[str] = None
    country: Optional[str] = None
    region: Optional[str] = None


@dataclass
class InstanceTelemetry:
    """Telemetry from a single instance."""
    instance_id: str
    hostname: str
    location: InstanceLocation
    turn_count: int
    total_tokens: int
    savings_percent: float
    uptime_seconds: int
    last_seen: datetime
    instance_version: str
    tenant_id: str


@dataclass
class ClusterStats:
    """Aggregated statistics across all instances."""
    timestamp: datetime
    instance_count: int
    total_turns: int
    total_tokens: int
    avg_tokens_per_turn: int
    avg_savings_percent: float
    instances: Dict[str, InstanceTelemetry] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to JSON-serializable dict."""
        instances_list = []
        for inst in self.instances.values():
            inst_dict = asdict(inst)
            inst_dict['location'] = {
                'latitude': inst.location.latitude,
                'longitude': inst.location.longitude,
                'city': inst.location.city,
                'country': inst.location.country,
                'region': inst.location.region,
            }
            inst_dict['last_seen'] = inst.last_seen.isoformat()
            instances_list.append(inst_dict)

        return {
            'timestamp': self.timestamp.isoformat(),
            'instance_count': self.instance_count,
            'total_turns': self.total_turns,
            'total_tokens': self.total_tokens,
            'avg_tokens_per_turn': self.avg_tokens_per_turn,
            'avg_savings_percent': round(self.avg_savings_percent, 1),
            'instances': instances_list,
            'summary': {
                'instance_count': self.instance_count,
                'total_turns': self.total_turns,
                'total_tokens': self.total_tokens,
                'avg_tokens_per_turn': self.avg_tokens_per_turn,
                'avg_savings_percent': round(self.avg_savings_percent, 1),
            }
        }


class TelemetryAggregator:
    """Central service for aggregating telemetry from all instances."""

    def __init__(self, stale_timeout_seconds: int = 300):
        """Initialize aggregator.

        Args:
            stale_timeout_seconds: Consider instance stale if no update in this time
        """
        self.stale_timeout_seconds = stale_timeout_seconds
        self.instances: Dict[str, InstanceTelemetry] = {}
        self.submission_history: List[tuple[datetime, str]] = []  # (timestamp, instance_id)
        self.subscribers: Set[str] = set()  # Client IDs listening for updates
        self._lock_acquired = False

    def register_instance(
        self,
        instance_id: str,
        hostname: str,
        location: InstanceLocation,
        version: str,
        tenant_id: str,
    ) -> None:
        """Register a new instance in the cluster.

        Args:
            instance_id: Unique instance identifier
            hostname: Hostname/address of the instance
            location: Geographic location
            version: CorvinOS version
            tenant_id: Tenant this instance belongs to
        """
        if not instance_id or not isinstance(instance_id, str):
            raise ValueError(f"Invalid instance_id: {instance_id}")

        if not tenant_id or not isinstance(tenant_id, str):
            raise ValueError(f"Invalid tenant_id: {tenant_id}")

        logger.info(f"Registering instance {instance_id} in tenant {tenant_id}")

        self.instances[instance_id] = InstanceTelemetry(
            instance_id=instance_id,
            hostname=hostname,
            location=location,
            turn_count=0,
            total_tokens=0,
            savings_percent=25.0,
            uptime_seconds=0,
            last_seen=datetime.utcnow(),
            instance_version=version,
            tenant_id=tenant_id,
        )

    def submit_telemetry(
        self,
        instance_id: str,
        turn_count: int,
        total_tokens: int,
        savings_percent: float,
        uptime_seconds: int,
        tenant_id: str,
    ) -> None:
        """Submit telemetry update from an instance.

        Args:
            instance_id: Instance submitting telemetry
            turn_count: Number of turns processed
            total_tokens: Total tokens used
            savings_percent: Cost savings percentage
            uptime_seconds: Instance uptime in seconds
            tenant_id: Tenant ID (must match registered instance)

        Raises:
            ValueError: If instance not registered or tenant mismatch
        """
        if instance_id not in self.instances:
            raise ValueError(f"Instance not registered: {instance_id}")

        instance = self.instances[instance_id]

        if instance.tenant_id != tenant_id:
            raise ValueError(
                f"Tenant mismatch for instance {instance_id}: "
                f"registered={instance.tenant_id}, submitted={tenant_id}"
            )

        # Update instance telemetry
        instance.turn_count = turn_count
        instance.total_tokens = total_tokens
        instance.savings_percent = savings_percent
        instance.uptime_seconds = uptime_seconds
        instance.last_seen = datetime.utcnow()

        # Record submission
        self.submission_history.append((datetime.utcnow(), instance_id))

        logger.debug(
            f"Updated telemetry for {instance_id}: "
            f"turns={turn_count}, tokens={total_tokens}, uptime={uptime_seconds}s"
        )

    def get_cluster_stats(self, tenant_id: str) -> ClusterStats:
        """Get aggregated cluster statistics for a tenant.

        Args:
            tenant_id: Tenant to get stats for

        Returns:
            ClusterStats with aggregated data
        """
        if not tenant_id or not isinstance(tenant_id, str):
            raise ValueError(f"Invalid tenant_id: {tenant_id}")

        # Filter to tenant's instances
        tenant_instances = {
            iid: inst for iid, inst in self.instances.items()
            if inst.tenant_id == tenant_id
        }

        # Aggregate metrics
        total_turns = sum(i.turn_count for i in tenant_instances.values())
        total_tokens = sum(i.total_tokens for i in tenant_instances.values())

        avg_tokens_per_turn = (
            total_tokens // total_turns if total_turns > 0 else 0
        )

        savings_list = [i.savings_percent for i in tenant_instances.values()]
        avg_savings = (
            sum(savings_list) / len(savings_list) if savings_list else 0
        )

        return ClusterStats(
            timestamp=datetime.utcnow(),
            instance_count=len(tenant_instances),
            total_turns=total_turns,
            total_tokens=total_tokens,
            avg_tokens_per_turn=avg_tokens_per_turn,
            avg_savings_percent=avg_savings,
            instances=tenant_instances,
        )

    def get_active_instances(self, tenant_id: str) -> List[InstanceTelemetry]:
        """Get list of currently active instances (not stale).

        Args:
            tenant_id: Tenant to filter by

        Returns:
            List of active InstanceTelemetry objects
        """
        if not tenant_id or not isinstance(tenant_id, str):
            raise ValueError(f"Invalid tenant_id: {tenant_id}")

        now = datetime.utcnow()
        stale_before = now - timedelta(seconds=self.stale_timeout_seconds)

        return [
            inst for inst in self.instances.values()
            if inst.tenant_id == tenant_id and inst.last_seen > stale_before
        ]

    def mark_instance_stale(self, instance_id: str) -> None:
        """Mark an instance as stale (no recent updates).

        Args:
            instance_id: Instance to mark stale
        """
        if instance_id in self.instances:
            # Could trigger alerts or cleanup here
            logger.warning(f"Instance marked stale: {instance_id}")

    def cleanup_stale_instances(self, tenant_id: str) -> List[str]:
        """Remove stale instances from cluster.

        Args:
            tenant_id: Tenant to clean up

        Returns:
            List of removed instance IDs
        """
        removed = []
        now = datetime.utcnow()
        stale_before = now - timedelta(seconds=self.stale_timeout_seconds)

        for iid, inst in list(self.instances.items()):
            if inst.tenant_id == tenant_id and inst.last_seen < stale_before:
                del self.instances[iid]
                removed.append(iid)
                logger.info(f"Removed stale instance: {iid}")

        return removed

    def subscribe_to_updates(self, client_id: str) -> None:
        """Subscribe a client to real-time updates (for WebSocket/SSE).

        Args:
            client_id: Unique client identifier
        """
        self.subscribers.add(client_id)
        logger.debug(f"Client subscribed to updates: {client_id}")

    def unsubscribe_from_updates(self, client_id: str) -> None:
        """Unsubscribe a client from updates.

        Args:
            client_id: Client to unsubscribe
        """
        self.subscribers.discard(client_id)
        logger.debug(f"Client unsubscribed from updates: {client_id}")

    def get_active_subscribers(self) -> int:
        """Get number of currently active subscribers."""
        return len(self.subscribers)

    def validate_consistency(self, tenant_id: str) -> List[str]:
        """Validate data consistency for a tenant.

        Args:
            tenant_id: Tenant to validate

        Returns:
            List of validation errors (empty if consistent)
        """
        errors = []

        for iid, inst in self.instances.items():
            if inst.tenant_id != tenant_id:
                continue

            # Validate required fields
            if not inst.instance_id:
                errors.append(f"Instance missing ID: {iid}")
            if not inst.hostname:
                errors.append(f"Instance {iid} missing hostname")
            if inst.turn_count < 0:
                errors.append(f"Instance {iid} has negative turn count")
            if inst.total_tokens < 0:
                errors.append(f"Instance {iid} has negative token count")
            if not 0 <= inst.savings_percent <= 100:
                errors.append(f"Instance {iid} has invalid savings_percent: {inst.savings_percent}")

        return errors

    def reset_for_testing(self) -> None:
        """Clear all state (TEST ONLY)."""
        self.instances.clear()
        self.submission_history.clear()
        self.subscribers.clear()
