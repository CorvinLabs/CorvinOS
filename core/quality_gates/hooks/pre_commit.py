"""Enhanced git pre-commit hook for Quality Gates System (ADR-0688).

Detects ADR changes and validates them before commit.
"""

import subprocess
import json
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def get_staged_files() -> List[str]:
    """Get list of staged files from git.

    Returns:
        List of file paths
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip().split("\n") if result.stdout.strip() else []
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to get staged files: {e}")
        return []


def filter_adr_files(files: List[str]) -> List[str]:
    """Filter files to only ADRs in decisions/ directory.

    Args:
        files: List of file paths

    Returns:
        List of ADR file paths
    """
    adr_files = []
    for f in files:
        if "decisions/ADR-" in f and f.endswith(".md"):
            adr_files.append(f)
    return adr_files


def get_file_content(filepath: str) -> Optional[str]:
    """Get content of a file.

    Args:
        filepath: Path to file

    Returns:
        File content, or None if file not found
    """
    try:
        with open(filepath, "r") as f:
            return f.read()
    except Exception as e:
        logger.error(f"Failed to read {filepath}: {e}")
        return None


def extract_frontmatter(content: str) -> Optional[Dict[str, Any]]:
    """Extract YAML frontmatter from ADR markdown.

    Args:
        content: File content

    Returns:
        Parsed frontmatter as dict, or None if not found
    """
    if not content.startswith("---"):
        return None

    try:
        # Find closing ---
        lines = content.split("\n")
        end_idx = -1
        for i, line in enumerate(lines[1:], 1):
            if line.strip() == "---":
                end_idx = i
                break

        if end_idx == -1:
            return None

        # Parse YAML manually (simple key: value format)
        frontmatter = {}
        for line in lines[1:end_idx]:
            if ":" in line:
                key, value = line.split(":", 1)
                key = key.strip()
                value = value.strip()

                # Handle lists and simple values
                if value.startswith("["):
                    try:
                        frontmatter[key] = json.loads(value)
                    except:
                        frontmatter[key] = value
                else:
                    frontmatter[key] = value

        return frontmatter
    except Exception as e:
        logger.error(f"Failed to parse frontmatter: {e}")
        return None


def validate_adr_frontmatter(filepath: str, frontmatter: Dict[str, Any]) -> List[str]:
    """Validate ADR frontmatter.

    Args:
        filepath: Path to ADR file
        frontmatter: Parsed frontmatter

    Returns:
        List of violation messages (empty if valid)
    """
    violations = []

    # Check required fields
    required_fields = ["id", "status", "depends_on", "paths", "docs", "commits"]
    for field in required_fields:
        if field not in frontmatter:
            violations.append(f"  Missing field: {field}")
        elif isinstance(frontmatter[field], list) and len(frontmatter[field]) == 0:
            if field not in ["depends_on"]:  # depends_on can be empty
                violations.append(f"  Empty list: {field}")

    # Validate id format
    if "id" in frontmatter:
        id_val = frontmatter["id"]
        if not id_val.startswith("ADR-"):
            violations.append(f"  Invalid id format: {id_val} (must start with ADR-)")

    # Validate status
    if "status" in frontmatter:
        status = frontmatter["status"]
        valid_statuses = ["proposed", "accepted", "deprecated", "superseded"]
        if status not in valid_statuses:
            violations.append(f"  Invalid status: {status}")

    return violations


def validate_adr_file(filepath: str) -> List[str]:
    """Validate an ADR file.

    Args:
        filepath: Path to ADR file

    Returns:
        List of violation messages (empty if valid)
    """
    violations = []

    # Read file
    content = get_file_content(filepath)
    if content is None:
        violations.append(f"Could not read file")
        return violations

    # Extract frontmatter
    frontmatter = extract_frontmatter(content)
    if frontmatter is None:
        violations.append(f"Could not parse frontmatter (must start with ---)")
        return violations

    # Validate frontmatter
    frontmatter_violations = validate_adr_frontmatter(filepath, frontmatter)
    violations.extend(frontmatter_violations)

    return violations


def main() -> int:
    """Main hook logic.

    Returns:
        0 if all ADRs valid, 1 if any invalid
    """
    # Get staged files
    staged_files = get_staged_files()
    if not staged_files:
        return 0

    # Filter to ADR files
    adr_files = filter_adr_files(staged_files)
    if not adr_files:
        return 0

    # Validate each ADR
    violations_by_file = {}
    for adr_file in adr_files:
        violations = validate_adr_file(adr_file)
        if violations:
            violations_by_file[adr_file] = violations

    # Report results
    if violations_by_file:
        print("❌ ADR validation failed:")
        for adr_file, violations in violations_by_file.items():
            print(f"\n{adr_file}:")
            for violation in violations:
                print(violation)
        print("\nCommit blocked. Fix violations and try again.")
        return 1

    if adr_files:
        print(f"✓ ADR validation passed ({len(adr_files)} files)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
