"""Plugin-Builder — assisted plugin development (ADR-0253, ADR-0262).

Interview → auto-classify → generate (Idea/Architecture/ADR/Plan) → scaffold → build.

Entered via the ``/plugin-builder`` console command or driven directly.
``InterviewSession`` has no opinion about its transport.

Build System (v2, ADR-0262 Phase B):
- PluginBuilder: tenant-scoped builder with audit-chain integration
- PluginManifest: ADR-0264 frontmatter support
- BuildConfig: semantic versioning + audit requirements
- Real setuptools build (not fake)

This package emits artifacts and never loads them (ADR-0244's constraint,
restated for this tool): a plugin scaffolded here depends only on
``corvin_plugins``, never on ``plugin_builder``. Deleting this package leaves
every previously generated plugin working.
"""
from __future__ import annotations

from .classifier import classify
from .interview import InterviewPhase, InterviewSession
from .models import (
    Classification,
    Constraints,
    DependencySpec,
    PluginIdea,
    PluginKind,
    ProblemStatement,
    Tier,
)

# Build system v2 (ADR-0262 Phase B)
from .build_system.builder import (
    PluginBuilder,
    PackageBuilder,  # Backward compatibility alias
    PackageMetadata,
    BuildConfig,
    BuildResult,
)
from .build_system.manifest import (
    PluginManifest,
    ManifestStatus,
    ManifestValidator,
    generate_adr_frontmatter,
)

# v2 Integration layer (ADR-0262 Phase 2, ADR-0534)
from .v2_integration import (
    PluginDeveloper,
    PluginDevelopmentPlan,
    DevelopmentResult,
    develop_plugin,
)

__version__ = "0.2.0"

__all__ = [
    "__version__",
    # Interview + Classification
    "classify",
    "InterviewPhase",
    "InterviewSession",
    "Classification",
    "Constraints",
    "DependencySpec",
    "PluginIdea",
    "PluginKind",
    "ProblemStatement",
    "Tier",
    # Build system v2
    "PluginBuilder",
    "PackageBuilder",  # Backward compatibility
    "PackageMetadata",
    "BuildConfig",
    "BuildResult",
    "PluginManifest",
    "ManifestStatus",
    "ManifestValidator",
    "generate_adr_frontmatter",
    # v2 Integration (orchestration + audit + tenant isolation)
    "PluginDeveloper",
    "PluginDevelopmentPlan",
    "DevelopmentResult",
    "develop_plugin",
]
