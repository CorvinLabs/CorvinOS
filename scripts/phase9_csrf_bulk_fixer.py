#!/usr/bin/env python3
"""
Phase 2: CSRF Protection Bulk Fixer
Automatically adds @require_csrf decorator to all vulnerable endpoints
"""

import re
import os
from pathlib import Path
from collections import defaultdict

REPO_ROOT = Path("/home/shumway/projects/CorvinOS")

# Paths to ignore (venv, external libs, tests)
IGNORE_DIRS = {
    'venv_temp',
    'venv',
    '.venv',
    'site-packages',
    '__pycache__',
    '.pytest_cache',
    '.claude/worktrees',
}

def should_ignore(file_path):
    """Check if file should be ignored."""
    path_str = str(file_path)
    for ignore_dir in IGNORE_DIRS:
        if ignore_dir in path_str.split(os.sep):
            return True
    return False

def check_require_csrf_import(file_path):
    """Check if file imports require_csrf."""
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        return 'require_csrf' in content or 'from core.security.csrf import' in content
    except:
        return False

def add_csrf_import(content):
    """Add @require_csrf import if not present."""
    if 'from core.security.csrf import require_csrf' in content:
        return content

    if 'require_csrf' in content:
        # Already has require_csrf somewhere
        if 'from core.security.csrf' not in content:
            # Add the import
            lines = content.split('\n')
            import_idx = 0
            for i, line in enumerate(lines):
                if line.startswith('from ') or line.startswith('import '):
                    import_idx = i + 1
                elif line and not line.startswith('#'):
                    break
            lines.insert(import_idx, 'from core.security.csrf import require_csrf')
            content = '\n'.join(lines)
        return content

    # Add the import at the top of imports
    lines = content.split('\n')
    import_idx = 0
    for i, line in enumerate(lines):
        if line.startswith('from ') or line.startswith('import '):
            import_idx = i + 1
        elif line.strip() and not line.startswith('#'):
            # We've passed all imports
            break

    lines.insert(import_idx, 'from core.security.csrf import require_csrf')
    return '\n'.join(lines)

def fix_file(file_path):
    """Fix CSRF vulnerabilities in a file."""
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            original_content = f.read()
    except:
        return False, "Could not read file"

    content = original_content
    lines = content.split('\n')
    fixed_count = 0

    i = 0
    while i < len(lines):
        line = lines[i]

        # Check for route decorator
        if re.search(r'@(?:app|router|bp|blueprint)\.(post|put|delete|patch)\s*\(', line, re.IGNORECASE):
            # Check if CSRF decorator already exists in preceding lines
            has_csrf = False
            for j in range(max(0, i-5), i):
                if 'require_csrf' in lines[j] or 'skip_csrf' in lines[j] or 'csrf_exempt' in lines[j]:
                    has_csrf = True
                    break

            if not has_csrf:
                # Add @require_csrf decorator BEFORE the route decorator
                # Determine indentation
                indent_match = re.match(r'^(\s*)', line)
                indent = indent_match.group(1) if indent_match else ''

                # Insert the decorator
                lines.insert(i, f"{indent}@require_csrf")
                fixed_count += 1
                i += 1  # Skip the newly inserted line

        i += 1

    if fixed_count > 0:
        # Add import if needed
        new_content = '\n'.join(lines)
        new_content = add_csrf_import(new_content)

        # Write the file
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            return True, f"Fixed {fixed_count} endpoints"
        except Exception as e:
            return False, f"Could not write file: {e}"

    return True, f"No endpoints needed fixing"

def find_vulnerable_files():
    """Find all files with vulnerable endpoints."""
    vulnerable_files = set()

    for py_file in REPO_ROOT.rglob("*.py"):
        if should_ignore(py_file):
            continue

        try:
            with open(py_file, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()

            # Check for route decorators
            if re.search(r'@(?:app|router|bp|blueprint)\.(post|put|delete|patch)\s*\(', content, re.IGNORECASE):
                # Check if has csrf protection
                if 'require_csrf' not in content and 'skip_csrf' not in content and 'csrf_exempt' not in content:
                    vulnerable_files.add(py_file)
        except:
            pass

    return vulnerable_files

def main():
    print("\n🔧 PHASE 2: CSRF Protection Bulk Fixer\n")
    print(f"Scanning repository: {REPO_ROOT}")
    print("=" * 80)

    print("\n📍 Finding vulnerable files...")
    vulnerable_files = find_vulnerable_files()

    print(f"Found {len(vulnerable_files)} files with vulnerable endpoints\n")

    if len(vulnerable_files) == 0:
        print("✅ No vulnerable endpoints found!")
        return

    print("🔧 Fixing endpoints...")
    print("-" * 80)

    fixed_files = 0
    total_fixes = 0
    errors = 0

    for file_path in sorted(vulnerable_files):
        rel_path = file_path.relative_to(REPO_ROOT)
        success, message = fix_file(file_path)

        if success:
            fixed_files += 1
            if "Fixed" in message:
                count = int(message.split()[1])
                total_fixes += count
                print(f"  ✅ {rel_path}: {message}")
            else:
                print(f"  ⏭️  {rel_path}: {message}")
        else:
            errors += 1
            print(f"  ❌ {rel_path}: {message}")

    print("-" * 80)
    print(f"\n📊 RESULTS:")
    print(f"  Files processed: {len(vulnerable_files)}")
    print(f"  Files successfully fixed: {fixed_files}")
    print(f"  Total endpoints fixed: {total_fixes}")
    print(f"  Errors: {errors}")

    if errors == 0:
        print(f"\n✅ Phase 2 Complete! All {total_fixes} vulnerable endpoints have been fixed.\n")
        print("Next: Run Phase 3 to test the fixes\n")
    else:
        print(f"\n⚠️  Phase 2 Complete with {errors} errors.\n")

if __name__ == "__main__":
    main()
