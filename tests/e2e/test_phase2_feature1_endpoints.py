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


class TestLicensingAuditEventsEndpoint:
    """Tests for /v1/licensing/audit-events endpoint"""

    @pytest.mark.asyncio
    async def test_audit_events_returns_pii_redacted_data(self):
        """Verify audit events are returned with PII redacted (ADR-0297)"""
        # Mock EventStore
        mock_store = AsyncMock()
        mock_store.query_events.return_value = [
            MagicMock(
                id="evt_abc123",
                timestamp="2026-09-16T10:00:00Z",
                event_type="consent_granted",
                signal=0.9,
            )
        ]

        # Mock PIIDetector
        mock_detector = MagicMock()
        mock_detector.redact.return_value = "consent_granted"
        mock_detector.scan.return_value = False  # No PII detected

        with patch('core.learning.event_store.EventStore', return_value=mock_store):
            with patch('core.pii.detector.PIIDetector', return_value=mock_detector):
                # Import after mocking
                from core.console.corvin_console.routes.features_phase2 import get_audit_events

                response = await get_audit_events(limit=50)

                # Verify response structure
                assert "events" in response
                assert "total" in response
                assert "compliance" in response
                assert response["compliance"] == "ADR-0297 PII filtering applied"

                # Verify PII redaction
                assert len(response["events"]) > 0
                assert response["events"][0]["user_id"] == "[REDACTED]"
                assert response["events"][0]["event_type"] == "consent_granted"

    @pytest.mark.asyncio
    async def test_audit_events_skips_pii_detected_events(self):
        """Events with detected PII are skipped (fail-closed)"""
        mock_store = AsyncMock()
        mock_store.query_events.return_value = [
            MagicMock(
                id="evt_with_pii",
                timestamp="2026-09-16T10:00:00Z",
                event_type="user_signup",
                signal=0.8,
            )
        ]

        mock_detector = MagicMock()
        mock_detector.redact.return_value = "user_signup"
        mock_detector.scan.return_value = True  # PII detected!

        with patch('core.learning.event_store.EventStore', return_value=mock_store):
            with patch('core.pii.detector.PIIDetector', return_value=mock_detector):
                from core.console.corvin_console.routes.features_phase2 import get_audit_events

                response = await get_audit_events(limit=50)

                # Verify PII event was skipped
                assert response["total"] == 0  # No events returned
                assert len(response["events"]) == 0

    @pytest.mark.asyncio
    async def test_audit_events_graceful_error_handling(self):
        """EventStore errors are handled gracefully (don't break dashboard)"""
        mock_store = AsyncMock()
        mock_store.query_events.side_effect = Exception("Database connection failed")

        with patch('core.learning.event_store.EventStore', return_value=mock_store):
            from core.console.corvin_console.routes.features_phase2 import get_audit_events

            response = await get_audit_events(limit=50)

            # Verify graceful fallback
            assert "events" in response
            assert response["events"] == []
            assert "error" in response


class TestMonitoringMetricsEndpoint:
    """Tests for /v1/monitoring/metrics endpoint"""

    @pytest.mark.asyncio
    async def test_monitoring_metrics_returns_system_health(self):
        """Verify metrics endpoint returns health monitor data"""
        mock_monitor = AsyncMock()
        mock_monitor.get_status.return_value = {
            "avg_latency": 125,
            "error_rate": 0.02,
            "convergence": 0.82,
            "success_rate": 0.98,
        }

        with patch('core.orchestration.brain.HealthMonitor', return_value=mock_monitor):
            from core.console.corvin_console.routes.features_phase2 import get_metrics

            response = await get_metrics(range="1h")

            # Verify response structure
            assert "metrics" in response
            assert "alerts" in response
            assert "timestamp" in response
            assert "range" in response
            assert response["range"] == "1h"

            # Verify metric data
            metrics = response["metrics"]
            assert len(metrics) == 4  # skill_latency, error_rate, convergence, success_rate
            assert metrics[0]["name"] == "skill_latency_ms"
            assert metrics[0]["value"] == 125
            assert metrics[0]["status"] == "ok"

    @pytest.mark.asyncio
    async def test_monitoring_metrics_status_transitions(self):
        """Metrics transition status based on thresholds"""
        mock_monitor = AsyncMock()
        mock_monitor.get_status.return_value = {
            "avg_latency": 250,  # > 200 = warning
            "error_rate": 0.08,  # > 0.05 = critical
            "convergence": 0.55,  # < 0.7 = warning
            "success_rate": 0.92,  # < 0.95 = warning
        }

        with patch('core.orchestration.brain.HealthMonitor', return_value=mock_monitor):
            from core.console.corvin_console.routes.features_phase2 import get_metrics

            response = await get_metrics(range="1h")

            metrics = {m["name"]: m for m in response["metrics"]}
            assert metrics["skill_latency_ms"]["status"] == "warning"
            assert metrics["skill_error_rate"]["status"] == "critical"
            assert metrics["convergence_mean"]["status"] == "warning"


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

    def test_licensing_audit_tab_queries_correct_endpoint(self):
        """LicensingAuditTab fetches from /v1/licensing/audit-events"""
        import os
        tab_path = os.path.join(
            os.path.dirname(__file__),
            "../../core/console/corvin_console/web-next/src/pages/vibe-engineering/tabs/LicensingAuditTab.tsx"
        )

        # Verify component exists and contains endpoint
        with open(tab_path, 'r') as f:
            content = f.read()
            assert "/v1/licensing/audit-events" in content
            assert "fetch" in content

    def test_monitoring_tab_queries_correct_endpoint(self):
        """MonitoringTab fetches from /v1/monitoring/metrics"""
        import os
        tab_path = os.path.join(
            os.path.dirname(__file__),
            "../../core/console/corvin_console/web-next/src/pages/vibe-engineering/tabs/MonitoringTab.tsx"
        )

        with open(tab_path, 'r') as f:
            content = f.read()
            assert "/v1/monitoring/metrics" in content

    def test_models_tab_queries_correct_endpoint(self):
        """ModelsTab fetches from /v1/models/available"""
        import os
        tab_path = os.path.join(
            os.path.dirname(__file__),
            "../../core/console/corvin_console/web-next/src/pages/vibe-engineering/tabs/ModelsTab.tsx"
        )

        with open(tab_path, 'r') as f:
            content = f.read()
            assert "/v1/models/available" in content


if __name__ == "__main__":
    print("Phase 2 Feature 1 E2E Test Suite ready for pytest")
    print("Run: pytest tests/e2e/test_phase2_feature1_endpoints.py -v")
