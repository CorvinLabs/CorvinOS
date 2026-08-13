"""Recursive plugin node model with hierarchy and work delegation (ADR-0345).

This module provides the data structures for hierarchical plugins that can contain
sub-plugins and delegate work down the tree. Each plugin is both a consumer (receives
work from parent) and provider (delegates to children).

Key concepts:
- PluginNode: A plugin that may have children and handle delegated work
- WorkTier: Priority levels for work (COMPLIANCE > HIGH > STANDARD > LOW)
- BudgetConfig: Tier-aware resource constraints
- DelegationStrategy: How a plugin decides to delegate work
- DelegationEvent: Audit record for one delegation hop
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from hashlib import sha256
import json

log = logging.getLogger("corvin.plugins.node")


class WorkTier(str, Enum):
    """Work priority tier (inspired by ADR-0195)."""

    COMPLIANCE = "compliance"  # Security/audit work, never deprioritized
    HIGH = "high"  # User-facing, latency-sensitive
    STANDARD = "standard"  # Normal work
    LOW = "low"  # Background/batch work


class DelegationStrategy(str, Enum):
    """How a plugin decides to delegate work."""

    HIERARCHICAL = "hierarchical"  # Delegate down tree
    LOCAL_ONLY = "local_only"  # Never delegate
    HYBRID = "hybrid"  # Try local, then delegate


# ── Exceptions ─────────────────────────────────────────────────────────────


class PluginNodeError(Exception):
    """Base exception for plugin node errors."""


class PluginCycleDetected(PluginNodeError):
    """Registering this plugin would create a cycle in the DAG."""


class BootLayerMismatch(PluginNodeError):
    """Child's boot_layer does not match parent's."""


class WorkUnhandleable(PluginNodeError):
    """Plugin cannot handle this work and has no capable children."""


class BudgetExhausted(PluginNodeError):
    """Budget limit reached; cannot delegate more work."""


class AuditHashMismatchError(PluginNodeError):
    """Child's audit chain hash does not match expected value."""


# ── Work Request ───────────────────────────────────────────────────────────


@dataclass
class WorkRequest:
    """Unit of work that can be delegated through the plugin tree."""

    work_id: str
    input_data: Any
    required_capability: str  # e.g., "transcribe_audio"
    priority_tier: WorkTier = WorkTier.STANDARD
    budget_cost: int = 10  # How much budget this consumes
    timeout_sec: int = 30

    # Response tracking
    assigned_to: Optional[str] = None  # Which child handled this
    status: str = "pending"  # pending | processing | success | failed

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict (for audit trail)."""
        return {
            "work_id": self.work_id,
            "required_capability": self.required_capability,
            "priority_tier": self.priority_tier.value,
            "budget_cost": self.budget_cost,
            "timeout_sec": self.timeout_sec,
            "assigned_to": self.assigned_to,
            "status": self.status,
        }


# ── Budget Configuration ───────────────────────────────────────────────────


@dataclass
class BudgetConfig:
    """Budget constraints (from ADR-0195, adapted for hierarchy)."""

    work_budget_per_cycle: int = 100  # Total work units per health-check cycle

    # Tier-based pools
    compliance_budget_pool: int = 50  # Untouchable by non-COMPLIANCE work
    high_priority_budget_pool: int = 30  # Reserved, can be preempted by COMPLIANCE
    standard_budget_pool: int = 20  # General work
    low_priority_budget_pool: int = 10  # Background work

    max_concurrent_children: int = 3  # Max children working in parallel
    timeout_seconds: int = 30  # RPC timeout for child calls

    # Degradation
    degradation_threshold: float = 0.8  # At 80% budget, stop delegating new work

    def can_delegate(self, work: WorkRequest, current_usage: Dict[str, int]) -> bool:
        """Can we delegate this work given current budget & tier?"""
        if work.priority_tier == WorkTier.COMPLIANCE:
            return (
                current_usage.get("compliance", 0) + work.budget_cost
                <= self.compliance_budget_pool
            )
        elif work.priority_tier == WorkTier.HIGH:
            return (
                current_usage.get("high", 0) + work.budget_cost
                <= self.high_priority_budget_pool
            )
        elif work.priority_tier == WorkTier.STANDARD:
            return (
                current_usage.get("standard", 0) + work.budget_cost
                <= self.standard_budget_pool
            )
        else:  # LOW
            return (
                current_usage.get("low", 0) + work.budget_cost
                <= self.low_priority_budget_pool
            )

    def get_tier_limit(self, tier: WorkTier) -> int:
        """Get budget pool limit for a tier."""
        if tier == WorkTier.COMPLIANCE:
            return self.compliance_budget_pool
        elif tier == WorkTier.HIGH:
            return self.high_priority_budget_pool
        elif tier == WorkTier.STANDARD:
            return self.standard_budget_pool
        else:
            return self.low_priority_budget_pool


# ── Child Status ───────────────────────────────────────────────────────────


@dataclass
class ChildStatus:
    """Track one child's work capacity & health."""

    child_id: str
    is_busy: bool = False
    work_in_progress: Optional[WorkRequest] = None
    work_count: int = 0  # Lifetime work handled
    avg_latency_ms: float = 0.0  # Exponential moving average
    depth: int = 1  # How many levels deep (parent=1, grandchild=2)
    audit_failures_10min: int = 0  # Recent audit failures
    status: str = "healthy"  # healthy | degraded | quarantined

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict."""
        return {
            "child_id": self.child_id,
            "is_busy": self.is_busy,
            "work_count": self.work_count,
            "avg_latency_ms": self.avg_latency_ms,
            "depth": self.depth,
            "audit_failures_10min": self.audit_failures_10min,
            "status": self.status,
        }


# ── Plugin Node ────────────────────────────────────────────────────────────


@dataclass
class PluginNode:
    """One node in the plugin DAG — both a component and an agent (ADR-0345)."""

    # Identity (from ADR-0243)
    id: str
    boot_layer: str  # compliance/core/bundled/installed
    origin: str  # builtin/vetted/community

    # Hierarchy (new in ADR-0345)
    parent_id: Optional[str] = None
    sub_plugins: List[str] = field(default_factory=list)  # Child IDs
    fallback_chain: List[str] = field(default_factory=list)  # Failover order

    # Capabilities this plugin provides
    capabilities: List[str] = field(default_factory=list)

    # Delegation (new in v2 — inspired by ADR-0195)
    delegation_strategy: DelegationStrategy = DelegationStrategy.HIERARCHICAL
    budget_config: BudgetConfig = field(default_factory=BudgetConfig)

    # State
    status: str = "ready"  # ready/loading/healthy/degraded/quarantined/locked_down
    health_check_interval: int = 300  # seconds

    # Audit
    registration_hash: str = ""
    tree_hash: str = ""  # Hash of self + all descendants

    # Runtime (delegation-specific)
    current_budget_used: Dict[str, int] = field(default_factory=dict)
    child_status: Dict[str, ChildStatus] = field(default_factory=dict)
    delegation_history: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Initialize runtime fields."""
        if not self.current_budget_used:
            self.current_budget_used = {
                "compliance": 0,
                "high": 0,
                "standard": 0,
                "low": 0,
            }
        if not self.child_status:
            self.child_status = {}
        if not self.delegation_history:
            self.delegation_history = []

    def get_depth(self) -> int:
        """Get this node's depth in tree (root=0).

        NOTE: This returns 1 if parent_id is set (graph manages actual depth).
        For actual depth computation, use graph._compute_depth(plugin_id).
        """
        if self.parent_id is None:
            return 0
        # This node has a parent, so it's at least depth 1.
        # For actual depth via recursion, use graph._compute_depth()
        return 1

    def can_handle_capability(self, capability: str) -> bool:
        """Can this plugin handle the given capability?"""
        return capability in self.capabilities

    def get_budget_usage_ratio(self) -> float:
        """Get current budget usage as ratio [0.0, 1.0]."""
        total_budget = self.budget_config.work_budget_per_cycle
        if total_budget == 0:
            return 0.0
        used = sum(self.current_budget_used.values())
        return min(1.0, used / total_budget)

    def is_degraded(self) -> bool:
        """Is this plugin at or above degradation threshold?"""
        return self.get_budget_usage_ratio() >= self.budget_config.degradation_threshold

    def to_dict(self) -> Dict[str, Any]:
        """Serialize node to dict."""
        return {
            "id": self.id,
            "boot_layer": self.boot_layer,
            "origin": self.origin,
            "parent_id": self.parent_id,
            "sub_plugins": self.sub_plugins,
            "fallback_chain": self.fallback_chain,
            "capabilities": self.capabilities,
            "delegation_strategy": self.delegation_strategy.value,
            "status": self.status,
            "tree_hash": self.tree_hash,
            "current_budget_used": self.current_budget_used,
            "child_status": {
                cid: status.to_dict()
                for cid, status in self.child_status.items()
            },
        }


