"""
Transport Decorator Suite — ADR-0303

Unified decorators for Flask, CLI, async, and internal functions.
Enforces dual-gate pipeline (capability + audit) across all transports.
"""

from core.decorators.flask_decorators import (
    requires_auth_capability,
    flask_audit_log,
)
from core.decorators.cli_decorators import (
    cli_requires_capability,
)
from core.decorators.async_decorators import (
    async_requires_capability,
)
from core.decorators.internal_decorators import (
    internal_requires_capability,
)

__all__ = [
    "requires_auth_capability",
    "flask_audit_log",
    "cli_requires_capability",
    "async_requires_capability",
    "internal_requires_capability",
]
