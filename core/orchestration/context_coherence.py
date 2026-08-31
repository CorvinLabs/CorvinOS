"""Context Coherence — Cross-session tool/strategy inheritance (Gap 5).

Enables multi-session tasks to inherit tool and strategy history from parent sessions.
Maintains coherence chain for audit trail and enables learned preferences to carry forward.

ADR Reference: ADR-0390 (Context Coherence Architecture) — to be created in Corvin-ADR

Key components:
1. ToolCoherence — Tracks known good/bad tools and their success rates per error class
2. SessionCheckpoint extension — Adds coherence field for persistence
3. ContextCoherenceManager — Manages inheritance chain and validates age constraints
4. inherit_parent_context() — Blends parent preferences with current session preferences

Design Doc: docs/implementation/GAP-5-CONTEXT-COHERENCE-IMPLEMENTATION.md
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class ConflictResolutionStrategy(str, Enum):
    """How to resolve conflicts between parent and current preferences."""

    PARENT_PREFERRED = "parent_preferred"  # Parent wins
    CURRENT_PREFERRED = "current_preferred"  # Current session wins
    BLEND = "blend"  # Merge, current overrides parent on conflicts


@dataclass(frozen=True)
class ToolSuccessRate:
    """Success rate of a tool for a specific error class."""

    error_class: str
    success_count: int
    total_count: int
    avg_latency_ms: int
    avg_cost_cents: int
    last_used_timestamp: datetime

    @property
    def success_rate(self) -> float:
        """Calculate success rate as percentage."""
        if self.total_count == 0:
            return 0.0
        return self.success_count / self.total_count

    @property
    def confidence(self) -> float:
        """Confidence score (increases with sample count, max at 30 samples)."""
        # Converges at 30 samples
        return min(1.0, self.total_count / 30.0)


@dataclass
class ToolCoherence:
    """Tool coherence tracking for cross-session reuse.

    Maintains list of known good/bad tools and their success rates per error class.
    Updated as tools are executed in the session and inherited from parent sessions.
    """

    parent_session_id: Optional[str] = None
    parent_coherence_id: Optional[str] = None
    coherence_chain: List[str] = field(default_factory=list)  # Full ancestry for audit

    # Tools known to succeed
    tools_known_good: Dict[str, float] = field(default_factory=dict)  # tool_id -> success_rate
    # Tools known to fail
    tools_known_bad: Dict[str, float] = field(default_factory=dict)  # tool_id -> failure_rate

    # Success rates by error class: {error_class: {tool_id: ToolSuccessRate}}
    success_rates_per_error: Dict[str, Dict[str, ToolSuccessRate]] = field(
        default_factory=dict
    )

    # Strategy preferences learned from this session
    learned_strategies: Dict[str, str] = field(default_factory=dict)  # error_class -> strategy
    learned_preferences: Dict[str, Any] = field(default_factory=dict)  # Operator choices

    # Cost tracking for refinement
    cost_deltas: List[float] = field(default_factory=list)  # Estimate vs actual
    cost_corrections: List[Tuple[float, float]] = field(
        default_factory=list
    )  # (estimated, actual)

    # Metadata
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    tenant_id: str = "_default"

    def is_stale(self, max_age_hours: int = 24) -> bool:
        """Check if coherence context is too old to be trusted.

        Args:
            max_age_hours: Maximum age in hours (default 24)

        Returns:
            True if context is older than max_age_hours
        """
        age = datetime.now(timezone.utc) - self.created_at
        return age > timedelta(hours=max_age_hours)

    def get_success_rate_for_tool_and_error(
        self, tool_id: str, error_class: str
    ) -> Optional[float]:
        """Get success rate for a specific tool and error class.

        Args:
            tool_id: Tool identifier
            error_class: Error class (e.g., 'syntax', 'logic')

        Returns:
            Success rate (0.0-1.0) or None if not found
        """
        if error_class not in self.success_rates_per_error:
            return None
        if tool_id not in self.success_rates_per_error[error_class]:
            return None
        return self.success_rates_per_error[error_class][tool_id].success_rate

    def get_recommended_tools_for_error(
        self, error_class: str, top_n: int = 3, min_confidence: float = 0.3
    ) -> List[Tuple[str, float, float]]:
        """Get top N tools for an error class, ranked by success rate.

        Args:
            error_class: Error class to filter by
            top_n: Maximum number of tools to return
            min_confidence: Minimum confidence threshold (0.0-1.0)

        Returns:
            List of (tool_id, success_rate, confidence) sorted by success_rate DESC
        """
        if error_class not in self.success_rates_per_error:
            return []

        tools = []
        for tool_id, rate_info in self.success_rates_per_error[error_class].items():
            if rate_info.confidence >= min_confidence:
                tools.append((tool_id, rate_info.success_rate, rate_info.confidence))

        # Sort by success_rate descending
        tools.sort(key=lambda x: x[1], reverse=True)
        return tools[:top_n]

    def record_tool_execution(
        self,
        tool_id: str,
        error_class: str,
        succeeded: bool,
        latency_ms: int,
        cost_cents: int,
    ) -> None:
        """Record execution of a tool for learning.

        Args:
            tool_id: Tool identifier
            error_class: Error class this tool was used for
            succeeded: Whether tool succeeded
            latency_ms: Tool execution latency
            cost_cents: Tool cost in cents
        """
        # Initialize error_class if needed
        if error_class not in self.success_rates_per_error:
            self.success_rates_per_error[error_class] = {}

        # Get or create ToolSuccessRate
        if tool_id in self.success_rates_per_error[error_class]:
            old_rate = self.success_rates_per_error[error_class][tool_id]
            new_success_count = old_rate.success_count + (1 if succeeded else 0)
            new_total_count = old_rate.total_count + 1
            # Weighted average
            new_latency = int(
                (old_rate.avg_latency_ms * (old_rate.total_count - 1) + latency_ms)
                / new_total_count
            )
            new_cost = int(
                (old_rate.avg_cost_cents * (old_rate.total_count - 1) + cost_cents)
                / new_total_count
            )
        else:
            new_success_count = 1 if succeeded else 0
            new_total_count = 1
            new_latency = latency_ms
            new_cost = cost_cents

        self.success_rates_per_error[error_class][tool_id] = ToolSuccessRate(
            error_class=error_class,
            success_count=new_success_count,
            total_count=new_total_count,
            avg_latency_ms=new_latency,
            avg_cost_cents=new_cost,
            last_used_timestamp=datetime.now(timezone.utc),
        )

        # Update known good/bad at session level
        success_rate = new_success_count / new_total_count
        if success_rate >= 0.8:
            self.tools_known_good[tool_id] = success_rate
            self.tools_known_bad.pop(tool_id, None)
        elif success_rate <= 0.3:
            self.tools_known_bad[tool_id] = 1.0 - success_rate
            self.tools_known_good.pop(tool_id, None)
        else:
            # Neutral; don't track
            self.tools_known_good.pop(tool_id, None)
            self.tools_known_bad.pop(tool_id, None)

    def record_cost_estimate(self, estimated: float, actual: float) -> None:
        """Record cost estimate vs actual for calibration.

        Args:
            estimated: Estimated cost
            actual: Actual cost
        """
        delta = actual - estimated
        self.cost_deltas.append(delta)
        self.cost_corrections.append((estimated, actual))

    def average_cost_error(self) -> float:
        """Mean absolute error of cost estimates."""
        if not self.cost_deltas:
            return 0.0
        return sum(abs(d) for d in self.cost_deltas) / len(self.cost_deltas)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary for persistence."""
        # Convert datetime objects to ISO strings
        return {
            "parent_session_id": self.parent_session_id,
            "parent_coherence_id": self.parent_coherence_id,
            "coherence_chain": self.coherence_chain,
            "tools_known_good": self.tools_known_good,
            "tools_known_bad": self.tools_known_bad,
            "success_rates_per_error": {
                error_class: {
                    tool_id: {
                        "error_class": rate.error_class,
                        "success_count": rate.success_count,
                        "total_count": rate.total_count,
                        "avg_latency_ms": rate.avg_latency_ms,
                        "avg_cost_cents": rate.avg_cost_cents,
                        "last_used_timestamp": rate.last_used_timestamp.isoformat(),
                    }
                    for tool_id, rate in tools.items()
                }
                for error_class, tools in self.success_rates_per_error.items()
            },
            "learned_strategies": self.learned_strategies,
            "learned_preferences": self.learned_preferences,
            "cost_deltas": self.cost_deltas,
            "cost_corrections": self.cost_corrections,
            "created_at": self.created_at.isoformat(),
            "tenant_id": self.tenant_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ToolCoherence:
        """Deserialize from dictionary.

        Args:
            data: Dictionary representation

        Returns:
            ToolCoherence instance
        """
        # Reconstruct ToolSuccessRate objects
        success_rates_per_error = {}
        for error_class, tools in data.get("success_rates_per_error", {}).items():
            success_rates_per_error[error_class] = {}
            for tool_id, rate_data in tools.items():
                success_rates_per_error[error_class][tool_id] = ToolSuccessRate(
                    error_class=rate_data["error_class"],
                    success_count=rate_data["success_count"],
                    total_count=rate_data["total_count"],
                    avg_latency_ms=rate_data["avg_latency_ms"],
                    avg_cost_cents=rate_data["avg_cost_cents"],
                    last_used_timestamp=datetime.fromisoformat(
                        rate_data["last_used_timestamp"]
                    ),
                )

        return cls(
            parent_session_id=data.get("parent_session_id"),
            parent_coherence_id=data.get("parent_coherence_id"),
            coherence_chain=data.get("coherence_chain", []),
            tools_known_good=data.get("tools_known_good", {}),
            tools_known_bad=data.get("tools_known_bad", {}),
            success_rates_per_error=success_rates_per_error,
            learned_strategies=data.get("learned_strategies", {}),
            learned_preferences=data.get("learned_preferences", {}),
            cost_deltas=data.get("cost_deltas", []),
            cost_corrections=data.get("cost_corrections", []),
            created_at=datetime.fromisoformat(data.get("created_at", datetime.now(timezone.utc).isoformat())),
            tenant_id=data.get("tenant_id", "_default"),
        )


@dataclass
class SessionCheckpointWithCoherence:
    """Extended SessionCheckpoint with coherence field for Gap 5.

    Adds context coherence tracking to session checkpoint for multi-session
    task continuation with tool/strategy inheritance.
    """

    task_id: str
    session_id: str
    parent_session_id: Optional[str] = None
    coherence: Optional[ToolCoherence] = None  # Coherence from this session
    completion_percentage: float = 0.0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    tenant_id: str = "_default"

    def to_dict(self) -> Dict[str, Any]:
        """Serialize checkpoint with coherence."""
        return {
            "task_id": self.task_id,
            "session_id": self.session_id,
            "parent_session_id": self.parent_session_id,
            "coherence": self.coherence.to_dict() if self.coherence else None,
            "completion_percentage": self.completion_percentage,
            "created_at": self.created_at.isoformat(),
            "tenant_id": self.tenant_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SessionCheckpointWithCoherence:
        """Deserialize checkpoint with coherence."""
        coherence_data = data.get("coherence")
        coherence = ToolCoherence.from_dict(coherence_data) if coherence_data else None

        return cls(
            task_id=data["task_id"],
            session_id=data["session_id"],
            parent_session_id=data.get("parent_session_id"),
            coherence=coherence,
            completion_percentage=data.get("completion_percentage", 0.0),
            created_at=datetime.fromisoformat(data.get("created_at", datetime.now(timezone.utc).isoformat())),
            tenant_id=data.get("tenant_id", "_default"),
        )


class ContextCoherenceManager:
    """Manage context coherence chains across sessions.

    Handles:
    - Creating new coherence contexts
    - Inheriting from parent sessions
    - Validating age constraints
    - Conflict resolution when blending preferences
    - Audit trail integration
    """

    def __init__(self, max_age_hours: int = 24):
        """Initialize coherence manager.

        Args:
            max_age_hours: Maximum age for context reuse (default 24 hours)
        """
        self.max_age_hours = max_age_hours
        self._coherence_cache: Dict[str, ToolCoherence] = {}

    def create_coherence(
        self,
        task_id: str,
        session_id: str,
        tenant_id: str = "_default",
        parent_coherence: Optional[ToolCoherence] = None,
    ) -> ToolCoherence:
        """Create new coherence context for a session.

        Args:
            task_id: Task identifier
            session_id: Session identifier
            tenant_id: Tenant identifier
            parent_coherence: Optional parent coherence to inherit from

        Returns:
            New ToolCoherence instance
        """
        coherence_chain = []
        parent_session_id = None
        parent_coherence_id = None

        if parent_coherence is not None:
            # Validate parent coherence age
            if parent_coherence.is_stale(self.max_age_hours):
                logger.warning(
                    f"Parent coherence for task {task_id} is stale "
                    f"(age > {self.max_age_hours}h); not inheriting"
                )
                parent_coherence = None
            else:
                parent_session_id = parent_coherence.parent_session_id
                parent_coherence_id = id(parent_coherence)
                coherence_chain = parent_coherence.coherence_chain + [
                    parent_coherence_id
                ]

        coherence = ToolCoherence(
            parent_session_id=parent_session_id,
            parent_coherence_id=parent_coherence_id,
            coherence_chain=coherence_chain,
            tenant_id=tenant_id,
        )

        # Cache by task_id
        self._coherence_cache[task_id] = coherence

        logger.debug(
            f"Created coherence for task {task_id} "
            f"(chain_length={len(coherence_chain)}, parent={parent_session_id})"
        )

        return coherence

    def inherit_parent_context(
        self,
        task_id: str,
        parent_coherence: ToolCoherence,
        strategy: ConflictResolutionStrategy = ConflictResolutionStrategy.BLEND,
    ) -> bool:
        """Inherit learning from parent session.

        Args:
            task_id: Task to inherit into
            parent_coherence: Parent coherence to inherit from
            strategy: Conflict resolution strategy

        Returns:
            True if inheritance succeeded, False if validation failed
        """
        if task_id not in self._coherence_cache:
            logger.error(f"Task {task_id} not found in coherence cache")
            return False

        my_coherence = self._coherence_cache[task_id]

        # Validate parent age
        if parent_coherence.is_stale(self.max_age_hours):
            logger.warning(f"Parent coherence is stale; inheritance skipped")
            return False

        # Validate tenant match
        if parent_coherence.tenant_id != my_coherence.tenant_id:
            logger.error(
                f"Tenant mismatch: parent={parent_coherence.tenant_id}, "
                f"current={my_coherence.tenant_id}"
            )
            return False

        try:
            # Inherit tool success rates
            for error_class, tools in parent_coherence.success_rates_per_error.items():
                if error_class not in my_coherence.success_rates_per_error:
                    my_coherence.success_rates_per_error[error_class] = {}

                for tool_id, rate in tools.items():
                    if tool_id not in my_coherence.success_rates_per_error[error_class]:
                        my_coherence.success_rates_per_error[error_class][tool_id] = rate

            # Inherit known good/bad tools
            if strategy == ConflictResolutionStrategy.PARENT_PREFERRED:
                my_coherence.tools_known_good = parent_coherence.tools_known_good.copy()
                my_coherence.tools_known_bad = parent_coherence.tools_known_bad.copy()
            elif strategy == ConflictResolutionStrategy.CURRENT_PREFERRED:
                # Keep current, don't override
                pass
            elif strategy == ConflictResolutionStrategy.BLEND:
                # Merge: current overrides parent on conflicts
                for tool_id, rate in parent_coherence.tools_known_good.items():
                    if tool_id not in my_coherence.tools_known_good:
                        my_coherence.tools_known_good[tool_id] = rate
                for tool_id, rate in parent_coherence.tools_known_bad.items():
                    if tool_id not in my_coherence.tools_known_bad:
                        my_coherence.tools_known_bad[tool_id] = rate

            # Inherit strategies and preferences
            my_coherence.learned_strategies.update(parent_coherence.learned_strategies)
            my_coherence.learned_preferences.update(parent_coherence.learned_preferences)

            # Inherit cost deltas
            my_coherence.cost_deltas.extend(parent_coherence.cost_deltas)
            my_coherence.cost_corrections.extend(parent_coherence.cost_corrections)

            logger.info(
                f"Task {task_id} inherited from parent; "
                f"tools_good={len(my_coherence.tools_known_good)}, "
                f"tools_bad={len(my_coherence.tools_known_bad)}, "
                f"strategies={len(my_coherence.learned_strategies)}"
            )

            return True

        except Exception as e:
            logger.error(f"Error inheriting parent context: {e}")
            return False

    def get_coherence(self, task_id: str) -> Optional[ToolCoherence]:
        """Get coherence for a task.

        Args:
            task_id: Task identifier

        Returns:
            ToolCoherence or None if not found
        """
        return self._coherence_cache.get(task_id)

    def clear_coherence(self, task_id: str) -> None:
        """Clear coherence for a task.

        Args:
            task_id: Task identifier
        """
        self._coherence_cache.pop(task_id, None)

    def validate_coherence_chain(self, coherence: ToolCoherence) -> bool:
        """Validate that coherence chain has no cycles (DAG property).

        Args:
            coherence: Coherence to validate

        Returns:
            True if chain is valid (acyclic), False if cycle detected
        """
        seen = set()
        for coherence_id in coherence.coherence_chain:
            if coherence_id in seen:
                logger.error(f"Cycle detected in coherence chain: {coherence_id}")
                return False
            seen.add(coherence_id)
        return True
