"""E2E tests for Error Healing plugin."""

import pytest
import asyncio
from datetime import datetime, timezone

from buildin.observability.error_healing import ErrorHealing
from core.compliance.tripwire import boot_tripwire


class TestErrorHealingE2E:
    """End-to-end tests for Error Healing."""

    @pytest.fixture
    async def healer(self):
        """Fixture: initialized error healer."""
        await boot_tripwire()
        healer = ErrorHealing(tenant_id="test_tenant")
        await healer.initialize()
        yield healer
        await healer.shutdown()

    @pytest.mark.asyncio
    async def test_error_classification(self, healer):
        """Test error classification."""
        errors = [
            {"type": "context_loss", "severity": "high"},
            {"type": "timeout", "severity": "medium"},
            {"type": "permission_denied", "severity": "critical"}
        ]

        for error in errors:
            classification = await healer.classify_error(error)
            assert classification["recoverable"] in [True, False]
            assert classification["strategy"] is not None

    @pytest.mark.asyncio
    async def test_recovery_execution(self, healer):
        """Test recovery strategy execution."""
        error = {
            "type": "context_loss",
            "severity": "high",
            "message": "Lost context checkpoint"
        }

        result = await healer.report_error(error)
        assert result["healing_attempted"] is True
        assert "recovery_time_ms" in result

    @pytest.mark.asyncio
    async def test_concurrent_error_handling(self, healer):
        """Test concurrent error reports."""
        async def report_error(error_id):
            error = {
                "type": "timeout",
                "severity": "medium",
                "id": error_id
            }
            return await healer.report_error(error)

        tasks = [report_error(i) for i in range(20)]
        results = await asyncio.gather(*tasks)
        assert len(results) == 20

    @pytest.mark.asyncio
    async def test_healing_success_rate(self, healer):
        """Test healing success rate tracking."""
        stats = await healer.get_healing_stats()
        assert "total_errors" in stats
        assert "successful_recoveries" in stats
        assert "success_rate" in stats


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
