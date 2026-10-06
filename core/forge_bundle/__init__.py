"""Forge Bundle — cross-forge ZIP envelope (ADR-2229).

Phase 1: envelope schema + fail-closed validator. Pure: reads the bytes it is
given, writes nothing, touches no registry. Export/import entry points come in
later phases and hand each artifact to its own forge's intake.
"""
from .envelope import ARTIFACT_KINDS, FORMAT, FORMAT_VERSION, LIMITS, ArtifactEntry, BundleEnvelope
from .export import (
    BundleResult,
    ExportError,
    LayerSelection,
    PluginSelection,
    SkillSelection,
    ToolSelection,
    build_bundle,
)
from .validate import BundleReport, BundleRejected, validate_bundle

__all__ = [
    "ARTIFACT_KINDS",
    "FORMAT",
    "FORMAT_VERSION",
    "LIMITS",
    "ArtifactEntry",
    "BundleEnvelope",
    "BundleReport",
    "BundleRejected",
    "BundleResult",
    "ExportError",
    "LayerSelection",
    "PluginSelection",
    "SkillSelection",
    "ToolSelection",
    "build_bundle",
    "validate_bundle",
]
