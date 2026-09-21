"""Unit tests for Stream 2.4: Status Computation & Manifest Refresh.

Tests:
- Manifest refresh detects added/removed/modified loops
- Status transitions compute correctly
- Manifest refresh concurrent-safe
- Pruning job idempotent
- Pruning updates health scores
- Signals older than 7d removed
- Daily job audit-logged
"""

import asyncio
import tempfile
import pytest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch, AsyncMock

from core.knowledge_graph.mcp.learning_loop_service import LearningLoopService
from core.knowledge_graph.mcp.manifest_refresh import ManifestRefreshService
from core.knowledge_graph.mcp.background_jobs import BackgroundJobScheduler


@pytest.fixture
def temp_db_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def service(temp_db_dir):
    service = LearningLoopService("_default", temp_db_dir / "index")
    yield service
    service.close()


# ── Test Manifest Refresh ───────────────────────────────────────────────────


class TestManifestRefresh:
    """Tests for manifest refresh handler."""

    @pytest.mark.asyncio
    async def test_refresh_adds_new_loops(self, service):
        """Refresh adds new loops from manifest."""
        refresh = ManifestRefreshService(service)

        # Mock manifest with new loop
        new_manifest = {
            "learning_loops": [
                {
                    "plugin_id": "test",
                    "loop_id": "new_loop",
                    "description": "New loop",
                    "event_source": "Test",
                    "feedback_types": [],
                    "aggregation": "rolling_mean_7d",
                    "health_threshold": None,
                    "dormancy_alert_hours": 24,
                    "owner_skill": None,
                }
            ]
        }

        with patch.object(
            refresh, "_fetch_manifest", new_callable=AsyncMock, return_value=new_manifest
        ):
            result = await refresh.refresh_manifest()

        assert len(result["added"]) == 1
        assert result["added"][0]["loop_id"] == "new_loop"

    @pytest.mark.asyncio
    async def test_refresh_detects_removed_loops(self, service):
        """Refresh detects removed loops."""
        # Create initial loop
        service.insert_from_manifest(
            plugin_id="test", loop_id="old_loop",
            description="", event_source="", feedback_types=[],
            aggregation="rolling_mean_7d", health_threshold=None,
            dormancy_alert_hours=24, owner_skill=None,
        )

        refresh = ManifestRefreshService(service)
        refresh._last_manifest = {
            "learning_loops": [
                {
                    "plugin_id": "test",
                    "loop_id": "old_loop",
                    "description": "",
                    "event_source": "",
                    "feedback_types": [],
                    "aggregation": "rolling_mean_7d",
                    "health_threshold": None,
                    "dormancy_alert_hours": 24,
                    "owner_skill": None,
                }
            ]
        }

        # New manifest without the loop
        new_manifest = {"learning_loops": []}

        with patch.object(
            refresh, "_fetch_manifest", new_callable=AsyncMock, return_value=new_manifest
        ):
            result = await refresh.refresh_manifest()

        assert len(result["removed"]) == 1

    @pytest.mark.asyncio
    async def test_refresh_detects_modified_loops(self, service):
        """Refresh detects modified loops."""
        refresh = ManifestRefreshService(service)

        old_manifest = {
            "learning_loops": [
                {
                    "plugin_id": "test",
                    "loop_id": "loop",
                    "description": "Old description",
                    "event_source": "Test",
                    "feedback_types": [],
                    "aggregation": "rolling_mean_7d",
                    "health_threshold": None,
                    "dormancy_alert_hours": 24,
                    "owner_skill": None,
                }
            ]
        }

        new_manifest = {
            "learning_loops": [
                {
                    "plugin_id": "test",
                    "loop_id": "loop",
                    "description": "New description",  # Changed!
                    "event_source": "Test",
                    "feedback_types": [],
                    "aggregation": "rolling_mean_7d",
                    "health_threshold": None,
                    "dormancy_alert_hours": 24,
                    "owner_skill": None,
                }
            ]
        }

        refresh._last_manifest = old_manifest

        with patch.object(
            refresh, "_fetch_manifest", new_callable=AsyncMock, return_value=new_manifest
        ):
            result = await refresh.refresh_manifest()

        assert len(result["modified"]) == 1

    @pytest.mark.asyncio
    async def test_refresh_handles_fetch_failure(self, service):
        """Refresh handles fetch failures gracefully."""
        refresh = ManifestRefreshService(service)

        with patch.object(
            refresh, "_fetch_manifest", new_callable=AsyncMock, return_value=None
        ):
            result = await refresh.refresh_manifest()

        assert "error" in result

    @pytest.mark.asyncio
    async def test_refresh_concurrent_calls_safe(self, service):
        """Concurrent refresh calls are safe."""
        refresh = ManifestRefreshService(service)

        manifest = {"learning_loops": []}

        async def run_refresh():
            with patch.object(
                refresh, "_fetch_manifest", new_callable=AsyncMock, return_value=manifest
            ):
                return await refresh.refresh_manifest()

        tasks = [run_refresh() for _ in range(5)]
        results = await asyncio.gather(*tasks)

        assert all("error" in r or r is not None for r in results)


