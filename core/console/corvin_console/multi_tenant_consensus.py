"""Multi-tenant tier consensus (Phase 9b, ADR-0287)."""

from dataclasses import dataclass
from typing import Dict, List, Optional
from datetime import datetime


@dataclass
class TenantMetricsSnapshot:
    """Metrics snapshot from one tenant."""

    tenant_id: str
    flag_id: str
    error_rate_24h: float
    invocation_count_24h: int
    adoption_rate: float
    days_in_tier: int
    timestamp: str


@dataclass
class ConsensusDecision:
    """Result of multi-tenant consensus voting."""

    flag_id: str
    current_tier: str
    consensus_decision: str  # "promote" | "demote" | "hold"
    voting_tenants: List[str]
    consensus_percentage: float  # e.g., 0.75 = 75% agree
    reason: str


class MultiTenantConsensus:
    """Multi-tenant promotion consensus (Phase 9b).

    If 3+ tenants in a network (or 60%+ of connected tenants) agree
    a feature is ready to promote, auto-promote across ALL tenants.
    """

    CONSENSUS_THRESHOLD = 0.60  # 60% of tenants must agree

    @staticmethod
    def vote_on_promotion(
        snapshots: List[TenantMetricsSnapshot],
        current_tier: str,
    ) -> ConsensusDecision:
        """Tally votes: should this flag promote based on multi-tenant agreement?

        Args:
            snapshots: Metrics from all tenants in the network
            current_tier: Current tier of the flag (alpha|beta|stable|production)

        Returns:
            ConsensusDecision with voting breakdown + recommendation
        """
        if not snapshots:
            return ConsensusDecision(
                flag_id="unknown",
                current_tier=current_tier,
                consensus_decision="hold",
                voting_tenants=[],
                consensus_percentage=0.0,
                reason="No tenant snapshots available",
            )

        flag_id = snapshots[0].flag_id
        votes_to_promote = 0

        # Count how many tenants meet promotion criteria for THIS tier
        for snapshot in snapshots:
            if MultiTenantConsensus._meets_promotion_criteria(current_tier, snapshot):
                votes_to_promote += 1

        voting_percentage = votes_to_promote / len(snapshots)
        tenant_ids = [s.tenant_id for s in snapshots]

        # Consensus: 60%+ agree + at least 3 tenants
        meets_consensus = (
            voting_percentage >= MultiTenantConsensus.CONSENSUS_THRESHOLD
            and len(snapshots) >= 3
        )

        decision = (
            f"promote {current_tier} -> {MultiTenantConsensus._next_tier(current_tier)}"
            if meets_consensus
            else "hold"
        )
        reason = (
            f"{votes_to_promote}/{len(snapshots)} tenants ready ({voting_percentage:.1%})"
        )

        return ConsensusDecision(
            flag_id=flag_id,
            current_tier=current_tier,
            consensus_decision=decision,
            voting_tenants=tenant_ids,
            consensus_percentage=voting_percentage,
            reason=reason,
        )

    @staticmethod
    def _meets_promotion_criteria(tier: str, snapshot: TenantMetricsSnapshot) -> bool:
        """Check if tenant meets promotion criteria for current tier."""
        if tier == "alpha":
            return snapshot.days_in_tier >= 7 and snapshot.error_rate_24h < 0.05
        elif tier == "beta":
            return (
                snapshot.days_in_tier >= 30
                and snapshot.error_rate_24h < 0.01
                and snapshot.adoption_rate > 0.05
                and snapshot.invocation_count_24h > 100
            )
        elif tier == "stable":
            return (
                snapshot.days_in_tier >= 60
                and snapshot.error_rate_24h < 0.001
                and snapshot.adoption_rate > 0.25
                and snapshot.invocation_count_24h > 500
            )
        return False

    @staticmethod
    def _next_tier(tier: str) -> str:
        """Get next tier in progression."""
        tier_map = {
            "alpha": "beta",
            "beta": "stable",
            "stable": "production",
            "production": "production",
        }
        return tier_map.get(tier, "alpha")
