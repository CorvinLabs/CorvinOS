"""registry._emit_plugin_executed → audit + learning event (ADR-0923).

NOT WIRED: ``_emit_plugin_executed`` has no production caller as of
2026-09-27 (adversarial review); these tests prove the function itself. The
earlier suite called ``EventStore.query_events`` (which does not exist) and the
emitter handed a ``learning_events.LearningEvent`` to the async
``event_persistence.EventStore.write_event`` (other schema, no tenant), so no
learning event was ever persisted — every scenario failed.
tests/conftest.py isolates CORVIN_HOME per test.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.learning import learning_events
from core.learning.event_store import EventStore
from forge import paths as forge_paths
from core.plugins.corvin_plugins.registry import _emit_plugin_executed

TENANT = "_default"
PLUGIN = "test.learning.plugin"


def _ctx(tenant_id=TENANT):
    ctx = MagicMock()
    ctx.tenant_id = tenant_id
    ctx.emitted = []
    ctx.audit_emit = lambda et, details: ctx.emitted.append((et, details))
    return ctx


def _plugin_events():
    store = EventStore(forge_paths.tenant_home(TENANT), tenant_id=TENANT)
    return store.query_events(TENANT, event_type=learning_events.EventType.PLUGIN_EXECUTED,
                              skill_id=f"plugin.{PLUGIN}")


def test_execution_is_audited_and_persisted_as_learning_event():
    ctx = _ctx()
    _emit_plugin_executed(ctx=ctx, plugin_id=PLUGIN, latency_ms=42, success=True)

    assert [et for et, _ in ctx.emitted] == ["plugin.executed"]
    details = ctx.emitted[0][1]
    assert details == {"plugin_id": PLUGIN, "latency_ms": 42, "success": True,
                       "error_type": None, "tenant_id": TENANT}

    events = _plugin_events()
    assert len(events) == 1
    assert events[0].signal["latency_ms"] == 42 and events[0].signal["success"] is True


def test_failure_carries_error_class_only():
    ctx = _ctx()
    _emit_plugin_executed(ctx=ctx, plugin_id=PLUGIN, latency_ms=5, success=False,
                          error_type="TimeoutError")
    assert ctx.emitted[0][1]["error_type"] == "TimeoutError"
    assert _plugin_events()[0].signal["success"] is False


def test_context_without_tenant_records_nothing():
    """Never attribute a plugin run to a guessed (_default) tenant."""
    ctx = _ctx(tenant_id="")
    _emit_plugin_executed(ctx=ctx, plugin_id=PLUGIN, latency_ms=1, success=True)
    assert ctx.emitted == []
    assert _plugin_events() == []


@pytest.mark.parametrize("ctx", [None])
def test_no_context_is_a_noop(ctx):
    _emit_plugin_executed(ctx=ctx, plugin_id=PLUGIN, latency_ms=1, success=True)
