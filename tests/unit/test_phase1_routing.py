"""
Unit tests for Phase 1 Smart Routing.
"""

import pytest
from core.plugins.registry.routing import (
    Router,
    RoutingPredicate,
    PredicateType,
    create_routing_config,
)


def test_routing_predicate_event_type():
    """Test event type predicate matching."""
    pred = RoutingPredicate(PredicateType.EVENT_TYPE, ["error", "metric"])

    assert pred.matches({"type": "error"}) == True
    assert pred.matches({"type": "metric"}) == True
    assert pred.matches({"type": "decision"}) == False


def test_routing_predicate_user_tier():
    """Test user tier predicate matching."""
    pred = RoutingPredicate(PredicateType.USER_TIER, ["premium", "enterprise"])

    assert pred.matches({"user_tier": "premium"}) == True
    assert pred.matches({"user_tier": "free"}) == False


def test_routing_predicate_latency():
    """Test latency threshold predicate."""
    pred = RoutingPredicate(PredicateType.MIN_LATENCY_MS, 50)

    assert pred.matches({"latency_ms": 60}) == True
    assert pred.matches({"latency_ms": 30}) == False
    assert pred.matches({}) == False  # No latency field


def test_router_basic():
    """Test basic routing."""
    router = Router()

    # Register plugins
    router.register_plugin(create_routing_config(
        "monitor_errors",
        event_types=["error"],
    ))

    router.register_plugin(create_routing_config(
        "log_all",
        event_types=["error", "metric", "decision"],
    ))

    # Route error event
    matched = router.route({"type": "error"})
    assert "monitor_errors" in matched
    assert "log_all" in matched

    # Route metric event
    matched = router.route({"type": "metric"})
    assert "monitor_errors" not in matched
    assert "log_all" in matched


def test_router_multiple_predicates():
    """Test AND logic (all predicates must match)."""
    router = Router()

    router.register_plugin(create_routing_config(
        "premium_error_handler",
        event_types=["error"],
        user_tiers=["premium"],
    ))

    # Premium user error → matches
    matched = router.route({"type": "error", "user_tier": "premium"})
    assert "premium_error_handler" in matched

    # Free user error → no match
    matched = router.route({"type": "error", "user_tier": "free"})
    assert "premium_error_handler" not in matched

    # Premium user metric → no match
    matched = router.route({"type": "metric", "user_tier": "premium"})
    assert "premium_error_handler" not in matched


def test_router_performance():
    """Test routing performance (should be <1ms)."""
    router = Router()

    # Register 100 plugins
    for i in range(100):
        router.register_plugin(create_routing_config(
            f"plugin_{i}",
            event_types=[f"type_{i % 10}"],
        ))

    # Route 1000 events
    import time
    start = time.time() * 1000
    for _ in range(1000):
        router.route({"type": "type_5"})
    elapsed = (time.time() * 1000) - start

    avg_per_route = elapsed / 1000
    assert avg_per_route < 1.0, f"Routing too slow: {avg_per_route:.2f}ms per route"


def test_router_metrics():
    """Test routing metrics."""
    router = Router()
    router.register_plugin(create_routing_config("p1", event_types=["error"]))

    router.route({"type": "error"})
    router.route({"type": "metric"})

    metrics = router.get_metrics()
    assert metrics["total_routes"] == 2
    assert metrics["avg_match_rate"] > 0.4  # At least some match


def test_no_predicates_matches_all():
    """Test that plugin with no predicates matches all events."""
    router = Router()

    router.register_plugin(create_routing_config("catch_all"))

    assert "catch_all" in router.route({"type": "error"})
    assert "catch_all" in router.route({"type": "metric"})
    assert "catch_all" in router.route({})
