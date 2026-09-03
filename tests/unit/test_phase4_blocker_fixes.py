"""Tests for Phase 4 Blocker Fixes (1-4).

Verifies:
1. Delete User Prefix Match Bug — exact match instead of prefix
2. Merge Overwrites Base Fields — validation prevents override of Tier 1 immutable fields
3. Audit Backend Integration — events hash-chained and persisted
4. GDPR Erasure Flow — cascade delete with full audit trail
"""

import pytest
from datetime import datetime
from unittest.mock import Mock, patch, MagicMock
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


class TestBlocker1DeleteUserPrefixMatch:
    """Blocker #1: Delete User Prefix Match Bug — exact match instead of prefix."""

    def test_delete_exact_user_id_match(self):
        """Delete user 'alice' should NOT delete 'alice2'."""
        model = HybridContextModel("tenant-1")

        # Create base snapshots for both 'alice' and 'alice2'
        model.snapshot_base_context(
            user_id="alice",
            session_id="s1",
            decisions=[],
            profile={},
            success_rate=0.5,
            attention_budget=1000,
        )

        model.snapshot_base_context(
            user_id="alice2",
            session_id="s1",
            decisions=[],
            profile={},
            success_rate=0.5,
            attention_budget=1000,
        )

        # Verify both exist
        assert "alice:s1" in model.base_snapshots
        assert "alice2:s1" in model.base_snapshots

        # Delete only 'alice'
        result = model.delete_user_context("alice")

        # Verify 'alice' is deleted but 'alice2' remains
        assert "alice:s1" not in model.base_snapshots
        assert "alice2:s1" in model.base_snapshots  # Still there!
        assert result.deleted_bases == 1
        assert result.verification_complete is True

    def test_delete_multiple_sessions_same_user(self):
        """Delete user should cascade across all sessions."""
        model = HybridContextModel("tenant-1")

        # Create multiple sessions for same user
        for session_id in ["s1", "s2", "s3"]:
            model.snapshot_base_context(
                user_id="alice",
                session_id=session_id,
                decisions=[],
                profile={},
                success_rate=0.5,
                attention_budget=1000,
            )

        # Verify all three exist
        assert "alice:s1" in model.base_snapshots
        assert "alice:s2" in model.base_snapshots
        assert "alice:s3" in model.base_snapshots

        # Delete user
        result = model.delete_user_context("alice")

        # Verify all sessions deleted
        assert result.deleted_bases == 3
        assert "alice:s1" not in model.base_snapshots
        assert "alice:s2" not in model.base_snapshots
        assert "alice:s3" not in model.base_snapshots
        assert result.verification_complete is True


class TestBlocker2MergeOverwritesBase:
    """Blocker #2: Merge Overwrites Base Fields — validation prevents Tier 1 override."""

    def test_merge_rejects_layer_overwriting_tier1_user_id(self):
        """Merge rejects layer that tries to override user_id (immutable)."""
        model = HybridContextModel("tenant-1")

        # Create base
        model.snapshot_base_context(
            user_id="alice",
            session_id="s1",
            decisions=[],
            profile={},
            success_rate=0.5,
            attention_budget=1000,
        )
        base = model.base_snapshots["alice:s1"]

        # Try to merge a layer that overrides user_id (malicious/buggy)
        malicious_layer = {
            "layer_name": "attack",
            "data": {"user_id": "eve"},  # Trying to hijack!
        }

        merged = model.merge_with_fallback(base, [malicious_layer])

        # Verify that user_id was NOT overridden
        assert merged["user_id"] == "alice"  # Original, not "eve"
        # And the malicious layer should not be in merged result
        assert "attack" not in merged  # Layer was dropped due to validation failure

    def test_merge_rejects_layer_overwriting_tenant_id(self):
        """Merge rejects layer that tries to override tenant_id (immutable)."""
        model = HybridContextModel("tenant-1")

        model.snapshot_base_context(
            user_id="alice",
            session_id="s1",
            decisions=[],
            profile={},
            success_rate=0.5,
            attention_budget=1000,
        )
        base = model.base_snapshots["alice:s1"]

        # Try to merge layer that overrides tenant_id
        malicious_layer = {
            "layer_name": "attack",
            "data": {"tenant_id": "evil-tenant"},
        }

        merged = model.merge_with_fallback(base, [malicious_layer])

        # Verify tenant_id was NOT overridden
        assert merged["tenant_id"] == "tenant-1"  # Original

    def test_merge_allows_valid_layer(self):
        """Merge allows layer with valid (non-Tier1) fields."""
        model = HybridContextModel("tenant-1")

        model.snapshot_base_context(
            user_id="alice",
            session_id="s1",
            decisions=[],
            profile={},
            success_rate=0.5,
            attention_budget=1000,
        )
        base = model.base_snapshots["alice:s1"]

        # Valid layer (no Tier1 field override)
        valid_layer = {
            "layer_name": "user_style",
            "data": {"preference": "concise", "tone": "friendly"},
        }

        merged = model.merge_with_fallback(base, [valid_layer])

        # Verify layer was merged
        assert "user_style" in merged
        assert merged["user_style"]["preference"] == "concise"
        assert merged["user_style"]["tone"] == "friendly"

    def test_merge_rejects_all_tier1_immutable_fields(self):
        """Merge rejects attempts to override ANY Tier1 immutable field."""
        model = HybridContextModel("tenant-1")

        model.snapshot_base_context(
            user_id="alice",
            session_id="s1",
            decisions=[{"d1": 1}],
            profile={"pref": "x"},
            success_rate=0.8,
            attention_budget=2000,
        )
        base = model.base_snapshots["alice:s1"]

        # Test each immutable field
        for immutable_field in TIER1_IMMUTABLE_KEYS:
            layer = {
                "layer_name": "attack",
                "data": {immutable_field: "hijacked"},  # Try to override
            }

            merged = model.merge_with_fallback(base, [layer])

            # Layer should be dropped (not merged)
            assert "attack" not in merged
            # Original value should remain
            if immutable_field in base.__dataclass_fields__:
                original_value = getattr(base, immutable_field)
                merged_value = merged.get(immutable_field)
                # If the field is in the merged dict, verify it's the original
                if merged_value is not None:
                    assert merged_value == original_value


