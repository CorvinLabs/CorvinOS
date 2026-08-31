"""ComputeBackend Protocol and associated data structures (ADR-0026 Section A).

All structures are plain dataclasses or TypedDicts so no external
dependencies are introduced.  The Protocol uses runtime_checkable so
isinstance() checks work in the registry.

IMPORTANT: This module MUST NOT import anthropic, openai, or any cloud SDK.
"""
from __future__ import annotations

import dataclasses
import hashlib
from pathlib import Path
from typing import Any, Iterator, Optional, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

# DataCursor is an abstract type from Layer 24 (external). We use Any here
# to avoid a hard dependency on that layer.
DataCursor = Any


@dataclasses.dataclass
class JobSpec:
    """Specification for a compute job passed to ComputeBackend.create_session."""
    run_id: str
    max_epochs: int = 10
    primary_metric: str = "loss"
    minimize_metric: bool = True
    # backend-native parameters (initial hyperparams)
    params: dict[str, Any] = dataclasses.field(default_factory=dict)
    # shard context (filled by ShardManager if applicable)
    shard_index: int = 0
    total_shards: int = 1
    # path for checkpoints
    checkpoint_dir: Optional[Path] = None
    # arbitrary extra config for the backend
    extra: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class ConvSpec:
    """Convergence specification — when to stop training."""
    min_delta: float = 1e-4
    patience: int = 3
    max_epochs: int = 100


@dataclasses.dataclass
class EpochMetrics:
    """Metrics emitted by ComputeBackend.train_epoch."""
    epoch: int
    primary_metric: str
    metric_value: float
    wall_ms: float = 0.0
    extra: dict[str, Any] = dataclasses.field(default_factory=dict)

    def is_better_than(self, other: "EpochMetrics", minimize: bool = True) -> bool:
        if minimize:
            return self.metric_value < other.metric_value
        return self.metric_value > other.metric_value


@dataclasses.dataclass
class ArtifactManifest:
    """Describes a trained model artifact produced by ComputeBackend.finalize."""
    run_id: str
    backend: str
    backend_version: str
    primary_metric: str
    metric_value: float
    # sha256[:16] of the artifact path — NEVER the path itself (GDPR Art. 32)
    artifact_path_hash: str
    artifact_size_b: int = 0
    tags: list[str] = dataclasses.field(default_factory=list)
    extra: dict[str, Any] = dataclasses.field(default_factory=dict)

    @staticmethod
    def hash_path(path: Path | str) -> str:
        """Return sha256[:16] of the canonical string representation of path."""
        return hashlib.sha256(str(path).encode()).hexdigest()[:16]


@dataclasses.dataclass
class BackendParams:
    """Backend-native parameters returned by translate_steering."""
    params: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class BackendSession:
    """Opaque session object managed by a ComputeBackend.

    Backends may subclass this to hold framework-specific state.
    The Fabric core only reads the public fields defined here.
    """
    run_id: str
    backend_name: str
    shard_index: int = 0
    current_epoch: int = 0
    # current params (updated by apply_params)
    params: dict[str, Any] = dataclasses.field(default_factory=dict)
    # metrics history
    history: list[EpochMetrics] = dataclasses.field(default_factory=list)
    # internal state for backends — not touched by the Fabric core
    _state: dict[str, Any] = dataclasses.field(default_factory=dict)

    def apply_params(self, backend_params: BackendParams) -> None:
        """Apply translated steering params to the session."""
        self.params.update(backend_params.params)


# ---------------------------------------------------------------------------
# SteeringVector (shared between oracle and backends — defined here to avoid
# circular imports)
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class SteeringVector:
    """Abstract steering instruction from the Oracle.

    Keys are abstract ML parameter names (e.g. "lr", "max_depth").
    Values are direction strings like "↓0.3" or "↑1".

    The backend's translate_steering() converts this to BackendParams.
    """
    vector: dict[str, str] = dataclasses.field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.vector)


# ---------------------------------------------------------------------------
# ComputeBackend Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class ComputeBackend(Protocol):
    """Protocol every compute backend must satisfy.

    Built-in backends live in ``backends/builtin/``; third-party backends
    are discovered from plugin manifests via ``BackendRegistry``.

    MUST NOT import anthropic / openai / google.cloud.aiplatform.
    """

    name: str
    version: str
    supports_partial_fit: bool
    supports_checkpointing: bool
    supports_distributed: bool
    inter_job_compatible: bool  # for ShardManager

    def create_session(
        self, spec: JobSpec, cursor: DataCursor
    ) -> BackendSession: ...

    def train_epoch(self, session: BackendSession) -> EpochMetrics: ...

    def translate_steering(
        self, vector: SteeringVector
    ) -> BackendParams: ...

    def checkpoint(self, session: BackendSession, path: Path) -> None: ...

    def restore(self, session: BackendSession, path: Path) -> None: ...

    def finalize(self, session: BackendSession) -> ArtifactManifest: ...

    def cleanup(self, session: BackendSession) -> None: ...


__all__ = [
    "ComputeBackend",
    "DataCursor",
    "JobSpec",
    "ConvSpec",
    "EpochMetrics",
    "ArtifactManifest",
    "BackendParams",
    "BackendSession",
    "SteeringVector",
]
