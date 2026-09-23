#!/usr/bin/env python3
"""
Phase 2: CSRF Protection Bulk Fixer (from TODO list) - v2
Parses TODO_CSRF_ENDPOINTS.txt and fixes all vulnerable endpoints
Robust parsing for UTF-8 and emojis
"""

import re
from pathlib import Path

REPO_ROOT = Path("/home/shumway/projects/CorvinOS")
TODO_FILE = REPO_ROOT / "TODO_CSRF_ENDPOINTS.txt"

def parse_todo_file():
    """Parse TODO_CSRF_ENDPOINTS.txt to extract file and line information."""
    if not TODO_FILE.exists():
        print(f"❌ TODO file not found: {TODO_FILE}")
        return {}

    endpoints_by_file = {}
    current_file = None

    with open(TODO_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.rstrip('\n')

            # Remove emoji (if present at start) and look for file path
            # The line might start with "📄" or just "  " for indented content
            clean_line = line.encode('utf-8').decode('utf-8', errors='ignore')

            # Check if line contains a file path with .py extension
            if '.py' in clean_line and '(' not in clean_line:
                # This is likely a file entry
                # Extract the path (everything before whitespace and parenthesis markers)
                match = re.search(r'([\w\./\-_]+\.py)', clean_line)
                if match:
                    current_file = match.group(1)
                    if current_file not in endpoints_by_file:
                        endpoints_by_file[current_file] = []

            # Look for line entries (line number format)
            elif current_file:
                match = re.search(r'Line\s+(\d+):', clean_line)
                if match:
                    line_num = int(match.group(1))
                    endpoints_by_file[current_file].append(line_num)

    return endpoints_by_file

def add_csrf_import(file_path, content):
    """Add @require_csrf import if not present."""
    if 'from core.security.csrf import require_csrf' in content:
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

def fix_file(file_path, line_numbers):
    """Fix specific line numbers in a file."""
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
    except Exception as e:
        return False, f"Could not read: {e}"

    # Sort line numbers in reverse to avoid index shifting
    line_numbers = sorted(set(line_numbers), reverse=True)

    fixed_count = 0
    for line_num in line_numbers:
        # Line numbers are 1-indexed, list is 0-indexed
        idx = line_num - 1

        if idx < 0 or idx >= len(lines):
            continue

        line = lines[idx]

        # Check if this is a route decorator
        if re.search(r'@(?:app|router|bp|blueprint)\.(post|put|delete|patch)\s*\(', line, re.IGNORECASE):
            # Check if CSRF decorator already exists in preceding lines
            has_csrf = False
            for j in range(max(0, idx-5), idx):
                if 'require_csrf' in lines[j] or 'skip_csrf' in lines[j] or 'csrf_exempt' in lines[j]:
                    has_csrf = True
                    break

            if not has_csrf:
                # Add @require_csrf decorator BEFORE the route decorator
                indent_match = re.match(r'^(\s*)', line)
                indent = indent_match.group(1) if indent_match else ''

                # Insert the decorator
                lines.insert(idx, f"{indent}@require_csrf\n")
                fixed_count += 1

    if fixed_count > 0:
        # Add import
        content = ''.join(lines)
        content = add_csrf_import(file_path, content)

        # Write back
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            return True, f"Fixed {fixed_count} endpoints"
        except Exception as e:
            return False, f"Could not write: {e}"

    return True, "No endpoints to fix"

def main():
    print("\n🔧 PHASE 2: CSRF Protection Bulk Fixer (from TODO list) - v2\n")
    print(f"Repo root: {REPO_ROOT}")
    print(f"TODO file: {TODO_FILE}")
    print("=" * 80)

    print("\n📍 Parsing TODO_CSRF_ENDPOINTS.txt...")
    endpoints_by_file = parse_todo_file()

    if not endpoints_by_file:
        print("❌ No endpoints found in TODO file!")
        return

    print(f"Found {len(endpoints_by_file)} files with vulnerable endpoints\n")

    total_endpoints = sum(len(lines) for lines in endpoints_by_file.values())
    print(f"Total vulnerable endpoints: {total_endpoints}\n")

    print("🔧 Fixing endpoints...")
    print("-" * 80)

    fixed_files = 0
    total_fixed = 0
    errors = 0
    not_found = 0

    for file_rel in sorted(endpoints_by_file.keys()):
        file_path = REPO_ROOT / file_rel
        line_numbers = endpoints_by_file[file_rel]

        if not file_path.exists():
            print(f"  ⚠️  {file_rel}: File not found")
            not_found += 1
            continue

        success, message = fix_file(file_path, line_numbers)

        if success:
            if "Fixed" in message:
                count = int(message.split()[1])
                total_fixed += count
                fixed_files += 1
                print(f"  ✅ {file_rel}: {message}")
            else:
                print(f"  ⏭️  {file_rel}: {message}")
        else:
            errors += 1
            print(f"  ❌ {file_rel}: {message}")

    print("-" * 80)
    print(f"\n📊 RESULTS:")
    print(f"  Files in TODO: {len(endpoints_by_file)}")
    print(f"  Files successfully processed: {fixed_files}")
    print(f"  Files not found: {not_found}")
    print(f"  Total endpoints fixed: {total_fixed}")
    print(f"  Errors: {errors}")

    if total_fixed > 0:
        print(f"\n✅ Phase 2 Complete! {total_fixed} vulnerable endpoints have been fixed.\n")
    else:
        print(f"\n⚠️  Phase 2 Complete with issues.\n")

if __name__ == "__main__":
    main()