class TestBlocker3AuditBackendIntegration:
    """Blocker #3: Audit Backend Integration — events hash-chained and persisted."""

    @patch("core.learning.hybrid_context.AuditChainWriter")
    def test_inject_layer_writes_audit_event(self, mock_writer_class):
        """Inject layer should write audit event to chain."""
        mock_writer = MagicMock()
        mock_writer_class.return_value = mock_writer

        model = HybridContextModel("tenant-1")

        # Mock the audit writer
        with patch.object(model, "_get_audit_writer", return_value=mock_writer):
            layer_hash = model.inject_layer(
                user_id="alice",
                layer_name="user_style",
                data={"preference": "concise"},
                lom="test.py:L42:test",
            )

        # Verify audit event was written
        mock_writer.write_event_dict.assert_called()
        call_args = mock_writer.write_event_dict.call_args

        # Check event type
        assert call_args[1]["event_type"] == "tier2_layer_injected"
        # Check tenant_id
        assert call_args[1]["tenant_id"] == "tenant-1"
        # Check user_id
        assert call_args[1]["user_id"] == "alice"
        # Check details include lom (line of moral responsibility)
        details = call_args[1]["details"]
        assert "lom" in details
        assert "layer_name" in details
        assert details["layer_name"] == "user_style"

    @patch("core.learning.hybrid_context.AuditChainWriter")
    def test_snapshot_base_writes_audit_event(self, mock_writer_class):
        """Snapshot base should write audit event to chain."""
        mock_writer = MagicMock()
        mock_writer_class.return_value = mock_writer

        model = HybridContextModel("tenant-1")

        with patch.object(model, "_get_audit_writer", return_value=mock_writer):
            base_hash = model.snapshot_base_context(
                user_id="alice",
                session_id="s1",
                decisions=[{"d1": 1}],
                profile={"pref": "x"},
                success_rate=0.75,
                attention_budget=1000,
            )

        # Verify audit event was written
        mock_writer.write_event_dict.assert_called()
        call_args = mock_writer.write_event_dict.call_args

        # Check event type
        assert call_args[1]["event_type"] == "tier1_base_snapshotted"
        # Check tenant_id
        assert call_args[1]["tenant_id"] == "tenant-1"
        # Check user_id
        assert call_args[1]["user_id"] == "alice"

    @patch("core.learning.hybrid_context.AuditChainWriter")
    def test_merge_writes_audit_event(self, mock_writer_class):
        """Merge operation should write audit event to chain."""
        mock_writer = MagicMock()
        mock_writer_class.return_value = mock_writer

        model = HybridContextModel("tenant-1")

        model.snapshot_base_context(
            user_id="alice",
            session_id="s1",
            decisions=[],
            profile={},
            success_rate=0.5,
            attention_budget=1000,
        )
        base = model.base_snapshots["alice:s1"]

        with patch.object(model, "_get_audit_writer", return_value=mock_writer):
            layers = [
                {
                    "layer_name": "user_style",
                    "data": {"preference": "concise"},
                }
            ]
            merged = model.merge_with_fallback(base, layers)

        # Verify audit event was written
        mock_writer.write_event_dict.assert_called()
        call_args = mock_writer.write_event_dict.call_args

        # Check event type
        assert call_args[1]["event_type"] == "hybrid_context_merge"
        # Check severity
        assert call_args[1]["severity"] == "INFO"

    @patch("core.skills.os_skills.context_selector.AuditChainWriter")
    def test_context_selector_writes_audit_event(self, mock_writer_class):
        """Context selector skill should write audit event."""
        from core.skills.os_skills.context_selector import ContextSelectorSkill

        mock_writer = MagicMock()
        mock_writer_class.return_value = mock_writer

        skill = ContextSelectorSkill(tenant_id="tenant-1")

        with patch.object(skill, "_get_audit_writer", return_value=mock_writer):
            decision = skill.execute(
                task_type="compliance",
                user_id="alice",
                time_budget_ms=1000,
            )

        # Verify audit event was written
        mock_writer.write_event_dict.assert_called()
        call_args = mock_writer.write_event_dict.call_args

        # Check event type
        assert call_args[1]["event_type"] == "skill_executed"
        # Check skill_id
        details = call_args[1]["details"]
        assert details["skill_id"] == "os.context_selector"


