"""corvin_compute.fabric.parallel — Section C: Inter-Job Parallelism (ADR-0026)."""
from __future__ import annotations

from .shard import ShardCursor, ShardManager, ShardPlan
from .resources import FabricResourceDenied, ResourceSlot, ResourceManager
from .aggregation import (
    Aggregator,
    BestAggregator,
    AverageAggregator,
    VoteAggregator,
    StackAggregator,
    FederatedAvgAggregator,
    CustomAggregator,
    get_aggregator,
)

__all__ = [
    "ShardCursor",
    "ShardManager",
    "ShardPlan",
    "FabricResourceDenied",
    "ResourceSlot",
    "ResourceManager",
    "Aggregator",
    "BestAggregator",
    "AverageAggregator",
    "VoteAggregator",
    "StackAggregator",
    "FederatedAvgAggregator",
    "CustomAggregator",
    "get_aggregator",
]
