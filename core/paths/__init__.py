"""Path utilities for CorvinOS (core/paths).

Central repository for path construction with validation. All path functions
accept tenant_id and validate it before constructing the path, ensuring
fail-closed safety against path traversal and cross-tenant access.

ADR-0007 multi-tenant axis: tenant_id is required for all tenant-scoped paths.
"""

from .tenant import (
    tenant_audit_file,
    tenant_bridge_dir,
    tenant_home,
    tenant_learning_dir,
    tenant_memory_dir,
    tenant_session_dir,
    tenant_skill_dir,
    tenant_tool_dir,
)

__all__ = [
    "tenant_home",
    "tenant_skill_dir",
    "tenant_tool_dir",
    "tenant_session_dir",
    "tenant_learning_dir",
    "tenant_memory_dir",
    "tenant_audit_file",
    "tenant_bridge_dir",
]
