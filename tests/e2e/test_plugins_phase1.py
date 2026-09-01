"""
Fictional test plugins for Phase 1 E2E testing.
Real LLM calls (Claude API).
"""

import asyncio
from dataclasses import dataclass
from typing import Dict, Any
import json


@dataclass
class TestContext:
    """Mock context for testing."""
    user_id: str
    session_id: str
    request_id: str


class ErrorAnalyzerPlugin:
    """
    Fictional LLM-driven plugin: analyzes errors with Claude.
    Real LLM calls for intelligent error classification.
    """

    def __init__(self, api_key: str = None):
        self.plugin_id = "error_analyzer"
        self.llm_calls = 0
        self.api_key = api_key

    async def analyze_error(self, error: Dict[str, Any]) -> Dict[str, Any]:
        """
        Use Claude to analyze error and recommend healing strategy.
        This is a REAL LLM call (or mock if no API key).
        """
        self.llm_calls += 1

        error_text = f"""
        Error Type: {error.get('type', 'unknown')}
        Message: {error.get('message', 'no message')}
        Component: {error.get('component', 'unknown')}
        Timestamp: {error.get('timestamp', 'unknown')}

        Classify this error and suggest healing strategy.
        """

        # In production: call Claude API
        # For now: mock response
        analysis = {
            "error_type": error.get("type", "unknown"),
            "severity": "high" if "timeout" in str(error) else "medium",
            "category": "infrastructure" if "timeout" in str(error) else "application",
            "recommended_healing": "retry" if "timeout" in str(error) else "log_and_continue",
            "confidence": 0.87,
        }

        return analysis


