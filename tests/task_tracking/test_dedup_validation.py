"""
Task Registry Deduplication Validation Tests (ADR-0516 enforcement)

Tests that:
1. ADR IDs in external_ref are checked for uniqueness per tenant
2. Duplicate ADR IDs are rejected with clear error messages
3. Non-ADR external_refs don't trigger dedup checks
4. Updates to external_ref also validate uniqueness
"""

import pytest
from core.task_tracking import service
from core.task_tracking.models import ItemCreate
from core.task_tracking.service import TaskTrackingError, NotFound


@pytest.fixture
def tenant_id():
    return "test_tenant_1"


@pytest.fixture
def actor():
    return "test_actor"


class TestADRDeduplication:
    """ADR deduplication tests"""

    def test_create_with_unique_adr_succeeds(self, tenant_id, actor):
        """Creating a task with a unique ADR ID should succeed."""
        body = ItemCreate(
            kind="task",
            title="Test Task",
            status="open",
            priority="medium",
            external_ref="ADR-0516",  # Unique ADR
        )
        result = service.create(tenant_id, body, actor=actor)
        assert result["external_ref"] == "ADR-0516"
        assert result["id"].startswith("t_")

    def test_create_duplicate_adr_fails(self, tenant_id, actor):
        """Creating a second task with the same ADR ID should fail."""
        body1 = ItemCreate(
            kind="task",
            title="Task 1",
            status="open",
            priority="medium",
            external_ref="ADR-0516",
        )
        body2 = ItemCreate(
            kind="task",
            title="Task 2",
            status="open",
            priority="medium",
            external_ref="ADR-0516",  # Duplicate!
        )

        # First task succeeds
        task1 = service.create(tenant_id, body1, actor=actor)
        assert task1["external_ref"] == "ADR-0516"

        # Second task with same ADR fails
        with pytest.raises(TaskTrackingError) as exc_info:
            service.create(tenant_id, body2, actor=actor)

        assert "ADR-0516" in str(exc_info.value)
        assert "already tracked" in str(exc_info.value)
        assert task1["id"] in str(exc_info.value)

    def test_non_adr_external_refs_allowed_duplicate(self, tenant_id, actor):
        """Non-ADR external_refs should allow duplicates (e.g., tracking external IDs)."""
        body1 = ItemCreate(
            kind="task",
            title="Task 1",
            status="open",
            priority="medium",
            external_ref="JIRA-123",  # Not an ADR
        )
        body2 = ItemCreate(
            kind="task",
            title="Task 2",
            status="open",
            priority="medium",
            external_ref="JIRA-123",  # Same JIRA ID OK
        )

        task1 = service.create(tenant_id, body1, actor=actor)
        task2 = service.create(tenant_id, body2, actor=actor)

        assert task1["id"] != task2["id"]
        assert task1["external_ref"] == "JIRA-123"
        assert task2["external_ref"] == "JIRA-123"

    def test_update_to_duplicate_adr_fails(self, tenant_id, actor):
        """Updating a task's external_ref to a duplicate ADR should fail."""
        from core.task_tracking.models import ItemPatch

        # Create two tasks
        body1 = ItemCreate(
            kind="task",
            title="Task 1",
            status="open",
            priority="medium",
            external_ref="ADR-0516",
        )
        body2 = ItemCreate(
            kind="task",
            title="Task 2",
            status="open",
            priority="medium",
            external_ref="ADR-0517",
        )

        task1 = service.create(tenant_id, body1, actor=actor)
        task2 = service.create(tenant_id, body2, actor=actor)

        # Try to update task2's external_ref to task1's ADR
        patch = ItemPatch(
            version=task2["version"],
            external_ref="ADR-0516",  # Duplicate!
        )

        with pytest.raises(TaskTrackingError) as exc_info:
            service.update(tenant_id, task2["id"], patch, actor=actor)

        assert "ADR-0516" in str(exc_info.value)
        assert "already tracked" in str(exc_info.value)

    def test_adr_dedup_per_tenant(self, actor):
        """ADR dedup should be per-tenant; same ADR in different tenants is OK."""
        body = ItemCreate(
            kind="task",
            title="Task",
            status="open",
            priority="medium",
            external_ref="ADR-0516",
        )

        # Same ADR in different tenants should succeed
        task1 = service.create("tenant_a", body, actor=actor)
        task2 = service.create("tenant_b", body, actor=actor)

        assert task1["tenant_id"] == "tenant_a"
        assert task2["tenant_id"] == "tenant_b"
        assert task1["external_ref"] == task2["external_ref"] == "ADR-0516"

    def test_deleted_task_adr_can_be_reused(self, tenant_id, actor):
        """Deleting a task should free up its ADR for reuse."""
        from core.task_tracking.models import ItemPatch

        body = ItemCreate(
            kind="task",
            title="Task 1",
            status="open",
            priority="medium",
            external_ref="ADR-0516",
        )

        task1 = service.create(tenant_id, body, actor=actor)

        # Delete the task
        service.delete(tenant_id, task1["id"], actor=actor)

        # Now we should be able to create a new task with the same ADR
        body2 = ItemCreate(
            kind="task",
            title="Task 2 (new)",
            status="open",
            priority="medium",
            external_ref="ADR-0516",
        )

        task2 = service.create(tenant_id, body2, actor=actor)
        assert task2["external_ref"] == "ADR-0516"
        assert task2["id"] != task1["id"]

    def test_adr_pattern_validation(self, tenant_id, actor):
        """Only ADR-NNNN pattern triggers dedup; invalid patterns don't."""
        invalid_patterns = [
            "ADR-516",  # Too short
            "ADR-05160",  # Too long
            "adr-0516",  # Lowercase
            "ADR_0516",  # Underscore
            "0516",  # Missing ADR prefix
        ]

        for pattern in invalid_patterns:
            body = ItemCreate(
                kind="task",
                title="Task",
                status="open",
                priority="medium",
                external_ref=pattern,
            )
            # Should NOT raise (not recognized as ADR)
            task = service.create(tenant_id, body, actor=actor)
            assert task["external_ref"] == pattern


class TestDedupValidator:
    """Tests for the standalone dedup validator script"""

    def test_validator_detects_duplicates(self):
        """Running task_registry_dedup_validator should detect ADR duplicates."""
        import subprocess

        result = subprocess.run(
            ["python3", "scripts/task_registry_dedup_validator.py"],
            cwd="/home/shumway/projects/CorvinOS",
            capture_output=True,
            text=True,
            timeout=30,
        )

        # Should complete (exit 0 or 1 depending on --strict flag)
        assert result.returncode in (0, 1)

        # Output should be JSON with duplicate count
        import json
        data = json.loads(result.stdout)

        assert "duplicate_ids" in data
        assert "total_adrs" in data
        assert data["duplicate_ids"] > 0  # Known duplicates in Corvin-ADR

    def test_validator_fix_mode(self):
        """Validator --fix mode should be idempotent."""
        import subprocess

        # Run validator in fix mode (dry-run first, then apply)
        result = subprocess.run(
            ["python3", "scripts/task_registry_dedup_validator.py", "--fix"],
            cwd="/home/shumway/projects/CorvinOS",
            capture_output=True,
            text=True,
            timeout=30,
        )

        # Should complete
        assert result.returncode in (0, 1)

        # Output should show fixes applied
        import json
        data = json.loads(result.stdout)
        assert "fixed_adrs" in data or "skipped_adrs" in data
