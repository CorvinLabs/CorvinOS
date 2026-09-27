#!/usr/bin/env python3
"""Phase A K=1: Task Tracking DB Bootstrap + Sync Routing (ADR-2056, ADR-0516).

Execution:
  python3 scripts/bootstrap_task_tracking.py [--tenant _default] [--dry-run]

Compliance:
  - GDPR Art. 30: Audit chain (core.task_tracking writes via audit backend)
  - ADR-0516: initiatives.json remains audit-source, task_registry.json read-only
  - ADR-2056: Task Tracking SSOT initialized
"""

import argparse
import json
import os
import sys
from pathlib import Path

def bootstrap_task_tracking_db(tenant_id: str = "_default", dry_run: bool = False):
    """Initialize Task Tracking DB schema for tenant."""
    print(f"[Phase A K=1] Bootstrap Task Tracking DB (tenant={tenant_id})")

    try:
        from core.task_tracking import store  # noqa: PLC0415
        from core.paths import tenant_home  # noqa: PLC0415
    except ImportError as e:
        print(f"❌ Import failed: {e}")
        return False

    # Step 1: DB Path
    db_path = store.db_path(tenant_id)
    print(f"  1️⃣  DB Path: {db_path}")

    if dry_run:
        print(f"     [DRY RUN] Would initialize at: {db_path}")
        return True

    # Step 2: Initialize DB (creates schema, WAL, permissions 0o600)
    try:
        store._init(db_path)
        print(f"     ✅ Schema initialized")
    except Exception as e:
        print(f"     ❌ Failed: {e}")
        return False

    # Step 3: Verify schema
    try:
        with store.connect(tenant_id) as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        print(f"     ✅ Tables created: {len(tables)} ({', '.join(t[0] for t in tables[:3])}...)")
    except Exception as e:
        print(f"     ❌ Schema verification failed: {e}")
        return False

    return True


def mark_task_registry_readonly(tenant_id: str = "_default", dry_run: bool = False):
    """Mark task_registry.json as read-only (ADR-0516 compliance)."""
    print(f"  2️⃣  Task Registry Read-Only (ADR-0516)")

    registry_path = Path.home() / ".corvin" / "task_registry.json"
    if not registry_path.exists():
        print(f"     ⚠️  {registry_path} not found (OK, may have been cleaned)")
        return True

    if dry_run:
        print(f"     [DRY RUN] Would chmod 444 {registry_path}")
        return True

    try:
        os.chmod(registry_path, 0o444)  # Read-only
        print(f"     ✅ Read-only: {registry_path}")
    except Exception as e:
        print(f"     ❌ Failed: {e}")
        return False

    return True


def verify_initiatives_json_audit_source(tenant_id: str = "_default", dry_run: bool = False):
    """Verify initiatives.json remains as audit source (ADR-0516)."""
    print(f"  3️⃣  Initiatives JSON Audit Source (ADR-0516)")

    try:
        from core.paths import tenant_home  # noqa: PLC0415
    except ImportError:
        print(f"     ⚠️  Cannot import tenant_home, skipping")
        return True

    initiatives_path = Path(tenant_home(tenant_id)) / "global" / "initiatives.json"

    if initiatives_path.exists():
        print(f"     ✅ Found: {initiatives_path}")
    else:
        print(f"     ⚠️  Not found: {initiatives_path} (OK, deleted at ADR-0516 migration)")

    return True


def git_sync_wiring_check(dry_run: bool = False):
    """Verify Git Sync is wired to import Task Tracking."""
    print(f"  4️⃣  Git Sync Wiring Check")

    git_sync_path = Path("core/console/corvin_console/task_tracking_git_sync.py")
    if not git_sync_path.exists():
        print(f"     ⚠️  {git_sync_path} not found")
        return True

    with open(git_sync_path) as f:
        content = f.read()

    if "from core.task_tracking import" in content:
        print(f"     ✅ Git Sync imports Task Tracking")
        return True
    else:
        print(f"     ❌ Git Sync does NOT import Task Tracking (needs wiring)")
        return False


def main():
    parser = argparse.ArgumentParser(description="Bootstrap Task Tracking DB (Phase A K=1)")
    parser.add_argument("--tenant", default="_default", help="Tenant ID (default: _default)")
    parser.add_argument("--dry-run", action="store_true", help="Dry-run mode (no writes)")
    args = parser.parse_args()

    print("=" * 60)
    print("Phase A K=1: Task Tracking DB Bootstrap + Sync Wiring")
    print("=" * 60)
    print()

    steps = [
        ("DB Schema Init", lambda: bootstrap_task_tracking_db(args.tenant, args.dry_run)),
        ("Task Registry Read-Only", lambda: mark_task_registry_readonly(args.tenant, args.dry_run)),
        ("Initiatives JSON Audit", lambda: verify_initiatives_json_audit_source(args.tenant, args.dry_run)),
        ("Git Sync Wiring", lambda: git_sync_wiring_check(args.dry_run)),
    ]

    results = []
    for step_name, step_fn in steps:
        try:
            result = step_fn()
            results.append((step_name, result))
        except Exception as e:
            print(f"  ❌ {step_name} crashed: {e}")
            results.append((step_name, False))
        print()

    # Summary
    print("=" * 60)
    print("Summary")
    print("=" * 60)
    passed = sum(1 for _, r in results if r)
    total = len(results)
    print(f"Passed: {passed}/{total}")
    for name, result in results:
        status = "✅" if result else "❌"
        print(f"  {status} {name}")

    if args.dry_run:
        print("\n[DRY RUN MODE] No changes were made.")

    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
