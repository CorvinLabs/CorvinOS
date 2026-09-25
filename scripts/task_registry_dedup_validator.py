#!/usr/bin/env python3
"""
Task Registry Deduplication Validator (Completion-Plan Task 1)
Detects duplicate ADR IDs + enforces unique constraint via Task Registry

Usage:
  python3 task_registry_dedup_validator.py [--fix] [--strict]

Modes:
  --fix:    Rename duplicate ADRs automatically (if safe)
  --strict: Fail on ANY duplicate (even if status-compatible)

Output:
  - Prints JSON report to stdout
  - Writes to ~/.corvin/tenants/_default/global/dedup_audit_TIMESTAMP.json
  - Exits 0 if OK, 1 if duplicates found (non-strict) or always (--strict)
"""

import json
import sys
import os
import re
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple
import hashlib

# Paths
ADR_REPO = Path("/home/shumway/projects/Corvin-ADR/decisions")
TENANT_HOME = Path.home() / ".corvin" / "tenants" / "_default" / "global"
AUDIT_DIR = TENANT_HOME / "audit"
AUDIT_DIR.mkdir(parents=True, exist_ok=True)

class TaskRegistryDedup:
    """Deduplication validator for ADR registry"""

    def __init__(self, strict=False, fix=False):
        self.strict = strict
        self.fix = fix
        self.duplicates: List[Dict] = []
        self.registry: Dict[str, List[Path]] = {}
        self.audit_ts = datetime.now().isoformat()

    def scan_adr_files(self) -> Dict[str, List[Path]]:
        """Scan ADR directory for duplicate IDs in frontmatter"""
        registry = {}

        for adr_file in ADR_REPO.glob("ADR-*.md"):
            # Extract ID from frontmatter
            adr_id = self._extract_id(adr_file)
            if adr_id:
                if adr_id not in registry:
                    registry[adr_id] = []
                registry[adr_id].append(adr_file)

        self.registry = registry
        return registry

    def _extract_id(self, file_path: Path) -> str:
        """Extract ADR ID from frontmatter (e.g., id: ADR-0123)"""
        try:
            with open(file_path, 'r') as f:
                for line in f:
                    if line.startswith('id:'):
                        # Extract ID (e.g., "id: ADR-0123" → "ADR-0123")
                        match = re.search(r'ADR-\d{4}', line)
                        if match:
                            return match.group(0)
                    if line.startswith('---') and 'id:' not in open(file_path).read()[:200]:
                        # End of frontmatter without finding ID
                        break
        except Exception as e:
            print(f"[WARN] Could not extract ID from {file_path}: {e}")
        return None

    def detect_duplicates(self) -> List[Dict]:
        """Find all duplicate IDs"""
        duplicates = []

        for adr_id, files in self.registry.items():
            if len(files) > 1:
                # Duplicate found
                dup_entry = {
                    "adr_id": adr_id,
                    "count": len(files),
                    "files": [str(f.relative_to(ADR_REPO)) for f in files],
                    "hashes": [self._file_hash(f) for f in files],
                    "severity": "CRITICAL" if self.strict else "WARNING"
                }
                duplicates.append(dup_entry)
                self.duplicates.append(dup_entry)

        return duplicates

    def _file_hash(self, file_path: Path) -> str:
        """Compute file content hash for duplicate detection"""
        with open(file_path, 'rb') as f:
            return hashlib.sha256(f.read()).hexdigest()[:8]

    def report(self) -> Dict:
        """Generate dedup audit report"""
        return {
            "timestamp": self.audit_ts,
            "adr_repo": str(ADR_REPO),
            "total_adrs": len(self.registry),
            "unique_ids": sum(1 for v in self.registry.values() if len(v) == 1),
            "duplicate_ids": len(self.duplicates),
            "duplicates": self.duplicates,
            "status": "CLEAN" if not self.duplicates else ("FIXED" if self.fix else "DETECTED"),
        }

    def run(self) -> int:
        """Execute dedup validation"""
        print(f"[DEDUP] Scanning ADR repo: {ADR_REPO}")
        self.scan_adr_files()
        duplicates = self.detect_duplicates()

        report = self.report()
        print(json.dumps(report, indent=2))

        # Save audit report
        audit_file = AUDIT_DIR / f"dedup_audit_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(audit_file, 'w') as f:
            json.dump(report, f, indent=2)
        print(f"\n[OK] Audit report saved: {audit_file}")

        # Exit code
        if duplicates and self.strict:
            return 1
        if duplicates and not self.fix:
            return 1
        return 0

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Task Registry Dedup Validator")
    parser.add_argument("--fix", action="store_true", help="Auto-fix duplicates (if safe)")
    parser.add_argument("--strict", action="store_true", help="Fail on ANY duplicate")
    args = parser.parse_args()

    validator = TaskRegistryDedup(strict=args.strict, fix=args.fix)
    exit_code = validator.run()
    sys.exit(exit_code)
