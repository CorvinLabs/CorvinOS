"""Shared fixtures for plugin tests."""

import pytest
from unittest.mock import MagicMock
from pathlib import Path


@pytest.fixture
def mock_plugin_context():
    """Fixture: Mock PluginContext for plugin initialization."""
    ctx = MagicMock()
    ctx.plugin_id = "com.test.plugin"
    ctx.tenant_id = "_default"
    ctx.config = {
        "endpoint": "https://test.example.com",
        "api_key": "test-key",
    }
    ctx.audit_registry = MagicMock()
    ctx.user_registry = MagicMock()
    ctx.recall_registry = MagicMock()
    ctx.audit_emit = MagicMock()
    return ctx


@pytest.fixture
def temp_corvin_home(tmp_path):
    """Fixture: Temporary ~/.corvin directory for testing."""
    corvin_home = tmp_path / ".corvin"
    corvin_home.mkdir()
    (corvin_home / "plugins").mkdir()
    (corvin_home / "audit.jsonl").touch()
    (corvin_home / "registry.yaml").write_text("plugins: {}\n")
    return corvin_home


@pytest.fixture
def mock_audit_registry():
    """Fixture: Mock audit registry."""
    registry = MagicMock()
    registry.set_active = MagicMock()
    registry.get_active = MagicMock(return_value=None)
    registry.emit = MagicMock()
    return registry


@pytest.fixture
def mock_user_registry():
    """Fixture: Mock user registry."""
    registry = MagicMock()
    registry.authenticate = MagicMock(return_value={"uid": "test-user", "roles": ["user"]})
    registry.get_user = MagicMock(return_value={"uid": "test-user"})
    return registry


@pytest.fixture
def mock_recall_registry():
    """Fixture: Mock recall registry."""
    registry = MagicMock()
    registry.index_turn = MagicMock(return_value={"turn_id": "t123"})
    registry.recall = MagicMock(return_value=[{"turn_id": "t123", "content": "..."}])
    registry.forget = MagicMock(return_value={"deleted": 1})
    return registry


@pytest.fixture
def mock_notification_registry():
    """Fixture: Mock notification registry."""
    registry = MagicMock()
    registry.notify = MagicMock(return_value={"message_id": "msg123"})
    registry.batch_notify = MagicMock(return_value={"sent": 5, "failed": 0})
    return registry


@pytest.fixture
def mock_router_registry():
    """Fixture: Mock router registry."""
    registry = MagicMock()
    registry.route = MagicMock(return_value={"target": "opus", "confidence": 0.95})
    return registry


@pytest.fixture
def mock_learning_event_store():
    """Fixture: Mock learning event store."""
    store = MagicMock()
    store.write_event = MagicMock(return_value={"event_id": "evt123", "tenant_id": "_default"})
    store.read_events = MagicMock(return_value=[{"event_id": "evt123", "type": "skill_executed"}])
    store.query = MagicMock(return_value=[])
    return store


@pytest.fixture
def mock_consent_gate():
    """Fixture: Mock consent gate (L16 security)."""
    gate = MagicMock()
    gate.check_consent = MagicMock(return_value=True)
    gate.has_consent = MagicMock(return_value=True)
    gate.grant_consent = MagicMock(return_value={"timestamp": "2026-09-02T00:00:00Z"})
    gate.deny = MagicMock(return_value={"reason": "User denied"})
    return gate


@pytest.fixture
def mock_house_rules_enforcer():
    """Fixture: Mock house rules enforcer (L44)."""
    enforcer = MagicMock()
    enforcer.check = MagicMock(return_value=True)
    enforcer.is_violation = MagicMock(return_value=False)
    return enforcer


@pytest.fixture
def mock_data_flow_guard():
    """Fixture: Mock data flow guard (L34)."""
    guard = MagicMock()
    guard.classify = MagicMock(return_value="public")
    guard.allow_flow = MagicMock(return_value=True)
    guard.block_flow = MagicMock(return_value=False)
    return guard


@pytest.fixture(autouse=True)
def reset_global_state():
    """Fixture: Reset global plugin state between tests."""
    yield
    # Cleanup after test
    try:
        from corvin_plugins.circuit_breaker import _breakers
        _breakers._registry = {}
    except ImportError:
        pass


@pytest.fixture
def plugin_test_context(mock_plugin_context, mock_audit_registry, mock_user_registry, mock_recall_registry):
    """Fixture: Complete context for plugin testing."""
    mock_plugin_context.audit_registry = mock_audit_registry
    mock_plugin_context.user_registry = mock_user_registry
    mock_plugin_context.recall_registry = mock_recall_registry
    return mock_plugin_context
