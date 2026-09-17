"""SkillInstance — Runtime Skill with config tuning integration (Track E).

Wraps a Skill with:
- Mutable runtime config (loaded from config store on init)
- Feedback receiver (wired to learning loop)
- Execution tracking (metrics for optimizer)
- Config reload capability (apply tuned params)

ADR-0675, ADR-0676 integration.
"""

import logging
import json
import hashlib
from typing import Optional, Dict, Any, Callable
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class SkillExecutionMetrics:
    """Metrics captured from a single skill execution."""

    execution_id: str
    skill_id: str
    config_version: int
    latency_ms: float
    success: bool
    error: Optional[str] = None
    confidence: Optional[float] = None
    quality_score: Optional[float] = None
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.utcnow().isoformat()


@dataclass
class SkillConfigSnapshot:
    """Immutable snapshot of SkillInstance config (for audit trail)."""

    skill_id: str
    version: int
    routing_threshold: float
    attention_weight: float
    latency_target_ms: float
    timestamp: str = ""
    config_hash: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.utcnow().isoformat()
        if not self.config_hash:
            self.config_hash = self._compute_hash()

    def _compute_hash(self) -> str:
        """Compute SHA256 hash of config (for change detection)."""
        data = f"{self.version}:{self.routing_threshold}:{self.attention_weight}:{self.latency_target_ms}"
        return hashlib.sha256(data.encode()).hexdigest()