# ── Delegation Event (Audit) ───────────────────────────────────────────────


@dataclass
class DelegationEvent:
    """Audit record for one delegation hop (ADR-0345)."""

    event_type: str  # work_received | delegated | completed | failed
    plugin_id: str
    work_id: str
    target_child: Optional[str] = None
    priority_tier: str = "standard"
    budget_cost: int = 0
    latency_ms: int = 0
    reason: str = ""  # handled_locally | delegated | fallback_retry | quota_exceeded

    # Hash chain
    prior_hash: str = ""
    self_hash: str = ""
    tree_hash: str = ""  # Entire delegation chain hash
    timestamp_utc: str = ""

    def compute_self_hash(self) -> str:
        """Compute hash of this event alone."""
        data = {
            "event_type": self.event_type,
            "plugin_id": self.plugin_id,
            "work_id": self.work_id,
            "target_child": self.target_child,
            "priority_tier": self.priority_tier,
            "budget_cost": self.budget_cost,
            "latency_ms": self.latency_ms,
            "reason": self.reason,
            "timestamp_utc": self.timestamp_utc,
        }
        return sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict."""
        return {
            "event_type": self.event_type,
            "plugin_id": self.plugin_id,
            "work_id": self.work_id,
            "target_child": self.target_child,
            "priority_tier": self.priority_tier,
            "budget_cost": self.budget_cost,
            "latency_ms": self.latency_ms,
            "reason": self.reason,
            "prior_hash": self.prior_hash,
            "self_hash": self.self_hash,
            "tree_hash": self.tree_hash,
            "timestamp_utc": self.timestamp_utc,
        }


# ── Delegation Transaction ────────────────────────────────────────────────


@dataclass
class DelegationTransaction:
    """Entire distributed transaction for one work request."""

    work_id: str
    root_request_time: str
    breadcrumbs: List[DelegationEvent] = field(default_factory=list)
    final_status: str = "pending"  # success | timeout | failed
    total_latency_ms: int = 0
    tree_hash: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict."""
        return {
            "work_id": self.work_id,
            "root_request_time": self.root_request_time,
            "breadcrumbs": [bc.to_dict() for bc in self.breadcrumbs],
            "final_status": self.final_status,
            "total_latency_ms": self.total_latency_ms,
            "tree_hash": self.tree_hash,
        }
