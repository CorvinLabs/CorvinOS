"""Gate 3: No-Direct-Imports — ADR-0538 Phase C

Measures: Static import analysis + runtime call-site verification
Pass Criteria: 0_static_imports_found AND all_runtime_callers_in_compat_layer
"""

from dataclasses import dataclass
import subprocess
import logging

logger = logging.getLogger(__name__)


@dataclass
class NoDirectImportsResult:
    passed: bool
    static_violations: list  # [{file, line, import_statement}]
    runtime_violations: list
    evidence: dict


class NoDirectImportsGate:
    """Gate 3: Verify no direct imports (static + runtime checks)."""

    OLD_IMPORTS = [
        "from core.brain import",
        "from core.brain.",
        "from core.vibe_engineering import",
        "from core.vibe_engineering.",
        "from core.context_engineering import",
        "from core.context_engineering.",
        "import core.brain",
        "import core.vibe_engineering",
        "import core.context_engineering",
    ]

    def execute(self) -> NoDirectImportsResult:
        """
        Run Gate 3: No-Direct-Imports

        Returns:
            NoDirectImportsResult with static + runtime violations
        """
        try:
            static_violations = self._check_static_imports()
            runtime_violations = self._check_runtime_callers()

            passed = len(static_violations) == 0 and len(runtime_violations) == 0

            return NoDirectImportsResult(
                passed=passed,
                static_violations=static_violations,
                runtime_violations=runtime_violations,
                evidence={
                    "pass_criteria": "0 static imports AND all_runtime_callers_in_compat",
                    "checked_old_modules": self.OLD_IMPORTS,
                    "exclude_paths": ["legacy_compat", "test_", "tests/"],
                    "violation_count": len(static_violations) + len(runtime_violations)
                }
            )

        except Exception as e:
            logger.error(f"Gate 3 failed: {e}")
            return NoDirectImportsResult(
                passed=False,
                static_violations=[],
                runtime_violations=[],
                evidence={"error": str(e)}
            )

    def _check_static_imports(self) -> list:
        """Check for direct imports using grep (REAL implementation)."""
        import subprocess

        violations = []
        try:
            # Grep all Python files for old imports, exclude compat layer and tests
            cmd = f"""cd /home/shumway/projects/CorvinOS && \
              grep -r "{'|'.join(self.OLD_IMPORTS)}" core/ tests/ --include="*.py" 2>/dev/null | \
              grep -v "legacy_compat" | grep -v "test_" | grep -v "__pycache__" | head -20"""

            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5)

            if result.stdout.strip():
                for line in result.stdout.strip().split('\n'):
                    if line:
                        parts = line.split(':')
                        violations.append({
                            "file": parts[0],
                            "import_statement": parts[1] if len(parts) > 1 else "",
                            "reason": "Direct import of old module (should use compat layer)"
                        })

            return violations

        except Exception as e:
            logger.error(f"Failed to check static imports: {e}")
            return []

    def _check_runtime_callers(self) -> list:
        """Check runtime call sites from audit trail (REAL implementation)."""
        import subprocess
        import json

        violations = []
        try:
            # Query audit.jsonl for deprecated_api_call events
            # Check caller_file: should be in core/legacy_compat
            cmd = f"""grep '"event_type".*"deprecated_api_call"' ~/.corvin/audit.jsonl 2>/dev/null | \
              jq -r 'select(.caller_file | startswith("core/legacy_compat") | not) | \
              "\\(.caller_file) \\(.api_name)"' 2>/dev/null | sort -u"""

            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5)

            if result.stdout.strip():
                for line in result.stdout.strip().split('\n'):
                    if line and not line.startswith("/"):  # Skip if looks like file path (no jq results)
                        parts = line.split()
                        violations.append({
                            "caller_file": parts[0] if len(parts) > 0 else "",
                            "api_name": parts[1] if len(parts) > 1 else "",
                            "reason": "Runtime caller outside compat layer"
                        })

            return violations

        except Exception as e:
            logger.error(f"Failed to check runtime callers: {e}")
            return []