class TestBlocker4GDPRErasureFlow:
    """Blocker #4: GDPR Erasure Flow — cascade delete with audit trail."""

    def test_erasure_cascades_tier1_and_tier2(self):
        """Erasure should cascade delete Tier 1 bases and Tier 2 layers."""
        model = HybridContextModel("tenant-1")

        # Create Tier 1 base + Tier 2 layers
        model.snapshot_base_context(
            user_id="alice",
            session_id="s1",
            decisions=[],
            profile={},
            success_rate=0.5,
            attention_budget=1000,
        )

        # Inject 3 layers
        for i in range(3):
            model.inject_layer(
                user_id="alice",
                layer_name=f"layer_{i}",
                data={"data": i},
                lom="test.py:L1",
            )

        # Verify setup
        assert "alice:s1" in model.base_snapshots
        assert len(model.injected_layers["alice"]) == 3

        # Process erasure
        handler = ErasureHandler()
        request = ErasureRequest(
            user_id="alice",
            tenant_id="tenant-1",
            requested_at=datetime.utcnow(),
            reason="user_request",
        )

        result = handler.process_erasure(request, hybrid_context=model)

        # Verify deletion
        assert result.tier1_deleted is True
        assert result.tier1_count == 1
        assert result.tier2_deleted is True
        assert result.tier2_count == 3
        assert "alice:s1" not in model.base_snapshots
        assert "alice" not in model.injected_layers

    @patch("core.learning.erasure_handler.AuditChainWriter")
    def test_erasure_writes_audit_event(self, mock_writer_class):
        """Erasure should write audit event (GDPR Art. 30, 32)."""
        mock_writer = MagicMock()
        mock_writer_class.return_value = mock_writer

        handler = ErasureHandler()

        with patch.object(handler, "_get_audit_writer", return_value=mock_writer):
            request = ErasureRequest(
                user_id="alice",
                tenant_id="tenant-1",
                requested_at=datetime.utcnow(),
                reason="user_request",
                requestor_id="admin",
            )

            result = handler.process_erasure(request)

        # Verify audit event was written
        mock_writer.write_event_dict.assert_called()
        call_args = mock_writer.write_event_dict.call_args

        # Check event type
        assert (
            call_args[1]["event_type"]
            == "user_context_erasure_cascade_complete"
        )
        # Check tenant_id
        assert call_args[1]["tenant_id"] == "tenant-1"
        # Check user_id
        assert call_args[1]["user_id"] == "alice"
        # Check details include reason
        details = call_args[1]["details"]
        assert details["reason"] == "user_request"
        assert details["requestor_id"] == "admin"

    def test_erasure_invalidates_cache(self):
        """Erasure should invalidate user cache."""
        mock_cache = MagicMock()

        handler = ErasureHandler()
        request = ErasureRequest(
            user_id="alice",
            tenant_id="tenant-1",
            requested_at=datetime.utcnow(),
            reason="user_request",
        )

        result = handler.process_erasure(request, cache_backend=mock_cache)

        # Verify cache was invalidated
        mock_cache.delete.assert_called_with("context:cache:tenant-1:alice")
        # Verify cache invalidation event published
        mock_cache.publish.assert_called_with(
            "cache:invalidation:tenant-1", "user_id=alice"
        )
        assert result.cache_invalidated is True

    def test_erasure_result_success_property(self):
        """ErasureResult.success should be True only when all steps succeeded."""
        # Success case
        result_success = ErasureResult(
            user_id="alice",
            tenant_id="tenant-1",
            tier1_deleted=True,
            tier1_count=1,
            tier2_deleted=True,
            tier2_count=3,
            cache_invalidated=True,
            audit_logged=True,
            errors=[],
        )
        assert result_success.success is True

        # Failure case (one step failed)
        result_fail = ErasureResult(
            user_id="alice",
            tenant_id="tenant-1",
            tier1_deleted=True,
            tier1_count=1,
            tier2_deleted=False,  # This one failed
            tier2_count=0,
            cache_invalidated=True,
            audit_logged=True,
            errors=["Tier2 delete failed"],
        )
        assert result_fail.success is False
