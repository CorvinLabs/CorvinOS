"""Skill Package System — ZIP-based skill/plugin/hook distribution (ADR-0268)."""
from __future__ import annotations

from .corvin_package_manager import PackageManager
from .package_registry import PackageRegistry
from .validators import (
    MANIFEST_SCHEMA,
    SKILL_DEFINITION_SCHEMA,
    PackageValidator,
    ValidationError,
)

__all__ = [
    "PackageManager",
    "PackageRegistry",
    "PackageValidator",
    "ValidationError",
    "MANIFEST_SCHEMA",
    "SKILL_DEFINITION_SCHEMA",
]
