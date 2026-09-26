"""ReachabilityCheck: Is there a real call site outside tests?"""

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class CheckResult:
    """Immutable result of a single check."""
    passed: bool
    evidence: str
    check_name: str = "reachability"


class ReachabilityCheck:
    """Grep-based reachability check (fail-closed)."""

    def run(
        self,
        symbol_name: str,
        cwd: Path,
        timeout_s: int = 5,
        exclude_tests: bool = True,
    ) -> CheckResult:
        """
        Search for real call site of symbol.

        Returns CheckResult with passed=(symbol found outside tests) + evidence.
        Fail-closed: timeout, error, or no matches → passed=False.
        """
        if not symbol_name or not symbol_name.strip():
            return CheckResult(
                passed=False,
                evidence="Symbol name is empty",
                check_name="reachability"
            )

        # Grep for symbol name (exclude test directories by default)
        try:
            cmd = ["grep", "-r", symbol_name]
            if exclude_tests:
                cmd.extend(["--exclude-dir=test", "--exclude-dir=tests", "--exclude-dir=__pycache__"])
            cmd.append(str(cwd))

            result = subprocess.run(
                cmd,
                timeout=timeout_s,
                cwd=cwd,
                capture_output=True,
                text=True,
                shell=False,
            )

            if result.returncode == 0 and result.stdout:
                # Found matches
                lines = result.stdout.strip().split("\n")[:3]
                evidence = "\n".join(lines)
                return CheckResult(
                    passed=True,
                    evidence=evidence,
                    check_name="reachability"
                )
            else:
                # No matches
                return CheckResult(
                    passed=False,
                    evidence=f"Symbol '{symbol_name}' not found in call sites",
                    check_name="reachability"
                )

        except subprocess.TimeoutExpired:
            return CheckResult(
                passed=False,
                evidence=f"Grep timeout (>{timeout_s}s) — symbol search took too long",
                check_name="reachability"
            )

        except Exception as e:
            # Fail-closed: any error → fail
            return CheckResult(
                passed=False,
                evidence=f"Grep error: {type(e).__name__}: {str(e)[:50]}",
                check_name="reachability"
            )
