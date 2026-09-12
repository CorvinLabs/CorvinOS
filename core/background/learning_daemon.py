"""
ADR-0663: Background Learning Daemon — Event-Driven Architecture.

Components:
1. DataSourceChangeDetector — watches DataHub changes
2. SkillExecutionListener — tracks real skill executions
3. FeedbackCollector — aggregates user feedback signals
4. CausalGraph — DAG: DataSource → Skill → Outcome
5. WeightLearner — gradient descent with convergence detection
6. RegenerationScheduler — batching + threshold-based queuing
7. DaemonIntegration — event loop, subprocess lifecycle

All events audit-logged + hash-chained (ADR-0665).
"""

import asyncio
from typing import Dict, List, Any, Optional, Set
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
import hashlib
import json
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)


# ============================================================================
# EVENTS
# ============================================================================

@dataclass(frozen=True)
class DaemonEvent:
    """Base event for daemon consumption."""
    event_type: str  # "data_changed", "skill_executed", "user_feedback"
    timestamp: str
    payload: Dict[str, Any]
    tenant_id: str = "_default"  # Multi-tenant (ADR-0007)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GenerationEvent:
    """Skill generation event (immutable, audit-logged)."""
    skill_id: str
    generation_id: str
    timestamp: str
    data_sources_used: List[str]
    phase_events: List[Dict[str, Any]]  # phase 0–10 events
    final_loss_vector: Dict[str, float]  # relevance, completeness, performance, maintainability
    output_zip_hash: str
    tenant_id: str = "_default"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class UsageEvent:
    """Skill execution event (immutable)."""
    skill_id: str
    execution_id: str
    timestamp: str
    success: bool
    error_if_any: Optional[str] = None
    data_source_used: Optional[str] = None  # which source provided best data?
    execution_time_ms: float = 0.0
    tenant_id: str = "_default"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FeedbackEvent:
    """User feedback event (immutable, privacy-respecting)."""
    feedback_id: str
    skill_id: str
    timestamp: str
    signal: float  # −1.0 (bad) to +1.0 (good)
    dimension: str  # "overall" | "quality" | "performance" | "relevance"
    user_id_masked: str  # sha256(email)[:16]
    tenant_id: str = "_default"
    # justification intentionally omitted for privacy (never store user text)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LearningEvent:
    """Daemon decision event (immutable, audit-logged)."""
    learning_id: str
    event_type: str  # "weight_updated", "regeneration_queued", "convergence_detected"
    timestamp: str
    input_events: List[str]  # which event IDs triggered this?
    decision: Dict[str, Any]  # weight delta, reason, confidence
    before_state: Dict[str, float]  # weights before
    after_state: Dict[str, float]  # weights after
    confidence: float  # how sure about this decision? [0, 1]
    tenant_id: str = "_default"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ============================================================================
# COMPONENT 1: DataSourceChangeDetector
# ============================================================================

