"""
Video Producer Worker Base Class — Skill Forge v2.0 Standard Interface

All workers inherit from WorkerSkillBase and implement the Worker Contract:
- Input: structured dict with required keys
- Output: structured dict with "status", "error", result fields
- Audit: every execute() is logged to audit trail (immutable, hash-chained)
- Config: tunable parameters via manifest + config_overrides
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class WorkerManifest:
    """Worker metadata (Skill Forge v2.0 compliant)."""
    id: str  # e.g., "video-producer:audio-synthesis"
    version: str  # semver, e.g., "1.0.0"
    name: str
    description: str
    plugin_id: str  # e.g., "video-producer"
    boot_layer: str  # "bundled" for built-in workers
    capabilities: list[str]  # ["audio_synthesis", "tts"]
    config: Dict[str, Any]  # tunable parameters
    required_checks: list[str]  # audit/compliance gates


@dataclass
class WorkerResult:
    """Worker output (JSON-serializable)."""
    worker_id: str
    status: str  # "success" | "partial" | "error"
    output: Dict[str, Any]
    error: Optional[str] = None
    latency_ms: float = 0.0
    timestamp: Optional[str] = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class WorkerSkillBase(ABC):
    """
    Abstract base class for all Video Producer Workers.

    Contract:
    - Each worker has a unique ID (e.g., "video-producer:audio-synthesis")
    - Input is a dict; output is WorkerResult (JSON-serializable)
    - Every execute() is audit-logged (immutable, hash-chained)
    - Config is tunable via manifest + runtime overrides
    - Errors are caught + logged, never crash the orchestrator
    """

    def __init__(self, manifest: WorkerManifest):
        self.manifest = manifest
        self.config = manifest.config.copy()
        logger.info(f"Initialized Worker: {manifest.id} v{manifest.version}")

    @abstractmethod
    async def execute(self, input_data: Dict[str, Any], **config_overrides) -> WorkerResult:
        """
        Execute the worker's primary task.

        Args:
            input_data: Worker-specific input dict (validated by subclass)
            **config_overrides: Runtime config tuning (e.g., tts_voice="en-US-AriaNeural")

        Returns:
            WorkerResult (status, output, error, latency_ms, timestamp)

        Postcondition:
            - Result is audit-logged immediately after execute()
            - If error: status="error", error field populated, output may be partial
            - If success: status="success", error=None, output complete
        """
        pass

    def apply_config_overrides(self, **overrides):
        """Apply runtime config tuning (before execute)."""
        self.config.update(overrides)
        logger.debug(f"Config override: {list(overrides.keys())}")

    def get_manifest(self) -> Dict[str, Any]:
        """Return manifest as dict (for registration, discovery)."""
        return asdict(self.manifest)


class WorkerRegistry:
    """
    Central registry of all available workers.

    Responsibilities:
    - Discover workers by ID
    - Validate manifest compliance
    - Track worker versions + dependencies
    """

    def __init__(self):
        self.workers: Dict[str, WorkerSkillBase] = {}
        self.manifests: Dict[str, WorkerManifest] = {}

    def register(self, worker: WorkerSkillBase):
        """Register a worker (called at startup by Maestro)."""
        # Type validation (FIX for HIGH finding #5)
        if not isinstance(worker, WorkerSkillBase):
            raise TypeError(f"Worker must be instance of WorkerSkillBase, got {type(worker).__name__}")

        worker_id = worker.manifest.id
        if worker_id in self.workers:
            logger.warning(f"Worker {worker_id} already registered, overwriting")
        self.workers[worker_id] = worker
        self.manifests[worker_id] = worker.manifest
        logger.info(f"Registered worker: {worker_id}")

    def get_worker(self, worker_id: str) -> Optional[WorkerSkillBase]:
        """Get worker by ID."""
        return self.workers.get(worker_id)

    def get_all_workers(self) -> Dict[str, WorkerSkillBase]:
        """Get all registered workers."""
        return self.workers.copy()

    def validate_manifests(self) -> list[str]:
        """
        Validate all manifests are Skill Forge v2.0 compliant.
        Returns list of errors (empty = all valid).
        """
        errors = []
        for worker_id, manifest in self.manifests.items():
            required_fields = ["id", "version", "name", "description", "plugin_id", "boot_layer", "capabilities", "config"]
            for field in required_fields:
                if not hasattr(manifest, field):
                    errors.append(f"Worker {worker_id} missing field: {field}")
        return errors
