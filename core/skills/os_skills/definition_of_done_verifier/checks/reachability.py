"""ReachabilityCheck: Is there a real call site outside tests?"""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CheckResult:
    """Immutable result of a single check."""
    passed: bool
    evidence: str
    check_name: str = "reachability"


_TEST_PATH = re.compile(r"(^|/)(tests?/|test_[^/]*$|[^/]*_test\.py$|[^/]*\.test\.[jt]sx?$)")


# Prose that mentions a symbol is not a call site.
_CODE_PATHSPECS = ("*.py", "*.ts", "*.tsx", "*.js", "*.jsx", "*.mjs", "*.sh",
                   "*.yaml", "*.yml", "*.toml")

# An import, a bare name in a parenthesised import list, or an ``__all__`` entry
# names the symbol without using it.
_IMPORT_ONLY = re.compile(
    r"^\s*(from\s+\S+\s+)?import\b|^\s*['\"]?\w+['\"]?\s*,?\s*(#.*)?$"
)


class ReachabilityCheck:
    """Search tracked files for a use of the symbol that is not its definition (fail-closed)."""

    def run(
        self,
        symbol_name: str,
        cwd: Path,
        timeout_s: int = 5,
        exclude_tests: bool = True,
    ) -> CheckResult:
        """Return passed=True only for a match outside tests that is not a def/class line.

        ``git grep`` searches tracked files only; ``grep -r`` over a checkout with
        .venv, node_modules and worktrees exceeded the budget on every run.
        """
        if not symbol_name or not symbol_name.strip():
            return CheckResult(passed=False, evidence="Symbol name is empty")

        try:
            result = subprocess.run(
                ["git", "grep", "-n", "-w", "-F", "-e", symbol_name, "--", *_CODE_PATHSPECS],
                timeout=timeout_s,
                cwd=cwd,
                capture_output=True,
                text=True,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            return CheckResult(passed=False, evidence=f"git grep timeout (>{timeout_s}s)")
        except Exception as e:
            return CheckResult(passed=False, evidence=f"git grep error: {type(e).__name__}")

        if result.returncode not in (0, 1):
            return CheckResult(passed=False, evidence="git grep failed")

        definition = re.compile(
            rf"^\s*(async\s+)?(def|class)\s+{re.escape(symbol_name)}\b"
            rf"|^\s*(export\s+)?(function|const|class|interface|type)\s+{re.escape(symbol_name)}\b"
        )
        call_sites = []
        for line in result.stdout.splitlines():
            path, _, rest = line.partition(":")
            _lineno, _, code = rest.partition(":")
            if exclude_tests and _TEST_PATH.search(path):
                continue
            if definition.search(code) or _IMPORT_ONLY.search(code):
                continue
            call_sites.append(line)

        if call_sites:
            return CheckResult(passed=True, evidence="\n".join(call_sites[:3]))
        return CheckResult(
            passed=False,
            evidence=f"No use of '{symbol_name}' outside tests and its definition",
        )
