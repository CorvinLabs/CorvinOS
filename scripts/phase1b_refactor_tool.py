#!/usr/bin/env python3
"""Phase 1b Wave 1 Refactoring Tool

Auto-refactors feature_flags API calls to Skills API.
Patterns: is_enabled, set_enabled, worker_engine_mode

Usage: python3 scripts/phase1b_refactor_tool.py --file <path> --apply
       python3 scripts/phase1b_refactor_tool.py --wave1 (refactor all Wave 1 files)
"""

import re
import sys
from pathlib import Path

# Wave 1 target files (8 with actual calls)
WAVE_1_FILES = [
    "operator/bridges/shared/adapter.py",
    "core/console/corvin_console/routes/settings.py",
    "tests/test_tde_measurement_k3_decision_collection.py",
    "operator/bridges/shared/remote_trigger_sender.py",
    "operator/context_engineering/pipeline.py",
    "operator/bridges/shared/bg_monitor.py",
    "operator/bridges/shared/acs_runtime.py",
    "operator/bridges/shared/a2a_friendship.py",
]

def refactor_is_enabled(content):
    """Refactor is_enabled() calls to Skills API."""
    def replace_fn(match):
        args = match.group(2)
        if ',' in args:
            flag, tenant = args.split(',', 1)
            flag = flag.strip()
            tenant = tenant.strip()
        else:
            flag = args.strip()
            tenant = '"_default"'
        return f'feature_flags_skill.execute({{"operation": "is_enabled", "flag_id": {flag}, "tenant_id": {tenant}}})["result"]["enabled"]'

    pattern = r'(_?(?:ff|cel_ff|pb_ff|_ff_badge))\.is_enabled\(([^)]+)\)'
    return re.sub(pattern, replace_fn, content)

def refactor_set_enabled(content):
    """Refactor set_enabled() calls."""
    def replace_fn(match):
        args = match.group(2)
        parts = [p.strip() for p in args.split(',')]

        if len(parts) == 2:
            flag, enabled = parts
            return f'feature_flags_skill.execute({{"operation": "set_enabled", "flag_id": {flag}, "enabled": {enabled}, "tenant_id": "_default"}})'
        elif len(parts) >= 3:
            flag, enabled = parts[0], parts[1]
            tenant = ','.join(parts[2:])
            return f'feature_flags_skill.execute({{"operation": "set_enabled", "flag_id": {flag}, "enabled": {enabled}, "tenant_id": {tenant}}})'
        return match.group(0)

    pattern = r'(_?ff)\.set_enabled\(([^)]+)\)'
    return re.sub(pattern, replace_fn, content)

def refactor_worker_engine_mode(content):
    """Refactor worker_engine_mode() calls."""
    def replace_fn(match):
        args = match.group(2)
        if args.strip():
            return f'feature_flags_skill.execute({{"operation": "worker_engine_mode", "tenant_id": {args}}})["result"]["mode"]'
        else:
            return f'feature_flags_skill.execute({{"operation": "worker_engine_mode", "tenant_id": "_default"}})["result"]["mode"]'

    pattern = r'(_?ff)\.worker_engine_mode\(([^)]*)\)'
    return re.sub(pattern, replace_fn, content)

def add_skill_import(content):
    """Add Skills import if not present."""
    if 'from core.skills.feature_flags_skill import feature_flags_skill' in content:
        return content

    # Add after existing imports
    lines = content.split('\n')
    insert_idx = 0

    # Find first import after module docstring
    in_docstring = False
    docstring_quote = None
    for i, line in enumerate(lines):
        if '"""' in line or "'''" in line:
            if not in_docstring:
                in_docstring = True
                docstring_quote = '"""' if '"""' in line else "'''"
            elif docstring_quote in line:
                in_docstring = False
        elif not in_docstring and (line.startswith('from ') or line.startswith('import ')):
            insert_idx = i
            break

    lines.insert(insert_idx, 'from core.skills.feature_flags_skill import feature_flags_skill')
    return '\n'.join(lines)

def refactor_file(file_path, apply=False):
    """Refactor a single file."""
    path = Path(file_path)
    if not path.exists():
        return {"file": file_path, "status": "NOT_FOUND", "changes": 0}

    original = path.read_text(encoding='utf-8')
    content = original

    # Count before
    before_is_enabled = len(re.findall(r'\.is_enabled\(', content))
    before_set_enabled = len(re.findall(r'\.set_enabled\(', content))
    before_engine = len(re.findall(r'\.worker_engine_mode\(', content))

    # Apply transformations
    content = refactor_is_enabled(content)
    content = refactor_set_enabled(content)
    content = refactor_worker_engine_mode(content)
    content = add_skill_import(content)

    # Count after
    after_is_enabled = len(re.findall(r'\.is_enabled\(', content))
    after_set_enabled = len(re.findall(r'\.set_enabled\(', content))
    after_engine = len(re.findall(r'\.worker_engine_mode\(', content))

    changes = (before_is_enabled - after_is_enabled) + (before_set_enabled - after_set_enabled) + (before_engine - after_engine)

    if apply and content != original:
        path.write_text(content, encoding='utf-8')
        return {"file": file_path, "status": "REFACTORED", "changes": changes}
    elif content != original:
        return {"file": file_path, "status": "READY", "changes": changes}
    else:
        return {"file": file_path, "status": "NO_CHANGES", "changes": 0}

if __name__ == '__main__':
    if '--wave1' in sys.argv:
        apply = '--apply' in sys.argv
        print("╔════════════════════════════════════════════════════════════╗")
        print("║        PHASE 1B WAVE 1: REFACTORING EXECUTION             ║")
        print("╚════════════════════════════════════════════════════════════╝")
        print()

        total_changes = 0
        for file_path in WAVE_1_FILES:
            result = refactor_file(file_path, apply=apply)
            status_emoji = "✅" if result["changes"] > 0 else "⏭️"
            print(f"{status_emoji} {result['file']}: {result['status']} ({result['changes']} changes)")
            total_changes += result['changes']

        print()
        print(f"TOTAL CHANGES: {total_changes}")
        print(f"MODE: {'APPLIED' if apply else 'DRY RUN'}")

    elif '--file' in sys.argv:
        idx = sys.argv.index('--file')
        if idx + 1 < len(sys.argv):
            file_path = sys.argv[idx + 1]
            apply = '--apply' in sys.argv
            result = refactor_file(file_path, apply=apply)
            print(f"{result['file']}: {result['status']} ({result['changes']} changes)")

    else:
        print("Usage:")
        print("  python3 scripts/phase1b_refactor_tool.py --wave1 [--apply]")
        print("  python3 scripts/phase1b_refactor_tool.py --file <path> [--apply]")
