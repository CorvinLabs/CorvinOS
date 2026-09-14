"""ReproducibilityCheck: Is there a reproducibility command in commit message?"""

from dataclasses import dataclass


@dataclass(frozen=True)
class CheckResult:
    passed: bool
    evidence: str
    check_name: str = "reproducibility"


class ReproducibilityCheck:
    """Verify commit message contains reproduce command."""

    REPRODUCE_KEYWORDS = [
        "pytest", "python", "npm", "make", "bash", "sh",
        "cargo", "go run", "maven", "gradle", "docker"
    ]

    def run(
        self,
        commit_msg: str = "",
        task_type: str = "cli_command",
    ) -> CheckResult:
        """
        Check if commit message mentions a reproduce command.

        Fail-closed: no reproduce command found → fail.
        """
        if not commit_msg or not commit_msg.strip():
            return CheckResult(
                passed=False,
                evidence="Commit message is empty",
                check_name="reproducibility"
            )

        try:
            # Check for any reproduce keyword
            has_reproduce = any(
                kw in commit_msg.lower()
                for kw in self.REPRODUCE_KEYWORDS
            )

            if has_reproduce:
                return CheckResult(
                    passed=True,
                    evidence="Commit message contains reproduce command",
                    check_name="reproducibility"
                )
            else:
                return CheckResult(
                    passed=False,
                    evidence=f"No reproduce command in commit message (expected: {', '.join(self.REPRODUCE_KEYWORDS[:3])}...)",
                    check_name="reproducibility"
                )

        except Exception as e:
            return CheckResult(
                passed=False,
                evidence=f"Reproducibility check error: {type(e).__name__}",
                check_name="reproducibility"
            )
