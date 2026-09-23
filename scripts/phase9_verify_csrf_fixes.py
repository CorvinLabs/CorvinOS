#!/usr/bin/env python3
"""
Phase 4: Verify CSRF Protection Fixes
Scans all modified files to ensure @require_csrf is present
"""

import re
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).parent.parent

# Patterns
MUTATION_PATTERNS = [
    r'@(?:app|router|bp|blueprint)\.post\s*\(',
    r'@(?:app|router|bp|blueprint)\.put\s*\(',
    r'@(?:app|router|bp|blueprint)\.delete\s*\(',
    r'@(?:app|router|bp|blueprint)\.patch\s*\(',
]

CSRF_PATTERNS = [
    r'@require_csrf',
    r'@skip_csrf',
    r'@csrf_exempt',
]

def find_mutation_endpoints(file_path):
    """Find all mutation endpoints and check CSRF protection."""
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
    except:
        return []

    results = []

    for i, line in enumerate(lines, 1):
        # Check for mutation decorator
        if any(re.search(pat, line, re.IGNORECASE) for pat in MUTATION_PATTERNS):
            # Check if CSRF protection exists in preceding lines
            has_csrf = False
            for j in range(max(0, i-6), i):
                if any(re.search(pat, lines[j-1]) for pat in CSRF_PATTERNS):
                    has_csrf = True
                    break

            # Extract function name
            func_name = "unknown"
            for j in range(i, min(len(lines), i+2)):
                func_match = re.search(r'def\s+(\w+)\s*\(', lines[j-1])
                if func_match:
                    func_name = func_match.group(1)
                    break

            results.append({
                'line': i,
                'file': file_path,
                'func': func_name,
                'protected': has_csrf,
            })

    return results

def verify_imports(file_path):
    """Check if file has require_csrf import when using @require_csrf."""
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
    except:
        return True

    # If file uses @require_csrf, it must have the import
    if '@require_csrf' in content:
        if 'from core.security.csrf import require_csrf' not in content:
            return False

    return True

def main():
    print("\n🔐 PHASE 4: Verify CSRF Protection Fixes\n")
    print(f"Repository: {REPO_ROOT}")
    print("=" * 80)

    # Get list of modified files from git
    import subprocess
    try:
        result = subprocess.run(
            ['git', 'diff', '--name-only', 'HEAD~1', 'HEAD'],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True
        )
        modified_files = [f for f in result.stdout.strip().split('\n') if f and f.endswith('.py')]
    except:
        print("❌ Could not get modified files from git")
        return 1

    print(f"\nModified files in last commit: {len(modified_files)}")

    # Check console routes
    console_routes = REPO_ROOT / "core/console/corvin_console/routes"
    gateway_routes = REPO_ROOT / "core/gateway/routes"

    all_endpoints = []
    unprotected = []
    import_errors = []

    print("\n📊 Scanning for mutation endpoints...")
    print("-" * 80)

    # Scan console routes
    if console_routes.exists():
        for py_file in console_routes.glob("*.py"):
            endpoints = find_mutation_endpoints(py_file)
            all_endpoints.extend(endpoints)

            # Check each endpoint
            for ep in endpoints:
                if not ep['protected']:
                    unprotected.append(ep)

            # Check imports
            if not verify_imports(py_file):
                import_errors.append(py_file)

    # Scan gateway routes
    if gateway_routes.exists():
        for py_file in gateway_routes.glob("*.py"):
            endpoints = find_mutation_endpoints(py_file)
            all_endpoints.extend(endpoints)

            for ep in endpoints:
                if not ep['protected']:
                    unprotected.append(ep)

            if not verify_imports(py_file):
                import_errors.append(py_file)

    # Scan standalone
    standalone = REPO_ROOT / "core/console/corvin_console/standalone.py"
    if standalone.exists():
        endpoints = find_mutation_endpoints(standalone)
        all_endpoints.extend(endpoints)

        for ep in endpoints:
            if not ep['protected']:
                unprotected.append(ep)

        if not verify_imports(standalone):
            import_errors.append(standalone)

    # Print results
    protected_count = len([ep for ep in all_endpoints if ep['protected']])

    print(f"\n📊 RESULTS:")
    print(f"  Total mutation endpoints scanned: {len(all_endpoints)}")
    print(f"  ✅ Protected endpoints (@require_csrf): {protected_count}")
    print(f"  ❌ Unprotected endpoints: {len(unprotected)}")
    print(f"  ⚠️  Import errors: {len(import_errors)}")

    # Show unprotected endpoints
    if unprotected:
        print(f"\n❌ UNPROTECTED ENDPOINTS (first 20):")
        for ep in unprotected[:20]:
            print(f"   {ep['file'].relative_to(REPO_ROOT)}:{ep['line']} ({ep['func']})")
        if len(unprotected) > 20:
            print(f"   ... and {len(unprotected) - 20} more")

    # Show import errors
    if import_errors:
        print(f"\n⚠️  IMPORT ERRORS:")
        for f in import_errors:
            print(f"   {f.relative_to(REPO_ROOT)} — missing 'from core.security.csrf import require_csrf'")

    print("\n" + "=" * 80)

    if unprotected or import_errors:
        print("\n❌ VERIFICATION FAILED")
        return 1
    elif len(all_endpoints) == 0:
        print("\n⏭️  NO MUTATION ENDPOINTS FOUND (may be OK)")
        return 0
    else:
        print(f"\n✅ VERIFICATION PASSED — All {len(all_endpoints)} mutation endpoints protected")
        return 0

if __name__ == "__main__":
    sys.exit(main())
