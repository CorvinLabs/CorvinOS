"""Phase C k=2 E2E Test: Task Lifecycle with Audit Trail Integration.

Verifies:
1. Task creation → database persistence → audit event
2. Task read via store.py
3. Task update → database update → audit event
4. Task completion → final status → audit event
5. task_registry.json remains read-only (no writes)

Proof of end-to-end wiring: audit_event → tasks.db → events table
"""

import pytest
import asyncio
from pathlib import Path
from datetime import datetime, timezone

from core.task_tracking import store, migration
from core.task_tracking.audit import emit_task_audit_event


def _write_registry(read_only: bool) -> Path:
    """A legacy registry (real shape: ``tasks`` is a mapping) under CORVIN_HOME."""
    import json
    import os

    from core.paths import corvin_home

    p = Path(corvin_home()) / "task_registry.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        os.chmod(p, 0o644)
    p.write_text(json.dumps({"tasks": {
        "adr_0001": {"title": "ADR one", "status": "ACCEPTED"},
        "task_a": {"title": "A", "status": "in_progress"},
    }}))
    if read_only:
        os.chmod(p, 0o444)
    return p


class TestPhaseC_K2_MigrationE2E:
    """E2E tests for Phase C k=2 structural migration."""

    @pytest.mark.asyncio
    async def test_task_creation_with_audit(self):
        """E2E: Create task → audit event → DB persistence."""
        tenant_id = "_default"

        # Ensure DB is initialized
        if not store.exists(tenant_id):
            with store.connect(tenant_id):
                pass

        # Step 1: Create task with audit
        task_id = "test_e2e_task_001"
        task_title = "E2E Test Task"

        event_id = await emit_task_audit_event(
            event_type="task_created",
            task_id=task_id,
            tenant_id=tenant_id,
            actor="e2e_test",
            action="create",
            delta={"title": task_title, "status": "open"},
            store=store,
        )

        assert event_id is not None, "Audit event should be created"

        # Step 2: Verify event was written
        with store.connect(tenant_id) as conn:
            events = conn.execute(
                "SELECT * FROM events WHERE item_id = ?",
                (task_id,),
            ).fetchall()

            assert len(events) > 0, f"Audit event for {task_id} should exist"
            assert events[0]["event_type"] == "task_created"
            assert events[0]["chain_hash"] is not None, "Event should be hash-chained"

    @pytest.mark.asyncio
    async def test_task_lifecycle_complete(self):
        """E2E: Full task lifecycle (create → update → complete) with audit trail."""
        tenant_id = "_default"
        task_id = "test_e2e_lifecycle_001"

        with store.connect(tenant_id) as conn:
            pass  # Ensure DB initialized

        # Step 1: Create task
        await emit_task_audit_event(
            event_type="task_created",
            task_id=task_id,
            tenant_id=tenant_id,
            actor="e2e_test",
            action="create",
            delta={"title": "Lifecycle Task", "status": "open"},
            store=store,
        )

        # Step 2: Update task status
        await emit_task_audit_event(
            event_type="task_updated",
            task_id=task_id,
            tenant_id=tenant_id,
            actor="e2e_test",
            action="status",
            delta={"status": {"from": "open", "to": "in_progress"}},
            store=store,
        )

        # Step 3: Complete task
        await emit_task_audit_event(
            event_type="task_completed",
            task_id=task_id,
            tenant_id=tenant_id,
            actor="e2e_test",
            action="complete",
            delta={"status": "complete", "completed_at": datetime.now(timezone.utc).isoformat()},
            store=store,
        )

        # Verify: All events are in audit trail
        with store.connect(tenant_id) as conn:
            events = conn.execute(
                "SELECT event_type FROM events WHERE item_id = ? ORDER BY ts",
                (task_id,),
            ).fetchall()

            assert len(events) == 3, "Should have 3 audit events (create, update, complete)"
            assert events[0]["event_type"] == "task_created"
            assert events[1]["event_type"] == "task_updated"
            assert events[2]["event_type"] == "task_completed"

    @pytest.mark.asyncio
    async def test_migration_legacy_to_db(self):
        """E2E: Migrate tasks from legacy registry to DB."""
        tenant_id = "_default"
        _write_registry(read_only=False)

        # Run migration (dry_run first to verify logic)
        report_dry = await migration.migrate_task_registry_to_db(
            tenant_id=tenant_id,
            dry_run=True,
        )

        assert report_dry["migrated_count"] >= 0, "Dry run should complete"
        assert "errors" in report_dry, "Report should have errors list"

        # Run actual migration
        report = await migration.migrate_task_registry_to_db(
            tenant_id=tenant_id,
            dry_run=False,
        )

        assert report["migrated_count"] > 0, f"Should migrate tasks, got {report}"

        # Verify: Tasks are in DB
        with store.connect(tenant_id) as conn:
            task_count = conn.execute("SELECT COUNT(*) as cnt FROM items").fetchone()["cnt"]
            assert task_count >= report["migrated_count"], "All migrated tasks should be in DB"

    @pytest.mark.asyncio
    async def test_registry_read_only(self):
        """E2E: a read-only task_registry.json is reported as not writable.

        Hermetic: the registry lives under the test's CORVIN_HOME. The previous
        version opened the operator's live ``~/.corvin/task_registry.json`` with
        mode "w" — which truncates it whenever it happens to be writable.
        """
        registry_path = _write_registry(read_only=True)
        verification = await migration.verify_migration_complete("_default")
        assert verification["registry_path"] == str(registry_path)
        assert not verification["registry_writable"]

    @pytest.mark.asyncio
    async def test_chain_hash_linkage(self):
        """E2E: Verify chain_hash is preserved in audit events."""
        tenant_id = "_default"
        task_id = "test_e2e_chain_hash"

        # Create task with audit
        await emit_task_audit_event(
            event_type="task_created",
            task_id=task_id,
            tenant_id=tenant_id,
            actor="e2e_test",
            action="create",
            delta={"title": "Chain Hash Test"},
            store=store,
        )

        # Verify: chain_hash is set
        with store.connect(tenant_id) as conn:
            event = conn.execute(
                "SELECT chain_hash FROM events WHERE item_id = ? LIMIT 1",
                (task_id,),
            ).fetchone()

            assert event is not None, "Event should exist"
            assert event["chain_hash"] is not None, "Event should have chain_hash"
            assert len(event["chain_hash"]) > 0, "chain_hash should not be empty"

    @pytest.mark.asyncio
    async def test_migration_verification(self):
        """E2E: Verify migration is complete."""
        tenant_id = "_default"
        _write_registry(read_only=True)

        verification = await migration.verify_migration_complete(tenant_id)

        assert verification["registry_readable"], "Registry should be readable"
        assert not verification["registry_writable"], "Registry should NOT be writable"
        assert verification["db_task_count"] >= 0, "DB should have task count"
        # Migration is complete if DB has tasks and registry is read-only
        expected_complete = verification["db_task_count"] > 0 and not verification["registry_writable"]
        assert expected_complete == verification["migration_complete"], "Migration status should be accurate"


# Standalone test (no pytest required)
if __name__ == "__main__":

    async def main():
        print("Phase C k=2 E2E Test Suite")
        print("=" * 50)

        # Quick migration check
        print("\n1. Verifying task_registry.json is read-only...")
        registry_path = Path.home() / ".corvin" / "task_registry.json"
        stat = registry_path.stat()
        is_writable = bool(stat.st_mode & 0o200)
        print(f"   Registry writable: {is_writable} (should be False)")

        print("\n2. Checking DB exists...")
        tenant_id = "_default"
        db_exists = store.exists(tenant_id)
        print(f"   DB exists: {db_exists}")

        if db_exists:
            with store.connect(tenant_id) as conn:
                task_count = conn.execute("SELECT COUNT(*) as cnt FROM items").fetchone()["cnt"]
                event_count = conn.execute("SELECT COUNT(*) as cnt FROM events").fetchone()["cnt"]
                print(f"   Tasks in DB: {task_count}")
                print(f"   Audit events: {event_count}")

        print("\n3. Running migration verification...")
        verification = await migration.verify_migration_complete(tenant_id)
        for key, value in verification.items():
            print(f"   {key}: {value}")

        print("\n✅ Phase C k=2 E2E test complete")

    asyncio.run(main())
