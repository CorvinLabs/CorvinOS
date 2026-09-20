"""E2E tests for Vibe Engineering plugin."""
import pytest
from vibe_routing import VibeRouter


@pytest.mark.asyncio
async def test_vibe_plugin_loads():
    """Verify plugin loads + initializes."""
    router = VibeRouter(
        plugin_id="ai.vibe_engineering",
        version="2.0.0",
        config={"active_mode": False}
    )
    await router.initialize()
    assert router.active_mode == False


@pytest.mark.asyncio
async def test_vibe_brief_mode_routes():
    """Test deterministic CEL routing."""
    router = VibeRouter(
        plugin_id="ai.vibe_engineering",
        version="2.0.0",
        config={"active_mode": False}
    )
    await router.initialize()

    decision = await router.route_request({"type": "task"})
    assert decision.mode == "brief"
    assert decision.target_engine == "opus"
    assert decision.confidence > 0.8


@pytest.mark.asyncio
async def test_vibe_active_mode_routes():
    """Test LLM-powered active routing."""
    router = VibeRouter(
        plugin_id="ai.vibe_engineering",
        version="2.0.0",
        config={"active_mode": True}
    )
    await router.initialize()

    decision = await router.route_request({"type": "task"})
    assert decision.mode == "active"
    assert decision.target_engine == "claude-opus-5"
    assert decision.confidence > 0.9


def test_vibe_is_enabled():
    """Test is_enabled() method."""
    router = VibeRouter(
        plugin_id="ai.vibe_engineering",
        version="2.0.0",
        config={}
    )
    assert router.is_enabled() == True
