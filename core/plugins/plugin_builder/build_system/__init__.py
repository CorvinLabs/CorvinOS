"""Build system for plugin scaffolds (ADR-0262 Phase 2).

Provides:
- PackageBuilder: orchestrates wheel building
- generate_setup_py: generates setup.py for a plugin
- generate_pyproject_toml: generates pyproject.toml for a plugin
- validate_package: validates a built wheel

Generated scaffolds use these functions to create distributable wheels.
"""
from __future__ import annotations

from .builder import (
    BuildResult,
    PackageBuilder,
    PackageMetadata,
    generate_pyproject_toml,
    generate_setup_py,
    validate_package,
)

__version__ = "2.0.0"

__all__ = [
    "__version__",
    "BuildResult",
    "PackageBuilder",
    "PackageMetadata",
    "generate_pyproject_toml",
    "generate_setup_py",
    "validate_package",
]
