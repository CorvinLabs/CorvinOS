"""Work delegation engine for hierarchical plugins (ADR-0345).

This module handles routing work through the plugin tree, managing budget constraints,
handling failures, and maintaining audit trails of all delegation decisions.

Key responsibilities:
- Route work to local handler or delegate to children
- Enforce tier-aware budget constraints
- Score children for optimal routing
- Handle audit hash mismatches (Tier 1/2 isolation)
- Maintain delegation history
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
import time
import threading
import hashlib
import json

from .node import (
    PluginNode,
    WorkRequest,
    WorkTier,
    DelegationStrategy,
    DelegationEvent,
    DelegationTransaction,
    WorkUnhandleable,
    BudgetExhausted,
    AuditHashMismatchError,
    ChildStatus,
)  # ChildStatus import at top-level (not lazy)
from .graph import PluginGraph
from .utils import now_utc

log = logging.getLogger("corvin.plugins.delegation")


class PluginWorkHandler:
    """Handle work delegation within plugin tree (ADR-0345)."""

    def __init__(self, graph: PluginGraph, audit_log=None, quarantine_registry=None):
        """Initialize the work handler.

        Args:
            graph: PluginGraph instance
            audit_log: Optional audit logger
            quarantine_registry: Optional quarantine system
        """
        self.graph = graph
        self.audit_log = audit_log
        self.quarantine_registry = quarantine_registry
        self.active_transactions: Dict[str, DelegationTransaction] = {}
        # Thread-safety lock for concurrent delegation access
        self._budget_lock = threading.Lock()

    def handle_work(self, plugin_id: str, work: WorkRequest) -> Any:
        """
        Plugin receives work. Handle locally or delegate down tree.

        This is the main entry point for work delegation. It:
        1. Checks if plugin can handle locally
        2. If not, delegates to children based on scoring
        3. Tracks budget and audit trail
        4. Handles failures with fallback chains

        Args:
            plugin_id: ID of plugin receiving work
            work: WorkRequest to handle

        Returns:
            Result of work (from local handler or delegated child)

        Raises:
            WorkUnhandleable: If plugin cannot handle and has no capable children
            BudgetExhausted: If budget limit reached
        """
        node = self.graph.get_node(plugin_id)
        if not node:
            raise ValueError(f"Plugin {plugin_id} not found")

        # Create transaction record
        if work.work_id not in self.active_transactions:
            tx = DelegationTransaction(
                work_id=work.work_id,
                root_request_time=now_utc(),
            )
            self.active_transactions[work.work_id] = tx
        else:
            tx = self.active_transactions[work.work_id]

        start_time = time.time()

        # Step 1: Can I handle this locally?
        if self._can_handle_locally(node, work):
            result = self._do_work(node, work)

            latency_ms = int((time.time() - start_time) * 1000)
            event = DelegationEvent(
                event_type="work_handled_locally",
                plugin_id=plugin_id,
                work_id=work.work_id,
                priority_tier=work.priority_tier.value,
                budget_cost=work.budget_cost,
                latency_ms=latency_ms,
                reason="handled_locally",
                timestamp_utc=now_utc(),
            )

            tx.breadcrumbs.append(event)
            node.delegation_history.append(event.to_dict())

            # Update budget under lock (thread-safe)
            with self._budget_lock:
                node.current_budget_used[work.priority_tier.value] += work.budget_cost

            if self.audit_log:
                self.audit_log.record({
                    "event": "work_handled_locally",
                    "plugin_id": plugin_id,
                    "work_id": work.work_id,
                    "budget_cost": work.budget_cost,
                    "latency_ms": latency_ms,
                })

            log.debug(f"Plugin {plugin_id} handled work {work.work_id} locally")
            return result

        # Step 2: Determine why we can't handle locally
        reason_for_failure = None

        if not node.can_handle_capability(work.required_capability):
            reason_for_failure = "no_capability"
        else:
            # Plugin has capability but check budget
            tier_budget = node.current_budget_used.get(work.priority_tier.value, 0)
            tier_limit = node.budget_config.get_tier_limit(work.priority_tier)
            if tier_budget + work.budget_cost > tier_limit:
                reason_for_failure = "budget_exhausted"

        # Step 3: Should I delegate?
        if node.delegation_strategy == DelegationStrategy.LOCAL_ONLY:
            # Can't delegate - if it's a budget issue, that's fatal
            if reason_for_failure == "budget_exhausted":
                raise BudgetExhausted(
                    f"Plugin {plugin_id} budget exhausted and delegation disabled"
                )
            raise WorkUnhandleable(
                f"Plugin {plugin_id} cannot handle work and delegation disabled"
            )

        # Step 4: Try to delegate to children
        if node.sub_plugins:
            result = self._delegate_to_children(node, work, tx, start_time)
            if self.active_transactions[work.work_id] == tx:
                tx.final_status = "success"
            return result

        # No children to delegate to
        if reason_for_failure == "budget_exhausted":
            raise BudgetExhausted(
                f"Plugin {plugin_id} budget exhausted and has no children to delegate to"
            )
        raise WorkUnhandleable(
            f"Plugin {plugin_id} has no children to delegate to"
        )

    def _can_handle_locally(self, node: PluginNode, work: WorkRequest) -> bool:
        """Can this plugin do the work directly?

        Args:
            node: PluginNode to check
            work: WorkRequest to evaluate

        Returns:
            True if plugin can handle the work locally
        """
        # Check status
        if node.status not in ["ready", "healthy", "degraded"]:
            return False

        # Check capability
        if not node.can_handle_capability(work.required_capability):
            return False

        # Budget check (tier-aware)
        tier_budget = node.current_budget_used.get(work.priority_tier.value, 0)
        tier_limit = node.budget_config.get_tier_limit(work.priority_tier)

        if tier_budget + work.budget_cost > tier_limit:
            return False

        return True

    def _do_work(self, node: PluginNode, work: WorkRequest) -> Any:
        """Execute work locally (placeholder).

        In a real implementation, this would call the plugin's handler.
        For testing, we return a mock result.

        Args:
            node: PluginNode handling the work
            work: WorkRequest to handle

        Returns:
            Mock result (success status)
        """
        # In production, this would invoke the actual plugin handler
        # For now, return a success result
        return {
            "status": "success",
            "work_id": work.work_id,
            "handled_by": node.id,
            "capability": work.required_capability,
        }

    def _delegate_to_children(
        self,
        node: PluginNode,
        work: WorkRequest,
        tx: DelegationTransaction,
        start_time: float,
        failed_children: Optional[set] = None,
    ) -> Any:
        """Delegate work to sub-plugins based on routing policy.

        Args:
            node: Parent PluginNode
            work: WorkRequest to delegate
            tx: DelegationTransaction being built
            start_time: Start time of top-level request
            failed_children: Set of child IDs that already failed (to avoid retry)

        Returns:
            Result from delegated child

        Raises:
            WorkUnhandleable: If no capable children available
            BudgetExhausted: If budget constraints prevent delegation
        """
        if failed_children is None:
            failed_children = set()
        # Find candidates: children that are either:
        # 1. Directly capable of handling the work, OR
        # 2. Not quarantined and can delegate to their own children
        # Exclude failed_children from retry
        candidates = []
        for child_id in node.sub_plugins:
            # Skip previously failed children (avoid infinite retry)
            if child_id in failed_children:
                continue

            child = self.graph.get_node(child_id)
            if not child:
                continue

            # Skip quarantined children
            if self.quarantine_registry and self.quarantine_registry.is_quarantined(child_id):
                continue

            # Include if directly capable
            if child.can_handle_capability(work.required_capability):
                candidates.append(child_id)
            # Or include if can delegate and has children
            elif (child.delegation_strategy != DelegationStrategy.LOCAL_ONLY and
                  child.sub_plugins):
                candidates.append(child_id)

        if not candidates:
            latency_ms = int((time.time() - start_time) * 1000)
            event = DelegationEvent(
                event_type="no_capable_children",
                plugin_id=node.id,
                work_id=work.work_id,
                priority_tier=work.priority_tier.value,
                latency_ms=latency_ms,
                reason="no_capable_children",
                timestamp_utc=now_utc(),
            )
            tx.breadcrumbs.append(event)

            if self.audit_log:
                self.audit_log.record({
                    "event": "no_capable_children",
                    "plugin_id": node.id,
                    "work_id": work.work_id,
                    "required_capability": work.required_capability,
                })

            raise WorkUnhandleable(
                f"No capable children for {work.required_capability}"
            )

        # Score children (busyness + latency + depth)
        scores = {
            child_id: self._score_child(node, child_id, work)
            for child_id in candidates
        }

        target_child = min(candidates, key=lambda c: scores[c])

        # Budget check
        if not node.budget_config.can_delegate(work, node.current_budget_used):
            # Try fallback
            if node.fallback_chain:
                for fallback_id in node.fallback_chain:
                    if fallback_id in candidates:
                        target_child = fallback_id
                        break
                else:
                    raise BudgetExhausted(
                        f"Plugin {node.id} budget exhausted, no fallback"
                    )
            else:
                raise BudgetExhausted(f"Plugin {node.id} budget exhausted")

        # Delegate (recursive call)
        child_node = self.graph.get_node(target_child)
        if not child_node:
            raise ValueError(f"Child {target_child} not found")

        # Track time spent on this child's work only (not accumulated from root)
        child_work_start = time.time()

        try:
            result = self.handle_work(target_child, work)
            latency_ms = int((time.time() - child_work_start) * 1000)

            # Success - populate hash for audit trail integrity
            event = DelegationEvent(
                event_type="work_delegated_success",
                plugin_id=node.id,
                work_id=work.work_id,
                target_child=target_child,
                priority_tier=work.priority_tier.value,
                budget_cost=work.budget_cost,
                latency_ms=latency_ms,
                reason="delegated",
                timestamp_utc=now_utc(),
            )
            # Populate audit hash fields (ADR-0345 compliance)
            event.self_hash = event.compute_self_hash()
            if tx.breadcrumbs:
                event.prior_hash = tx.breadcrumbs[-1].self_hash
            # tree_hash will be set at transaction completion
            event.tree_hash = ""
            tx.breadcrumbs.append(event)
            node.delegation_history.append(event.to_dict())

            # Update budget and child status under lock (thread-safe)
            with self._budget_lock:
                node.current_budget_used[work.priority_tier.value] += work.budget_cost
                # Update child status: latency and work count
                # Defensive: initialize if missing (shouldn't happen if add_child was used)
                if target_child not in node.child_status:
                    node.child_status[target_child] = ChildStatus(
                        child_id=target_child,
                        depth=self.graph._compute_depth(target_child)
                    )
                child_status = node.child_status[target_child]
                child_status.work_count += 1
                # Exponential moving average: new_avg = 0.7 * old_avg + 0.3 * new_latency
                if child_status.avg_latency_ms == 0.0:
                    child_status.avg_latency_ms = latency_ms
                else:
                    child_status.avg_latency_ms = (
                        0.7 * child_status.avg_latency_ms + 0.3 * latency_ms
                    )

            if self.audit_log:
                self.audit_log.record({
                    "event": "work_delegated_success",
                    "from_plugin": node.id,
                    "to_plugin": target_child,
                    "work_id": work.work_id,
                    "score": scores.get(target_child, 0),
                })

            log.debug(
                f"Plugin {node.id} delegated work {work.work_id} to {target_child}"
            )
            return result

        except AuditHashMismatchError as e:
            # Child's audit broke; handle gracefully (Tier 1/2 isolation)
            status = self._handle_audit_failure(
                node.id, target_child, e.expected_hash, e.actual_hash
            )

            if status == "quarantined":
                # Try next fallback
                pass
            elif status == "degraded":
                # Try next fallback
                pass

            # Track failed child and recursively try next candidate
            failed_children.add(target_child)
            remaining_candidates = [c for c in candidates if c not in failed_children]
            if remaining_candidates:
                return self._delegate_to_children(
                    node, work, tx, start_time, failed_children
                )

            raise

        except (TimeoutError, Exception) as e:
            # Compute time spent on child operation only (not accumulated from root)
            latency_ms = int((time.time() - child_work_start) * 1000)
            event = DelegationEvent(
                event_type="work_delegated_failed",
                plugin_id=node.id,
                work_id=work.work_id,
                target_child=target_child,
                priority_tier=work.priority_tier.value,
                latency_ms=latency_ms,
                reason=f"child_error: {type(e).__name__}",
                timestamp_utc=now_utc(),
            )
            tx.breadcrumbs.append(event)

            if self.audit_log:
                self.audit_log.record({
                    "event": "work_delegated_failed",
                    "from_plugin": node.id,
                    "to_plugin": target_child,
                    "work_id": work.work_id,
                    "error": str(e),
                })

            # Track failed child and try next candidate
            failed_children.add(target_child)
            remaining_candidates = [c for c in candidates if c not in failed_children]
            if remaining_candidates:
                return self._delegate_to_children(
                    node, work, tx, start_time, failed_children
                )

            raise WorkUnhandleable(
                f"All children failed for work {work.work_id}: {str(e)}"
            )

    def _is_child_capable(self, child_id: str, work: WorkRequest) -> bool:
        """Check if child can handle this work.

        Args:
            child_id: ID of child plugin
            work: WorkRequest to check

        Returns:
            True if child is capable and not quarantined
        """
        child = self.graph.get_node(child_id)
        if not child:
            return False

        # Check if quarantined
        if self.quarantine_registry:
            if self.quarantine_registry.is_quarantined(child_id):
                return False

        # Check capability
        return child.can_handle_capability(work.required_capability)

    def _score_child(
        self, parent: PluginNode, child_id: str, work: WorkRequest
    ) -> float:
        """
        Score a child for routing.

        Lower score = better candidate.

        Factors: busyness (0.6) + latency (0.3) + depth (0.1)

        Args:
            parent: Parent PluginNode
            child_id: ID of child to score
            work: WorkRequest being routed

        Returns:
            Float score (lower is better)
        """
        child = self.graph.get_node(child_id)
        status = parent.child_status.get(child_id)

        if not child or not status:
            return float("inf")  # Unreachable child

        # Factor 1: Busyness (0.0 = idle, 1.0 = at capacity)
        max_concurrent = parent.budget_config.max_concurrent_children
        work_in_progress = 1 if status.is_busy else 0
        busyness = work_in_progress / max(1, max_concurrent)

        # Factor 2: Latency (lower = better)
        expected_latency_ms = status.avg_latency_ms * (work.budget_cost / 10)
        latency_score = min(1.0, expected_latency_ms / 1000)  # Normalize to [0.0, 1.0]

        # Factor 3: Depth penalty (prefer shallower children; lower score = better)
        # depth=1 (shallow) → 0.1, depth=10 (deep) → 1.0
        depth_penalty = min(1.0, status.depth * 0.1)

        # Weighted average
        final_score = (0.6 * busyness) + (0.3 * latency_score) + (0.1 * depth_penalty)

        return final_score

    def _handle_audit_failure(
        self,
        parent_id: str,
        child_id: str,
        expected_hash: str,
        actual_hash: str,
    ) -> str:
        """
        Handle audit hash mismatch (Tier 1/2 isolation).

        Args:
            parent_id: ID of parent plugin
            child_id: ID of child plugin
            expected_hash: Expected audit hash
            actual_hash: Actual audit hash

        Returns:
            "degraded" (Tier 1) or "quarantined" (Tier 2)
        """
        # Log mismatch
        if self.audit_log:
            self.audit_log.record({
                "event": "audit_hash_mismatch",
                "plugin_id": child_id,
                "parent_id": parent_id,
                "expected_hash": expected_hash,
                "actual_hash": actual_hash,
                "timestamp": now_utc(),
            })

        # Count recent failures (10-min window)
        recent_count = 0
        if self.audit_log:
            recent_count = self.audit_log.count_recent(
                "audit_hash_mismatch", child_id, window_sec=600
            )

        child = self.graph.get_node(child_id)
        if not child:
            return "quarantined"

        if recent_count >= 3:
            # Tier 2: Quarantine
            child.status = "quarantined"
            if self.quarantine_registry:
                self.quarantine_registry.quarantine(
                    child_id, reason=f"repeated_audit_failures ({recent_count} in 10min)"
                )

            if self.audit_log:
                self.audit_log.record({
                    "event": "plugin_quarantined",
                    "plugin_id": child_id,
                    "reason": f"repeated_audit_failures ({recent_count})",
                    "timestamp": now_utc(),
                })

            log.warning(f"Plugin {child_id} quarantined due to audit failures")
            return "quarantined"
        else:
            # Tier 1: Degrade
            child.status = "degraded"

            if self.audit_log:
                self.audit_log.record({
                    "event": "plugin_degraded",
                    "plugin_id": child_id,
                    "reason": "audit_hash_mismatch",
                    "timestamp": now_utc(),
                })

            log.warning(f"Plugin {child_id} degraded due to audit mismatch")
            return "degraded"

    def complete_transaction(self, work_id: str) -> Optional[DelegationTransaction]:
        """Mark transaction complete, remove from active tracking, and return it.

        MUST be called exactly once per work_id to prevent data corruption and memory leak.

        Args:
            work_id: ID of work request

        Returns:
            Completed DelegationTransaction, or None if no active transaction
        """
        # CRITICAL: pop() not get() to prevent reuse and memory leak
        tx = self.active_transactions.pop(work_id, None)
        if not tx:
            return None

        tx.final_status = "success"
        tx.total_latency_ms = sum(bc.latency_ms for bc in tx.breadcrumbs)

        # Tree hash: hash entire breadcrumb chain (all self_hashes)
        if tx.breadcrumbs:
            breadcrumb_hashes = [bc.self_hash for bc in tx.breadcrumbs]
            tx.tree_hash = hashlib.sha256(
                json.dumps(breadcrumb_hashes, sort_keys=True).encode()
            ).hexdigest()

        return tx

    def get_transaction(self, work_id: str) -> Optional[DelegationTransaction]:
        """Get active transaction by work ID (read-only, does NOT remove)."""
        return self.active_transactions.get(work_id)

    def cleanup_transaction(self, work_id: str) -> None:
        """Clean up abandoned transaction (error paths only).

        Use when complete_transaction() won't be called to prevent memory leak.
        """
        self.active_transactions.pop(work_id, None)
