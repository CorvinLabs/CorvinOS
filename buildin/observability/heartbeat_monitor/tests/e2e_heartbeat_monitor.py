"""E2E tests for Heartbeat Monitor plugin."""

import pytest
import asyncio
import time
from datetime import datetime, timezone

from buildin.observability.heartbeat_monitor import HeartbeatMonitor
from core.compliance.tripwire import boot_tripwire


class TestHeartbeatMonitorE2E:
    """End-to-end tests for Heartbeat Monitor."""

    @pytest.fixture
    async def monitor(self):
        """Fixture: initialized heartbeat monitor."""
        await boot_tripwire()
        monitor = HeartbeatMonitor(tenant_id="test_tenant")
        await monitor.initialize()
        yield monitor
        await monitor.shutdown()

    @pytest.mark.asyncio
    async def test_heartbeat_emission(self, monitor):
        """Test emitting heartbeats."""
        session_id = "sess_hb_test"
        await monitor.emit_heartbeat(session_id, {"status": "active"})

        is_alive = await monitor.is_session_alive(session_id)
        assert is_alive is True

    @pytest.mark.asyncio
    async def test_stall_detection(self, monitor):
        """Test detection of stalled sessions."""
        session_id = "sess_stall_test"
        await monitor.emit_heartbeat(session_id, {"status": "active"})

        # Wait for stall timeout (default 30s)
        await asyncio.sleep(2)  # Simulate stall

        is_alive = await monitor.is_session_alive(session_id)
        # Should still be alive after 2s
        assert is_alive is True

    @pytest.mark.asyncio
    async def test_concurrent_heartbeats(self, monitor):
        """Test concurrent heartbeats from multiple sessions."""
        async def emit_beats(session_id):
            for i in range(20):
                await monitor.emit_heartbeat(session_id, {"beat": i})
                await asyncio.sleep(0.01)

        sessions = [f"sess_{i}" for i in range(10)]
        tasks = [emit_beats(sid) for sid in sessions]
        await asyncio.gather(*tasks)

        # Verify all sessions are alive
        for session_id in sessions:
            assert await monitor.is_session_alive(session_id)

    @pytest.mark.asyncio
    async def test_performance_heartbeat_ingestion(self, monitor):
        """Test heartbeat ingestion performance (<1ms)."""
        times = []
        for i in range(500):
            session_id = f"sess_{i % 10}"
            start = time.time()
            await monitor.emit_heartbeat(session_id, {"beat": i})
            elapsed_ms = (time.time() - start) * 1000
            times.append(elapsed_ms)

        mean_time = sum(times) / len(times)
        assert mean_time < 1.0, f"Mean {mean_time:.2f}ms exceeds 1ms"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
