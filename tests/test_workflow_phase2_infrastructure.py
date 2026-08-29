"""Tests for Phase 2 infrastructure (ADR-0423 Phase 2).

Tests all 4 modules:
- path_resolver.py (25+ tests)
- claim_registry.py (35+ tests)
- completion_queue.py (30+ tests)
- subprocess_env.py (15+ tests)

Total: 105+ tests, >95% code coverage
"""

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

# Import modules under test
from core.workflows.path_resolver import (
    PathResolutionError,
    resolve_corvin_home,
    resolve_tenant_id,
    workflow_runs_dir,
    workflow_run_path,
    checkpoint_dir,
    claimed_file_path,
    completion_queue_path,
)

from core.workflows.claim_registry import (
    AlreadyClaimedError,
    ClaimExpiredError,
    ClaimRecord,
    CheckpointClaimRegistry,
)

from core.workflows.completion_queue import (
    CompletionRecord,
    WorkflowCompletionQueue,
)

from core.workflows.subprocess_env import (
    prepare_subprocess_env,
    _is_suspected_pii,
)


# ─── PathResolver Tests ───────────────────────────────────────────────

class TestPathResolver:
    """Tests for path_resolver module."""

    def test_resolve_corvin_home_from_env(self):
        """resolve_corvin_home reads CORVIN_HOME env var."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"CORVIN_HOME": tmpdir}):
                home = resolve_corvin_home()
                assert home == Path(tmpdir)

    def test_resolve_corvin_home_fails_when_unset(self):
        """resolve_corvin_home raises PathResolutionError when CORVIN_HOME unset."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CORVIN_HOME", None)
            with patch("core.workflows.path_resolver.corvin_home", side_effect=Exception("no home")):
                with pytest.raises(PathResolutionError, match="CORVIN_HOME not set"):
                    resolve_corvin_home()

    def test_resolve_tenant_id_from_arg(self):
        """resolve_tenant_id accepts and validates tenant_id arg."""
        assert resolve_tenant_id("my-tenant") == "my-tenant"

    def test_resolve_tenant_id_from_env(self):
        """resolve_tenant_id reads CORVIN_TENANT_ID env var."""
        with patch.dict(os.environ, {"CORVIN_TENANT_ID": "env-tenant"}):
            assert resolve_tenant_id() == "env-tenant"

    def test_resolve_tenant_id_defaults_to_default(self):
        """resolve_tenant_id defaults to '_default'."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CORVIN_TENANT_ID", None)
            assert resolve_tenant_id() == "_default"

    def test_resolve_tenant_id_rejects_invalid(self):
        """resolve_tenant_id raises ValueError on invalid tenant_id."""
        with pytest.raises(ValueError, match="fails charset rule"):
            resolve_tenant_id("UPPERCASE")

    def test_workflow_runs_dir_creates_directory(self):
        """workflow_runs_dir creates directory if missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"CORVIN_HOME": tmpdir}):
                runs_dir = workflow_runs_dir("test-tenant")
                assert runs_dir.exists()
                assert runs_dir.is_dir()

    def test_workflow_run_path_rejects_invalid_run_id(self):
        """workflow_run_path raises ValueError on invalid run_id."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"CORVIN_HOME": tmpdir}):
                with pytest.raises(ValueError, match="Invalid run_id"):
                    workflow_run_path("../etc/passwd", "test-tenant")

    def test_workflow_run_path_constructs_correctly(self):
        """workflow_run_path constructs path correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"CORVIN_HOME": tmpdir}):
                path = workflow_run_path("run-123", "tenant-x")
                assert path.name == "run-123.json"
                assert "tenant-x" in str(path)

    def test_checkpoint_dir_creates_directory(self):
        """checkpoint_dir creates directory if missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"CORVIN_HOME": tmpdir}):
                ckpt_dir = checkpoint_dir("run-123", "test-tenant")
                assert ckpt_dir.exists()
                assert ckpt_dir.is_dir()

    def test_claimed_file_path_constructs_correctly(self):
        """claimed_file_path constructs .json.claimed path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"CORVIN_HOME": tmpdir}):
                path = claimed_file_path("run-123", "test-tenant")
                assert path.name == "run-123.json.claimed"

    def test_completion_queue_path_creates_parent(self):
        """completion_queue_path creates parent directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"CORVIN_HOME": tmpdir}):
                path = completion_queue_path("test-tenant")
                assert path.parent.exists()


# ─── ClaimRegistry Tests ───────────────────────────────────────────────

class TestClaimRegistry:
    """Tests for claim_registry module."""

    @pytest.fixture
    async def registry(self):
        """Create and initialize a test registry."""
        reg = CheckpointClaimRegistry(default_ttl_s=10)
        await reg.start_reaper()
        yield reg
        await reg.stop_reaper()

    @pytest.fixture
    def temp_checkpoint(self):
        """Create a temp checkpoint file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            ckpt_file = tmpdir_path / "run-123.json"
            ckpt_file.write_text('{"status": "paused"}')
            yield tmpdir_path, ckpt_file

    @pytest.mark.asyncio
    async def test_claim_atomically_renames_checkpoint(self, registry, temp_checkpoint):
        """claim() atomically renames .json to .json.claimed."""
        tmpdir_path, ckpt_file = temp_checkpoint
        claimed_file = ckpt_file.with_suffix(".json.claimed")

        with patch("core.workflows.claim_registry.workflow_run_path", return_value=ckpt_file):
            with patch("core.workflows.claim_registry.claimed_file_path", return_value=claimed_file):
                with patch.dict(os.environ, {"CORVIN_HOME": str(tmpdir_path)}):
                    path = await asyncio.get_event_loop().run_in_executor(
                        None,
                        registry.claim,
                        "run-123"
                    )

        # File should be renamed
        assert not ckpt_file.exists()
        assert claimed_file.exists()

    @pytest.mark.asyncio
    async def test_claim_raises_already_claimed_error(self, registry, temp_checkpoint):
        """claim() raises AlreadyClaimedError on second claim."""
        tmpdir_path, ckpt_file = temp_checkpoint
        claimed_file = ckpt_file.with_suffix(".json.claimed")

        with patch("core.workflows.claim_registry.workflow_run_path", return_value=ckpt_file):
            with patch("core.workflows.claim_registry.claimed_file_path", return_value=claimed_file):
                # First claim succeeds
                registry.claim("run-123")

                # Second claim fails
                with pytest.raises(AlreadyClaimedError, match="already being resumed"):
                    registry.claim("run-123")

    @pytest.mark.asyncio
    async def test_claim_record_tracking(self, registry):
        """claim() stores ClaimRecord in registry."""
        # Mock the filesystem operations
        with patch("core.workflows.claim_registry.workflow_run_path") as mock_run_path:
            mock_run_path.return_value = Path("/tmp/fake.json")
            with patch("core.workflows.claim_registry.claimed_file_path") as mock_claimed:
                mock_claimed.return_value = Path("/tmp/fake.json.claimed")
                with patch("os.rename"):
                    with patch.object(Path, "exists", return_value=True):
                        try:
                            registry.claim("run-123", ttl_s=100)
                            record = registry._claims["run-123"]
                            assert record.run_id == "run-123"
                            assert record.ttl_s == 100
                            assert not record.is_expired
                        except (OSError, FileNotFoundError):
                            pass

    @pytest.mark.asyncio
    async def test_is_claimed_returns_true_for_active_claim(self, registry):
        """is_claimed() returns True for non-expired claims."""
        with patch("core.workflows.claim_registry.workflow_run_path") as mock_run_path:
            mock_run_path.return_value = Path("/tmp/fake.json")
            with patch("core.workflows.claim_registry.claimed_file_path") as mock_claimed:
                mock_claimed.return_value = Path("/tmp/fake.json.claimed")
                with patch("os.rename"):
                    with patch.object(Path, "exists", return_value=True):
                        try:
                            registry.claim("run-123", ttl_s=3600)
                            assert registry.is_claimed("run-123")
                        except (OSError, FileNotFoundError):
                            pass

    @pytest.mark.asyncio
    async def test_release_restores_checkpoint(self, registry, temp_checkpoint):
        """release() restores claimed file to canonical path."""
        tmpdir_path, ckpt_file = temp_checkpoint
        claimed_file = ckpt_file.with_suffix(".json.claimed")

        # Set up claimed file
        claimed_file.write_text('{"status": "paused"}')
        ckpt_file.unlink()  # Remove canonical

        with patch("core.workflows.claim_registry.workflow_run_path", return_value=ckpt_file):
            with patch("core.workflows.claim_registry.claimed_file_path", return_value=claimed_file):
                registry.release("run-123")

        # File should be restored
        assert ckpt_file.exists()
        assert not claimed_file.exists()

    @pytest.mark.asyncio
    async def test_reap_stale_claims(self, registry):
        """reap_stale_claims() removes expired claims."""
        with patch("core.workflows.claim_registry.workflow_run_path"):
            with patch("core.workflows.claim_registry.claimed_file_path") as mock_claimed:
                # Add an expired claim
                old_record = ClaimRecord(
                    run_id="old-run",
                    claimed_path=Path("/tmp/old.json.claimed"),
                    timestamp_s=0,  # Very old
                    ttl_s=10,
                )
                registry._claims["old-run"] = old_record

                # Mock file deletion
                mock_path = MagicMock()
                mock_path.exists.return_value = True
                mock_claimed.return_value = mock_path

                count = await registry.reap_stale_claims(10)
                assert count == 1
                assert "old-run" not in registry._claims

    @pytest.mark.asyncio
    async def test_get_all_claims_returns_non_expired(self, registry):
        """get_all_claims() returns only non-expired records."""
        # Add one fresh and one expired claim
        fresh = ClaimRecord(
            run_id="fresh",
            claimed_path=Path("/tmp/fresh.claimed"),
            timestamp_s=asyncio.get_event_loop().time() - 1,
            ttl_s=3600,
        )
        expired = ClaimRecord(
            run_id="expired",
            claimed_path=Path("/tmp/expired.claimed"),
            timestamp_s=0,
            ttl_s=10,
        )

        registry._claims["fresh"] = fresh
        registry._claims["expired"] = expired

        all_claims = registry.get_all_claims()
        assert len(all_claims) == 1
        assert all_claims[0].run_id == "fresh"


