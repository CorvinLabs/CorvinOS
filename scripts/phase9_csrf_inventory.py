#!/usr/bin/env python3
"""
Phase 1: CSRF Protection Inventory
Finds all POST/PUT/DELETE endpoints and identifies which need @require_csrf decorator.
"""

import re
import os
from pathlib import Path
from collections import defaultdict

# Configuration
REPO_ROOT = Path("/home/shumway/projects/CorvinOS")
MUTATION_METHODS = ["post", "put", "delete", "patch"]
CSRF_PATTERNS = [
    r"@require_csrf",
    r"@skip_csrf",
    r"@csrf_exempt",
]

def find_mutation_endpoints(file_path):
    """
    Find all POST/PUT/DELETE endpoints in a file and check for CSRF protection.
    Returns list of (line_number, method, function_name, has_csrf_protection, has_skip_decorator)
    """
    endpoints = []

    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
    except Exception as e:
        print(f"  ⚠️  Could not read {file_path}: {e}")
        return endpoints

    for i, line in enumerate(lines, 1):
        # Look for route decorators
        match = re.search(
            r"@(?:app|router|bp|blueprint)\.(post|put|delete|patch)\s*\(",
            line,
            re.IGNORECASE
        )
        if match:
            method = match.group(1).lower()

            # Look backward for CSRF decorators (within 5 lines)
            has_csrf = False
            has_skip = False
            for j in range(max(0, i-6), i):
                decorator_line = lines[j]
                if any(re.search(pat, decorator_line) for pat in CSRF_PATTERNS):
                    has_csrf = True
                    if "skip" in decorator_line or "exempt" in decorator_line:
                        has_skip = True
                    break

            # Look forward for function definition (next 2 lines)
            func_name = "unknown"
            for j in range(i, min(len(lines), i+2)):
                func_match = re.search(r"def\s+(\w+)\s*\(", lines[j])
                if func_match:
                    func_name = func_match.group(1)
                    break

            endpoints.append({
                'line': i,
                'method': method.upper(),
                'func_name': func_name,
                'has_csrf': has_csrf,
                'has_skip': has_skip,
                'file': file_path,
            })

    return endpoints

def scan_all_files():
    """Scan all Python files in the repository for mutation endpoints."""
    vulnerable = defaultdict(list)
    protected = defaultdict(list)
    skipped = defaultdict(list)

    count = 0
    for py_file in REPO_ROOT.rglob("*.py"):
        # Skip tests and scripts for now
        if "test_" in str(py_file) or "/scripts/" in str(py_file):
            continue

        # Skip virtual environments
        if "venv" in str(py_file) or ".venv" in str(py_file):
            continue

        if ".git" in str(py_file):
            continue

        count += 1
        if count % 100 == 0:
            print(f"  Scanning... {count} files processed")

        endpoints = find_mutation_endpoints(py_file)
        for ep in endpoints:
            if ep['has_skip']:
                skipped[py_file].append(ep)
            elif ep['has_csrf']:
                protected[py_file].append(ep)
            else:
                vulnerable[py_file].append(ep)

    return vulnerable, protected, skipped, count

def main():
    print("\n🔍 PHASE 1: CSRF Protection Inventory\n")
    print(f"Scanning repository: {REPO_ROOT}")
    print("=" * 80)

    vulnerable, protected, skipped, total_files = scan_all_files()

    # Count totals
    total_vulnerable = sum(len(eps) for eps in vulnerable.values())
    total_protected = sum(len(eps) for eps in protected.values())
    total_skipped = sum(len(eps) for eps in skipped.values())

    print(f"\n📊 RESULTS:")
    print(f"  Files scanned: {total_files}")
    print(f"  ✅ Protected endpoints (have @require_csrf): {total_protected}")
    print(f"  ⏭️  Skipped endpoints (have @skip_csrf): {total_skipped}")
    print(f"  ❌ Vulnerable endpoints (no CSRF protection): {total_vulnerable}")
    print(f"  Total mutation endpoints: {total_protected + total_skipped + total_vulnerable}")

    # Generate TODO list
    output_file = REPO_ROOT / "TODO_CSRF_ENDPOINTS.txt"
    with open(output_file, 'w') as f:
        f.write("CSRF PROTECTION TODO LIST\n")
        f.write("=" * 80 + "\n")
        f.write(f"Generated: {__import__('datetime').datetime.now().isoformat()}\n")
        f.write(f"Total vulnerable endpoints: {total_vulnerable}\n")
        f.write("=" * 80 + "\n\n")

        # Sort by file
        for file_path in sorted(vulnerable.keys()):
            rel_path = file_path.relative_to(REPO_ROOT)
            endpoints = vulnerable[file_path]

            f.write(f"\n📄 {rel_path}\n")
            f.write(f"   ({len(endpoints)} vulnerable endpoints)\n")
            f.write("-" * 80 + "\n")

            for ep in sorted(endpoints, key=lambda x: x['line']):
                f.write(f"   Line {ep['line']:4d}: {ep['method']:6s} {ep['func_name']}\n")

            f.write("\n")

    print(f"\n✅ Inventory saved to: {output_file}")

    # Print summary by directory
    print("\n📁 BREAKDOWN BY DIRECTORY:\n")
    by_dir = defaultdict(lambda: {'vulnerable': 0, 'protected': 0, 'skipped': 0})

    for file_path, endpoints in vulnerable.items():
        dir_name = file_path.parent.relative_to(REPO_ROOT)
        by_dir[dir_name]['vulnerable'] += len(endpoints)

    for file_path, endpoints in protected.items():
        dir_name = file_path.parent.relative_to(REPO_ROOT)
        by_dir[dir_name]['protected'] += len(endpoints)

    for file_path, endpoints in skipped.items():
        dir_name = file_path.parent.relative_to(REPO_ROOT)
        by_dir[dir_name]['skipped'] += len(endpoints)

    for dir_name in sorted(by_dir.keys()):
        counts = by_dir[dir_name]
        if counts['vulnerable'] > 0:
            print(f"  {dir_name}")
            print(f"    ❌ Vulnerable: {counts['vulnerable']}")
            if counts['protected'] > 0:
                print(f"    ✅ Protected: {counts['protected']}")
            if counts['skipped'] > 0:
                print(f"    ⏭️  Skipped: {counts['skipped']}")
            print()

    print("=" * 80)
    print("\n✅ Phase 1 Complete!\n")
    print(f"Next: Review TODO_CSRF_ENDPOINTS.txt and run Phase 2 (bulk fix script)\n")

if __name__ == "__main__":
    main()
