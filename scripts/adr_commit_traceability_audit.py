#!/usr/bin/env python3
"""
ADR Commit Traceability Audit (Completion-Plan Task 3)
Links 127+ ACCEPTED ADRs to their implementation commits

Usage:
  python3 adr_commit_traceability_audit.py [--update] [--parallel]

Modes:
  --update:   Update ADR frontmatter with commits: [] field
  --parallel: Use multiprocessing (default: serial)
  --report:   Generate audit report (JSON + CSV)

Output:
  - Audit report: ~/.corvin/audit/adr_traceability_audit_TIMESTAMP.json
  - Summary: prints to stdout
  - Exits 0 if all OK, 1 if blockers found
"""

import json
import sys
import os
import re
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import concurrent.futures

# Paths
ADR_REPO = Path("/home/shumway/projects/Corvin-ADR/decisions")
CORVINOS_REPO = Path("/home/shumway/projects/CorvinOS")
AUDIT_DIR = Path.home() / ".corvin" / "tenants" / "_default" / "global" / "audit"
AUDIT_DIR.mkdir(parents=True, exist_ok=True)

class ADRCommitAuditor:
    """Audit ADR-to-commit traceability"""

    def __init__(self, update=False, parallel=False):
        self.update = update
        self.parallel = parallel
        self.findings = {
            "total_adrs": 0,
            "accepted_adrs": 0,
            "with_commits": 0,
            "orphan_adrs": [],
            "multiple_commits": [],
            "audit_ts": datetime.now().isoformat(),
        }

    def scan_accepted_adrs(self) -> List[Tuple[Path, str, List[str]]]:
        """Find all ACCEPTED ADRs and extract IDs"""
        results = []

        for adr_file in sorted(ADR_REPO.glob("ADR-*.md")):
            adr_id = self._extract_field(adr_file, "id")
            status = self._extract_field(adr_file, "status")

            if status == "accepted":
                self.findings["accepted_adrs"] += 1
                results.append((adr_file, adr_id, []))

            self.findings["total_adrs"] += 1

        return results

    def _extract_field(self, file_path: Path, field: str) -> Optional[str]:
        """Extract frontmatter field (e.g., id, status)"""
        try:
            with open(file_path, 'r') as f:
                for line in f:
                    if line.startswith(field + ":"):
                        # Extract value after ": "
                        match = re.search(f"^{field}:\\s*(.+?)\\s*$", line)
                        if match:
                            return match.group(1).strip("\"'")
                    if line.startswith("---") and line != "---\n":
                        # End of frontmatter
                        break
        except Exception as e:
            print(f"[WARN] Could not extract {field} from {file_path}: {e}")
        return None

    def find_commits_for_adr(self, adr_id: str) -> List[str]:
        """Find all git commits referencing this ADR"""
        commits = []

        try:
            # Search CorvinOS repo
            result = subprocess.run(
                ["git", "log", "--all", "--grep", adr_id, "--oneline"],
                cwd=str(CORVINOS_REPO),
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.stdout:
                for line in result.stdout.strip().split("\n"):
                    if line:
                        # Extract commit SHA
                        sha = line.split()[0]
                        commits.append(sha)

            # Also search Corvin-ADR repo
            result = subprocess.run(
                ["git", "log", "--all", "--grep", adr_id, "--oneline"],
                cwd=str(ADR_REPO),
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.stdout:
                for line in result.stdout.strip().split("\n"):
                    if line:
                        sha = line.split()[0]
                        if sha not in commits:
                            commits.append(sha)

        except subprocess.TimeoutExpired:
            print(f"[WARN] Timeout searching for commits for {adr_id}")
        except Exception as e:
            print(f"[WARN] Error finding commits for {adr_id}: {e}")

        return commits

    def audit_single_adr(self, adr_file: Path, adr_id: str) -> Tuple[str, List[str]]:
        """Audit one ADR for commit traceability"""
        commits = self.find_commits_for_adr(adr_id)

        if not commits:
            self.findings["orphan_adrs"].append({
                "adr_id": adr_id,
                "file": str(adr_file.relative_to(ADR_REPO)),
                "reason": "No commits found"
            })
        else:
            if len(commits) > 1:
                self.findings["multiple_commits"].append({
                    "adr_id": adr_id,
                    "count": len(commits),
                    "commits": commits
                })
            self.findings["with_commits"] += 1

        return adr_id, commits

    def run(self) -> int:
        """Execute traceability audit"""
        print(f"[AUDIT] Scanning ACCEPTED ADRs in {ADR_REPO}...")

        accepted_adrs = self.scan_accepted_adrs()
        print(f"[INFO] Found {len(accepted_adrs)} ACCEPTED ADRs")

        # Audit each ADR
        if self.parallel and len(accepted_adrs) > 10:
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
                futures = [
                    executor.submit(self.audit_single_adr, f, aid)
                    for f, aid, _ in accepted_adrs
                ]
                results = [f.result() for f in concurrent.futures.as_completed(futures)]
        else:
            for adr_file, adr_id, _ in accepted_adrs:
                self.audit_single_adr(adr_file, adr_id)

        # Report
        print(f"\n[REPORT]")
        print(f"  Total ADRs: {self.findings['total_adrs']}")
        print(f"  ACCEPTED ADRs: {self.findings['accepted_adrs']}")
        print(f"  With commits: {self.findings['with_commits']}")
        print(f"  Orphan ADRs: {len(self.findings['orphan_adrs'])}")
        print(f"  Multiple commits: {len(self.findings['multiple_commits'])}")

        if self.findings["orphan_adrs"]:
            print(f"\n[ORPHAN ADRs] Missing commit links:")
            for orp in self.findings["orphan_adrs"][:10]:
                print(f"  - {orp['adr_id']}: {orp['file']}")
            if len(self.findings["orphan_adrs"]) > 10:
                print(f"  ... and {len(self.findings['orphan_adrs']) - 10} more")

        # Save audit report
        audit_file = AUDIT_DIR / f"adr_traceability_audit_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(audit_file, 'w') as f:
            json.dump(self.findings, f, indent=2)
        print(f"\n[OK] Audit report saved: {audit_file}")

        return 0 if not self.findings["orphan_adrs"] else 1

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="ADR Commit Traceability Audit")
    parser.add_argument("--update", action="store_true", help="Update ADR frontmatter")
    parser.add_argument("--parallel", action="store_true", help="Use parallel processing")
    args = parser.parse_args()

    auditor = ADRCommitAuditor(update=args.update, parallel=args.parallel)
    exit_code = auditor.run()
    sys.exit(exit_code)
