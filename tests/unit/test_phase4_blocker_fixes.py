"""Tests for Phase 4 Blocker Fixes (1-4).

Verifies:
1. Delete User Prefix Match Bug — exact match instead of prefix
2. Merge Overwrites Base Fields — validation prevents override of Tier 1 immutable fields
3. Audit Backend Integration — events hash-chained on the tenant CORE chain
4. GDPR Erasure Flow — cascade delete with full audit trail

Adversarial review N-04: the model audits through the REAL core writer
(``core_audit_event``, fail-closed), so every test runs in a sandbox — a valid
tenant id (``tenant_1``; a hyphen is rejected by ``validate_tenant_id``), the
chain redirected to a temp file via ``VOICE_AUDIT_PATH``, ``CORVIN_TENANT_ID``
set (the core writer commits only for the current tenant) and ``CORVIN_HOME``
pointed at a temp dir. The old ``AuditChainWriter`` mocks patched a class that
no longer exists.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.learning.hybrid_context import (
    HybridContextModel,
    ImmutableContextBase,
    InjectedLayer,
    CascadeDeleteResult,
    TIER1_IMMUTABLE_KEYS,
)
from core.learning.erasure_handler import (
    ErasureHandler,
    ErasureRequest,
    ErasureResult,
)

TENANT = "tenant_1"


@pytest.fixture(autouse=True)
def sandbox(tmp_path: Path, monkeypatch):
    """Temp core chain + tenant context + temp CORVIN_HOME (never the live install)."""
    chain = tmp_path / "chain" / "audit.jsonl"
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(chain))
    monkeypatch.setenv("CORVIN_TENANT_ID", TENANT)
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "home"))
    return chain


def _chain_records(chain: Path) -> list[dict]:
    if not chain.exists():
        return []
    return [json.loads(l) for l in chain.read_text().splitlines() if l.strip()]


def _records_of(chain: Path, event_type: str) -> list[dict]:
    return [r for r in _chain_records(chain) if r.get("event_type") == event_type]


def _snapshot(model: HybridContextModel, user_id: str = "alice", session_id: str = "s1", **kw) -> str:
    base = dict(decisions=[], profile={}, success_rate=0.5, attention_budget=1000)
    base.update(kw)
    return model.snapshot_base_context(user_id=user_id, session_id=session_id, **base)


class TestBlocker1DeleteUserPrefixMatch:
    """Blocker #1: Delete User Prefix Match Bug — exact match instead of prefix."""

    def test_delete_exact_user_id_match(self):
        """Delete user 'alice' should NOT delete 'alice2'."""
        model = HybridContextModel(TENANT)
        _snapshot(model, "alice")
        _snapshot(model, "alice2")
        assert "alice:s1" in model.base_snapshots
        assert "alice2:s1" in model.base_snapshots

        result = model.delete_user_context("alice")

        assert "alice:s1" not in model.base_snapshots
        assert "alice2:s1" in model.base_snapshots  # Still there!
        assert result.deleted_bases == 1
        assert result.verification_complete is True

    def test_delete_multiple_sessions_same_user(self):
        """Delete user should cascade across all sessions."""
        model = HybridContextModel(TENANT)
        for session_id in ["s1", "s2", "s3"]:
            _snapshot(model, "alice", session_id)
        assert {"alice:s1", "alice:s2", "alice:s3"} <= set(model.base_snapshots)

        result = model.delete_user_context("alice")

        assert result.deleted_bases == 3
        assert not any(k.startswith("alice:") for k in model.base_snapshots)
        assert result.verification_complete is True


