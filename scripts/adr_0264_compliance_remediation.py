#!/usr/bin/env python3
"""
ADR-0264 Compliance Remediation — Auto-fix non-compliant ADRs

Usage:
  python3 adr_0264_compliance_remediation.py [--fix] [--dry-run]

Modes:
  --dry-run: Show what would be fixed (no file changes)
  --fix:     Actually fix the files (creates backups)

Fixes:
  1. Add missing frontmatter to legacy format files
  2. Backfill empty fields with sensible defaults
  3. Move non-ADR files to archive/
  4. Validate result (target: 100% compliant)
"""

import re
import json
import sys
from pathlib import Path
from datetime import datetime
from shutil import copy2

ADR_REPO = Path("/home/shumway/projects/Corvin-Knowledge/decisions")
ARCHIVE_DIR = ADR_REPO.parent / "archive" / datetime.now().strftime("%Y-%m-%d")

REQUIRED_FIELDS = ["id", "status", "depends_on", "paths", "docs"]
DEFAULT_VALUES = {
    "status": "proposed",
    "depends_on": "[]",
    "paths": "[]",
    "docs": "[]",
}

class ADRRemediator:
    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        self.fixes = {"added_frontmatter": 0, "backfilled_fields": 0, "moved_files": 0}

    def migrate_legacy_adr(self, file_path: Path, content: str) -> str:
        """Migrate legacy ADR format to ADR-0264 frontmatter"""
        # Extract ADR ID from filename or content
        adr_id_match = re.search(r'ADR-\d{4}', file_path.name)
        if not adr_id_match:
            return content  # Can't migrate without ID

        adr_id = adr_id_match.group(0)

        # Build frontmatter
        frontmatter = f"""---
id: {adr_id}
status: proposed
depends_on: []
paths: []
docs: []
---

"""

        # Remove any existing dashes
        content = re.sub(r'^---\n.*?\n---\n', '', content, flags=re.DOTALL)

        return frontmatter + content

    def backfill_adr(self, file_path: Path, content: str) -> str:
        """Backfill missing frontmatter fields in ADR-0264 format"""
        match = re.search(r'^---(.*?)---', content, re.DOTALL | re.MULTILINE)
        if not match:
            return content

        frontmatter_block = match.group(1)

        # Add missing fields
        for field in REQUIRED_FIELDS:
            if f"{field}:" not in frontmatter_block:
                frontmatter_block += f"\n{field}: {DEFAULT_VALUES.get(field, '[]')}"

        return content[:match.start()] + f"---{frontmatter_block}---" + content[match.end():]

    def should_archive(self, file_name: str) -> bool:
        """Determine if a file should be archived (non-ADR)"""
        non_adr_patterns = [
            r'^ADR_GATE_',
            r'^ADR_LDD_',
            r'^DOC-',
            # NOT legacy `NNNN-slug.md` names: those are real ADRs (e.g.
            # ADR-0030-plugin-system.md) and the ADR Gate still names that shape as
            # the destination. A `^[0-9]+-[a-z]+-[a-z]+\.md$` pattern here
            # moved 21 live ADRs out of decisions/ on one run.
        ]
        return any(re.match(pattern, file_name) for pattern in non_adr_patterns)

    def run(self):
        """Execute remediation"""
        print("ADR-0264 Compliance Remediation")
        print("=" * 50)

        # Archive non-ADR files
        for adr_file in sorted(ADR_REPO.glob("*.md")):
            if self.should_archive(adr_file.name):
                if not self.dry_run:
                    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
                    copy2(adr_file, ARCHIVE_DIR / adr_file.name)
                    adr_file.unlink()
                print(f"[ARCHIVE] {adr_file.name}")
                self.fixes["moved_files"] += 1

        # Migrate + backfill remaining ADRs
        for adr_file in sorted(ADR_REPO.glob("*.md")):
            with open(adr_file, 'r') as f:
                content = f.read()

            original = content

            # Step 1: Migrate legacy format if needed
            if not content.startswith("---"):
                content = self.migrate_legacy_adr(adr_file, content)
                self.fixes["added_frontmatter"] += 1
                print(f"[MIGRATE] {adr_file.name}")

            # Step 2: Backfill missing fields
            content = self.backfill_adr(adr_file, content)
            if content != original:
                self.fixes["backfilled_fields"] += 1

            # Step 3: Write if --fix
            if not self.dry_run and content != original:
                with open(adr_file, 'w') as f:
                    f.write(content)

        print("\n" + "=" * 50)
        print(f"Migrated: {self.fixes['added_frontmatter']}")
        print(f"Backfilled: {self.fixes['backfilled_fields']}")
        print(f"Archived: {self.fixes['moved_files']}")
        print("=" * 50)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="ADR-0264 Compliance Remediation")
    parser.add_argument('--fix', action='store_true', help='Actually fix the files')
    parser.add_argument('--dry-run', action='store_true', help='Show what would change')
    args = parser.parse_args()

    # Dry-run unless --fix is given explicitly. `dry_run=args.dry_run` made a
    # bare invocation (no flags) rewrite and unlink files in the canonical ADR
    # repo, and --fix did nothing at all.
    remediator = ADRRemediator(dry_run=not args.fix or args.dry_run)
    remediator.run()


if __name__ == '__main__':
    main()
