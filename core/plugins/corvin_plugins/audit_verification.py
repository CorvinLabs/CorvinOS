"""Audit chain verification for hierarchical plugin delegation (ADR-0345).

This module verifies the integrity of delegation transactions across the plugin tree,
ensuring that:
1. Hash chains are unbroken (GDPR Art. 30/32 integrity)
2. Tree hashes match expected values (tamper-detection)
3. All delegation events are properly sequenced
4. Audit trails are immutable and complete

Key responsibilities:
- Verify hash-chain integrity (event[i].prior_hash == event[i-1].self_hash)
- Verify tree_hash against computed plugin tree hashes
- Detect tampering (hash mismatches)
- Provide audit trail reconstruction
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple
from hashlib import sha256
import json

from .node import DelegationEvent, DelegationTransaction, AuditHashMismatchError
from .graph import PluginGraph

log = logging.getLogger("corvin.plugins.audit_verification")


class AuditVerificationResult:
    """Result of audit chain verification."""

    def __init__(self):
        """Initialize verification result."""
        self.is_valid = True
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.chain_integrity_ok = True
        self.tree_hash_ok = True
        self.tamper_detected = False

    def add_error(self, message: str) -> None:
        """Record a verification error."""
        self.errors.append(message)
        self.is_valid = False

    def add_warning(self, message: str) -> None:
        """Record a verification warning."""
        self.warnings.append(message)

    def mark_chain_broken(self) -> None:
        """Mark chain as broken."""
        self.chain_integrity_ok = False
        self.is_valid = False

    def mark_tampering(self) -> None:
        """Mark tampering detected."""
        self.tamper_detected = True
        self.is_valid = False

    def __str__(self) -> str:
        """String representation."""
        lines = []
        lines.append(f"AuditVerificationResult(valid={self.is_valid})")
        if self.errors:
            lines.append(f"  Errors: {len(self.errors)}")
            for err in self.errors[:3]:  # Show first 3
                lines.append(f"    - {err}")
        if self.warnings:
            lines.append(f"  Warnings: {len(self.warnings)}")
            for warn in self.warnings[:3]:  # Show first 3
                lines.append(f"    - {warn}")
        return "\n".join(lines)


class AuditVerifier:
    """Verify audit chains and tree integrity (ADR-0345 compliance)."""

    def __init__(self, graph: PluginGraph):
        """Initialize the audit verifier.

        Args:
            graph: PluginGraph instance for tree structure
        """
        self.graph = graph

    def verify_delegation_chain(
        self, transaction: DelegationTransaction
    ) -> AuditVerificationResult:
        """Verify hash-chain integrity of a delegation transaction.

        Checks:
        1. Each event's prior_hash matches previous event's self_hash
        2. All events have non-empty self_hash
        3. Transaction tree_hash matches breadcrumb chain hash

        Args:
            transaction: DelegationTransaction to verify

        Returns:
            AuditVerificationResult with validation status
        """
        result = AuditVerificationResult()

        # Empty breadcrumbs is not an error (no delegation occurred)
        if not transaction.breadcrumbs:
            result.add_warning("Transaction has no breadcrumbs (local handling?)")
            return result

        # Verify breadcrumb sequence
        for i, event in enumerate(transaction.breadcrumbs):
            # Check self_hash exists
            if not event.self_hash:
                result.add_error(f"Event {i} has empty self_hash")
                result.mark_chain_broken()
                continue

            # Check prior_hash chain
            if i > 0:
                prev_event = transaction.breadcrumbs[i - 1]
                if event.prior_hash != prev_event.self_hash:
                    result.add_error(
                        f"Event {i} prior_hash mismatch: expected {prev_event.self_hash}, "
                        f"got {event.prior_hash}"
                    )
                    result.mark_chain_broken()
                    result.mark_tampering()
            else:
                # First event should have empty prior_hash
                if event.prior_hash:
                    result.add_warning(f"Event 0 should have empty prior_hash")

            # Verify event's self_hash is correct (recompute and compare)
            computed_hash = event.compute_self_hash()
            if computed_hash != event.self_hash:
                result.add_error(
                    f"Event {i} self_hash invalid: expected {computed_hash}, got {event.self_hash}"
                )
                result.mark_chain_broken()
                result.mark_tampering()

        # Verify transaction tree_hash (hash of all breadcrumb self_hashes)
        expected_tree_hash = self._compute_transaction_tree_hash(transaction)
        if transaction.tree_hash and transaction.tree_hash != expected_tree_hash:
            result.add_error(
                f"Transaction tree_hash mismatch: expected {expected_tree_hash}, "
                f"got {transaction.tree_hash}"
            )
            result.mark_tampering()

        return result

    def verify_tree_hash_integrity(self) -> AuditVerificationResult:
        """Verify tree hashes for all nodes in the graph.

        Checks that each node's tree_hash matches the computed hash of its
        self + all descendants. Detects tampering or corruption.

        Returns:
            AuditVerificationResult with validation status
        """
        result = AuditVerificationResult()

        for node_id, node in self.graph.get_all_nodes().items():
            # Skip nodes with empty tree_hash (may not be set yet)
            if not node.tree_hash:
                continue

            # Compute expected tree_hash
            expected_hash = self.graph._compute_tree_hash(node_id)

            # Compare
            if node.tree_hash != expected_hash:
                result.add_error(
                    f"Node {node_id} tree_hash tampered: expected {expected_hash}, "
                    f"got {node.tree_hash}"
                )
                result.mark_tampering()

        return result

    def verify_audit_immutability(
        self, audit_records: List[Dict], since_timestamp: Optional[str] = None
    ) -> AuditVerificationResult:
        """Verify audit records are in chronological order and have correct structure.

        Args:
            audit_records: List of audit record dicts (from audit log)
            since_timestamp: Only check records after this timestamp (ISO format)

        Returns:
            AuditVerificationResult with validation status
        """
        result = AuditVerificationResult()

        if not audit_records:
            result.add_warning("No audit records to verify")
            return result

        # Verify chronological order
        prev_timestamp = None
        for i, record in enumerate(audit_records):
            timestamp = record.get("timestamp") or record.get("timestamp_utc")
            if not timestamp:
                result.add_warning(f"Record {i} has no timestamp")
                continue

            if prev_timestamp and timestamp < prev_timestamp:
                result.add_error(
                    f"Record {i} timestamp {timestamp} < previous {prev_timestamp} (out of order)"
                )
                result.mark_chain_broken()

            prev_timestamp = timestamp

        # Verify immutability markers (if present)
        for i, record in enumerate(audit_records):
            if "prior_hash" in record and "self_hash" in record:
                if i > 0:
                    prev_record = audit_records[i - 1]
                    if (
                        record.get("prior_hash")
                        != prev_record.get("self_hash")
                    ):
                        result.add_error(
                            f"Record {i} prior_hash mismatch (tampering detected)"
                        )
                        result.mark_tampering()

        return result

    def reconstruct_delegation_path(
        self, transaction: DelegationTransaction
    ) -> List[Tuple[str, str, str]]:
        """Reconstruct the full delegation path from a transaction.

        Returns list of (plugin_id, work_id, reason) tuples showing the path
        work took through the plugin tree.

        Args:
            transaction: DelegationTransaction to analyze

        Returns:
            List of (plugin_id, work_id, reason) tuples
        """
        path = []
        for event in transaction.breadcrumbs:
            path.append(
                (event.plugin_id, event.work_id, event.reason)
            )
        return path

    def get_audit_summary(self, transaction: DelegationTransaction) -> Dict:
        """Get summary statistics for a delegation transaction.

        Args:
            transaction: DelegationTransaction to summarize

        Returns:
            Dict with summary stats
        """
        summary = {
            "work_id": transaction.work_id,
            "root_request_time": transaction.root_request_time,
            "final_status": transaction.final_status,
            "total_latency_ms": transaction.total_latency_ms,
            "hop_count": len(transaction.breadcrumbs),
            "path": [],
            "events_by_type": {},
        }

        for event in transaction.breadcrumbs:
            summary["path"].append(
                f"{event.plugin_id} ({event.reason})"
            )
            event_type = event.event_type
            if event_type not in summary["events_by_type"]:
                summary["events_by_type"][event_type] = 0
            summary["events_by_type"][event_type] += 1

        return summary

    @staticmethod
    def _compute_transaction_tree_hash(transaction: DelegationTransaction) -> str:
        """Compute tree hash from transaction breadcrumbs.

        Args:
            transaction: DelegationTransaction

        Returns:
            Hex-encoded SHA256 hash
        """
        if not transaction.breadcrumbs:
            return sha256(b"").hexdigest()

        breadcrumb_hashes = [bc.self_hash for bc in transaction.breadcrumbs]
        return sha256(
            json.dumps(breadcrumb_hashes, sort_keys=True).encode()
        ).hexdigest()

    def log_audit_trail(
        self, transaction: DelegationTransaction, audit_log=None
    ) -> None:
        """Log entire delegation transaction to audit trail.

        Args:
            transaction: DelegationTransaction to log
            audit_log: Optional audit logger
        """
        if not audit_log:
            return

        summary = self.get_audit_summary(transaction)
        audit_log.record({
            "event": "delegation_transaction_completed",
            "work_id": transaction.work_id,
            "final_status": transaction.final_status,
            "hop_count": len(transaction.breadcrumbs),
            "total_latency_ms": transaction.total_latency_ms,
            "tree_hash": transaction.tree_hash,
            "path": " → ".join(summary["path"]),
            "timestamp": transaction.root_request_time,
        })

        # Log individual events for detailed audit trail
        for event in transaction.breadcrumbs:
            audit_log.record({
                "event": event.event_type,
                "work_id": event.work_id,
                "plugin_id": event.plugin_id,
                "target_child": event.target_child,
                "priority_tier": event.priority_tier,
                "budget_cost": event.budget_cost,
                "latency_ms": event.latency_ms,
                "reason": event.reason,
                "self_hash": event.self_hash,
                "prior_hash": event.prior_hash,
                "tree_hash": event.tree_hash,
                "timestamp": event.timestamp_utc,
            })