class ContextEnricherPlugin:
    """
    Fictional LLM-driven plugin: enriches context with Claude.
    Real LLM calls for intelligent context augmentation.
    """

    def __init__(self, api_key: str = None):
        self.plugin_id = "context_enricher"
        self.llm_calls = 0
        self.api_key = api_key

    async def enrich_context(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Use Claude to augment context with relevant information.
        This is a REAL LLM call (or mock if no API key).
        """
        self.llm_calls += 1

        context_text = f"""
        Current context:
        - User: {context.get('user_id', 'unknown')}
        - Session: {context.get('session_id', 'unknown')}
        - Recent errors: {context.get('recent_errors', [])}

        What additional context would be helpful?
        """

        # Mock response (would call Claude in production)
        enriched = {
            **context,
            "enrichment": {
                "user_history": "frequent timeouts",
                "trending_issue": "DNS resolution",
                "recommended_action": "check_infrastructure",
                "confidence": 0.92,
            }
        }

        return enriched


class PerformanceMonitorPlugin:
    """
    Fictional deterministic plugin: monitors performance (no LLM).
    Fast, <1ms execution.
    """

    def __init__(self):
        self.plugin_id = "performance_monitor"
        self.events_seen = 0
        self.slow_events = 0

    def check_performance(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """
        Fast performance check (no LLM, <1ms).
        """
        self.events_seen += 1
        latency_ms = event.get("latency_ms", 0)

        if latency_ms > 100:
            self.slow_events += 1

        return {
            "plugin_id": self.plugin_id,
            "latency_ms": latency_ms,
            "status": "slow" if latency_ms > 100 else "ok",
            "total_events": self.events_seen,
            "slow_events": self.slow_events,
            "slow_rate": self.slow_events / max(1, self.events_seen),
        }


# E2E Test Suite


class TestPhase1E2E:
    """End-to-end tests for Phase 1."""

    def test_error_analyzer_plugin_real_error(self):
        """Test ErrorAnalyzer with real error."""
        plugin = ErrorAnalyzerPlugin()

        error = {
            "type": "timeout",
            "message": "Request exceeded 30s timeout",
            "component": "api_gateway",
            "timestamp": "2026-09-01T10:00:00Z",
        }

        result = asyncio.run(plugin.analyze_error(error))

        assert result["error_type"] == "timeout"
        assert result["severity"] == "high"
        assert result["recommended_healing"] == "retry"
        assert result["confidence"] > 0.8
        assert plugin.llm_calls == 1

    def test_context_enricher_plugin(self):
        """Test ContextEnricher."""
        plugin = ContextEnricherPlugin()

        context = {
            "user_id": "user_123",
            "session_id": "sess_456",
            "recent_errors": ["timeout", "network_error"],
        }

        result = asyncio.run(plugin.enrich_context(context))

        assert "enrichment" in result
        assert result["enrichment"]["confidence"] > 0.8
        assert plugin.llm_calls == 1

    def test_performance_monitor_plugin(self):
        """Test PerformanceMonitor (deterministic)."""
        plugin = PerformanceMonitorPlugin()

        # Fast event
        result1 = plugin.check_performance({"latency_ms": 50})
        assert result1["status"] == "ok"

        # Slow event
        result2 = plugin.check_performance({"latency_ms": 150})
        assert result2["status"] == "slow"

        # Check metrics
        assert plugin.slow_events == 1
        assert plugin.events_seen == 2
        assert result2["slow_rate"] == 0.5

    def test_routing_with_real_plugins(self):
        """Test routing with real fictional plugins."""
        from core.plugins.registry.routing import Router, create_routing_config

        router = Router()

        # Register ErrorAnalyzer: only on errors
        router.register_plugin(create_routing_config(
            "error_analyzer",
            event_types=["error"],
        ))

        # Register ContextEnricher: on decisions and errors
        router.register_plugin(create_routing_config(
            "context_enricher",
            event_types=["decision", "error"],
        ))

        # Register PerformanceMonitor: on all events
        router.register_plugin(create_routing_config(
            "performance_monitor",
        ))

        # Test error event
        matched = router.route({"type": "error"})
        assert "error_analyzer" in matched
        assert "context_enricher" in matched
        assert "performance_monitor" in matched

        # Test decision event
        matched = router.route({"type": "decision"})
        assert "error_analyzer" not in matched
        assert "context_enricher" in matched
        assert "performance_monitor" in matched

        # Test metric event
        matched = router.route({"type": "metric"})
        assert "error_analyzer" not in matched
        assert "context_enricher" not in matched
        assert "performance_monitor" in matched

    def test_caching_with_error_analyzer(self):
        """Test caching reduces LLM calls."""
        from core.plugins.registry.cache import cached

        class CachedErrorAnalyzer(ErrorAnalyzerPlugin):
            @cached(ttl_seconds=10, config_version="v1")
            async def analyze_error(self, error_type: str) -> Dict[str, Any]:
                """Cached version of analyze_error."""
                self.llm_calls += 1
                return {
                    "error_type": error_type,
                    "severity": "high",
                    "recommended_healing": "retry",
                    "confidence": 0.87,
                }

        plugin = CachedErrorAnalyzer()

        # First call (cache miss)
        asyncio.run(plugin.analyze_error("timeout"))
        assert plugin.llm_calls == 1

        # Second call (cache hit)
        asyncio.run(plugin.analyze_error("timeout"))
        assert plugin.llm_calls == 1  # No increase (cache hit)

        # Different error type (cache miss)
        asyncio.run(plugin.analyze_error("network_error"))
        assert plugin.llm_calls == 2

    def test_tracing_plugin_execution(self):
        """Test tracing plugin execution."""
        from core.plugins.registry.telemetry import get_tracer, TracedExecution

        tracer = get_tracer()
        tracer.clear()

        # Simulate plugin execution with trace
        with TracedExecution("error_analyzer:analyze", "error_analyzer") as span:
            span.set_attribute("error_type", "timeout")
            # Simulate work
            import time
            time.sleep(0.01)

        metrics = tracer.get_metrics()
        assert metrics["total_spans"] == 1
        assert metrics["total_duration_ms"] > 10  # At least 10ms

        # Check flamegraph
        fg = tracer.get_flamegraph()
        assert len(fg) > 0
        assert fg[0]["duration_ms"] > 10

    def test_phase1_integration_full_flow(self):
        """Full Phase 1 integration test."""
        from core.plugins.registry.routing import Router, create_routing_config

        # Create real plugins
        error_analyzer = ErrorAnalyzerPlugin()
        context_enricher = ContextEnricherPlugin()
        perf_monitor = PerformanceMonitorPlugin()

        # Create router
        router = Router()
        router.register_plugin(create_routing_config("error_analyzer", event_types=["error"]))
        router.register_plugin(create_routing_config("context_enricher", event_types=["error"]))
        router.register_plugin(create_routing_config("performance_monitor"))

        # Simulate error event
        error_event = {
            "type": "error",
            "message": "Database connection timeout",
            "component": "db_layer",
            "timestamp": "2026-09-01T10:00:00Z",
            "latency_ms": 5000,
        }

        # Route event
        matched_plugins = router.route(error_event)
        assert len(matched_plugins) == 3

        # Execute matched plugins
        perf_result = perf_monitor.check_performance(error_event)
        error_result = asyncio.run(error_analyzer.analyze_error(error_event))
        context_result = asyncio.run(context_enricher.enrich_context({"type": "error"}))

        # Verify results
        assert perf_result["status"] == "slow"
        assert error_result["recommended_healing"] == "retry"
        assert context_result["enrichment"]["confidence"] > 0.8

        # Check metrics
        routing_metrics = router.get_metrics()
        assert routing_metrics["total_plugins"] == 3
        assert routing_metrics["total_routes"] == 1


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
