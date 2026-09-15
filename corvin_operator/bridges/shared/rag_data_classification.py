"""L34: Data Classification Gate for RAG Queries.

Enforces data classification policy before query execution:
- Checks query metadata against tenant allowlist
- Blocks unsafe data flows (CONFIDENTIAL→PUBLIC, etc.)
- Emits audit events (metadata-only, no query text)
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


# ── Data Classification Levels ────────────────────────────────

class DataClassification(Enum):
    """Data sensitivity levels (EU AI Act + GDPR compliance)."""
    PUBLIC = 1          # No restrictions
    INTERNAL = 2        # Organization-only
    CONFIDENTIAL = 3    # Leadership + security team
    SECRET = 4          # Encryption at rest required


# ── Classification Policy ──────────────────────────────────────

class ClassificationPolicy:
    """Tenant-specific data classification allowlist."""

    def __init__(
        self,
        allowed_classifications: list[DataClassification],
        engine_locality: str = "local",  # local, eu_cloud, us_cloud
        network_egress: str = "none",    # none, internal, external
    ):
        """Initialize policy.

        Args:
            allowed_classifications: List of allowed levels (fail-closed)
            engine_locality: Where queries execute (local = most restricted)
            network_egress: Network permissions (none = most restricted)
        """
        self.allowed = allowed_classifications
        self.locality = engine_locality
        self.egress = network_egress

    def is_allowed(self, query_classification: DataClassification) -> bool:
        """Check if query classification is permitted."""
        return query_classification in self.allowed

    def explain_denial(self, query_classification: DataClassification) -> str:
        """Return user-facing reason for denial."""
        policy_str = ", ".join(c.name for c in self.allowed)
        return (
            f"Query classification {query_classification.name} not allowed. "
            f"Tenant policy permits: {policy_str}"
        )


# ── Default Policies ───────────────────────────────────────────

def get_default_policy(tenant_id: str) -> ClassificationPolicy:
    """Load tenant-specific policy (hardcoded defaults for now).

    Phase 5.3: Load from tenant.corvin.yaml
    """
    # Default: INTERNAL only (safest for initial rollout)
    return ClassificationPolicy(
        allowed_classifications=[
            DataClassification.PUBLIC,
            DataClassification.INTERNAL,
        ],
        engine_locality="local",
        network_egress="none",
    )


# ── L34 Gate ───────────────────────────────────────────────────

class L34DataClassificationGate:
    """L34 Data Classification Gate for RAG queries."""

    def __init__(self, tenant_id: str):
        """Initialize gate for a tenant."""
        self.tenant_id = tenant_id
        self.policy = get_default_policy(tenant_id)
        self.blocked_count = 0
        self.allowed_count = 0

    def check(
        self,
        query_text: str,
        classification: DataClassification = DataClassification.PUBLIC,
        reason: Optional[str] = None,
    ) -> tuple[bool, Optional[str]]:
        """Check if query is allowed.

        Returns:
            (allowed: bool, denial_reason: Optional[str])
        """
        if self.policy.is_allowed(classification):
            self.allowed_count += 1
            logger.info(
                f"L34 ALLOW query [{classification.name}] "
                f"(tenant={self.tenant_id})"
            )
            return True, None

        # Blocked
        self.blocked_count += 1
        denial = self.policy.explain_denial(classification)
        logger.warning(
            f"L34 BLOCK query [{classification.name}] — {denial} "
            f"(tenant={self.tenant_id})"
        )

        # Emit audit event (metadata only, no query text)
        self._audit_blocked_query(classification, reason)

        return False, denial

    def _audit_blocked_query(
        self,
        classification: DataClassification,
        reason: Optional[str],
    ) -> None:
        """Log blocked query to audit trail (metadata only)."""
        # Phase 5.3: Emit to L16 audit chain
        # For now, just log
        logger.warning(
            {
                "event_type": "data_classification.query_blocked",
                "classification": classification.name,
                "tenant_id": self.tenant_id,
                "reason": reason or "Classification not allowed",
                "timestamp": None,  # Phase 5.3: add timestamp
            }
        )

    def stats(self) -> dict:
        """Return gate statistics."""
        return {
            "allowed": self.allowed_count,
            "blocked": self.blocked_count,
            "policy": {
                "allowed_classifications": [c.name for c in self.policy.allowed],
                "engine_locality": self.policy.locality,
                "network_egress": self.policy.egress,
            },
        }