class DataSourceChangeDetector:
    """Watch DataHub changes and emit SourceChangeEvent."""

    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        self.last_manifest_hash: Dict[str, str] = {}  # source_id → hash
        self.change_history: List[Dict[str, Any]] = []

    async def check_for_changes(self, manifest_dict: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Compare manifest against last known state.
        Returns change event if detected, else None.
        """
        source_id = manifest_dict.get("manifest_id", "unknown")
        current_hash = self._compute_hash(manifest_dict)

        if source_id not in self.last_manifest_hash:
            # First time seeing this source
            self.last_manifest_hash[source_id] = current_hash
            return None

        if current_hash != self.last_manifest_hash[source_id]:
            # Change detected
            old_hash = self.last_manifest_hash[source_id]
            self.last_manifest_hash[source_id] = current_hash

            change_event = {
                "timestamp": datetime.utcnow().isoformat(),
                "source_id": source_id,
                "old_hash": old_hash,
                "new_hash": current_hash,
                "significance": self._assess_significance(manifest_dict),
            }
            self.change_history.append(change_event)
            return change_event

        return None

    def _compute_hash(self, manifest_dict: Dict[str, Any]) -> str:
        """SHA256 of manifest (deterministic)."""
        json_str = json.dumps(manifest_dict, sort_keys=True)
        return hashlib.sha256(json_str.encode()).hexdigest()

    def _assess_significance(self, manifest_dict: Dict[str, Any]) -> float:
        """Assess how significant is this change? [0, 1]."""
        doc_count = len(manifest_dict.get("documents", []))
        metadata = manifest_dict.get("metadata", {})
        quality = metadata.get("quality_score", 0.5)

        # Higher quality + more docs = more significant
        significance = (quality * 0.6) + (min(doc_count / 100, 1.0) * 0.4)
        return min(1.0, max(0.0, significance))


# ============================================================================
# COMPONENT 2: SkillExecutionListener
# ============================================================================

class SkillExecutionListener:
    """Track real skill executions and feed into CausalGraph."""

    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        self.execution_history: List[UsageEvent] = []
        self.skill_success_rate: Dict[str, List[bool]] = defaultdict(list)

    async def record_execution(self, event: UsageEvent) -> None:
        """Record a skill execution."""
        if event.tenant_id != self.tenant_id:
            logger.warning(f"Cross-tenant event ignored: {event.tenant_id} != {self.tenant_id}")
            return

        self.execution_history.append(event)
        self.skill_success_rate[event.skill_id].append(event.success)

    def get_success_rate(self, skill_id: str) -> float:
        """Compute success rate for a skill (if enough data)."""
        successes = self.skill_success_rate.get(skill_id, [])
        if not successes:
            return 0.5  # No data, neutral
        return sum(successes) / len(successes)

    def get_recent_executions(self, skill_id: str, window_hours: int = 24) -> List[UsageEvent]:
        """Get executions in recent time window."""
        cutoff = datetime.utcnow() - timedelta(hours=window_hours)
        return [
            e for e in self.execution_history
            if e.skill_id == skill_id and datetime.fromisoformat(e.timestamp) > cutoff
        ]


# ============================================================================
# COMPONENT 3: FeedbackCollector
# ============================================================================

class FeedbackCollector:
    """Aggregate user feedback signals and compute consensus scores."""

    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        self.feedback_history: List[FeedbackEvent] = []
        self.skill_consensus: Dict[str, Dict[str, Any]] = {}

    async def record_feedback(self, event: FeedbackEvent) -> None:
        """Record user feedback."""
        if event.tenant_id != self.tenant_id:
            logger.warning(f"Cross-tenant event ignored: {event.tenant_id} != {self.tenant_id}")
            return

        self.feedback_history.append(event)
        self._update_consensus(event.skill_id)

    def _update_consensus(self, skill_id: str) -> None:
        """Recompute consensus score for a skill."""
        feedback_list = [
            e for e in self.feedback_history
            if e.skill_id == skill_id
        ]

        if not feedback_list:
            self.skill_consensus[skill_id] = {"signal": 0.0, "confidence": 0.0, "count": 0}
            return

        # Consensus = mean signal, weighted by recency
        now = datetime.utcnow()
        weighted_signals = []
        for fb in feedback_list:
            age_hours = (now - datetime.fromisoformat(fb.timestamp)).total_seconds() / 3600
            recency_weight = 1.0 / (1.0 + age_hours / 24.0)  # exponential decay
            weighted_signals.append(fb.signal * recency_weight)

        consensus_signal = sum(weighted_signals) / len(weighted_signals)
        confidence = min(1.0, len(feedback_list) / 5.0)  # confidence grows with more feedback

        self.skill_consensus[skill_id] = {
            "signal": consensus_signal,
            "confidence": confidence,
            "count": len(feedback_list),
        }

    def get_consensus(self, skill_id: str) -> Dict[str, Any]:
        """Get current consensus for a skill."""
        return self.skill_consensus.get(skill_id, {"signal": 0.0, "confidence": 0.0, "count": 0})


# ============================================================================
# COMPONENT 4: CausalGraph (DAG validation)
# ============================================================================

class CausalGraph:
    """DAG: DataSource → Skill → Outcome. Validate no cycles."""

    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        self.edges: Dict[str, Set[str]] = defaultdict(set)  # source → skills
        self.skill_edges: Dict[str, Set[str]] = defaultdict(set)  # skill → outcomes
        self.weights: Dict[str, float] = {}  # source-specific weights

    async def add_edge(self, from_node: str, to_node: str, node_type: str) -> bool:
        """
        Add edge to DAG. Return False if it would create a cycle.
        node_type: "data_source" | "skill" | "outcome"
        """
        if node_type == "data_source":
            if to_node in self.edges[from_node]:
                return True  # Edge already exists

            self.edges[from_node].add(to_node)

            # Check for cycles (simple DFS)
            if self._has_cycle_dfs(from_node):
                self.edges[from_node].remove(to_node)
                logger.warning(f"Cycle detected: {from_node} → {to_node}, edge rejected")
                return False
            return True

        return True

    def _has_cycle_dfs(self, start: str, visited: Optional[Set[str]] = None, rec_stack: Optional[Set[str]] = None) -> bool:
        """DFS to detect cycles (simple implementation for now)."""
        if visited is None:
            visited = set()
            rec_stack = set()

        visited.add(start)
        rec_stack.add(start)

        for neighbor in self.edges.get(start, []):
            if neighbor not in visited:
                if self._has_cycle_dfs(neighbor, visited, rec_stack):
                    return True
            elif neighbor in rec_stack:
                return True

        rec_stack.remove(start)
        return False

    def get_edges(self) -> Dict[str, Set[str]]:
        """Export DAG edges."""
        return {k: set(v) for k, v in self.edges.items()}


# ============================================================================
# COMPONENT 5: WeightLearner (gradient descent + convergence)
# ============================================================================

class WeightLearner:
    """Gradient descent on data-source weights with convergence detection."""

    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        self.weights = {
            "memory:tier2": 0.50,
            "rag:embeddings": 0.30,
            "files": 0.20,
        }
        self.learning_rate = 0.01
        self.weight_history: List[Dict[str, float]] = [self.weights.copy()]
        self.convergence_threshold = 0.01
        self.convergence_window = 50  # samples over which to check variance

    async def update_weights(self, attribution: Dict[str, float]) -> Dict[str, Any]:
        """
        Gradient step: weights[source] += learning_rate * attribution[source].
        Returns decision event for audit trail.
        """
        before = self.weights.copy()

        for source, delta in attribution.items():
            if source in self.weights:
                self.weights[source] += self.learning_rate * delta
                # Clamp to [0, 1]
                self.weights[source] = max(0.0, min(1.0, self.weights[source]))

        self.weight_history.append(self.weights.copy())

        return {
            "before": before,
            "after": self.weights.copy(),
            "attribution": attribution,
        }

    def check_convergence(self) -> bool:
        """
        Are weights stabilizing?
        Check variance over recent window.
        """
        if len(self.weight_history) < self.convergence_window:
            return False

        recent = self.weight_history[-self.convergence_window:]

        # Compute variance per weight across window
        variances = {}
        for source in self.weights:
            values = [w.get(source, 0.5) for w in recent]
            mean = sum(values) / len(values)
            variance = sum((v - mean) ** 2 for v in values) / len(values)
            variances[source] = variance

        # Convergence if all variances < threshold
        max_variance = max(variances.values())
        return max_variance < self.convergence_threshold


# ============================================================================
# COMPONENT 6: RegenerationScheduler
# ============================================================================

class RegenerationScheduler:
    """Batch skill regenerations, threshold-based queuing."""

    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        self.regeneration_queue: List[str] = []
        self.queued_skills: Set[str] = set()  # dedup
        self.last_batch_time = datetime.utcnow()
        self.batch_window_seconds = 60.0  # wait 60s before executing batch
        self.regeneration_threshold = 0.80  # regenerate if quality drops below this

    async def should_queue_regeneration(self, skill_id: str, current_quality: float) -> bool:
        """Check if regeneration should be queued."""
        if skill_id in self.queued_skills:
            return False  # Already queued, idempotent

        if current_quality < self.regeneration_threshold:
            self.regeneration_queue.append(skill_id)
            self.queued_skills.add(skill_id)
            logger.info(f"Queued regeneration for {skill_id} (quality={current_quality:.2f})")
            return True

        return False

    async def get_ready_batch(self) -> List[str]:
        """Check if batch is ready to execute (quiet window elapsed)."""
        now = datetime.utcnow()
        elapsed = (now - self.last_batch_time).total_seconds()

        if elapsed >= self.batch_window_seconds and self.regeneration_queue:
            batch = self.regeneration_queue.copy()
            self.regeneration_queue = []
            self.queued_skills = set()
            self.last_batch_time = now
            return batch

        return []


# ============================================================================
# COMPONENT 7: DaemonIntegration (main event loop)
# ============================================================================

class DataHubLearningDaemon:
    """
    Main daemon: event loop + component orchestration.

    Runs in subprocess, non-blocking. Emits LearningEvent for every decision.
    All events hash-chained + tenant-scoped (ADR-0665, ADR-0007).
    """

    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        self.event_queue: asyncio.Queue = asyncio.Queue()
        self.audit_trail: List[Dict[str, Any]] = []
        self.audit_chain_hash = "genesis"  # Start of hash chain

        # Components
        self.change_detector = DataSourceChangeDetector(tenant_id)
        self.execution_listener = SkillExecutionListener(tenant_id)
        self.feedback_collector = FeedbackCollector(tenant_id)
        self.causal_graph = CausalGraph(tenant_id)
        self.weight_learner = WeightLearner(tenant_id)
        self.regeneration_scheduler = RegenerationScheduler(tenant_id)

    async def on_event(self, event: DaemonEvent) -> None:
        """Dispatch event to appropriate handler."""
        try:
            if event.event_type == "data_changed":
                await self.on_data_changed(event)
            elif event.event_type == "skill_executed":
                await self.on_skill_executed(event)
            elif event.event_type == "user_feedback":
                await self.on_user_feedback(event)
            else:
                logger.warning(f"Unknown event type: {event.event_type}")
        except Exception as e:
            logger.error(f"Error handling event: {e}", exc_info=True)

    async def on_data_changed(self, event: DaemonEvent) -> None:
        """Handle DataSourceChangedEvent."""
        payload = event.payload
        manifest = payload.get("manifest", {})

        # Detect if it's a real change
        change = await self.change_detector.check_for_changes(manifest)
        if not change:
            return  # No change

        significance = change.get("significance", 0.5)
        affected_skills = payload.get("affected_skills", [])

        # Decide: should we queue regeneration?
        for skill_id in affected_skills:
            quality = payload.get(f"quality_{skill_id}", 0.8)
            should_queue = await self.regeneration_scheduler.should_queue_regeneration(skill_id, quality)

            if should_queue:
                await self._emit_learning_event(
                    "regeneration_queued",
                    input_events=[event],
                    decision={
                        "skill_id": skill_id,
                        "significance": significance,
                        "quality": quality,
                    },
                )

    async def on_skill_executed(self, event: DaemonEvent) -> None:
        """Handle SkillExecutedEvent."""
        payload = event.payload
        skill_id = payload.get("skill_id")
        success = payload.get("success", False)
        execution_id = payload.get("execution_id", f"exec-{datetime.utcnow().isoformat()}")

        # Record execution
        usage_event = UsageEvent(
            skill_id=skill_id,
            execution_id=execution_id,
            timestamp=datetime.utcnow().isoformat(),
            success=success,
            error_if_any=payload.get("error"),
            data_source_used=payload.get("data_source_used"),
            execution_time_ms=payload.get("execution_time_ms", 0.0),
            tenant_id=self.tenant_id,
        )
        await self.execution_listener.record_execution(usage_event)

    async def on_user_feedback(self, event: DaemonEvent) -> None:
        """Handle UserFeedbackEvent."""
        payload = event.payload
        skill_id = payload.get("skill_id")
        signal = payload.get("signal", 0.0)
        sources = payload.get("data_sources", [])
        user_id_masked = payload.get("user_id_masked", "unknown")
        feedback_id = payload.get("feedback_id", f"fb-{datetime.utcnow().isoformat()}")

        # Record feedback
        feedback_event = FeedbackEvent(
            feedback_id=feedback_id,
            skill_id=skill_id,
            timestamp=datetime.utcnow().isoformat(),
            signal=signal,
            dimension=payload.get("dimension", "overall"),
            user_id_masked=user_id_masked,
            tenant_id=self.tenant_id,
        )
        await self.feedback_collector.record_feedback(feedback_event)

        # Attribute feedback to sources and learn
        if sources:
            attribution = {src: signal / len(sources) for src in sources}
            update = await self.weight_learner.update_weights(attribution)

            await self._emit_learning_event(
                "weight_updated",
                input_events=[event],
                decision={
                    "skill_id": skill_id,
                    "signal": signal,
                    "attribution": attribution,
                },
                before_state=update["before"],
                after_state=update["after"],
                confidence=feedback_event.timestamp and 0.8 or 0.5,
            )

        # Check convergence
        if self.weight_learner.check_convergence():
            await self._emit_learning_event(
                "convergence_detected",
                input_events=[event],
                decision={"message": "Weights have converged"},
                before_state=self.weight_learner.weights.copy(),
                after_state=self.weight_learner.weights.copy(),
                confidence=0.95,
            )

    async def _emit_learning_event(
        self,
        event_type: str,
        input_events: List[DaemonEvent],
        decision: Dict[str, Any],
        before_state: Optional[Dict[str, float]] = None,
        after_state: Optional[Dict[str, float]] = None,
        confidence: float = 0.5,
    ) -> None:
        """
        Emit an immutable LearningEvent to audit trail.
        Hash-chained + tenant-scoped.
        """
        learning_event = LearningEvent(
            learning_id=f"learn-{datetime.utcnow().isoformat()}",
            event_type=event_type,
            timestamp=datetime.utcnow().isoformat(),
            input_events=[e.timestamp for e in input_events],
            decision=decision,
            before_state=before_state or self.weight_learner.weights.copy(),
            after_state=after_state or self.weight_learner.weights.copy(),
            confidence=confidence,
            tenant_id=self.tenant_id,
        )

        # Hash-chain it
        event_dict = learning_event.to_dict()
        event_hash = hashlib.sha256(
            f"{self.audit_chain_hash}{json.dumps(event_dict, sort_keys=True)}".encode()
        ).hexdigest()
        event_dict["hash"] = event_hash
        event_dict["prev_hash"] = self.audit_chain_hash

        self.audit_trail.append(event_dict)
        self.audit_chain_hash = event_hash
        logger.info(f"Emitted LearningEvent: {event_type} → {event_hash[:8]}...")

    async def run(self) -> None:
        """
        Main daemon loop: process events, check for batches, maintain state.
        """
        logger.info(f"DataHubLearningDaemon starting (tenant={self.tenant_id})")

        try:
            while True:
                try:
                    # Process one event with timeout
                    event = await asyncio.wait_for(self.event_queue.get(), timeout=2.0)
                    await self.on_event(event)
                except asyncio.TimeoutError:
                    # Check if regeneration batch is ready
                    batch = await self.regeneration_scheduler.get_ready_batch()
                    if batch:
                        logger.info(f"Ready to regenerate: {batch}")
                        # (In Phase 4, this would call Creator 2.0)
                except Exception as e:
                    logger.error(f"Daemon loop error: {e}", exc_info=True)
        except KeyboardInterrupt:
            logger.info("Daemon shutting down")

    async def emit_event(self, event: DaemonEvent) -> None:
        """Public API: emit event for daemon to process."""
        await self.event_queue.put(event)
