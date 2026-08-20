"""Learning Engine subsystem: Learn from errors and strategies.

Phase C: Tenant-native persistence via ExecutionContext.tenant_id
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import Subsystem
from core.paths.tenant import tenant_learning_dir

logger = logging.getLogger(__name__)


class LearningEngine(Subsystem):
    """Learn from error/strategy patterns.

    Phase C: All learning data stored in tenant-scoped directory.
    """

    def __init__(
        self,
        context: Optional[Any] = None,
        db_path: Optional[str] = None,
        min_confidence: float = 0.5,
        learning_window_turns: int = 100,
    ):
        """Initialize LearningEngine.

        Args:
            context: ExecutionContext (Phase C) with tenant_id for tenant-scoped operations
            db_path: Tenant-specific database path (auto-computed from context if not provided)
            min_confidence: Minimum confidence threshold
            learning_window_turns: Learning window size
        """
        # Phase C: Store ExecutionContext for tenant-native operations
        self.context = context
        self.tenant_id = context.tenant_id if context else "_default"

        # Use tenant-scoped database path
        if db_path:
            self.db_path = Path(db_path).expanduser()
        else:
            self.db_path = tenant_learning_dir(self.tenant_id) / "engine.db"

        self.min_confidence = min_confidence
        self.learning_window_turns = learning_window_turns
        self.strategies_by_error: Dict[str, List[Dict[str, Any]]] = {}
        self.success_rate: Dict[str, float] = {}
        self.error_skill_map: Dict[str, List[str]] = {}  # ADR-0372: error_type → [skill_names]
        self._load_db()

    @property
    def name(self) -> str:
        return "learning_engine"

    @property
    def version(self) -> str:
        return "1.0.0"

    def startup(self, hub) -> None:
        """Subscribe to learning events."""
        self.hub = hub
        hub.subscribe("error_detected", self.on_error)
        hub.subscribe("strategy_applied", self.on_strategy)
        hub.subscribe("strategy_succeeded", self.on_success)
        hub.subscribe("strategy_failed", self.on_failure)
        logger.info("LearningEngine started")

    def _load_db(self) -> None:
        """Load learning database."""
        if self.db_path.exists():
            try:
                with open(self.db_path) as f:
                    data = json.load(f)
                    self.strategies_by_error = data.get("strategies_by_error", {})
                    self.success_rate = data.get("success_rate", {})
                    self.error_skill_map = data.get("error_skill_map", {})  # ADR-0372
            except Exception as e:
                logger.error(f"Failed to load DB: {e}")

    def _save_db(self) -> None:
        """Save learning database."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.db_path, "w") as f:
                json.dump(
                    {
                        "strategies_by_error": self.strategies_by_error,
                        "success_rate": self.success_rate,
                        "error_skill_map": self.error_skill_map,  # ADR-0372
                    },
                    f,
                )
        except Exception as e:
            logger.error(f"Failed to save DB: {e}")

    async def on_event(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """React to events."""
        if event_name == "error_detected":
            await self.on_error(event_name, event_data)
        elif event_name == "strategy_applied":
            await self.on_strategy(event_name, event_data)
        elif event_name == "strategy_succeeded":
            await self.on_success(event_name, event_data)
        elif event_name == "strategy_failed":
            await self.on_failure(event_name, event_data)

    async def on_error(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """Track error."""
        error_type = str(event_data.get("error", "unknown"))
        if error_type not in self.strategies_by_error:
            self.strategies_by_error[error_type] = []

    async def on_strategy(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """Track applied strategy."""
        error_type = str(event_data.get("error", "unknown"))
        strategy = event_data.get("strategy", "unknown")

        if error_type not in self.strategies_by_error:
            self.strategies_by_error[error_type] = []

        self.strategies_by_error[error_type].append(
            {
                "strategy": strategy,
                "attempt": event_data.get("attempt", 1),
                "timestamp": event_data.get("timestamp", ""),
            }
        )

    async def on_success(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """Reinforce successful strategy."""
        strategy = event_data.get("strategy", "unknown")
        key = f"success:{strategy}"

        if key not in self.success_rate:
            self.success_rate[key] = 0.0

        self.success_rate[key] += 1.0
        self._save_db()

    async def on_failure(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """Penalize failed strategy."""
        strategy = event_data.get("strategy", "unknown")
        key = f"fail:{strategy}"

        if key not in self.success_rate:
            self.success_rate[key] = 0.0

        self.success_rate[key] -= 0.5

    async def handle_request(self, request_type: str, **kwargs) -> Any:
        """Handle learning queries."""
        if request_type == "recommend_strategy":
            error_type = str(kwargs.get("error", "unknown"))
            if error_type in self.strategies_by_error:
                strategies = self.strategies_by_error[error_type]
                scored = [
                    {
                        "strategy": s["strategy"],
                        "confidence": self._get_confidence(s["strategy"]),
                    }
                    for s in strategies
                ]
                return sorted(scored, key=lambda x: x["confidence"], reverse=True)
            return []

        elif request_type == "confidence_score":
            strategy = kwargs.get("strategy", "unknown")
            key = f"success:{strategy}"
            return self.success_rate.get(key, 0.0)

        elif request_type == "get_strategies_for_error":
            error_type = str(kwargs.get("error", "unknown"))
            return self.strategies_by_error.get(error_type, [])

        raise ValueError(f"Unknown request type: {request_type}")

    def _get_confidence(self, strategy: str) -> float:
        """Get confidence score for strategy."""
        success_key = f"success:{strategy}"
        fail_key = f"fail:{strategy}"

        success_count = self.success_rate.get(success_key, 0.0)
        fail_count = abs(self.success_rate.get(fail_key, 0.0))
        total = success_count + fail_count

        if total == 0:
            return 0.5

        return success_count / total

    def link_error_to_skill(self, error_type: str, skill_name: str) -> None:
        """Map error type to applicable skill (ADR-0372: Closed-loop learning).

        Args:
            error_type: Error class name (e.g., 'TypeError', 'TokenLimitExceeded')
            skill_name: Name of skill that applies to this error
        """
        if error_type not in self.error_skill_map:
            self.error_skill_map[error_type] = []
        if skill_name not in self.error_skill_map[error_type]:
            self.error_skill_map[error_type].append(skill_name)
            logger.debug(f"Linked error '{error_type}' to skill '{skill_name}'")
            self._save_db()

    def get_skills_for_error(self, error_type: str) -> List[str]:
        """Get skills applicable to error type (ADR-0372).

        Args:
            error_type: Error class name

        Returns:
            List of skill names applicable to this error
        """
        return self.error_skill_map.get(error_type, [])

    def shutdown(self) -> None:
        """Save state."""
        self._save_db()
        logger.info("LearningEngine shutdown")
