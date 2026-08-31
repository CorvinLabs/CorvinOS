"""TaskManager Subsystem — Cross-task Learning (Proposal 3 + 5, Week 3).

Learns patterns from task outcomes and recommends parameters for next tasks.
Stores task history + patterns in persistent JSONL (audit trail integration).
Integrates with Brain at spawn_task() to initialize new tasks with learned defaults.

Safety (Proposal 5): Dangerous optimizations blocked (always_expensive_model, skip_safety_checks, etc.)
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any
from datetime import datetime
import json
from pathlib import Path
from .base import Subsystem

logger = logging.getLogger(__name__)


@dataclass
class TaskPattern:
    """Learned pattern from past task."""
    task_type: str  # "refactoring", "testing", "debugging", etc.
    strategy: str  # "decompose", "direct_fix", "pivot", etc.
    model: str  # "Haiku", "Opus", etc.
    success_rate: float  # 0.0-1.0 (% of uses that succeeded)
    sample_size: int  # Number of past uses
    confidence: float  # LOW / MEDIUM / HIGH
    estimated_cost: Optional[float] = None
    estimated_time_minutes: Optional[float] = None
    user_preference: bool = False  # Explicitly requested by user

    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self):
        return {
            "task_type": self.task_type,
            "strategy": self.strategy,
            "model": self.model,
            "success_rate": self.success_rate,
            "sample_size": self.sample_size,
            "confidence": self.confidence,
            "estimated_cost": self.estimated_cost,
            "estimated_time_minutes": self.estimated_time_minutes,
            "user_preference": self.user_preference,
            "metadata": self.metadata,
        }


class TaskPatternStore:
    """Persistent JSONL storage for task patterns (audit trail)."""

    def __init__(self, tenant_id: str, corvin_home: str = None):
        if not corvin_home:
            corvin_home = str(Path.home() / ".corvin")
        self.db_path = Path(corvin_home) / "tenants" / tenant_id / "global" / "task_patterns.jsonl"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.last_hash = None
        self._lock = asyncio.Lock()

    async def record_pattern(self, pattern: TaskPattern):
        """Append pattern to JSONL (hash-chained for audit trail)."""
        async with self._lock:
            record = {
                "timestamp": datetime.utcnow().isoformat(),
                "pattern": pattern.to_dict(),
                "prev_hash": self.last_hash or "genesis",
                "hash": self._compute_hash(pattern),
            }

            async with asyncio.to_thread(self.db_path.open, "a") as f:
                f.write(json.dumps(record) + "\n")

            self.last_hash = record["hash"]
            logger.info(f"Recorded pattern: {pattern.task_type} / {pattern.strategy}")

    async def get_patterns_for_type(self, task_type: str, min_confidence: float = 0.7) -> List[TaskPattern]:
        """Retrieve patterns for task type (confidence threshold)."""
        async with self._lock:
            patterns = []
            if not self.db_path.exists():
                return patterns

            async with asyncio.to_thread(self.db_path.open, "r") as f:
                for line in f:
                    try:
                        record = json.loads(line)
                        pattern_data = record["pattern"]

                        if pattern_data["task_type"] == task_type:
                            if pattern_data["confidence"] >= min_confidence:
                                pattern = TaskPattern(**pattern_data)
                                patterns.append(pattern)
                    except Exception as e:
                        logger.warning(f"Skipping malformed pattern record: {e}")

            return patterns

    def _compute_hash(self, pattern: TaskPattern) -> str:
        """Simple hash for audit trail (could be replaced with real hash)."""
        import hashlib
        data = f"{pattern.task_type}:{pattern.strategy}:{pattern.model}:{pattern.sample_size}"
        return hashlib.md5(data.encode()).hexdigest()[:8]


class LearningValidator:
    """Prevent dangerous learning optimizations (Proposal 5 safety rails)."""

    FORBIDDEN_OPTIMIZATIONS = {
        "always_use_expensive_model": False,
        "skip_safety_checks": False,
        "disable_audit_logging": False,
        "ignore_budget_limit": False,
    }

    @staticmethod
    async def validate_recommendation(recommendation: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Block dangerous patterns before applying."""
        for forbidden_key in LearningValidator.FORBIDDEN_OPTIMIZATIONS:
            if recommendation.get(forbidden_key):
                logger.warning(f"Blocked dangerous optimization: {forbidden_key}")
                return None

        return recommendation


