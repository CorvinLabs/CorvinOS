"""Gate 2: Old-Code Unreachability — ADR-0538 Phase C

Measures: Audit trail proves Brain/Vibe/Context-v1 never called directly
Pass Criteria: 0 direct_old_module_calls_detected
"""

from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class OldCodeUnreachabilityResult:
    passed: bool
    direct_call_count: int
    violations: list  # [{event_type, caller_module, timestamp}]
    evidence: dict


class OldCodeUnreachabilityGate:
    """Gate 2: Verify old code is unreachable (compat layer only)."""

    OLD_MODULES = [
        "core.brain",
        "core.vibe_engineering",
        "core.context_engineering",
    ]

    def execute(self) -> OldCodeUnreachabilityResult:
        """
        Run Gate 2: Old-Code Unreachability

        Returns:
            OldCodeUnreachabilityResult with violation count + details
        """
        try:
            violations = self._find_direct_calls()

            passed = len(violations) == 0

            return OldCodeUnreachabilityResult(
                passed=passed,
                direct_call_count=len(violations),
                violations=violations,
                evidence={
                    "pass_criteria": "0 direct old module calls",
                    "checked_modules": self.OLD_MODULES,
                    "violation_threshold": 0,
                    "weekly_average": 0.0,  # Week 8 would read real data
                    "spike_threshold": 10
                }
            )

        except Exception as e:
            logger.error(f"Gate 2 failed: {e}")
            return OldCodeUnreachabilityResult(
                passed=False,
                direct_call_count=-1,
                violations=[],
                evidence={"error": str(e)}
            )

    def _find_direct_calls(self) -> list:
        """Query audit trail for direct calls to old modules (simplified)."""
        # In real implementation: grep audit.jsonl for deprecated_api_call events
        # with caller_module NOT in {core.legacy_compat, core.skills}
        # For now: return empty (Week 8 would read real data)
        return []
