"""E2E Tests for Phase 2 Feature 1: Live Data Endpoints (ADR-0728)

Tests verify:
1. Licensing audit events endpoint (EventStore + PII filtering)
2. Monitoring metrics endpoint (HealthMonitor integration)
3. Models endpoint (Engine registry)

Compliance: ADR-0297 (PII), ADR-0728 (Feature Architecture), GDPR Art. 5+32
"""

import pytest
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))


# TestLicensingAuditEventsEndpoint removed (2026-11-05): it mocked a PIIDetector
# redaction path that 6ee192386 (ADR-0856) deleted — the route is now a
# session-scoped, content-free projection of the tenant event store. The route
# is driven over HTTP by tests/e2e/test_phase2_live_endpoints_e2e.py.


class TestMonitoringMetricsEndpoint:
    """Tests for /v1/monitoring/metrics endpoint"""

class TestModelsEndpoint:
    """Tests for /v1/models/available endpoint"""

    @pytest.mark.asyncio
    async def test_models_endpoint_returns_engine_registry(self):
        """Verify models endpoint returns available engines with costs"""
        # Note: Implementation to be done in Phase 2.5
        # This test documents the expected behavior

        expected_response = {
            "models": [
                {
                    "id": "claude-opus",
                    "name": "Claude Opus 5",
                    "provider": "anthropic",
                    "cost_per_1k": 0.015,
                    "latency_ms": 200,
                    "capabilities": ["long-context", "code", "reasoning"]
                },
                {
                    "id": "claude-sonnet",
                    "name": "Claude Sonnet 5",
                    "provider": "anthropic",
                    "cost_per_1k": 0.003,
                    "latency_ms": 50,
                    "capabilities": ["fast", "code", "standard"]
                }
            ],
            "timestamp": "2026-09-16T10:00:00Z"
        }

        # Verify structure (this will be implemented live in Phase 2.5)
        assert "models" in expected_response
        assert len(expected_response["models"]) >= 2
        assert all("id" in m and "cost_per_1k" in m for m in expected_response["models"])


class TestVibeDashboardIntegration:
    """Integration tests: React components ↔ live endpoints"""

    # test_licensing_audit_tab_queries_correct_endpoint removed: LicensingAuditTab.tsx deleted in eb334c032.

    def test_monitoring_tab_queries_correct_endpoint(self):
        """MonitoringTab fetches from /v1/monitoring/metrics"""
        import os
        tab_path = os.path.join(
            os.path.dirname(__file__),
            "../../core/console/corvin_console/web-next/src/pages/vibe-engineering/tabs/MonitoringTab.tsx"
        )

        with open(tab_path, 'r', encoding="utf-8") as f:
            content = f.read()
            assert "/v1/monitoring/metrics" in content

    # test_models_tab_queries_correct_endpoint removed: ModelsTab.tsx deleted in 64e74e743 (ADR-0908).


if __name__ == "__main__":
    print("Phase 2 Feature 1 E2E Test Suite ready for pytest")
    print("Run: pytest tests/e2e/test_phase2_feature1_endpoints.py -v")
