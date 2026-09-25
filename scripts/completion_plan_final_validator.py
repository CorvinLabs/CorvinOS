#!/usr/bin/env python3
"""
Completion-Plan Final Validator (Task 4)
Runs all validation checks, generates completion report

Usage:
  python3 completion_plan_final_validator.py [--full]

Outputs:
  - Validation report: ~/.corvin/audit/completion_plan_final_TIMESTAMP.json
  - Summary: stdout
  - Exits 0 if all OK, 1 if blockers
"""

import json
import subprocess
import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple

# Paths
CORVINOS_REPO = Path("/home/shumway/projects/CorvinOS")
ADR_REPO = Path("/home/shumway/projects/Corvin-ADR/decisions")
AUDIT_DIR = Path.home() / ".corvin" / "tenants" / "_default" / "global" / "audit"
AUDIT_DIR.mkdir(parents=True, exist_ok=True)

class CompletionValidator:
    """Final validation suite for Completion-Plan"""

    def __init__(self):
        self.results = {
            "timestamp": datetime.now().isoformat(),
            "tasks": {
                "task_1_dedup": {"status": "PENDING", "details": {}},
                "task_2_ci_cd": {"status": "PENDING", "details": {}},
                "task_3_audit": {"status": "PENDING", "details": {}},
                "task_4_validation": {"status": "PENDING", "details": {}},
            },
            "summary": {},
            "blockers": [],
        }

    def validate_task_1_dedup(self) -> Tuple[bool, Dict]:
        """Verify dedup validator exists and runs"""
        dedup_script = CORVINOS_REPO / "scripts" / "task_registry_dedup_validator.py"

        if not dedup_script.exists():
            return False, {"error": "dedup validator not found"}

        try:
            result = subprocess.run(
                ["python3", str(dedup_script)],
                cwd=str(ADR_REPO),
                capture_output=True,
                text=True,
                timeout=30
            )

            # Parse JSON output
            for line in result.stdout.split("\n"):
                try:
                    report = json.loads(line)
                    return True, {
                        "unique_ids": report.get("unique_ids", 0),
                        "duplicate_ids": report.get("duplicate_ids", 0),
                        "status": report.get("status", "UNKNOWN"),
                        "exit_code": result.returncode,
                    }
                except:
                    pass

            return False, {"error": f"Could not parse output, exit_code: {result.returncode}"}

        except subprocess.TimeoutExpired:
            return False, {"error": "Timeout"}
        except Exception as e:
            return False, {"error": str(e)}

    def validate_task_2_ci_cd(self) -> Tuple[bool, Dict]:
        """Verify CI/CD gate exists"""
        gate_file = CORVINOS_REPO / ".github" / "workflows" / "root-md-policy-gate.yml"

        if not gate_file.exists():
            return False, {"error": "CI/CD gate YAML not found"}

        # Check content
        with open(gate_file) as f:
            content = f.read()

        required = [
            "root-md-policy",
            "README.md",
            "CLAUDE.md",
            "ADR-0516",
        ]

        missing = [r for r in required if r not in content]

        if missing:
            return False, {"error": f"Missing: {missing}"}

        return True, {
            "file_exists": True,
            "required_checks": len(required),
            "found_checks": len(required) - len(missing),
        }

    def validate_task_3_audit(self) -> Tuple[bool, Dict]:
        """Verify commit traceability auditor exists"""
        audit_script = CORVINOS_REPO / "scripts" / "adr_commit_traceability_audit.py"

        if not audit_script.exists():
            return False, {"error": "Commit traceability script not found"}

        # Check for key functions
        with open(audit_script) as f:
            content = f.read()

        required_methods = [
            "find_commits_for_adr",
            "scan_accepted_adrs",
            "audit_single_adr",
        ]

        missing = [m for m in required_methods if m not in content]

        if missing:
            return False, {"error": f"Missing methods: {missing}"}

        return True, {
            "script_exists": True,
            "methods_found": len(required_methods) - len(missing),
            "total_methods": len(required_methods),
        }

    def validate_adr_compliance(self) -> Tuple[bool, List[Dict]]:
        """Check ADR-0264 frontmatter compliance"""
        required_fields = ["id", "status", "depends_on", "paths", "docs"]
        violations = []
        checked = 0

        for adr_file in sorted(ADR_REPO.glob("ADR-*.md"))[:20]:  # Sample 20
            missing = []
            with open(adr_file) as f:
                for line in f:
                    if line.startswith("---") and checked > 0:
                        break
                    for field in required_fields:
                        if line.startswith(field + ":"):
                            required_fields.remove(field)
                        if line.startswith("---") and checked == 0:
                            checked += 1
                            break

            if missing:
                violations.append({
                    "file": adr_file.name,
                    "missing": missing,
                })
            checked += 1

        return len(violations) == 0, violations

    def run_quick_tests(self) -> Tuple[bool, Dict]:
        """Run quick validation tests"""
        tests_passed = 0
        tests_failed = 0

        try:
            # Check CorvinOS tests (quick smoke test)
            result = subprocess.run(
                ["pytest", "tests/", "-q", "--tb=no", "-x"],
                cwd=str(CORVINOS_REPO),
                capture_output=True,
                text=True,
                timeout=60,
            )

            # Parse output
            if "passed" in result.stdout:
                tests_passed = int(result.stdout.split()[0]) if result.stdout[0].isdigit() else 0

            if result.returncode == 0:
                return True, {"tests_passed": tests_passed, "status": "ALL_PASS"}
            else:
                return False, {"tests_passed": tests_passed, "status": "SOME_FAIL"}

        except subprocess.TimeoutExpired:
            return False, {"error": "Pytest timeout"}
        except Exception as e:
            return False, {"error": str(e)}

    def run(self) -> int:
        """Execute full validation"""
        print("[VALIDATION] Running Completion-Plan final validator...\n")

        # Task 1: Dedup
        print("[TASK 1] Dedup Validator...")
        ok, details = self.validate_task_1_dedup()
        self.results["tasks"]["task_1_dedup"]["status"] = "OK" if ok else "FAIL"
        self.results["tasks"]["task_1_dedup"]["details"] = details
        print(f"  {'✅' if ok else '❌'} {details}\n")

        # Task 2: CI/CD
        print("[TASK 2] CI/CD Gate...")
        ok, details = self.validate_task_2_ci_cd()
        self.results["tasks"]["task_2_ci_cd"]["status"] = "OK" if ok else "FAIL"
        self.results["tasks"]["task_2_ci_cd"]["details"] = details
        print(f"  {'✅' if ok else '❌'} {details}\n")

        # Task 3: Audit
        print("[TASK 3] Commit Traceability Auditor...")
        ok, details = self.validate_task_3_audit()
        self.results["tasks"]["task_3_audit"]["status"] = "OK" if ok else "FAIL"
        self.results["tasks"]["task_3_audit"]["details"] = details
        print(f"  {'✅' if ok else '❌'} {details}\n")

        # Task 4: Tests
        print("[TASK 4] Quick Test Smoke...")
        ok, details = self.run_quick_tests()
        self.results["tasks"]["task_4_validation"]["status"] = "OK" if ok else "FAIL"
        self.results["tasks"]["task_4_validation"]["details"] = details
        print(f"  {'✅' if ok else '❌'} {details}\n")

        # ADR Compliance
        print("[COMPLIANCE] ADR-0264 Frontmatter Check...")
        ok, violations = self.validate_adr_compliance()
        print(f"  {'✅' if ok else '⚠️'} {len(violations)} violations found\n")

        # Summary
        all_ok = all(self.results["tasks"][t]["status"] == "OK" for t in self.results["tasks"])

        print("[SUMMARY]")
        print(f"  Task 1 (Dedup):              {self.results['tasks']['task_1_dedup']['status']}")
        print(f"  Task 2 (CI/CD):              {self.results['tasks']['task_2_ci_cd']['status']}")
        print(f"  Task 3 (Audit):              {self.results['tasks']['task_3_audit']['status']}")
        print(f"  Task 4 (Validation):         {self.results['tasks']['task_4_validation']['status']}")
        print(f"  Overall:                     {'🟢 PASS' if all_ok else '🔴 FAIL'}\n")

        # Save report
        report_file = AUDIT_DIR / f"completion_plan_final_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(report_file, 'w') as f:
            json.dump(self.results, f, indent=2)
        print(f"[OK] Report saved: {report_file}")

        return 0 if all_ok else 1

if __name__ == "__main__":
    validator = CompletionValidator()
    exit_code = validator.run()
    sys.exit(exit_code)