class TestBlocker2MergeOverwritesBase:
    """Blocker #2: Merge Overwrites Base Fields — validation prevents Tier 1 override."""

    def test_merge_rejects_layer_overwriting_tier1_user_id(self):
        """Merge rejects layer that tries to override user_id (immutable)."""
        model = HybridContextModel(TENANT)
        _snapshot(model, "alice")
        base = model.base_snapshots["alice:s1"]

        layers = [{"layer_name": "malicious", "data": {"user_id": "bob", "extra": "x"}}]
        merged = model.merge_with_fallback(base, layers)

        assert "malicious" not in merged
        assert merged.get("user_id", base.user_id) == "alice"

    def test_merge_rejects_layer_overwriting_tenant_id(self):
        """Merge rejects layer that tries to override tenant_id (immutable)."""
        model = HybridContextModel(TENANT)
        _snapshot(model, "alice")
        base = model.base_snapshots["alice:s1"]

        layers = [{"layer_name": "attack", "data": {"tenant_id": "evil_tenant"}}]
        merged = model.merge_with_fallback(base, layers)

        assert "attack" not in merged
        assert merged.get("tenant_id", base.tenant_id) == TENANT

    def test_merge_allows_valid_layer(self):
        """Merge allows a layer with valid (non-Tier1) fields and an intact chain."""
        model = HybridContextModel(TENANT)
        _snapshot(model, "alice")
        base = model.base_snapshots["alice:s1"]

        # A layer injected through the model carries a verifiable hash + chain link.
        model.inject_layer(
            user_id="alice", layer_name="user_style",
            data={"preference": "concise", "tone": "friendly"}, lom="test.py:L1",
        )
        layers = [
            {"layer_name": l.layer_name, "version": l.version, "data": l.data,
             "hash": l.hash, "prev_hash": l.prev_hash}
            for l in model.injected_layers["alice"]
        ]

        merged = model.merge_with_fallback(base, layers)

        assert "user_style" in merged
        assert merged["user_style"]["preference"] == "concise"
        assert merged["user_style"]["tone"] == "friendly"

    def test_merge_rejects_all_tier1_immutable_fields(self):
        """Merge rejects attempts to override ANY Tier1 immutable field."""
        model = HybridContextModel(TENANT)
        _snapshot(model, "alice", decisions=[{"d1": 1}], profile={"pref": "x"},
                  success_rate=0.8, attention_budget=2000)
        base = model.base_snapshots["alice:s1"]

        for immutable_field in TIER1_IMMUTABLE_KEYS:
            layer = {"layer_name": "attack", "data": {immutable_field: "hijacked"}}
            merged = model.merge_with_fallback(base, [layer])
            assert "attack" not in merged
            if immutable_field in base.__dataclass_fields__:
                original_value = getattr(base, immutable_field)
                merged_value = merged.get(immutable_field)
                if merged_value is not None:
                    assert merged_value == original_value

    def test_layer_named_like_a_tier1_field_is_rejected(self):
        """L-03: the layer NAME must not shadow a Tier 1 field either."""
        model = HybridContextModel(TENANT)
        _snapshot(model, "alice")
        with pytest.raises(ValueError):
            model.inject_layer(user_id="alice", layer_name="user_profile",
                               data={"style": "EVIL"}, lom="test.py:L1")


class TestBlocker3AuditBackendIntegration:
    """Blocker #3: Audit Backend Integration — events hash-chained on the CORE chain."""

    def test_inject_layer_writes_audit_event(self, sandbox):
        model = HybridContextModel(TENANT)
        layer_hash = model.inject_layer(
            user_id="alice", layer_name="user_style",
            data={"preference": "concise"}, lom="test.py:L42:test",
        )

        recs = _records_of(sandbox, "tier2_layer_injected")
        assert len(recs) == 1
        rec = recs[0]
        assert "hash" in rec, "record must be hash-chained"
        assert rec["details"]["tenant_id"] == TENANT
        assert rec["details"]["user"] == "alice"
        details = rec["details"]
        assert details["layer_name"] == "user_style"
        assert details["hash"] == layer_hash
        assert details["lom"] == "test.py:L42:test"
        assert "concise" not in sandbox.read_text(), "layer CONTENT never reaches the chain"

    def test_snapshot_base_writes_audit_event(self, sandbox):
        model = HybridContextModel(TENANT)
        base_hash = _snapshot(model, "alice", decisions=[{"d1": 1}], profile={"pref": "x"},
                              success_rate=0.75)

        recs = _records_of(sandbox, "tier1_base_snapshotted")
        assert len(recs) == 1
        rec = recs[0]
        assert rec["details"]["tenant_id"] == TENANT
        assert rec["details"]["user"] == "alice"
        assert rec["details"]["base_hash"] == base_hash
        assert rec["details"]["session_id"] == "s1"
        assert rec["details"]["decisions_count"] == 1

    def test_merge_writes_audit_event(self, sandbox):
        model = HybridContextModel(TENANT)
        _snapshot(model, "alice")
        base = model.base_snapshots["alice:s1"]
        model.inject_layer(user_id="alice", layer_name="user_style",
                           data={"preference": "concise"}, lom="test.py:L1")
        layers = [
            {"layer_name": l.layer_name, "version": l.version, "data": l.data,
             "hash": l.hash, "prev_hash": l.prev_hash}
            for l in model.injected_layers["alice"]
        ]

        model.merge_with_fallback(base, layers)

        recs = _records_of(sandbox, "hybrid_context_merge")
        assert len(recs) == 1
        assert recs[0]["details"]["total_layers"] == 1
        assert recs[0]["details"]["failed_count"] == 0
        assert recs[0]["details"]["tenant_id"] == TENANT

    def test_audit_unavailable_is_fail_closed(self, sandbox, monkeypatch):
        """No core writer → the layer is NOT stored (ADR-0232/0233)."""
        from core.learning import event_persistence

        def _boom():
            raise RuntimeError("core audit writer unavailable")

        monkeypatch.setattr(event_persistence, "_resolve_core_audit", _boom)
        model = HybridContextModel(TENANT)
        with pytest.raises(RuntimeError):
            model.inject_layer(user_id="alice", layer_name="x", data={"a": 1}, lom="t")
        assert "alice" not in model.injected_layers

    def test_context_selector_writes_audit_event(self, tmp_path):
        """ContextSelectorSkill writes ``skill.executed`` to the tenant core chain
        (``<CORVIN_HOME>/tenants/<t>/global/forge/audit.jsonl``, via emit_skill_audit)."""
        from core.skills.os_skills.context_selector import ContextSelectorSkill

        skill = ContextSelectorSkill(tenant_id=TENANT)
        decision = skill.execute(task_type="compliance", user_id="alice", time_budget_ms=1000)
        assert decision.quality_mode is not None

        chain = tmp_path / "home" / "tenants" / TENANT / "global" / "forge" / "audit.jsonl"
        assert chain.exists(), "skill audit must land on the tenant core chain under CORVIN_HOME"
        recs = [json.loads(l) for l in chain.read_text().splitlines() if l.strip()]
        hits = [r for r in recs if r.get("event_type") == "skill.executed"
                and "os.context_selector" in json.dumps(r)]
        assert hits, recs


