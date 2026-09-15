#!/usr/bin/env python3
"""
ADR-to-Memory Sync Script

Liest alle ADRs aus Corvin-ADR/decisions/ und aktualisiert die Memory-Datei
automatisch. Läuft nach jedem Major Merge (via Cron/Post-Merge Hook).

Usage:
  python3 operator/scripts/sync_memory_from_adrs.py [--dry-run] [--commit]

Output:
  ~/.claude/projects/CorvinOS/memory/ADR-INDEX.md (aktualisiert)
"""

import os
import sys
import json
import re
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


@dataclass
class ADREntry:
    """Parsed ADR metadata"""
    id: str
    title: str
    status: str
    paths: List[str]
    docs: List[str]
    depends_on: List[str]
    file_path: Path


def parse_frontmatter(content: str) -> Tuple[Dict, str]:
    """
    Parse YAML-style frontmatter from ADR.

    Returns: (frontmatter_dict, body)
    """
    if not content.startswith("---"):
        return {}, content

    lines = content.split("\n", 1)
    if len(lines) < 2:
        return {}, content

    rest = lines[1]
    if "---" not in rest:
        return {}, content

    fm_text, body = rest.split("---", 1)

    # Try YAML parser if available
    if HAS_YAML:
        try:
            fm_dict = yaml.safe_load(fm_text) or {}
            return fm_dict, body
        except Exception:
            pass

    # Fallback: simple line-by-line parser
    fm_dict = {}
    current_key = None
    current_list = []

    for line in fm_text.split("\n"):
        stripped = line.strip()

        if not stripped or stripped.startswith("#"):
            continue

        # Multi-line list item (starts with -)
        if stripped.startswith("- "):
            if current_key:
                item = stripped[2:].strip().strip('"').strip("'")
                current_list.append(item)
            continue

        # New key
        if ":" in line and not line.startswith(" "):
            # Save previous key if it was a list
            if current_key and current_list:
                fm_dict[current_key] = current_list
                current_list = []

            key, val = line.split(":", 1)
            key = key.strip()
            val = val.strip()
            current_key = key

            # Single-line value
            if val and not val.endswith(":"):
                if val.startswith("[") and val.endswith("]"):
                    try:
                        fm_dict[key] = json.loads(val.replace("'", '"'))
                    except (json.JSONDecodeError, ValueError):
                        fm_dict[key] = [v.strip().strip('"') for v in val[1:-1].split(",") if v.strip()]
                elif val.lower() in ("true", "false"):
                    fm_dict[key] = val.lower() == "true"
                else:
                    fm_dict[key] = val
                current_key = None
            elif not val:
                # Might be a list key
                current_list = []

    # Save last key if it was a list
    if current_key and current_list:
        fm_dict[current_key] = current_list

    return fm_dict, body


def find_adrs(adr_repo: Path) -> List[ADREntry]:
    """
    Finde alle ADRs in Corvin-ADR/decisions/.

    Returns: List[ADREntry]
    """
    entries: List[ADREntry] = []
    decisions_dir = adr_repo / "decisions"

    if not decisions_dir.exists():
        print(f"⚠ ADR repo not found: {decisions_dir}", file=sys.stderr)
        return entries

    for file_path in sorted(decisions_dir.glob("ADR-*.md")):
        try:
            content = file_path.read_text(encoding="utf-8")
            fm, _ = parse_frontmatter(content)

            # Extract title from filename
            match = re.match(r"ADR-(\d{4})-(.*?)\.md", file_path.name)
            if not match:
                continue

            adr_id = f"ADR-{match.group(1)}"
            title = match.group(2).replace("-", " ").title()

            status = fm.get("status", "UNKNOWN")
            if isinstance(status, str):
                status = status.upper()

            entry = ADREntry(
                id=adr_id,
                title=fm.get("title", title),
                status=status,
                paths=fm.get("paths", []) or [],
                docs=fm.get("docs", []) or [],
                depends_on=fm.get("depends_on", []) or [],
                file_path=file_path,
            )
            entries.append(entry)
        except Exception as e:
            print(f"⚠ Error parsing {file_path.name}: {e}", file=sys.stderr)
            continue

    return entries


