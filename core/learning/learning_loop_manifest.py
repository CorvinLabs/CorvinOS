"""Learning Loop Manifest Builder — ADR-0906

Parses learning_loops from plugin.json + skill.json manifests.
Exposes as structured LearningLoop objects for Console + KG MCP discovery.
"""

from dataclasses import dataclass
from typing import List, Dict, Any, Optional
import json
from enum import Enum


class LoopStatus(str, Enum):
    """Health status of a learning loop."""
    ACTIVE = "active"
    DORMANT = "dormant"
    STALE = "stale"
    DEGRADING = "degrading"


class LoopAggregation(str, Enum):
    """Aggregation strategy for loop health computation."""
    ROLLING_MEAN_7D = "rolling_mean_7d"
    PERCENTILE_P95 = "percentile_p95"
    PERCENTILE_P50 = "percentile_p50"
    COUNT = "count"
    SUM = "sum"
    LATEST = "latest"


class FeedbackType(str, Enum):
    """Valid feedback types per ADR-0314."""
    OUTCOME = "outcome_feedback"
    PREFERENCE = "preference_feedback"
    CONFIDENCE = "confidence_score"
    METRIC = "metric_observed"


@dataclass(frozen=True)
class LearningLoop:
    """Immutable learning loop descriptor (schema from ADR-0906)."""

    # Declaration (from manifest)
    loop_id: str                           # scoped: "{plugin_id}:{loop.id}"
    plugin_id: str                         # owner plugin
    description: str
    event_source: str                      # "SkillExecutedEvent.confidence_score"
    feedback_types: tuple[FeedbackType, ...]
    aggregation: LoopAggregation
    health_threshold: Optional[float]      # null or [0.0, 1.0]
    dormancy_alert_hours: Optional[int]    # null or positive int
    owner_skill: Optional[str] = None      # "os.delegation_router"
    metadata: Optional[Dict[str, Any]] = None

    # Runtime (computed at query time, not in manifest)
    last_event_ts: Optional[str] = None    # ISO 8601
    event_count_7d: int = 0
    health_score: Optional[float] = None   # 0.0–1.0 or null
    status: LoopStatus = LoopStatus.ACTIVE

    @classmethod
    def from_manifest(cls, plugin_id: str, loop_dict: Dict[str, Any]) -> "LearningLoop":
        """Parse a learning loop from plugin.json/skill.json declaration."""

        # Validate required fields
        required = {"id", "description", "event_source", "feedback_types", "aggregation"}
        missing = required - set(loop_dict.keys())
        if missing:
            raise ValueError(f"Missing required fields in learning_loop: {missing}")

        # Parse feedback types
        feedback_types = tuple(
            FeedbackType(ft) for ft in loop_dict["feedback_types"]
        )

        # Parse aggregation
        aggregation = LoopAggregation(loop_dict["aggregation"])

        # Loop ID is scoped under plugin
        loop_id = f"{plugin_id}:{loop_dict['id']}"

        return cls(
            loop_id=loop_id,
            plugin_id=plugin_id,
            description=loop_dict["description"],
            event_source=loop_dict["event_source"],
            feedback_types=feedback_types,
            aggregation=aggregation,
            health_threshold=loop_dict.get("health_threshold"),
            dormancy_alert_hours=loop_dict.get("dormancy_alert_hours"),
            owner_skill=loop_dict.get("owner_skill"),
            metadata=loop_dict.get("metadata", {}),
        )

    def to_manifest_dict(self) -> Dict[str, Any]:
        """Convert to Console manifest response format (ADR-0906 § 3)."""
        return {
            "loop_id": self.loop_id,
            "plugin_id": self.plugin_id,
            "owner_skill": self.owner_skill,
            "description": self.description,
            "event_source": self.event_source,
            "feedback_types": [ft.value for ft in self.feedback_types],
            "aggregation": self.aggregation.value,
            "health_threshold": self.health_threshold,
            "dormancy_alert_hours": self.dormancy_alert_hours,
            "last_event_ts": self.last_event_ts,
            "event_count_7d": self.event_count_7d,
            "health_score": self.health_score,
            "status": self.status.value,
        }


class ManifestParser:
    """Parse learning_loops from plugin.json/skill.json and build LearningLoop objects."""

    @staticmethod
    def parse_plugin_manifest(plugin_json: Dict[str, Any]) -> List[LearningLoop]:
        """Extract learning_loops array from a plugin.json manifest."""
        plugin_id = plugin_json.get("plugin_id")
        if not plugin_id:
            raise ValueError("plugin.json must include plugin_id")

        loops = []
        for loop_dict in plugin_json.get("learning_loops", []):
            try:
                loop = LearningLoop.from_manifest(plugin_id, loop_dict)
                loops.append(loop)
            except ValueError as e:
                # Log warning but continue (graceful degradation per ADR-0906)
                print(f"Warning: failed to parse learning_loop in {plugin_id}: {e}")
                continue

        return loops

    @staticmethod
    def validate_loop_schema(loop_dict: Dict[str, Any]) -> None:
        """Validate learning_loop schema against ADR-0906 constraints."""
        # ID must match regex
        loop_id = loop_dict.get("id", "")
        if not re.match(r"^[a-z0-9_-]+$", loop_id):
            raise ValueError(f"Invalid loop id: {loop_id} (must be lowercase alphanumeric + underscore/hyphen)")

        # Description constraints
        description = loop_dict.get("description", "")
        if not description or len(description) > 200:
            raise ValueError(f"Description must be non-empty and ≤200 chars")

        # Event source format
        event_source = loop_dict.get("event_source", "")
        if "." not in event_source:
            raise ValueError(f"event_source must be <EventType>.<field>, got: {event_source}")

        # Feedback types must be non-empty and valid
        feedback_types = loop_dict.get("feedback_types", [])
        if not feedback_types:
            raise ValueError("feedback_types must be non-empty")
        valid_types = {ft.value for ft in FeedbackType}
        invalid = set(feedback_types) - valid_types
        if invalid:
            raise ValueError(f"Invalid feedback_types: {invalid}")

        # Aggregation must be valid
        aggregation = loop_dict.get("aggregation", "")
        valid_agg = {agg.value for agg in LoopAggregation}
        if aggregation not in valid_agg:
            raise ValueError(f"Invalid aggregation: {aggregation}")

        # Health threshold must be null or [0.0, 1.0]
        health_threshold = loop_dict.get("health_threshold")
        if health_threshold is not None and not (0.0 <= health_threshold <= 1.0):
            raise ValueError(f"health_threshold must be null or [0.0, 1.0], got: {health_threshold}")

        # Dormancy alert must be null or positive int
        dormancy_hours = loop_dict.get("dormancy_alert_hours")
        if dormancy_hours is not None and dormancy_hours <= 0:
            raise ValueError(f"dormancy_alert_hours must be null or > 0, got: {dormancy_hours}")


import re
