#!/usr/bin/env python3
"""ADR Commit Traceability Audit — backfill missing commit links from git history"""

import json, re, subprocess, sys
from datetime import datetime
from pathlib import Path

ADR_REPO = Path("/home/shumway/projects/Corvin-ADR/decisions")
CORVIN_OS = Path("/home/shumway/projects/CorvinOS")

class ADRCommitAudit:
    def __init__(self, fix=False, verbose=False, dry_run=False):
        self.fix = fix
        self.verbose = verbose
        self.dry_run = dry_run
        self.adrs_without_commits = {}
        self.discovered_commits = {}
        self.report = {
            "timestamp": datetime.now().isoformat(),
            "total_adrs": 0,
            "adrs_with_commits": 0,
            "adrs_without_commits": 0,
            "discovered_candidates": 0,
            "fixed_adrs": 0,
        }

    def scan_adr_files(self):
        for adr_file in sorted(ADR_REPO.glob("*.md")):
            with open(adr_file, 'r') as f:
                content = f.read()
            id_match = re.search(r'^id:\s*([A-Z0-9-]+)$', content, re.MULTILINE)
            if not id_match:
                continue
            adr_id = id_match.group(1)
            commits_match = re.search(r'^commits:\s*(\[.*?\])', content, re.MULTILINE | re.DOTALL)
            commits = []
            if commits_match:
                try:
                    commits = json.loads(commits_match.group(1))
                except: pass
            
            self.report["total_adrs"] += 1
            if commits:
                self.report["adrs_with_commits"] += 1
            else:
                self.report["adrs_without_commits"] += 1
                self.adrs_without_commits[adr_id] = str(adr_file)

    def find_commits(self, adr_id):
        candidates = []
        for repo in [CORVIN_OS, ADR_REPO.parent]:
            result = subprocess.run(
                ["git", "log", "--all", "--oneline", "--format=%H %s"],
                cwd=repo, capture_output=True, text=True, timeout=30
            )
            for line in result.stdout.split('\n'):
                if not line.strip(): continue
                parts = line.split(' ', 1)
                if len(parts) != 2: continue
                commit, msg = parts
                if re.search(rf'\b{re.escape(adr_id)}\b|adr:\s*add\s+{adr_id}\b', msg, re.IGNORECASE):
                    if commit not in candidates:
                        candidates.append(commit)
        return candidates

    def run_audit(self):
        self.scan_adr_files()
        print(f"✓ {self.report['adrs_with_commits']}/{self.report['total_adrs']} ADRs have commit links")
        print(f"✗ {self.report['adrs_without_commits']} ADRs missing commits")
        
        for adr_id in sorted(self.adrs_without_commits.keys()):
            candidates = self.find_commits(adr_id)
            if candidates:
                self.discovered_commits[adr_id] = candidates
                self.report["discovered_candidates"] += len(candidates)
        
        print(json.dumps(self.report, indent=2))
        return 0 if not self.adrs_without_commits else 1

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Audit ADR commit traceability")
    parser.add_argument('--fix', action='store_true')
    parser.add_argument('--verbose', action='store_true')
    args = parser.parse_args()
    
    audit = ADRCommitAudit(fix=args.fix, verbose=args.verbose)
    sys.exit(audit.run_audit())

if __name__ == '__main__':
    main()
