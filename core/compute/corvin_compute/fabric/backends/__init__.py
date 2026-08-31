"""corvin_compute.fabric.backends — Section A (ADR-0026)."""
from __future__ import annotations

from .protocol import (
    ComputeBackend,
    DataCursor,
    JobSpec,
    ConvSpec,
    EpochMetrics,
    ArtifactManifest,
    BackendParams,
    BackendSession,
    SteeringVector,
)
from .manifest import ManifestValidationError, PluginManifest, validate_manifest
from .registry import BackendRegistry, RegistryError, NetworkNotApproved

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
    "ManifestValidationError",
    "PluginManifest",
    "validate_manifest",
    "BackendRegistry",
    "RegistryError",
    "NetworkNotApproved",
]
