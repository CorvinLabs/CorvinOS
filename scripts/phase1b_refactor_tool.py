#!/usr/bin/env python3
"""Phase 1b Wave 1 Refactoring Tool

Auto-refactors feature_flags API calls to Skills API.
Patterns: is_enabled, set_enabled, worker_engine_mode

IMPLEMENTATION NOTE (Layer 2 Design):
  This tool uses regex-based pattern matching (not AST).
  Trade-off: Fast & simple, but fragile with edge cases (import ordering, kwargs).

  For production use, consider AST-based rewriting:
    - ast.parse() to build full syntax tree
    - Safer import placement (respects __future__ rules)
    - Better kwarg parsing (ast.Call.keywords)
    - Full verification via ast.unparse()

  For now, this tool is suitable for Wave 1-2 (21-25 calls with simple patterns).
  Waves 3+ may need more sophisticated approach.

Usage: python3 scripts/phase1b_refactor_tool.py --file <path> --apply
       python3 scripts/phase1b_refactor_tool.py --wave1 [--apply]
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
        args = match.group(1)
        if ',' in args:
            parts = args.split(',', 1)
            flag = parts[0].strip()
            tenant = parts[1].strip()
            if '=' in tenant:
                tenant = f'"{tenant.split("=")[1].strip()}"'
        else:
            flag = args.strip()
            tenant = '"_default"'
        return f'feature_flags_skill.execute({{"operation": "is_enabled", "flag_id": {flag}, "tenant_id": {tenant}}})["result"]["enabled"]'

    # Match both variable-style (_ff.is_enabled) and module-style (_feature_flags_module.is_enabled)
    # Capturing group 1: argument list inside parentheses
    pattern = r'(?:_?(?:ff|cel_ff|pb_ff|_ff_badge)|_feature_flags_module)\.is_enabled\(([^)]*)\)'
    return re.sub(pattern, replace_fn, content, flags=re.DOTALL)

def refactor_set_enabled(content):
    """Refactor set_enabled() calls."""
    def replace_fn(match):
        args = match.group(1)
        parts = [p.strip() for p in args.split(',')]

        if len(parts) == 2:
            flag, enabled = parts
            if '=' in enabled:
                enabled = f'"{enabled.split("=")[1].strip()}"'
            return f'feature_flags_skill.execute({{"operation": "set_enabled", "flag_id": {flag}, "enabled": {enabled}, "tenant_id": "_default"}})'
        elif len(parts) >= 3:
            flag, enabled = parts[0], parts[1]
            tenant = ','.join(parts[2:])
            if '=' in tenant:
                tenant = f'"{tenant.split("=")[1].strip()}"'
            return f'feature_flags_skill.execute({{"operation": "set_enabled", "flag_id": {flag}, "enabled": {enabled}, "tenant_id": {tenant}}})'
        return match.group(0)

    # Match both variable-style (_ff.set_enabled) and module-style (_feature_flags_module.set_enabled)
    pattern = r'(?:_?ff|_feature_flags_module)\.set_enabled\(([^)]*)\)'
    return re.sub(pattern, replace_fn, content, flags=re.DOTALL)

def refactor_worker_engine_mode(content):
    """Refactor worker_engine_mode() calls."""
    def replace_fn(match):
        args = match.group(1)
        if args.strip():
            tenant = args.strip()
            if '=' in tenant:
                tenant = f'"{tenant.split("=")[1].strip()}"'
            return f'feature_flags_skill.execute({{"operation": "worker_engine_mode", "tenant_id": {tenant}}})["result"]["mode"]'
        else:
            return f'feature_flags_skill.execute({{"operation": "worker_engine_mode", "tenant_id": "_default"}})["result"]["mode"]'

    # Match both variable-style (_ff.worker_engine_mode) and module-style (_feature_flags_module.worker_engine_mode)
    pattern = r'(?:_?ff|_feature_flags_module)\.worker_engine_mode\(([^)]*)\)'
    return re.sub(pattern, replace_fn, content, flags=re.DOTALL)

def add_skill_import(content):
    """Add Skills import if not present, after shebang/docstring/future imports."""
    if 'from core.skills.feature_flags_skill import feature_flags_skill' in content:
        return content

    lines = content.split('\n')
    insert_idx = 0
    in_docstring = False
    docstring_quote = None
    found_imports = False

    for i, line in enumerate(lines):
        # Track docstrings (both """ and ''')
        if not in_docstring and ('"""' in line or "'''" in line):
            quote = '"""' if '"""' in line else "'''"
            if line.count(quote) == 2:  # Single-line docstring
                in_docstring = False
                continue
            else:
                in_docstring = True
                docstring_quote = quote
                continue

        if in_docstring and docstring_quote in line:
            in_docstring = False
            continue

        if in_docstring:
            continue

        # Skip shebang
        if i == 0 and line.startswith('#!'):
            insert_idx = i + 1
            continue

        # Track if we've seen any imports
        if line.startswith('from ') or line.startswith('import '):
            insert_idx = i + 1
            found_imports = True
        elif found_imports and not (line.startswith('from ') or line.startswith('import ')):
            break

    lines.insert(insert_idx, 'from core.skills.feature_flags_skill import feature_flags_skill')
    return '\n'.join(lines)

def refactor_file(file_path, apply=False):
    """Refactor a single file. Returns status dict + error (if any)."""
    path = Path(file_path)
    if not path.exists():
        return {"file": file_path, "status": "NOT_FOUND", "changes": 0}, None

    try:
        original = path.read_text(encoding='utf-8')
    except Exception as e:
        return {"file": file_path, "status": "READ_ERROR", "changes": 0}, str(e)

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
        try:
            # Atomic write: write to temp, then rename
            tmp = path.with_suffix('.tmp')
            tmp.write_text(content, encoding='utf-8')
            tmp.replace(path)
            return {"file": file_path, "status": "REFACTORED", "changes": changes}, None
        except Exception as e:
            return {"file": file_path, "status": "WRITE_ERROR", "changes": 0}, str(e)
    elif content != original:
        return {"file": file_path, "status": "READY", "changes": changes}, None
    else:
        return {"file": file_path, "status": "NO_CHANGES", "changes": 0}, None

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
