"""
Phase B E2E: REAL Compat Layer Tests (ADR-0538 measured deletion)

Proves, against the REAL core hash-chained audit writer (isolated to tmp by
``tests/conftest.py``):
1. Old Brain/Vibe APIs are callable (backward compatible)
2. New Skills are invoked transparently (only the Skill is mocked)
3. Every call lands as a ``deprecated_api_call`` record on the core chain,
   tenant-scoped, CONTENT-FREE (no stack trace, no user id)
4. Fail-closed on error (exception propagates, the error is still audited)
5. Timeout is enforced (no hangs)

Rewritten 2026-09-07: the previous version patched
``core.telemetry.deprecated_api_calls.get_audit_writer`` — a name that never
existed — so every test errored at patch time and nothing here was ever proven.
The Phase C gates (``core/compliance/phase_c_gates/*``) read exactly the chain
records asserted below.
"""

import json
import os
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from core.legacy_compat.brain_compat import get_session_context, recall_recent_sessions
from core.legacy_compat.vibe_compat import VibeBrainAdapter, delegate_to_persona
from core.learning.event_persistence import _resolve_core_audit
from core.telemetry.deprecated_api_calls import DeprecatedAPIEvent

EVENT = "deprecated_api_call"


def _chain_path() -> Path:
    return Path(_resolve_core_audit().audit_path())


def _chain_records(event_type: str = EVENT) -> list[dict]:
    path = _chain_path()
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("event_type") == event_type or rec.get("event") == event_type:
            out.append(rec)
    return out


def _details(rec: dict) -> dict:
    return rec.get("details") or {}


def _tenant_of(rec: dict) -> str:
    """The core writer stamps ``tenant_id`` INSIDE ``details`` (audit.py)."""
    return _details(rec).get("tenant_id", "")


@pytest.fixture
def chain_before() -> int:
    """Records already on the (tmp) chain before the call under test."""
    assert "VOICE_AUDIT_PATH" in os.environ, "conftest must isolate the chain"
    return len(_chain_records())


@pytest.fixture
def bind_tenant(monkeypatch):
    """Bind the PROCESS tenant the way a deployment does (``CORVIN_TENANT_ID``).

    The core chain refuses a record tagged with any tenant other than the
    process tenant (``forge.security_events._current_tenant_id``, ADR-0007) —
    a compat call for tenant X is therefore only auditable, hence only
    executable, inside tenant X's process. ``bind_tenant("tenant_a")`` is the
    real contract, not a test convenience.
    """

    def _bind(tenant_id: str) -> str:
        monkeypatch.setenv("CORVIN_TENANT_ID", tenant_id)
        return tenant_id

    return _bind


def _new_records(chain_before: int) -> list[dict]:
    recs = _chain_records()
    assert len(recs) > chain_before, "deprecated call must reach the core chain"
    return recs[chain_before:]


