"""
Smart Plugin Routing — Phase 1

Route events to only relevant plugins based on hard predicates.
Latency target: <1ms eval, 15-20% overall latency reduction.
"""

from dataclasses import dataclass
from typing import Dict, List, Any, Callable, Optional
from enum import Enum
import time


class PredicateType(str, Enum):
    """Hard predicate types (no ML heuristics)."""
    EVENT_TYPE = "event_type"
    USER_TIER = "user_tier"
    MIN_LATENCY_MS = "min_latency_ms"
    ERROR_TYPE = "error_type"
    COMPONENT = "component"


@dataclass
class RoutingPredicate:
    """Single routing predicate."""
    pred_type: PredicateType
    values: Any  # str, int, List[str], etc.

    def matches(self, event: Dict[str, Any]) -> bool:
        """Check if predicate matches event."""
        if self.pred_type == PredicateType.EVENT_TYPE:
            return event.get("type") in (self.values if isinstance(self.values, list) else [self.values])

        elif self.pred_type == PredicateType.USER_TIER:
            return event.get("user_tier") in (self.values if isinstance(self.values, list) else [self.values])

        elif self.pred_type == PredicateType.MIN_LATENCY_MS:
            return event.get("latency_ms", 0) > self.values

        elif self.pred_type == PredicateType.ERROR_TYPE:
            return event.get("error", {}).get("type") in (self.values if isinstance(self.values, list) else [self.values])

        elif self.pred_type == PredicateType.COMPONENT:
            return event.get("component") in (self.values if isinstance(self.values, list) else [self.values])

        return False


@dataclass
class PluginRoutingConfig:
    """Plugin's routing predicates."""
    plugin_id: str
    predicates: List[RoutingPredicate]

    def matches_event(self, event: Dict[str, Any]) -> bool:
        """All predicates must match (AND logic)."""
        if not self.predicates:
            return True  # No predicates = all events
        return all(p.matches(event) for p in self.predicates)


class Router:
    """Routes events to relevant plugins."""

    def __init__(self):
        self.plugins: Dict[str, PluginRoutingConfig] = {}
        self.metrics = {
            "total_routes": 0,
            "avg_route_time_ms": 0.0,
            "skipped_plugins": 0,
        }

    def register_plugin(self, config: PluginRoutingConfig):
        """Register plugin with routing config."""
        self.plugins[config.plugin_id] = config

    def route(self, event: Dict[str, Any]) -> List[str]:
        """
        Route event to matching plugins.
        Returns: List of plugin_ids that should handle this event.
        """
        start_ms = time.time() * 1000

        matched = []
        for plugin_id, config in self.plugins.items():
            if config.matches_event(event):
                matched.append(plugin_id)

        # Update metrics
        elapsed_ms = (time.time() * 1000) - start_ms
        self.metrics["total_routes"] += 1
        self.metrics["avg_route_time_ms"] = (
            (self.metrics["avg_route_time_ms"] * (self.metrics["total_routes"] - 1) + elapsed_ms)
            / self.metrics["total_routes"]
        )
        self.metrics["skipped_plugins"] += len(self.plugins) - len(matched)

        return matched

    def get_metrics(self) -> Dict[str, Any]:
        """Get routing metrics."""
        return {
            **self.metrics,
            "total_plugins": len(self.plugins),
            "avg_match_rate": (
                (self.metrics["total_routes"] - self.metrics["skipped_plugins"])
                / max(1, self.metrics["total_routes"] * len(self.plugins))
                if self.metrics["total_routes"] > 0 else 0
            )
        }


def create_routing_config(plugin_id: str, **predicates) -> PluginRoutingConfig:
    """Helper to create routing config."""
    preds = []

    for key, value in predicates.items():
        if key == "event_types":
            preds.append(RoutingPredicate(PredicateType.EVENT_TYPE, value))
        elif key == "user_tiers":
            preds.append(RoutingPredicate(PredicateType.USER_TIER, value))
        elif key == "min_latency_ms":
            preds.append(RoutingPredicate(PredicateType.MIN_LATENCY_MS, value))
        elif key == "error_types":
            preds.append(RoutingPredicate(PredicateType.ERROR_TYPE, value))
        elif key == "components":
            preds.append(RoutingPredicate(PredicateType.COMPONENT, value))

    return PluginRoutingConfig(plugin_id, preds)
