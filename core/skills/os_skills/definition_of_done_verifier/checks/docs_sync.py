"""DocsSyncCheck: Do docs mention the code changes?"""

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CheckResult:
    passed: bool
    evidence: str
    check_name: str = "docs_sync"


class DocsSyncCheck:
    """Verify docs reflect code changes (git diff)."""

    def run(
        self,
        commit_range: str = "HEAD~1",
        keyword: str = "feat:",
        cwd: Path = None,
        timeout_s: int = 5,
    ) -> CheckResult:
        """
        Check if docs/ mention keyword in git diff.

        Fail-closed: timeout, git error, keyword not found → fail.
        """
        try:
            result = subprocess.run(
                ["git", "diff", commit_range, "--", "docs/"],
                timeout=timeout_s,
                cwd=cwd,
                capture_output=True,
                text=True,
                shell=False,
            )

            if keyword in result.stdout:
                lines = result.stdout.split("\n")[:3]
                evidence = "\n".join(lines)
                return CheckResult(
                    passed=True,
                    evidence=evidence,
                    check_name="docs_sync"
                )
            else:
                return CheckResult(
                    passed=False,
                    evidence=f"Docs don't mention '{keyword}'",
                    check_name="docs_sync"
                )

        except subprocess.TimeoutExpired:
            return CheckResult(
                passed=False,
                evidence="git diff timeout",
                check_name="docs_sync"
            )
        except Exception as e:
            return CheckResult(
                passed=False,
                evidence=f"Docs check error: {type(e).__name__}",
                check_name="docs_sync"
            )