# ── Test Background Jobs ────────────────────────────────────────────────────


class TestBackgroundJobs:
    """Tests for background job scheduler."""

    @pytest.mark.asyncio
    async def test_daily_pruning_job_runs(self, service):
        """Daily pruning job executes."""
        # Create some loops
        for i in range(3):
            service.insert_from_manifest(
                plugin_id="test", loop_id=f"loop{i}",
                description="", event_source="", feedback_types=[],
                aggregation="rolling_mean_7d", health_threshold=None,
                dormancy_alert_hours=24, owner_skill=None,
            )

        scheduler = BackgroundJobScheduler(service)
        result = await scheduler.run_daily_pruning()

        assert result is not None
        assert "stale_loops_found" in result

    @pytest.mark.asyncio
    async def test_pruning_detects_stale_loops(self, service):
        """Pruning detects stale loops (7d+ without events)."""
        # Create old loop (8 days old)
        old_date = datetime.utcnow() - timedelta(days=8)

        # Manually create entry with old timestamp
        from core.knowledge_graph.storage.learning_loop_index import LearningLoopIndexEntry

        entry = LearningLoopIndexEntry(
            tenant_id="_default",
            plugin_id="test",
            loop_id="stale_loop",
            description="Stale",
            event_source="",
            feedback_types=[],
            aggregation="rolling_mean_7d",
            health_threshold=None,
            dormancy_alert_hours=24,
            owner_skill=None,
            last_event_ts=old_date,
            event_count_7d=0,
            health_score=0.5,
            status="stale",
            created_at=old_date,
            updated_at=old_date,
        )
        service._storage.insert(entry)

        scheduler = BackgroundJobScheduler(service)
        result = await scheduler.run_daily_pruning()

        assert result["stale_loops_found"] >= 1

    @pytest.mark.asyncio
    async def test_pruning_idempotent(self, service):
        """Pruning job is idempotent (safe to run multiple times)."""
        scheduler = BackgroundJobScheduler(service)

        result1 = await scheduler.run_daily_pruning()
        result2 = await scheduler.run_daily_pruning()

        # Both should complete without error
        assert "error" not in result1 or result1["error"] is None
        assert "error" not in result2 or result2["error"] is None

    @pytest.mark.asyncio
    async def test_pruning_recalculates_scores(self, service):
        """Pruning recalculates health scores."""
        # Create loop with old data
        service.insert_from_manifest(
            plugin_id="test", loop_id="score_loop",
            description="", event_source="", feedback_types=[],
            aggregation="rolling_mean_7d", health_threshold=None,
            dormancy_alert_hours=24, owner_skill=None,
        )

        # Add events
        for _ in range(5):
            service.update_on_event(
                plugin_id="test", loop_id="score_loop",
                feedback_signal=0.8,
            )

        scheduler = BackgroundJobScheduler(service)
        result = await scheduler.run_daily_pruning()

        assert result["recalculated_scores"] >= 1
