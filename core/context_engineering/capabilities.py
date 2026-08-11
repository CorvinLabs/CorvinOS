"""
Capability and Persona/Role enums.

Load-bearing: deny-by-default capabilities. A capability not in the registry is always False.
"""

from dataclasses import dataclass
from enum import Enum


class Persona(Enum):
    """Persona represents how CorvinOS is accessed."""
    CONSOLE_OPERATOR = "console_operator"
    VOICE_USER = "voice_user"
    BRIDGE_ADAPTER = "bridge_adapter"
    MCP_TOOL = "mcp_tool"


class Role(Enum):
    """Role partitions capabilities within a persona."""
    ADMIN = "admin"
    OPERATOR = "operator"
    USER = "user"


class Tier(Enum):
    """Capability tier determines lifecycle and protection."""
    COMPLIANCE = "compliance"  # Cannot be revoked after boot lock
    STANDARD = "standard"      # Can be revoked
    USER = "user"              # User-added (future)


@dataclass(frozen=True)
class Capability:
    """Atomic permission (e.g., read_audit_log, write_feature_flag)."""
    id: str                        # "read_audit_log"
    description: str               # "Read audit log entries"
    tier: Tier                     # Tier.COMPLIANCE or Tier.STANDARD
    requires_mfa: bool = False     # If True, needs MFA verification
