"""L5 k=4: Conflict Resolver — Multi-Skill Parameter Coordination.

ADR-0581: Conflict Resolver (Multi-Skill Parameter Coordination)
Detects when multiple Skills request changes to the same parameter concurrently.
Resolves via SERIALIZE (default), MERGE (opt-in), or BLOCK (error).
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Set
from enum import Enum
import logging
from datetime import datetime, timedelta
import json
from pathlib import Path
import threading
import uuid

from .utils import format_iso_timestamp, parse_iso_timestamp

logger = logging.getLogger(__name__)


class ConflictStrategy(str, Enum):
    """Conflict resolution strategies."""
    SERIALIZE = "serialize"  # Queue 2nd approval after 1st applies
    MERGE = "merge"         # Weighted average (opt-in only)
    BLOCK = "block"         # Error; force manual resolution


class ConflictType(str, Enum):
    """Types of conflicts detected."""
    CONCURRENT_PARAMETER = "concurrent_parameter"  # ≥2 Skills tuning same param
    SAME_SKILL_RACE = "same_skill_race"            # Same Skill called twice
    CASCADING = "cascading"                        # One approval depends on another


@dataclass
class Conflict:
    """Represents a detected conflict between two approvals."""
    conflict_id: str
    skill_a_id: str
    skill_b_id: str
    metric_name: str           # Shared parameter name
    conflict_type: ConflictType
    time_overlap: Tuple[str, str]  # (start, end) of overlap window
    reason: str
    severity: str              # "low", "medium", "high"
    timestamp: str             # ISO 8601
    audit_event_id: str = ""   # Linked to audit trail


@dataclass
class ConflictResolution:
    """Outcome of conflict resolution."""
    conflict_id: str
    strategy: ConflictStrategy
    resolution: str            # Human-readable description
    action: str                # "serialize_b" | "merge_and_reapprove" | "block_b"
    affected_approval_ids: List[str]
    timestamp: str             # ISO 8601


class ConflictDetector:
    """Detect conflicts between concurrent approval requests."""

    @staticmethod
    def detect_conflicts(
        pending_approvals: Dict[str, Dict[str, dict]],
    ) -> List[Conflict]:
        """Scan pending approvals for conflicting parameter changes.

        Conflicts occur when:
        1. Two+ approvals are in-flight at same time (time overlap)
        2. They reference the same parameter (by metric_name)
        3. They come from DIFFERENT Skills (not same-Skill concurrency)

        Optimization: Group approvals by metric_name first (O(n)), then scan conflicts
        only within same-metric groups. Actual complexity: O(n + k²) where k = max group size.
        This is better than O(n²) when k << n.

        Args:
            pending_approvals: Dict[skill_id][metric_name] = approval_record

        Returns:
            List of detected Conflict objects
        """
        conflicts: List[Conflict] = []

        # Group approvals by metric_name to reduce comparison scope
        metrics_groups: Dict[str, List[Tuple[str, str, dict]]] = {}
        for skill_id, metric_dict in pending_approvals.items():
            for metric_name, record in metric_dict.items():
                if metric_name not in metrics_groups:
                    metrics_groups[metric_name] = []
                metrics_groups[metric_name].append((skill_id, metric_name, record))

        # Scan conflicts only within same-metric groups
        for metric_name, approvals_for_metric in metrics_groups.items():
            # Only worth checking if ≥2 approvals for this metric
            if len(approvals_for_metric) < 2:
                continue

            # Scan all pairs within this metric group
            for i, (skill_a, metric_a, record_a) in enumerate(approvals_for_metric):
                for skill_b, metric_b, record_b in approvals_for_metric[i + 1 :]:
                    # Only detect cross-Skill conflicts (same_Skill is handled elsewhere)
                    if skill_a == skill_b:
                        continue

                    # Same parameter? (guaranteed by grouping, but verify)
                    if metric_a != metric_b:
                        continue

                    # Time overlap?
                    time_a = (
                        record_a.get("operator_timestamp", ""),
                        record_a.get("ttl_expires", ""),
                    )
                    time_b = (
                        record_b.get("operator_timestamp", ""),
                        record_b.get("ttl_expires", ""),
                    )

                    if ConflictDetector._times_overlap(time_a, time_b):
                        # Compute actual intersection bounds
                        overlap_start = max(time_a[0], time_b[0])
                        overlap_end = min(time_a[1], time_b[1])

                        conflict = Conflict(
                            conflict_id=str(uuid.uuid4()),
                            skill_a_id=skill_a,
                            skill_b_id=skill_b,
                            metric_name=metric_a,
                            conflict_type=ConflictType.CONCURRENT_PARAMETER,
                            time_overlap=(overlap_start, overlap_end),
                            reason=f"{skill_a} and {skill_b} both requesting "
                            f"changes to {metric_a} in overlapping time windows",
                            severity="medium",
                            timestamp=format_iso_timestamp(),
                        )
                        conflicts.append(conflict)

        return conflicts

    @staticmethod
    def _times_overlap(
        interval_a: Tuple[str, str], interval_b: Tuple[str, str]
    ) -> bool:
        """Check if two time intervals overlap.

        Args:
            interval_a: (start, end) ISO 8601 timestamps
            interval_b: (start, end) ISO 8601 timestamps

        Returns:
            True if intervals overlap, False otherwise
        """
        try:
            start_a = datetime.fromisoformat(interval_a[0].replace("Z", ""))
            end_a = datetime.fromisoformat(interval_a[1].replace("Z", ""))
            start_b = datetime.fromisoformat(interval_b[0].replace("Z", ""))
            end_b = datetime.fromisoformat(interval_b[1].replace("Z", ""))

            # Overlap if start_a <= start_b < end_a OR start_b <= start_a < end_b
            return start_a <= start_b < end_a or start_b <= start_a < end_b
        except (ValueError, IndexError, TypeError):
            # If timestamps are invalid, assume no overlap (safe default)
            return False


class ConflictResolver:
    """Resolve conflicts between concurrent Skill learning approvals.

    Default strategy: SERIALIZE (queue 2nd approval after 1st applies).
    Merge only if both Skills explicitly opt-in.
    Block forces manual resolution.
    """

    def __init__(
        self,
        tenant_id: str = "_default",
        default_strategy: ConflictStrategy = ConflictStrategy.SERIALIZE,
        audit_backend=None,
        corvin_home: str = None,
    ):
        """Initialize conflict resolver.

        Args:
            tenant_id: Tenant for isolation
            default_strategy: Default resolution strategy
            audit_backend: Audit backend (for logging)
            corvin_home: Path to ~/.corvin
        """
        self.tenant_id = tenant_id
        self.default_strategy = default_strategy
        self.audit_backend = audit_backend

        # Persistence: path to conflicts.jsonl
        if corvin_home is None:
            import os

            corvin_home = os.getenv("CORVIN_HOME", os.path.expanduser("~/.corvin"))
        self.corvin_home = Path(corvin_home)
        self.conflicts_file = (
            self.corvin_home / "tenants" / tenant_id / "learning" / "conflicts.jsonl"
        )

        # Thread safety
        self._lock = threading.RLock()

        # In-memory conflict queue
        self.conflicts: Dict[str, Conflict] = {}
        self.resolutions: Dict[str, ConflictResolution] = {}

        # Skill-specific merge opt-ins
        self.merge_opt_in: Set[Tuple[str, str, str]] = set()  # (skill_a, skill_b, metric)

        # Load persisted conflicts
        self._load_persisted_conflicts()

    def _load_persisted_conflicts(self) -> None:
        """Load conflict history from disk (recovery after restart)."""
        if not self.conflicts_file.exists():
            return

        try:
            with open(self.conflicts_file, "r") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                        # Deserialize to Conflict dataclass (not raw dict)
                        if data.get("type") == "conflict":
                            conflict = Conflict(
                                conflict_id=data.get("conflict_id", ""),
                                skill_a_id=data.get("skill_a_id", ""),
                                skill_b_id=data.get("skill_b_id", ""),
                                metric_name=data.get("metric_name", ""),
                                conflict_type=ConflictType(data.get("conflict_type", "concurrent_parameter")),
                                time_overlap=(data.get("time_overlap_start", ""), data.get("time_overlap_end", "")),
                                reason=data.get("reason", ""),
                                severity=data.get("severity", "medium"),
                                timestamp=data.get("timestamp", ""),
                                audit_event_id=data.get("audit_event_id", ""),
                            )
                            self.conflicts[conflict.conflict_id] = conflict
                    except (json.JSONDecodeError, TypeError, ValueError) as e:
                        logger.warning(f"[L5 Conflict] Failed to load conflict: {e}")
        except Exception as e:
            logger.error(f"[L5 Conflict] Failed to load persisted conflicts: {e}")

    def _persist_conflict(self, conflict: Conflict) -> None:
        """Append conflict to disk (immutable log)."""
        try:
            self.conflicts_file.parent.mkdir(parents=True, exist_ok=True)

            with open(self.conflicts_file, "a") as f:
                record = {
                    "type": "conflict",
                    "conflict_id": conflict.conflict_id,
                    "skill_a_id": conflict.skill_a_id,
                    "skill_b_id": conflict.skill_b_id,
                    "metric_name": conflict.metric_name,
                    "conflict_type": conflict.conflict_type.value,
                    "time_overlap_start": conflict.time_overlap[0],
                    "time_overlap_end": conflict.time_overlap[1],
                    "reason": conflict.reason,
                    "severity": conflict.severity,
                    "timestamp": conflict.timestamp,
                    "audit_event_id": conflict.audit_event_id,
                }
                f.write(json.dumps(record) + "\n")
        except Exception as e:
            logger.error(f"[L5 Conflict] Failed to persist conflict: {e}")

    def detect_and_resolve(
        self,
        pending_approvals: Dict[str, Dict[str, dict]],
    ) -> List[ConflictResolution]:
        """Detect conflicts in pending approvals and resolve them.

        Args:
            pending_approvals: Dict[skill_id][metric_name] = approval_record

        Returns:
            List of ConflictResolution outcomes
        """
        with self._lock:
            # Detect conflicts
            conflicts = ConflictDetector.detect_conflicts(pending_approvals)

            resolutions: List[ConflictResolution] = []

            for conflict in conflicts:
                # Audit-first: log conflict detection (fail-closed)
                if self.audit_backend:
                    try:
                        audit_event = {
                            "tenant_id": self.tenant_id,
                            "event_type": "learning_conflict_detected",
                            "conflict_id": conflict.conflict_id,
                            "skill_a_id": conflict.skill_a_id,
                            "skill_b_id": conflict.skill_b_id,
                            "metric_name": conflict.metric_name,
                            "conflict_type": conflict.conflict_type.value,
                            "severity": conflict.severity,
                        }
                        event_id = self.audit_backend.write_event(audit_event)
                        conflict.audit_event_id = str(event_id) if event_id else ""
                    except Exception as e:
                        logger.error(f"[L5 Conflict] FATAL: audit_backend.write_event() failed: {e}")
                        raise RuntimeError(
                            f"[L5 Conflict] FATAL: audit_backend.write_event() failed: {e}. "
                            f"Conflict resolution BLOCKED (fail-closed constraint C5)."
                        )

                # Store conflict
                self.conflicts[conflict.conflict_id] = conflict
                self._persist_conflict(conflict)

                # Resolve
                resolution = self._resolve_conflict(conflict)
                self.resolutions[conflict.conflict_id] = resolution
                resolutions.append(resolution)

                logger.warning(
                    f"[L5 Conflict] {conflict.skill_a_id} vs {conflict.skill_b_id}: "
                    f"resolved via {resolution.strategy.value}"
                )

            return resolutions

    def _resolve_conflict(self, conflict: Conflict) -> ConflictResolution:
        """Resolve a single conflict via selected strategy.

        Args:
            conflict: The conflict to resolve

        Returns:
            ConflictResolution with action and explanation
        """
        # Choose strategy
        strategy = self._choose_strategy(conflict)

        if strategy == ConflictStrategy.SERIALIZE:
            action = "serialize_b"
            resolution = f"Queue {conflict.skill_b_id} after {conflict.skill_a_id} applies"
        elif strategy == ConflictStrategy.MERGE:
            action = "merge_and_reapprove"
            resolution = f"Merge {conflict.skill_a_id} and {conflict.skill_b_id} deltas; "
            resolution += "merged config will go through k=2 approval again"
        else:  # BLOCK
            action = "block_b"
            resolution = f"Blocked {conflict.skill_b_id}; operator must resolve manually"

        res = ConflictResolution(
            conflict_id=conflict.conflict_id,
            strategy=strategy,
            resolution=resolution,
            action=action,
            affected_approval_ids=[],  # Would be filled in by caller
            timestamp=format_iso_timestamp(),
        )

        return res

    def _choose_strategy(self, conflict: Conflict) -> ConflictStrategy:
        """Choose resolution strategy based on conflict type and Skill config.

        Args:
            conflict: The conflict to analyze

        Returns:
            ConflictStrategy (SERIALIZE | MERGE | BLOCK)
        """
        # Check if both Skills have opted in to merge
        key = (conflict.skill_a_id, conflict.skill_b_id, conflict.metric_name)
        if key in self.merge_opt_in or (
            conflict.skill_b_id,
            conflict.skill_a_id,
            conflict.metric_name,
        ) in self.merge_opt_in:
            # Both opted in — use MERGE
            return ConflictStrategy.MERGE

        # Default: SERIALIZE
        return self.default_strategy

    def set_merge_opt_in(
        self, skill_a: str, skill_b: str, metric_name: str, enabled: bool
    ) -> None:
        """Enable/disable merge for a specific Skill pair + metric.

        Args:
            skill_a: First Skill
            skill_b: Second Skill
            metric_name: Shared metric
            enabled: True to opt-in to merge, False to disable
        """
        key = (skill_a, skill_b, metric_name)
        with self._lock:
            if enabled:
                self.merge_opt_in.add(key)
                logger.info(f"[L5 Conflict] Merge opt-in: {skill_a} + {skill_b}.{metric_name}")
            else:
                self.merge_opt_in.discard(key)
                logger.info(f"[L5 Conflict] Merge opt-in disabled: {skill_a} + {skill_b}.{metric_name}")

    def get_conflicts(self) -> List[Conflict]:
        """Get all detected conflicts.

        Returns:
            List of Conflict objects
        """
        with self._lock:
            return list(self.conflicts.values())

    def get_conflict(self, conflict_id: str) -> Optional[Conflict]:
        """Get a specific conflict by ID.

        Args:
            conflict_id: UUID of conflict

        Returns:
            Conflict if found, None otherwise
        """
        with self._lock:
            return self.conflicts.get(conflict_id)

