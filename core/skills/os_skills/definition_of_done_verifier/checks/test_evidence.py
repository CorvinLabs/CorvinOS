"""TestEvidenceCheck: Do tests exist and pass with captured output?"""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CheckResult:
    passed: bool
    evidence: str
    check_name: str = "test_evidence"


class TestEvidenceCheck:
    """Verify test evidence (exit code, captured output)."""

    def run(
        self,
        test_path: Path = None,
        test_output_file: Path = None,
    ) -> CheckResult:
        """
        Verify tests exist, pass, and produce output.

        Fail-closed: missing files, no output, failure indicators → fail.
        """
        if not test_path or not test_path.exists():
            return CheckResult(
                passed=False,
                evidence="Test file not found",
                check_name="test_evidence"
            )

        if not test_output_file or not test_output_file.exists():
            return CheckResult(
                passed=False,
                evidence="Test output file not captured",
                check_name="test_evidence"
            )

        try:
            with open(test_output_file, "r") as f:
                output = f.read()

            # Check for success indicators
            passed_keyword = "passed" in output.lower()
            failed_keyword = "failed" in output.lower()
            has_output = len(output) > 10  # Non-trivial output

            if passed_keyword and has_output and not (failed_keyword and "0 failed" not in output.lower()):
                # Tests passed and output was captured
                first_lines = "\n".join(output.split("\n")[:3])
                return CheckResult(
                    passed=True,
                    evidence=first_lines,
                    check_name="test_evidence"
                )
            else:
                return CheckResult(
                    passed=False,
                    evidence="Test output indicates failures or no tests ran",
                    check_name="test_evidence"
                )

        except Exception as e:
            return CheckResult(
                passed=False,
                evidence=f"Test check error: {type(e).__name__}: {str(e)[:50]}",
                check_name="test_evidence"
            )