class TestPhaseBAuditTrailReal:
    """Real audit-trail integration (brain compat)."""

    @patch("core.legacy_compat.brain_compat.ContextAdapterSkill")
    def test_brain_compat_writes_audit_event(self, mock_skill_class, chain_before, bind_tenant):
        bind_tenant("tenant_a")
        mock_skill = Mock()
        mock_skill.execute.return_value = {
            "base_tier": {"user_id": "test_123"},
            "injected_tier": {"context": {"data": "value"}},
            "merged_tier": {"user_id": "test_123", "context": {"data": "value"}},
        }
        mock_skill_class.return_value = mock_skill

        result = get_session_context(task_id="test_123", tenant_id="tenant_a")

        rec = _new_records(chain_before)[-1]
        assert _tenant_of(rec) == "tenant_a"
        d = _details(rec)
        assert d["api_name"] == "get_session_context"
        assert d["module"] == "core.brain.conversation_recall"
        assert d["task_id"] == "test_123"
        assert d["failed"] is False
        assert d.get("audit_ref"), "chain record must carry its audit_ref"

        assert mock_skill.execute.called
        assert isinstance(result, dict) and "base_tier" in result

    @patch("core.legacy_compat.brain_compat.ContextAdapterSkill")
    def test_brain_compat_error_propagates_fails_closed(self, mock_skill_class, chain_before):
        mock_skill = Mock()
        mock_skill.execute.side_effect = RuntimeError("Skill call failed")
        mock_skill_class.return_value = mock_skill

        with pytest.raises(RuntimeError, match="Skill call failed"):
            get_session_context(task_id="test_123")

        new = _new_records(chain_before)
        assert len(new) == 2, "call record + error record"
        assert _details(new[0])["failed"] is False
        assert _details(new[1])["failed"] is True
        assert _details(new[1])["api_name"] == "get_session_context"

    @patch("core.legacy_compat.brain_compat.ContextAdapterSkill")
    @patch("core.legacy_compat.brain_compat.skill_call_timeout")
    def test_brain_compat_timeout_enforced(self, mock_timeout, mock_skill_class):
        mock_timeout.return_value.__enter__ = Mock()
        mock_timeout.return_value.__exit__ = Mock(return_value=None)
        mock_skill = Mock()
        mock_skill.execute.return_value = {"data": "test"}
        mock_skill_class.return_value = mock_skill

        get_session_context(task_id="test_123")

        assert mock_timeout.called, "skill_call_timeout must guard the Skill call"
        assert mock_timeout.call_args[1]["seconds"] == 5

    @patch("core.legacy_compat.brain_compat.ContextAdapterSkill")
    def test_brain_compat_tenant_scoped(self, mock_skill_class, chain_before, bind_tenant):
        bind_tenant("tenant_b")
        mock_skill = Mock()
        mock_skill.execute.return_value = {"data": "test"}
        mock_skill_class.return_value = mock_skill

        get_session_context(task_id="test_123", tenant_id="tenant_b", user_id="user_xyz")

        rec = _new_records(chain_before)[-1]
        assert _tenant_of(rec) == "tenant_b"
        # CONTENT-FREE: no user id, no stack trace on the chain.
        serialized = json.dumps(rec)
        assert "user_xyz" not in serialized
        assert "stack_trace" not in serialized

    @patch("core.legacy_compat.brain_compat.ContextAdapterSkill")
    def test_foreign_tenant_fails_closed_before_skill_runs(self, mock_skill_class, chain_before, bind_tenant):
        """Process tenant A, call for tenant B: refused at the chain chokepoint,
        surfaced as RuntimeError, and the Skill is NEVER executed (ADR-0007)."""
        bind_tenant("tenant_a")
        mock_skill = Mock()
        mock_skill.execute.return_value = {"data": "test"}
        mock_skill_class.return_value = mock_skill

        with pytest.raises(RuntimeError, match="did not commit"):
            get_session_context(task_id="t", tenant_id="tenant_b")

        assert not mock_skill.execute.called
        assert all(_tenant_of(r) != "tenant_b" for r in _chain_records()[chain_before:])

    def test_deprecated_api_event_immutable(self):
        event = DeprecatedAPIEvent(
            timestamp="2026-09-03T00:00:00Z",
            api_name="test",
            module="test.module",
            caller_file="test.py",
            caller_line=1,
            caller_func="test_func",
            stack_trace="",
            tenant_id="_default",
        )
        with pytest.raises(Exception):  # FrozenInstanceError
            event.timestamp = "2026-09-04T00:00:00Z"


class TestPhaseCAuditChainReadiness:
    """The Phase C gates need: event_type, tenant_id, timestamp, details."""

    @patch("core.legacy_compat.brain_compat.ContextAdapterSkill")
    def test_audit_events_contain_required_fields(self, mock_skill, chain_before, bind_tenant):
        bind_tenant("test_tenant")
        mock_skill.return_value.execute.return_value = {"data": "test"}
        get_session_context(task_id="gate_test", tenant_id="test_tenant")

        rec = _new_records(chain_before)[-1]
        assert _tenant_of(rec) == "test_tenant"
        assert rec.get("ts"), "gate needs a timestamp"
        assert rec.get("hash"), "gate needs a chained record"
        d = _details(rec)
        for key in ("api_name", "module", "caller_file", "caller_line", "caller_func", "failed"):
            assert key in d, f"gate needs details.{key}"


