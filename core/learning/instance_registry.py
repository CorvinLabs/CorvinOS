"""Instance Discovery + Registry for Multi-Instance Metrics Aggregation.

Discovers all CorvinOS instances (local + remote via A2A) and provides
aggregated stats across the cluster.
"""

from __future__ import annotations

import os
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, list


@dataclass
class CorvinInstance:
    """Discovered CorvinOS instance."""
    instance_id: str
    hostname: str
    version: str
    location: str  # City, Country or geo coordinates "lat,lon"
    last_seen: str  # ISO timestamp
    turn_count: int = 0
    total_tokens: int = 0
    avg_tokens_per_turn: int = 0
    savings_percent: float = 0.0
    api_url: str = ""  # http://hostname:port/api/metrics


class InstanceRegistry:
    """Discover and aggregate metrics across all CorvinOS instances."""

    def __init__(self, registry_path: str | Path = "~/.corvin/instances.json"):
        """Initialize instance registry.

        Args:
            registry_path: Path to instances.json (local discovery file)
        """
        self.registry_path = Path(registry_path).expanduser()
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        self._instances: dict[str, CorvinInstance] = {}
        self._load_instances()

    def _load_instances(self):
        """Load instances from registry file."""
        if self.registry_path.exists():
            try:
                with open(self.registry_path) as f:
                    data = json.load(f)
                    for inst_data in data.get("instances", []):
                        inst = CorvinInstance(**inst_data)
                        self._instances[inst.instance_id] = inst
            except (json.JSONDecodeError, KeyError):
                # Registry corrupted or empty; start fresh
                pass

    def _save_instances(self):
        """Persist instances to registry file."""
        with open(self.registry_path, "w") as f:
            json.dump({
                "updated_at": datetime.utcnow().isoformat(),
                "instances": [asdict(inst) for inst in self._instances.values()],
            }, f, indent=2)

    def register_instance(
        self,
        instance_id: str,
        hostname: str,
        version: str,
        location: str = "unknown",
        api_url: str = "",
    ) -> None:
        """Register or update an instance.

        Args:
            instance_id: Unique instance identifier
            hostname: Hostname or IP address
            version: CorvinOS version
            location: City, Country or "lat,lon"
            api_url: API endpoint for metrics
        """
        self._instances[instance_id] = CorvinInstance(
            instance_id=instance_id,
            hostname=hostname,
            version=version,
            location=location,
            last_seen=datetime.utcnow().isoformat(),
            api_url=api_url,
        )
        self._save_instances()

    def get_instances(self) -> list[CorvinInstance]:
        """Get all registered instances.

        Returns:
            List of CorvinInstance objects
        """
        # Remove stale instances (not seen in 24 hours)
        cutoff = datetime.utcnow() - timedelta(hours=24)
        active = {}
        for inst_id, inst in self._instances.items():
            try:
                last_seen = datetime.fromisoformat(inst.last_seen)
                if last_seen >= cutoff:
                    active[inst_id] = inst
            except ValueError:
                # Invalid timestamp; skip
                pass

        self._instances = active
        self._save_instances()
        return list(active.values())

    def get_instance(self, instance_id: str) -> Optional[CorvinInstance]:
        """Get a specific instance by ID.

        Args:
            instance_id: Instance identifier

        Returns:
            CorvinInstance if found, None otherwise
        """
        return self._instances.get(instance_id)

    def update_metrics(
        self,
        instance_id: str,
        turn_count: int,
        total_tokens: int,
        avg_tokens_per_turn: int,
        savings_percent: float,
    ) -> None:
        """Update metrics for an instance.

        Args:
            instance_id: Instance identifier
            turn_count: Number of turns processed
            total_tokens: Total tokens consumed
            avg_tokens_per_turn: Average tokens per turn
            savings_percent: Vibe vs Native savings percentage
        """
        if instance_id in self._instances:
            inst = self._instances[instance_id]
            inst.turn_count = turn_count
            inst.total_tokens = total_tokens
            inst.avg_tokens_per_turn = avg_tokens_per_turn
            inst.savings_percent = savings_percent
            inst.last_seen = datetime.utcnow().isoformat()
            self._save_instances()

    def aggregate_stats(self) -> dict:
        """Get aggregated stats across all instances.

        Returns:
            Cluster-wide aggregated metrics
        """
        instances = self.get_instances()

        if not instances:
            return {
                "instance_count": 0,
                "total_turns": 0,
                "total_tokens": 0,
                "avg_tokens_per_turn": 0,
                "avg_savings_percent": 0.0,
                "instances": [],
            }

        total_turns = sum(inst.turn_count for inst in instances)
        total_tokens = sum(inst.total_tokens for inst in instances)
        avg_savings = (
            sum(inst.savings_percent for inst in instances) / len(instances)
            if instances
            else 0.0
        )

        avg_per_turn = (
            total_tokens // total_turns if total_turns > 0 else 0
        )

        return {
            "instance_count": len(instances),
            "total_turns": total_turns,
            "total_tokens": total_tokens,
            "avg_tokens_per_turn": avg_per_turn,
            "avg_savings_percent": round(avg_savings, 1),
            "instances": [
                {
                    "instance_id": inst.instance_id,
                    "hostname": inst.hostname,
                    "location": inst.location,
                    "turn_count": inst.turn_count,
                    "total_tokens": inst.total_tokens,
                    "savings_percent": inst.savings_percent,
                }
                for inst in instances
            ],
        }


# Singleton instance registry (module-level)
_registry = InstanceRegistry()


def get_instance_registry() -> InstanceRegistry:
    """Get the module-level instance registry singleton.

    Returns:
        InstanceRegistry singleton
    """
    return _registry
