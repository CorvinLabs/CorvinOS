"""Unit tests for Skill invocation models (ADR-0598)."""

import pytest
from datetime import datetime
from core.engine.skill_invocation_models import (
    WorkerEngine,
    SkillInvocationRequest,
    SkillInvocationResponse,
    SkillInvocationTenantError,
)


class TestSkillInvocationRequest:
    """Test request model (immutable, validated)."""

    def test_request_valid(self):
        """Valid request construction."""
        req = SkillInvocationRequest(
            tenant_id="_default",
            skill_id="os.delegation_router",
            skill_version="1.2",
            input={"task_shape": "big_data"},
            engine=WorkerEngine.CLAUDE_CODE,
        )
        assert req.tenant_id == "_default"
        assert req.skill_id == "os.delegation_router"
        assert req.request_id  # Generated UUID

    def test_request_missing_tenant_id(self):
        """tenant_id is required (fail-closed)."""
        with pytest.raises(ValueError, match="tenant_id is required"):
            SkillInvocationRequest(
                tenant_id="",
                skill_id="os.delegation_router",
                skill_version="1.2",
                input={},
                engine=WorkerEngine.HERMES,
            )

    def test_request_missing_engine(self):
        """engine is required."""
        with pytest.raises(ValueError, match="engine is required"):
            SkillInvocationRequest(
                tenant_id="_default",
                skill_id="os.delegation_router",
                skill_version="1.2",
                input={},
                engine=None,
            )

    def test_request_immutable(self):
        """Request is frozen (immutable)."""
        req = SkillInvocationRequest(
            tenant_id="_default",
            skill_id="os.delegation_router",
            skill_version="1.2",
            input={},
            engine=WorkerEngine.CLAUDE_CODE,
        )
        with pytest.raises(AttributeError):
            req.tenant_id = "_other"

    def test_request_input_hash(self):
        """Input hash is deterministic."""
        req1 = SkillInvocationRequest(
            tenant_id="_default",
            skill_id="os.delegation_router",
            skill_version="1.2",
            input={"a": 1, "b": 2},
            engine=WorkerEngine.CLAUDE_CODE,
        )
        req2 = SkillInvocationRequest(
            tenant_id="_default",
            skill_id="os.delegation_router",
            skill_version="1.2",
            input={"b": 2, "a": 1},  # Same content, different order
            engine=WorkerEngine.CLAUDE_CODE,
        )
        # Hash should be same (JSON-sorted)
        assert req1.input_hash() == req2.input_hash()

    def test_request_to_dict(self):
        """Serialize request to dict."""
        req = SkillInvocationRequest(
            tenant_id="_default",
            skill_id="os.delegation_router",
            skill_version="1.2",
            input={"task_shape": "big_data"},
            engine=WorkerEngine.HERMES,
        )
        d = req.to_dict()
        assert d["tenant_id"] == "_default"
        assert d["engine"] == "hermes"
        assert d["input"]["task_shape"] == "big_data"


class TestSkillInvocationResponse:
    """Test response model (immutable, validated)."""

    def test_response_valid(self):
        """Valid response construction."""
        resp = SkillInvocationResponse(
            output={"decision": "native"},
            latency_ms=42,
            phase_completed=10,
        )
        assert resp.output["decision"] == "native"
        assert resp.latency_ms == 42
        assert resp.is_success is True

    def test_response_incomplete_phase(self):
        """Phase < 10 means incomplete."""
        resp = SkillInvocationResponse(
            output={},
            latency_ms=100,
            phase_completed=6,
            error="Timeout at phase 6",
        )
        assert resp.is_success is False

    def test_response_immutable(self):
        """Response is frozen."""
        resp = SkillInvocationResponse(output={}, latency_ms=42)
        with pytest.raises(AttributeError):
            resp.latency_ms = 50

    def test_response_output_hash(self):
        """Output hash is deterministic."""
        resp1 = SkillInvocationResponse(
            output={"decision": "native", "confidence": 0.8},
            latency_ms=42,
        )
        resp2 = SkillInvocationResponse(
            output={"confidence": 0.8, "decision": "native"},  # Reordered
            latency_ms=50,  # Different latency
        )
        # Output hash should match (JSON-sorted, ignoring latency)
        assert resp1.output_hash() == resp2.output_hash()

    def test_response_to_dict(self):
        """Serialize response to dict."""
        resp = SkillInvocationResponse(
            output={"decision": "acs"},
            latency_ms=100,
            phase_completed=10,
        )
        d = resp.to_dict()
        assert d["output"]["decision"] == "acs"
        assert d["latency_ms"] == 100
        assert d["phase_completed"] == 10


class TestWorkerEngine:
    """Test WorkerEngine enum."""

    def test_all_engines(self):
        """All engines are defined."""
        assert WorkerEngine.CLAUDE_CODE
        assert WorkerEngine.HERMES
        assert WorkerEngine.COPILOT
        assert WorkerEngine.OPENCODE

    def test_engine_values(self):
        """Engine values are correct."""
        assert WorkerEngine.CLAUDE_CODE.value == "claude_code"
        assert WorkerEngine.HERMES.value == "hermes"
