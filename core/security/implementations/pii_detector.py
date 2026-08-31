"""Role 2b: PIIDetector — PII and secret scanning."""

import logging
import re
from ..context import GateName, GateResult, PiiFinding, SecurityContext

logger = logging.getLogger(__name__)


class PIIDetectorImpl:
    """Concrete implementation of PIIDetector role."""

    def __init__(self):
        self.patterns = {
            "email": re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'),
            "ssn": re.compile(r'\b\d{3}-\d{2}-\d{4}\b'),
            "api_key": re.compile(r'(api[_-]?key|apikey|api[_-]?token)["\']?\s*[:=]', re.IGNORECASE),
            "password": re.compile(r'(password|passwd|pwd)["\']?\s*[:=]', re.IGNORECASE),
        }

    async def detect(self, context: SecurityContext) -> GateResult:
        """Scan input_data + context for PII/secrets."""
        findings = []

        # Scan input_data
        for key, value in context.input_data.items():
            if not isinstance(value, str):
                continue

            for pii_type, pattern in self.patterns.items():
                if pattern.search(value):
                    finding = PiiFinding(
                        data_type=pii_type,
                        severity="high" if pii_type in ["api_key", "ssn"] else "medium",
                        location=f"input.{key}",
                        action_taken="request_denied",
                    )
                    findings.append(finding)
                    logger.warning(f"[PIIDetector] Found {pii_type} in {key}")

        context.pii_detected.extend(findings)

        # High-severity PII in sensitive fields = deny
        high_severity = [f for f in findings if f.severity == "high"]
        if high_severity:
            return GateResult(
                gate_name=GateName.PII_DETECTION,
                passed=False,
                reason_code="high_severity_pii_detected",
                details={
                    "pii_types": list(set(f.data_type for f in high_severity)),
                    "count": len(high_severity),
                },
            )

        logger.debug(f"[PIIDetector] No high-severity PII found (medium={len(findings)})")
        return GateResult(
            gate_name=GateName.PII_DETECTION,
            passed=True,
            reason_code="no_pii" if not findings else "pii_low_severity",
            details={"pii_count": len(findings)},
        )
