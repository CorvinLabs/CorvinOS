#!/usr/bin/env python3
"""
Migration Script: Task-ID Canonicalization (FIX #2 — ADR-0863)

One-time migration to deduplicate tasks in ~/.corvin/task_registry.json
by normalizing task-ids to lowercase snake_case.

Usage:
    python3 scripts/migrate_task_registry_canonical.py
    python3 scripts/migrate_task_registry_canonical.py --registry /path/to/registry.json
    python3 scripts/migrate_task_registry_canonical.py --dry-run

Options:
    --registry PATH     Custom registry path (default: ~/.corvin/task_registry.json)
    --dry-run           Show what would be done, don't modify files
    --verbose           Print detailed migration info
"""

import json
import os
import sys
import shutil
from pathlib import Path
from datetime import datetime
import re
import argparse
from typing import Dict, Any, Set, Tuple


def _normalize_task_id(task_id: str) -> str:
    """Canonicalize task-id to lowercase snake_case"""
    normalized = task_id.strip().lower()
    normalized = normalized.replace('-', '_').replace(' ', '_')
    normalized = re.sub(r'_+', '_', normalized)
    normalized = normalized.strip('_')
    return normalized


def analyze_registry(registry_path: Path) -> Dict[str, Any]:
    """Analyze registry for duplicates and issues"""

    with open(registry_path, 'r') as f:
        registry = json.load(f)

    tasks = registry.get('tasks', {})

    # Group by normalized form
    normalized_groups: Dict[str, list] = {}
    for task_id in tasks.keys():
        normalized = _normalize_task_id(task_id)
        if normalized not in normalized_groups:
            normalized_groups[normalized] = []
        normalized_groups[normalized].append(task_id)

    # Find duplicates
    duplicates = {k: v for k, v in normalized_groups.items() if len(v) > 1}

    return {
        'total_tasks': len(tasks),
        'unique_normalized': len(normalized_groups),
        'duplicate_groups': len(duplicates),
        'duplicates': duplicates,
        'normalized_groups': normalized_groups
    }


def migrate_registry(registry_path: Path, dry_run: bool = False, verbose: bool = False) -> Dict[str, Any]:
    """Perform migration: deduplicate tasks by canonicalization

    Returns:
        {
            'status': 'success' | 'error',
            'message': str,
            'original_count': int,
            'final_count': int,
            'merged_count': int,
            'backup_path': str (if successful)
        }
    """

    print(f"📋 Analyzing registry: {registry_path}")

    # Read registry
    try:
        with open(registry_path, 'r') as f:
            registry = json.load(f)
    except Exception as e:
        return {
            'status': 'error',
            'message': f"Failed to read registry: {e}",
            'original_count': 0,
            'final_count': 0,
            'merged_count': 0
        }

    tasks = registry.get('tasks', {})
    original_count = len(tasks)

    # Analyze for duplicates
    analysis = analyze_registry(registry_path)

    print(f"\n📊 Registry Analysis:")
    print(f"   Total tasks: {analysis['total_tasks']}")
    print(f"   Unique normalized IDs: {analysis['unique_normalized']}")
    print(f"   Duplicate groups found: {analysis['duplicate_groups']}")

    if analysis['duplicate_groups'] > 0:
        print(f"\n🔀 Duplicate Groups:")
        for normalized_id, originals in analysis['duplicates'].items():
            print(f"   {normalized_id} ← {originals}")

    # Migrate: deduplicate by normalized form
    migrated: Dict[str, Any] = {}
    merged_count = 0

    for normalized_id, original_list in analysis['normalized_groups'].items():
        if len(original_list) == 1:
            # No duplicate, keep as-is
            original_id = original_list[0]
            migrated[normalized_id] = tasks[original_id]
        else:
            # Multiple forms of same ID: merge them
            merged_count += 1

            if verbose:
                print(f"\n🔗 Merging: {original_list}")

            # Merge strategy: keep task with highest priority status
            merged_task = tasks[original_list[0]]  # Start with first

            for other_id in original_list[1:]:
                other_task = tasks[other_id]

                # Prefer tasks with "done" status
                if other_task.get('status') in ['ACCEPTED', 'COMPLETE']:
                    if merged_task.get('status') not in ['ACCEPTED', 'COMPLETE']:
                        merged_task = other_task
                        if verbose:
                            print(f"   → Using {other_id} (status: {other_task.get('status')})")

            # Ensure task_id in record matches normalized form
            merged_task['task_id'] = normalized_id
            migrated[normalized_id] = merged_task

    final_count = len(migrated)

    print(f"\n📈 Migration Summary:")
    print(f"   Original tasks: {original_count}")
    print(f"   Final tasks: {final_count}")
    print(f"   Merged groups: {merged_count}")
    print(f"   Reduction: {original_count - final_count} duplicates")

    if dry_run:
        print(f"\n⏭️  DRY RUN: No changes made")
        return {
            'status': 'success',
            'message': f"Dry run: would migrate {original_count} → {final_count} tasks",
            'original_count': original_count,
            'final_count': final_count,
            'merged_count': merged_count
        }

    # Write backup
    backup_path = f"{registry_path}.backup-{datetime.now().isoformat().replace(':', '-')}"
    print(f"\n💾 Creating backup: {backup_path}")
    try:
        shutil.copy(registry_path, backup_path)
    except Exception as e:
        print(f"   ⚠️  Warning: backup failed: {e}")

    # Write migrated registry
    new_registry = registry.copy()
    new_registry['tasks'] = migrated
    new_registry['migration'] = {
        'timestamp': datetime.now().isoformat(),
        'fix': 'FIX #2 (ADR-0863)',
        'reason': 'Task-ID canonicalization (lowercase snake_case)',
        'original_count': original_count,
        'final_count': final_count,
        'merged_groups': merged_count
    }

    try:
        with open(registry_path, 'w') as f:
            json.dump(new_registry, f, indent=2)
        print(f"✅ Migration complete: {registry_path}")
    except Exception as e:
        print(f"❌ Failed to write registry: {e}")
        print(f"   Restore from backup: {backup_path}")
        return {
            'status': 'error',
            'message': f"Failed to write registry: {e}",
            'original_count': original_count,
            'final_count': final_count,
            'merged_count': merged_count,
            'backup_path': backup_path
        }

    return {
        'status': 'success',
        'message': f"Migration complete: {original_count} → {final_count} tasks",
        'original_count': original_count,
        'final_count': final_count,
        'merged_count': merged_count,
        'backup_path': backup_path
    }


def main():
    parser = argparse.ArgumentParser(
        description="Migrate task registry to use canonical task-ids (lowercase snake_case)"
    )
    parser.add_argument(
        '--registry',
        type=Path,
        default=Path.home() / '.corvin' / 'task_registry.json',
        help='Path to task registry file (default: ~/.corvin/task_registry.json)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be done without modifying files'
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Print detailed migration info'
    )

    args = parser.parse_args()
    registry_path = args.registry

    # Verify registry exists
    if not registry_path.exists():
        print(f"❌ Registry not found: {registry_path}")
        print(f"   Run this after task_completion_registry.py has created the initial registry")
        sys.exit(1)

    # Perform migration
    result = migrate_registry(
        registry_path,
        dry_run=args.dry_run,
        verbose=args.verbose
    )

    print(f"\n{'='*60}")
    if result['status'] == 'success':
        print(f"✅ {result['message']}")
        if 'backup_path' in result:
            print(f"   Backup: {result['backup_path']}")
        sys.exit(0)
    else:
        print(f"❌ {result['message']}")
        sys.exit(1)


if __name__ == '__main__':
    main()