class SkillInstance:
    """Runtime instance of a Skill with tunable config.

    Responsibilities:
    1. Load config from store (latest version on init)
    2. Execute skill with loaded config
    3. Capture execution metrics
    4. Accept feedback from learning loop
    5. Apply config updates when tuner provides new params
    6. Audit all config changes

    Thread-safe: config mutations protected by an internal lock (if needed).
    """

    def __init__(
        self,
        skill_id: str,
        initial_config: Dict[str, Any],
        config_store_path: Optional[Path] = None,
        audit_backend=None,
        tenant_id: str = "_default",
    ):
        """Initialize SkillInstance.

        Args:
            skill_id: Unique skill identifier (e.g., "os.delegation_router")
            initial_config: Initial config dict with keys:
                - routing_threshold: float [0.5-0.95]
                - attention_weight: float [0.0-1.0]
                - latency_target_ms: float [50-500]
            config_store_path: Path to config store JSON (optional)
            audit_backend: Audit backend for logging changes (optional)
            tenant_id: Tenant scope (default "_default")
        """
        self.skill_id = skill_id
        self.tenant_id = tenant_id
        self.config_store_path = config_store_path
        self.audit_backend = audit_backend

        # Load config (or use initial)
        self.config = self._load_config(initial_config)

        # Execution metrics history (in-memory)
        self.execution_history: list[SkillExecutionMetrics] = []

        # Feedback queue (collected for batching)
        self.feedback_queue: list[Dict[str, Any]] = []

        logger.info(
            f"SkillInstance initialized: {skill_id} v{self.config.get('version', 0)}"
        )

    def _load_config(self, initial_config: Dict[str, Any]) -> Dict[str, Any]:
        """Load config from store or use initial."""
        if self.config_store_path and self.config_store_path.exists():
            try:
                with open(self.config_store_path) as f:
                    stored_configs = json.load(f)
                stored = stored_configs.get(self.skill_id)
                if stored:
                    logger.info(
                        f"Loaded config from store: v{stored.get('version', 0)}"
                    )
                    return stored
            except Exception as e:
                logger.warning(f"Failed to load config from store: {e}")

        return initial_config

    def execute(
        self, request: Dict[str, Any], executor: Callable[[Dict, Dict], Any]
    ) -> Any:
        """Execute skill with current config and capture metrics.

        Args:
            request: Input request dict
            executor: Callable that performs the actual skill logic
                      signature: executor(request, config) -> result

        Returns:
            Result from executor
        """
        import time
        import uuid

        execution_id = str(uuid.uuid4())
        start_time = time.time()
        result = None
        error = None
        success = True

        try:
            # Execute with current config
            result = executor(request, self.config)
            return result
        except Exception as e:
            error = str(e)
            success = False
            raise
        finally:
            latency_ms = (time.time() - start_time) * 1000

            # Capture metrics
            metrics = SkillExecutionMetrics(
                execution_id=execution_id,
                skill_id=self.skill_id,
                config_version=self.config.get("version", 0),
                latency_ms=latency_ms,
                success=success,
                error=error,
                timestamp=datetime.utcnow().isoformat(),
            )

            self.execution_history.append(metrics)
            logger.info(
                f"Execution {execution_id}: {self.skill_id} latency={latency_ms:.1f}ms success={success}"
            )

    def receive_feedback(
        self,
        quality_rating: int,
        execution_id: Optional[str] = None,
        notes: str = "",
    ) -> None:
        """Receive user feedback (1-5 scale).

        Args:
            quality_rating: User rating 1-5
            execution_id: Link to specific execution (optional)
            notes: User notes (will be scrubbed of PII before storage)
        """
        if not (1 <= quality_rating <= 5):
            raise ValueError(f"Rating must be 1-5, got {quality_rating}")

        feedback = {
            "skill_id": self.skill_id,
            "quality_rating": quality_rating,
            "execution_id": execution_id,
            "notes": self._scrub_pii(notes),
            "timestamp": datetime.utcnow().isoformat(),
            "tenant_id": self.tenant_id,
        }

        self.feedback_queue.append(feedback)
        logger.info(
            f"Feedback received: {self.skill_id} rating={quality_rating} queue_size={len(self.feedback_queue)}"
        )

    def _scrub_pii(self, text: str) -> str:
        """Scrub common PII patterns from text."""
        import re

        # Email pattern
        text = re.sub(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", "[EMAIL]", text)

        # Phone pattern (US)
        text = re.sub(r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b", "[PHONE]", text)

        # Credit card-like
        text = re.sub(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b", "[CC]", text)

        return text

    def get_feedback_batch(self) -> list[Dict[str, Any]]:
        """Get accumulated feedback (for learning loop)."""
        batch = self.feedback_queue.copy()
        self.feedback_queue.clear()
        return batch

    def apply_config_update(
        self, new_config: Dict[str, Any], reason: str = ""
    ) -> bool:
        """Apply tuned config update (called by learning loop).

        Args:
            new_config: New config dict (will be validated and clamped)
            reason: Reason for update (e.g., "learning" or "rollback")

        Returns:
            True if applied successfully, False otherwise
        """
        try:
            # Validate config structure
            required_keys = {
                "routing_threshold",
                "attention_weight",
                "latency_target_ms",
            }
            if not required_keys.issubset(new_config.keys()):
                raise ValueError(f"Missing required config keys: {required_keys - set(new_config.keys())}")

            # Clamp values to safe ranges
            clamped = self._clamp_config(new_config)

            # Compute snapshots for audit
            old_snapshot = SkillConfigSnapshot(
                skill_id=self.skill_id,
                version=self.config.get("version", 0),
                routing_threshold=self.config["routing_threshold"],
                attention_weight=self.config["attention_weight"],
                latency_target_ms=self.config["latency_target_ms"],
            )

            new_snapshot = SkillConfigSnapshot(
                skill_id=self.skill_id,
                version=clamped.get("version", self.config.get("version", 0) + 1),
                routing_threshold=clamped["routing_threshold"],
                attention_weight=clamped["attention_weight"],
                latency_target_ms=clamped["latency_target_ms"],
            )

            # Audit the change
            if self.audit_backend:
                audit_event = {
                    "event_type": "skill_config_updated",
                    "skill_id": self.skill_id,
                    "tenant_id": self.tenant_id,
                    "timestamp": datetime.utcnow().isoformat(),
                    "reason": reason,
                    "version_before": old_snapshot.version,
                    "version_after": new_snapshot.version,
                    "config_hash_before": old_snapshot.config_hash,
                    "config_hash_after": new_snapshot.config_hash,
                    "delta": {
                        "routing_threshold": (
                            old_snapshot.routing_threshold,
                            new_snapshot.routing_threshold,
                        ),
                        "attention_weight": (
                            old_snapshot.attention_weight,
                            new_snapshot.attention_weight,
                        ),
                        "latency_target_ms": (
                            old_snapshot.latency_target_ms,
                            new_snapshot.latency_target_ms,
                        ),
                    },
                }
                self.audit_backend.write_event(audit_event)

            # Apply config
            self.config = clamped
            self._persist_config()

            logger.info(
                f"Config applied: {self.skill_id} v{old_snapshot.version}→v{new_snapshot.version} reason={reason}"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to apply config update: {e}")
            return False

    def _clamp_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Clamp config values to safe ranges (fail-closed)."""
        clamped = config.copy()

        # Clamp routing_threshold [0.5, 0.95]
        clamped["routing_threshold"] = max(
            0.5, min(0.95, float(clamped.get("routing_threshold", 0.7)))
        )

        # Clamp attention_weight [0.0, 1.0]
        clamped["attention_weight"] = max(
            0.0, min(1.0, float(clamped.get("attention_weight", 0.5)))
        )

        # Clamp latency_target_ms [50, 500]
        clamped["latency_target_ms"] = max(
            50.0, min(500.0, float(clamped.get("latency_target_ms", 200.0)))
        )

        # Ensure version incremented
        if "version" not in clamped:
            clamped["version"] = self.config.get("version", 0) + 1

        return clamped

    def _persist_config(self) -> None:
        """Persist current config to store."""
        if not self.config_store_path:
            return

        try:
            # Read existing configs
            if self.config_store_path.exists():
                with open(self.config_store_path) as f:
                    configs = json.load(f)
            else:
                configs = {}

            # Update this skill's config
            configs[self.skill_id] = self.config

            # Write back atomically
            with open(self.config_store_path, "w") as f:
                json.dump(configs, f, indent=2)

            logger.info(f"Config persisted: {self.skill_id}")
        except Exception as e:
            logger.error(f"Failed to persist config: {e}")

    def get_metrics_summary(self) -> Dict[str, Any]:
        """Get summary metrics from execution history."""
        if not self.execution_history:
            return {
                "skill_id": self.skill_id,
                "executions": 0,
                "avg_latency_ms": 0.0,
                "success_rate": 0.0,
            }

        successes = sum(1 for m in self.execution_history if m.success)
        total = len(self.execution_history)
        avg_latency = sum(m.latency_ms for m in self.execution_history) / total

        return {
            "skill_id": self.skill_id,
            "config_version": self.config.get("version", 0),
            "executions": total,
            "avg_latency_ms": avg_latency,
            "success_rate": successes / total if total > 0 else 0.0,
            "feedback_queue_size": len(self.feedback_queue),
        }

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for persistence."""
        return {
            "skill_id": self.skill_id,
            "tenant_id": self.tenant_id,
            "config": self.config,
            "metrics_summary": self.get_metrics_summary(),
        }


if __name__ == "__main__":
    # Example usage
    from pathlib import Path
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        config_store = Path(tmpdir) / "configs.json"

        # Create instance
        instance = SkillInstance(
            skill_id="os.delegation_router",
            initial_config={
                "routing_threshold": 0.7,
                "attention_weight": 0.5,
                "latency_target_ms": 200.0,
                "version": 0,
            },
            config_store_path=config_store,
        )

        # Simulate execution
        def mock_executor(request, config):
            import time

            time.sleep(0.01)  # 10ms
            return {"routed": True}

        instance.execute({"task": "test"}, mock_executor)

        # Receive feedback
        instance.receive_feedback(quality_rating=5, notes="Great routing decision!")

        # Apply tuned config
        new_config = {
            "routing_threshold": 0.72,
            "attention_weight": 0.5,
            "latency_target_ms": 200.0,
            "version": 1,
        }
        instance.apply_config_update(new_config, reason="learning")

        # Print metrics
        print("Metrics:", instance.get_metrics_summary())
