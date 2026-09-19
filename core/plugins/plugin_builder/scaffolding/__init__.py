"""Enhanced scaffolding system for plugin generation with tenant integration (ADR-0262 Phase 2).

Provides:
- PluginScaffoldConfig: Configuration for plugin scaffold generation with validation
- LifecycleHookTemplate: templates with explicit lifecycle hook support
- PluginScaffoldGenerator: improved scaffolding with tenant integration
- AuditEventEmitter: audit trail integration (GDPR Art. 30, 32)
- EnhancedScaffolder: improved scaffolding with better template system (legacy)
- bootstrap_scaffold: Bootstrap plugin scaffolds with tenant integration

Extends the original scaffold.py with:
- Better template organization
- Explicit on_load, on_execute, on_unload hooks
- Tenant-scoped generation (GDPR Art. 5)
- Audit trail events (immutable, hash-chained)
- Version tracking
- Configuration management and validation
"""
from __future__ import annotations

from .enhanced_scaffolder import (
    AuditEventEmitter,
    EnhancedScaffolder,
    LifecycleHookTemplate,
    PluginScaffoldConfig,
    PluginScaffoldGenerator,
    bootstrap_scaffold,
    bootstrap_scaffold_legacy,
)

__version__ = "2.1.0"

__all__ = [
    "__version__",
    "PluginScaffoldConfig",
    "PluginScaffoldGenerator",
    "AuditEventEmitter",
    "EnhancedScaffolder",
    "LifecycleHookTemplate",
    "bootstrap_scaffold",
    "bootstrap_scaffold_legacy",
]
