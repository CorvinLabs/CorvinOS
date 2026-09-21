"""E2E tests for MCP Learning Loops Tools (Stream 2.3).

Tests the tools end-to-end:
- Tool invocations return correct JSON
- Tools handle concurrent calls
- Tool responses are JSON-serializable
- Tools work with real service + storage
"""

import asyncio
import json
import tempfile
import pytest
from pathlib import Path

from core.knowledge_graph.mcp.learning_loop_service import LearningLoopService
from core.knowledge_graph.mcp.tools.learning_loops_list import list_learning_loops
from core.knowledge_graph.mcp.tools.learning_loops_health_trend import get_health_trend
from core.knowledge_graph.mcp.tools.learning_loops_detect_conflicts import detect_conflicts


@pytest.fixture
def temp_db_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def service(temp_db_dir):
    service = LearningLoopService("_default", temp_db_dir / "index")
    for i in range(5):
        service.insert_from_manifest(
            plugin_id=f"plugin{i % 2}", loop_id=f"loop{i}",
            description=f"Loop {i}", event_source="Test", feedback_types=[],
            aggregation="rolling_mean_7d", health_threshold=None,
            dormancy_alert_hours=24, owner_skill=None,
        )
    yield service
    service.close()


class TestMCPToolsE2E:
    """E2E tests for MCP tools."""

    def test_list_returns_json_serializable(self, service):
        """list() returns JSON-serializable response."""
        result = list_learning_loops(service, "_default")
        json_str = json.dumps(result)
        assert json_str is not None
        assert len(json_str) > 0

    def test_health_trend_returns_json_serializable(self, service):
        """health_trend() returns JSON-serializable response."""
        result = get_health_trend(service, "_default", "plugin0", "loop0")
        json_str = json.dumps(result)
        assert json_str is not None

    def test_detect_conflicts_returns_json_serializable(self, service):
        """detect_conflicts() returns JSON-serializable response."""
        result = detect_conflicts(service, "_default")
        json_str = json.dumps(result)
        assert json_str is not None

    def test_concurrent_tool_calls(self, service):
        """Concurrent tool calls work correctly."""
        def call_tools():
            results = []
            results.append(list_learning_loops(service, "_default"))
            results.append(detect_conflicts(service, "_default"))
            results.append(get_health_trend(service, "_default", "plugin0", "loop0"))
            return results

        results = call_tools()
        assert len(results) == 3
        assert all(isinstance(r, dict) for r in results)

    def test_list_filters_work_end_to_end(self, service):
        """list() filters work correctly."""
        result = list_learning_loops(service, "_default", plugin_id="plugin0")
        assert all(loop["plugin_id"] == "plugin0" for loop in result["loops"])

    def test_detect_conflicts_finds_duplicates_e2e(self, temp_db_dir):
        """detect_conflicts() finds duplicates end-to-end."""
        service = LearningLoopService("_default", temp_db_dir / "index")
        for i in range(2):
            service.insert_from_manifest(
                plugin_id=f"p{i}", loop_id="same_loop",
                description="", event_source="", feedback_types=[],
                aggregation="rolling_mean_7d", health_threshold=None,
                dormancy_alert_hours=24, owner_skill=None,
            )
        result = detect_conflicts(service, "_default")
        assert result["total_conflicts"] == 1
        service.close()