class LDDOptimizer:
    """Gradient-based learning from task loss signals (Proposal 3)."""

    LOSS_DIMENSIONS = {
        "errors": {
            "weight": 1.0,
            "target": 0,
            "penalty": "exponential",  # Errors are heavily penalized
        },
        "cost": {
            "weight": 0.3,
            "target": 0.10,  # $0.10 per file
            "penalty": "linear",
        },
        "latency": {
            "weight": 0.1,
            "target": "user_preference",  # No hard target
            "penalty": "none",
        },
    }

    async def process_loss_signal(self, loss_event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """React to loss signal; generate optimization recommendations."""
        task_id = loss_event.get("task_id")
        task_type = loss_event.get("task_type", "unknown")
        loss = loss_event.get("loss", {})

        # Compute gradient for each dimension
        recommendations = []

        for dimension, config in self.LOSS_DIMENSIONS.items():
            actual = loss.get(dimension, 0)
            target = config["target"]
            weight = config["weight"]

            if target == "user_preference":
                continue  # Skip dimensions without hard targets

            if actual > target:
                gradient = actual - target

                recommendation = {
                    "dimension": dimension,
                    "gradient": gradient,
                    "action": None,
                }

                if dimension == "errors" and gradient > 0:
                    # High error rate; switch strategy
                    recommendation["action"] = "try_different_strategy"
                    recommendation["suggested_strategy"] = "decompose"  # Safe fallback

                elif dimension == "cost" and gradient > 0:
                    # High cost; downgrade model
                    recommendation["action"] = "use_cheaper_model"
                    recommendation["suggested_model"] = "Haiku"

                if recommendation["action"]:
                    recommendations.append(recommendation)
                    logger.info(f"LDD recommendation: {dimension} → {recommendation['action']}")

        return {"recommendations": recommendations} if recommendations else None


class TaskManager(Subsystem):
    """Orchestrate task-level learning (Proposal 3)."""

    @property
    def name(self) -> str:
        return "task_manager"

    @property
    def version(self) -> str:
        return "1.0.0"

    def __init__(self, tenant_id: str = "_default", corvin_home: str = None):
        self.tenant_id = tenant_id
        self.pattern_store = TaskPatternStore(tenant_id, corvin_home)
        self.ldd_optimizer = LDDOptimizer()
        self.learning_validator = LearningValidator()
        self.active_tasks: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self.hub = None  # Set by startup()

    def startup(self, hub: "SubsystemHub") -> None:  # noqa: F821
        """Initialize TaskManager and subscribe to task events."""
        self.hub = hub
        self.hub.subscribe("task_started", self.on_event)
        self.hub.subscribe("task_completed", self.on_event)
        self.hub.subscribe("loss_signal", self.on_event)
        logger.info(f"{self.name} v{self.version} started")

    def shutdown(self) -> None:
        """Cleanup resources."""
        self.active_tasks.clear()
        logger.info(f"{self.name} shut down")

    async def on_event(self, event_name: str, event_data: Dict[str, Any]):
        """Handle task events."""
        if event_name == "task_started":
            await self._record_task_start(event_data)

        elif event_name == "task_completed":
            await self._handle_task_completion(event_data)

        elif event_name == "loss_signal":
            await self._process_loss_signal(event_data)

    async def handle_request(self, request_type: str, **kwargs) -> Optional[Dict]:
        """Handle requests from other subsystems."""
        if request_type == "recommend_task_parameters":
            task_type = kwargs.get("task_type", "")
            return await self.recommend_task_parameters(task_type)

        return None

    async def recommend_task_parameters(self, task_type: str) -> Optional[Dict]:
        """Get learned recommendations for next task of same type."""
        patterns = await self.pattern_store.get_patterns_for_type(task_type)

        if not patterns:
            return None

        # Use best pattern (highest success rate)
        best_pattern = max(patterns, key=lambda p: p.success_rate)

        return {
            "recommended_strategy": best_pattern.strategy,
            "recommended_model": best_pattern.model,
            "estimated_cost": best_pattern.estimated_cost,
            "confidence": "high" if best_pattern.confidence >= 0.85 else "medium",
            "sample_size": best_pattern.sample_size,
            "from_user_preference": best_pattern.user_preference,
        }

    async def _record_task_start(self, event_data: Dict[str, Any]):
        """Log task start."""
        task_id = event_data.get("task_id", "")
        async with self._lock:
            self.active_tasks[task_id] = event_data

    async def _handle_task_completion(self, event_data: Dict[str, Any]):
        """Extract pattern from completed task."""
        task_id = event_data.get("task_id", "")
        task_type = event_data.get("task_type", "unknown")
        strategy = event_data.get("strategy_used", "unknown")
        model = event_data.get("model_used", "unknown")
        success_rate = 1.0 - (event_data.get("error_count", 0) / max(event_data.get("item_count", 1), 1))
        cost = event_data.get("cost_spent", 0)
        items_completed = event_data.get("items_completed", 0)

        # Create pattern
        pattern = TaskPattern(
            task_type=task_type,
            strategy=strategy,
            model=model,
            success_rate=success_rate,
            sample_size=1,  # Will be incremented on reuse
            confidence=0.7,  # Initial; increases with repetition
            estimated_cost=cost / max(items_completed, 1),
            metadata={"task_id": task_id},
        )

        # Store pattern
        await self.pattern_store.record_pattern(pattern)

        async with self._lock:
            if task_id in self.active_tasks:
                del self.active_tasks[task_id]

    async def _process_loss_signal(self, event_data: Dict[str, Any]):
        """React to loss signal from LearningEngine."""
        recommendation = await self.ldd_optimizer.process_loss_signal(event_data)

        if recommendation:
            # Validate recommendation (Proposal 5 safety rails)
            validated = await self.learning_validator.validate_recommendation(
                recommendation.get("recommendations", [{}])[0]
            )

            if validated:
                logger.info(f"Validated LDD recommendation: {validated}")
            else:
                logger.warning(f"Rejected LDD recommendation (safety gate)")
