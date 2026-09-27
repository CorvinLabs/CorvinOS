"""L18, L34, L44, L36 privacy subsystems — ADR-0232 "Phase 1B" skeleton, DEFUSED.

NOT WIRED: no production caller as of 2026-09-27 (adversarial review).

These classes were SECOND implementations of mandatory compliance mechanisms,
each writing into the second chain of ``core.compliance.audit_trail`` with the
raw ``user_id`` as actor: an in-memory consent store (every grant lost on
restart), a regex flow guard, a confidence-threshold "house rules" check, and
an erasure orchestrator that deleted nothing and reported success. A second
consent gate / flow guard / erasure path can only diverge from the real one, so
each constructor now refuses and names the canonical mechanism (the same ones
the boot tripwire ``corvin_compliance_reports.tripwire`` asserts):

* L18 consent   → ``corvin_operator/bridges/shared/consent.py``
* L34 data flow → ``corvin_operator/bridges/shared/data_classification.py``
* L44 house rules → ``corvin_operator/bridges/shared/house_rules.py``
* L36 erasure   → ``corvin_operator/bridges/shared/erasure_orchestrator.py``

``DataClassification`` and ``ConsentRecord`` stay as inert value types.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from .exceptions import ComplianceError


def _refuse(name: str, canonical: str) -> None:
    raise NotImplementedError(
        f"core.compliance.privacy_subsystems.{name} is a disabled duplicate of a "
        f"mandatory compliance mechanism; use {canonical}"
    )


class DataClassification(Enum):
    """PII Classification levels."""
    PUBLIC = "public"
    SENSITIVE = "sensitive"
    PII = "pii"


@dataclass
class ConsentRecord:
    """L18: TTL-capped consent grant."""
    tenant_id: str
    user_id: str
    purpose: str  # e.g., "analytics", "marketing"
    granted_at: str  # ISO-8601
    ttl_seconds: int = 7776000  # 90 days default
    
    def is_valid(self) -> bool:
        """Check if consent still valid (not expired)."""
        granted = datetime.fromisoformat(self.granted_at.replace('Z', '+00:00'))
        expiry = granted + timedelta(seconds=self.ttl_seconds)
        now = datetime.now(timezone.utc)
        return now < expiry


class ConsentGate:
    """Refused duplicate of the L18 consent gate."""

    def __init__(self, *_a: Any, **_k: Any):
        _refuse("ConsentGate", "corvin_operator/bridges/shared/consent.py (is_granted / grant)")


class FlowGuard:
    """Refused duplicate of the L34 data-flow guard."""

    def __init__(self, *_a: Any, **_k: Any):
        _refuse("FlowGuard", "corvin_operator/bridges/shared/data_classification.py")


class HouseRules:
    """Refused duplicate of the L44 house-rules gate."""

    def __init__(self, *_a: Any, **_k: Any):
        _refuse("HouseRules", "corvin_operator/bridges/shared/house_rules.py")


class ErasureOrchestrator:
    """Refused duplicate of the L36 GDPR Art. 17 erasure orchestrator."""

    def __init__(self, *_a: Any, **_k: Any):
        _refuse("ErasureOrchestrator", "corvin_operator/bridges/shared/erasure_orchestrator.py")


__all__ = [
    "ComplianceError", "ConsentGate", "ConsentRecord", "DataClassification",
    "ErasureOrchestrator", "FlowGuard", "HouseRules",
]