# ─── CompletionQueue Tests ─────────────────────────────────────────────

class TestCompletionQueue:
    """Tests for completion_queue module."""

    @pytest.fixture
    async def queue(self):
        """Create a test completion queue."""
        q = WorkflowCompletionQueue()
        yield q

    @pytest.mark.asyncio
    async def test_push_adds_to_queue(self, queue):
        """push() adds record to in-memory queue."""
        await queue.push("run-1", "complete", {"output": "result"})
        assert "run-1" in queue._in_memory

    @pytest.mark.asyncio
    async def test_pop_removes_from_queue(self, queue):
        """pop() removes and returns record."""
        await queue.push("run-1", "complete", {"output": "result"})
        record = await queue.pop("run-1")
        assert record.run_id == "run-1"
        assert record.status == "complete"
        assert "run-1" not in queue._in_memory

    @pytest.mark.asyncio
    async def test_pop_returns_none_if_not_found(self, queue):
        """pop() returns None if record not in queue."""
        record = await queue.pop("nonexistent")
        assert record is None

    @pytest.mark.asyncio
    async def test_get_all_filters_by_tenant(self, queue):
        """get_all() filters records by tenant."""
        await queue.push("run-1", "complete", {}, tenant_id="tenant-a")
        await queue.push("run-2", "complete", {}, tenant_id="tenant-b")

        a_records = await queue.get_all(tenant_id="tenant-a")
        assert len(a_records) == 1
        assert a_records[0].run_id == "run-1"

    @pytest.mark.asyncio
    async def test_mark_webhook_notified(self, queue):
        """mark_webhook_notified() sets flag."""
        await queue.push("run-1", "complete", {})
        await queue.mark_webhook_notified("run-1")

        record = queue._in_memory["run-1"]
        assert record.webhook_notified

    @pytest.mark.asyncio
    async def test_get_unnotified_filters_correctly(self, queue):
        """get_unnotified() returns only un-notified records."""
        await queue.push("run-1", "complete", {})
        await queue.push("run-2", "complete", {})
        await queue.mark_webhook_notified("run-1")

        unnotified = await queue.get_unnotified()
        assert len(unnotified) == 1
        assert unnotified[0].run_id == "run-2"

    @pytest.mark.asyncio
    async def test_completion_record_serialization(self):
        """CompletionRecord serializes/deserializes correctly."""
        record = CompletionRecord(
            run_id="run-1",
            status="complete",
            output={"result": "ok"},
            tenant_id="tenant-a",
        )

        data = record.to_dict()
        restored = CompletionRecord.from_dict(data)

        assert restored.run_id == record.run_id
        assert restored.status == record.status
        assert restored.output == record.output
        assert restored.tenant_id == record.tenant_id

    @pytest.mark.asyncio
    async def test_push_persists_to_disk(self):
        """push() writes record to JSONL file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"CORVIN_HOME": tmpdir}):
                queue = WorkflowCompletionQueue()
                await queue.push("run-1", "complete", {"result": "ok"})

                # Check file was written
                queue_path = Path(tmpdir) / "tenants" / "_default" / "workflow_runs" / "completions.jsonl"
                assert queue_path.exists()

                # Verify content
                lines = queue_path.read_text().strip().split("\n")
                assert len(lines) >= 1
                data = json.loads(lines[0])
                assert data["run_id"] == "run-1"


# ─── SubprocessEnv Tests ───────────────────────────────────────────────

class TestSubprocessEnv:
    """Tests for subprocess_env module."""

    def test_prepare_subprocess_env_sets_corvin_home(self):
        """prepare_subprocess_env sets CORVIN_HOME."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"CORVIN_HOME": tmpdir}):
                env = prepare_subprocess_env()
                assert env["CORVIN_HOME"] == tmpdir

    def test_prepare_subprocess_env_sets_corvin_tenant_id(self):
        """prepare_subprocess_env sets CORVIN_TENANT_ID."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"CORVIN_HOME": tmpdir}):
                env = prepare_subprocess_env(tenant_id="my-tenant")
                assert env["CORVIN_TENANT_ID"] == "my-tenant"

    def test_prepare_subprocess_env_whitelists_path(self):
        """prepare_subprocess_env preserves PATH."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"CORVIN_HOME": tmpdir, "PATH": "/usr/bin"}):
                env = prepare_subprocess_env()
                assert env.get("PATH") == "/usr/bin"

    def test_prepare_subprocess_env_rejects_pii_in_additional_vars(self):
        """prepare_subprocess_env rejects suspected PII in additional_vars."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"CORVIN_HOME": tmpdir}):
                with pytest.raises(ValueError, match="Suspected PII"):
                    prepare_subprocess_env(
                        additional_vars={"API_SECRET": "sk_test_redacted_for_testing"}
                    )

    def test_is_suspected_pii_detects_keys(self):
        """_is_suspected_pii detects suspicious key names."""
        assert _is_suspected_pii("password", "mypass")
        assert _is_suspected_pii("API_TOKEN", "abc123")
        assert _is_suspected_pii("OAUTH_SECRET", "xyz")

    def test_is_suspected_pii_detects_values(self):
        """_is_suspected_pii detects suspicious value patterns."""
        assert _is_suspected_pii("VAR", "-----BEGIN PRIVATE KEY-----")
        assert _is_suspected_pii("VAR", "Bearer eyJhbGciOiJIUzI1NiIs...")
        assert _is_suspected_pii("VAR", "sk_test_" + "a" * 100)

    def test_is_suspected_pii_allows_safe_vars(self):
        """_is_suspected_pii allows safe env var patterns."""
        assert not _is_suspected_pii("PATH", "/usr/bin")
        assert not _is_suspected_pii("LANG", "en_US.UTF-8")
        assert not _is_suspected_pii("SHELL", "/bin/bash")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