class TestPhaseBAuditToJsonl:
    def test_audit_event_serializable_to_jsonl(self):
        event = DeprecatedAPIEvent(
            timestamp="2026-09-03T12:00:00Z",
            api_name="get_session_context",
            module="core.brain.conversation_recall",
            caller_file="my_code.py",
            caller_line=42,
            caller_func="my_func",
            stack_trace="...",
            tenant_id="tenant_x",
            task_id="task_001",
            user_id="user_scrubbed",
        )
        parsed = json.loads(json.dumps(event.to_dict()))
        assert parsed["api_name"] == "get_session_context"
        assert parsed["tenant_id"] == "tenant_x"
        assert "stack_trace" in parsed


class TestPhaseBAuditTrailRecallSessions:
    @patch("core.legacy_compat.brain_compat.ContextAdapterSkill")
    def test_recall_recent_sessions_writes_audit_event(self, mock_skill_class, chain_before, bind_tenant):
        bind_tenant("tenant_a")
        mock_skill = Mock()
        mock_skill.execute.return_value = {
            "sessions": [
                {"id": "sess_1", "timestamp": "2026-09-03T10:00:00Z"},
                {"id": "sess_2", "timestamp": "2026-09-03T11:00:00Z"},
            ]
        }
        mock_skill_class.return_value = mock_skill

        result = recall_recent_sessions(user_id="user_123", limit=2, tenant_id="tenant_a")

        assert isinstance(result, list) and len(result) == 2
        assert result[0]["id"] == "sess_1"
        rec = _new_records(chain_before)[-1]
        assert _tenant_of(rec) == "tenant_a"
        assert _details(rec)["api_name"] == "recall_recent_sessions"
        assert "user_123" not in json.dumps(rec)


class TestPhaseBAuditTrailDelegateToPersona:
    @patch("core.legacy_compat.vibe_compat.DelegationRouterSkill")
    def test_delegate_to_persona_writes_audit_event(self, mock_skill_class, chain_before, bind_tenant):
        bind_tenant("tenant_b")
        mock_skill = Mock()
        mock_skill.execute.return_value = {
            "engine_id": "opus",
            "routing_decision": "complex_task",
            "confidence": 0.95,
        }
        mock_skill_class.return_value = mock_skill

        result = delegate_to_persona(request={"prompt": "test"}, task_type="complex", tenant_id="tenant_b")

        assert result == "opus"
        rec = _new_records(chain_before)[-1]
        assert _tenant_of(rec) == "tenant_b"
        assert _details(rec)["api_name"] == "delegate_to_persona"
        assert _details(rec)["module"] == "core.vibe_engineering.routing"


class TestPhaseBAuditTrailVibeBrainAdapter:
    @patch("core.legacy_compat.vibe_compat.DelegationRouterSkill")
    def test_vibe_brain_adapter_do_decide_writes_audit_event(self, mock_skill_class, chain_before, bind_tenant):
        bind_tenant("tenant_c")
        mock_skill = Mock()
        mock_skill.execute.return_value = {
            "decision": "route_to_agent_a",
            "confidence": 0.92,
            "reasoning": "complex task detected",
        }
        mock_skill_class.return_value = mock_skill

        adapter = VibeBrainAdapter(tenant_id="tenant_c")
        result = adapter.do_decide(task_context={"type": "complex"})

        assert result == "route_to_agent_a"
        rec = _new_records(chain_before)[-1]
        assert _tenant_of(rec) == "tenant_c"
        assert _details(rec)["api_name"] == "VibeBrainAdapter.do_decide"