class TestBlocker4GDPRErasureFlow:
    """Blocker #4: GDPR Erasure Flow — cascade delete with audit trail."""

    def test_erasure_cascades_tier1_and_tier2(self):
        model = HybridContextModel(TENANT)
        _snapshot(model, "alice")
        for i in range(3):
            model.inject_layer(user_id="alice", layer_name=f"layer_{i}",
                               data={"data": i}, lom="test.py:L1")
        assert "alice:s1" in model.base_snapshots
        assert len(model.injected_layers["alice"]) == 3

        handler = ErasureHandler()
        request = ErasureRequest(user_id="alice", tenant_id=TENANT,
                                 requested_at=datetime.utcnow(), reason="user_request")
        result = handler.process_erasure(request, hybrid_context=model)

        assert result.tier1_deleted is True
        assert result.tier1_count == 1
        assert result.tier2_deleted is True
        assert result.tier2_count == 3
        assert "alice:s1" not in model.base_snapshots
        assert "alice" not in model.injected_layers

    def test_erasure_writes_audit_event(self, sandbox):
        handler = ErasureHandler()
        request = ErasureRequest(user_id="alice", tenant_id=TENANT,
                                 requested_at=datetime.utcnow(), reason="user_request",
                                 requestor_id="admin")
        result = handler.process_erasure(request)

        assert result.audit_logged is True
        recs = _records_of(sandbox, "user_context_erasure_cascade_complete")
        assert len(recs) == 1
        rec = recs[0]
        assert rec["details"]["tenant_id"] == TENANT
        assert rec["details"]["user"] == "alice"
        assert rec["details"]["reason"] == "user_request"
        assert rec["details"]["requestor_id"] == "admin"
        # No backend supplied → tiers reported as skipped, NOT as erased (L-13e)
        assert set(rec["details"]["skipped"]) >= {"tier1", "tier2"}
        assert rec["details"]["complete"] is False
        assert result.success is False

    def test_erasure_invalidates_cache(self):
        mock_cache = MagicMock()
        handler = ErasureHandler()
        request = ErasureRequest(user_id="alice", tenant_id=TENANT,
                                 requested_at=datetime.utcnow(), reason="user_request")
        result = handler.process_erasure(request, cache_backend=mock_cache)

        mock_cache.delete.assert_called_with(f"context:cache:{TENANT}:alice")
        mock_cache.publish.assert_called_with(f"cache:invalidation:{TENANT}", "user_id=alice")
        assert result.cache_invalidated is True

    def test_erasure_result_success_property(self):
        result_success = ErasureResult(
            user_id="alice", tenant_id=TENANT, tier1_deleted=True, tier1_count=1,
            tier2_deleted=True, tier2_count=3, cache_invalidated=True, audit_logged=True, errors=[],
        )
        assert result_success.success is True

        result_fail = ErasureResult(
            user_id="alice", tenant_id=TENANT, tier1_deleted=True, tier1_count=1,
            tier2_deleted=False, tier2_count=0, cache_invalidated=True, audit_logged=True,
            errors=["Tier2 delete failed"],
        )
        assert result_fail.success is False

    def test_invalid_tenant_id_is_rejected(self):
        """A hyphenated tenant id (the old ``tenant-1``) never reaches the chain."""
        with pytest.raises(Exception):
            HybridContextModel("tenant-1").inject_layer(
                user_id="alice", layer_name="x", data={"a": 1}, lom="t")