def generate_memory_index(entries: List[ADREntry]) -> str:
    """
    Generiere Memory-Datei aus ADR-Einträgen.

    Returns: Markdown content
    """
    timestamp = datetime.now().isoformat()

    md = f"""# ADR Index — Auto-Synced from Corvin-ADR

**Last updated:** {timestamp}

This file is auto-generated from `Corvin-ADR/decisions/` to keep memory in sync with
architectural decisions. Do NOT edit manually — run `python3 operator/scripts/sync_memory_from_adrs.py --commit`
to regenerate.

---

## Active ADRs (PROPOSED, ACCEPTED, IMPLEMENTED)

| ID | Status | Title | Modules | Depends On |
|---|---|---|---|---|
"""

    # Filter by status
    active_statuses = {"PROPOSED", "ACCEPTED", "IMPLEMENTED"}
    active_entries = [e for e in entries if e.status in active_statuses]

    for entry in sorted(active_entries, key=lambda e: e.id):
        modules = ", ".join(entry.paths[:2]) if entry.paths else "—"
        if len(entry.paths) > 2:
            modules += f", +{len(entry.paths) - 2} more"
        depends = ", ".join(entry.depends_on) if entry.depends_on else "—"

        md += f"| {entry.id} | {entry.status} | {entry.title} | {modules} | {depends} |\n"

    md += "\n---\n\n## Deprecated/Superseded\n\n| ID | Status | Title |\n|---|---|---|\n"

    deprecated = [e for e in entries if e.status not in active_statuses]
    for entry in sorted(deprecated, key=lambda e: e.id):
        md += f"| {entry.id} | {entry.status} | {entry.title} |\n"

    md += f"""

---

## Quick Lookup

### By Module (core/*)

"""

    # Group by module
    module_adrs: Dict[str, List[ADREntry]] = {}
    for entry in entries:
        for path in entry.paths:
            module = path.split("/")[1] if "/" in path else path
            if module not in module_adrs:
                module_adrs[module] = []
            module_adrs[module].append(entry)

    for module in sorted(module_adrs.keys()):
        md += f"**{module}:**\n"
        for entry in module_adrs[module]:
            md += f"  • {entry.id} — {entry.title}\n"

    md += f"""

### By Regulation (GDPR, EU AI Act, etc.)

See `Corvin-ADR/decisions/` for regulation annotations in ADR frontmatter.

---

## Metadata

Total ADRs: {len(entries)}
- Active: {len(active_entries)}
- Deprecated: {len(deprecated)}

Generated by: `operator/scripts/sync_memory_from_adrs.py`
Sync trigger: Post-merge hook + nightly cron
"""

    return md


def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Sync ADRs from Corvin-ADR to CorvinOS memory"
    )
    parser.add_argument("--dry-run", action="store_true", help="Show changes without writing")
    parser.add_argument("--commit", action="store_true", help="Git commit after update")
    args = parser.parse_args()

    # Locate repos
    corvinos_root = Path(__file__).parent.parent.parent  # CorvinOS/
    adr_repo = corvinos_root.parent / "Corvin-ADR"
    memory_dir = Path.home() / ".claude" / "projects" / "CorvinOS" / "memory"
    memory_file = memory_dir / "ADR-INDEX.md"

    if not adr_repo.exists():
        print(f"ERROR: Corvin-ADR not found at {adr_repo}", file=sys.stderr)
        sys.exit(1)

    memory_dir.mkdir(parents=True, exist_ok=True)

    print(f"📚 Reading ADRs from {adr_repo}/decisions/...")
    entries = find_adrs(adr_repo)
    print(f"✓ Found {len(entries)} ADRs")

    print(f"📝 Generating memory index...")
    content = generate_memory_index(entries)

    if args.dry_run:
        print(f"\n--- DRY RUN ---\n{content}\n--- END DRY RUN ---")
        return 0

    print(f"💾 Writing {memory_file}...")
    memory_file.write_text(content, encoding="utf-8")
    print(f"✓ Updated {memory_file}")

    if args.commit:
        os.chdir(corvinos_root)
        result = os.system(
            f'git add {memory_file} && git commit -m "chore: sync memory from ADRs" 2>&1 | head -5'
        )
        if result == 0:
            print("✓ Committed to git")
        else:
            print("⚠ Git commit failed or nothing to commit")

    return 0


if __name__ == "__main__":
    sys.exit(main())
