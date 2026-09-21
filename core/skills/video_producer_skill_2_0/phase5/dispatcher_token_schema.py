"""Dispatcher Token Schema — Unique Identification for Renderer Modules (M1)

Token-based identification for every renderer module in the dispatcher ecosystem.

Design:
- Each renderer gets a UNIQUE token (UUID format)
- Tokens encode: renderer_id, version, tier, capabilities
- Immutable tokens enable audit trail + learning tracking
- Tokens are cacheable and fast to validate

Token Format: <renderer_id>-<version_hash>-<tier>-<checksum>
  Example: quick_renderer-5d1b2c-tier1-9f4e

ADR-0742: 3-Tier Animation Architecture (Phase 5)
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Set, List
from uuid import uuid4, UUID
import hashlib
import time
from datetime import datetime, timedelta


class RendererTier(Enum):
    """Tier levels for renderer classification."""
    TIER_1_QUICK = "tier_1_quick"
    TIER_1_5_THREE_JS = "tier_1_5_threejs"
    TIER_2_RICH = "tier_2_rich"
    TIER_2_MANIM = "tier_2_manim"
    TIER_3_PREMIUM = "tier_3_premium"


class RendererCapability(Enum):
    """Capabilities a renderer module can declare."""
    SYNC_RENDERING = "sync_rendering"       # Synchronous, blocking execution
    ASYNC_RENDERING = "async_rendering"     # Asynchronous, job-based
    VOICE_SYNC_SUPPORT = "voice_sync_support"  # Can integrate narration
    FALLBACK_CAPABLE = "fallback_capable"    # Can fallback to simpler tier
    QUALITY_SCORING = "quality_scoring"      # Produces quality_score metric
    LEARNING_FEEDBACK = "learning_feedback"  # Supports learning optimizer
    TIMEOUT_MANAGEMENT = "timeout_management"  # Enforces render timeout
    ERROR_RECOVERY = "error_recovery"        # Supports error recovery


@dataclass(frozen=True)
class RendererTokenMetadata:
    """Metadata embedded in dispatcher token.

    Immutable to ensure token integrity.
    """
    renderer_id: str                    # Unique renderer identifier
    version: str                        # Semantic version (e.g., "5.1.0")
    tier: RendererTier                  # Tier classification
    capabilities: Set[RendererCapability] = field(default_factory=set)
    created_at: datetime = field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None  # Token expiration (optional)

    def is_expired(self) -> bool:
        """Check if token has expired."""
        if self.expires_at is None:
            return False
        return datetime.utcnow() > self.expires_at

    def to_dict(self) -> dict:
        """Serialize to dictionary."""
        return {
            "renderer_id": self.renderer_id,
            "version": self.version,
            "tier": self.tier.value,
            "capabilities": [c.value for c in self.capabilities],
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }


@dataclass(frozen=True)
class DispatcherToken:
    """Immutable token for renderer identification.

    Format: <renderer_id>-<version_hash>-<tier>-<checksum>
    Example: quick_renderer-5d1b2c-tier1-9f4e
    """
    token_id: str                       # Unique token identifier (UUID)
    renderer_id: str                    # Renderer module ID
    version: str                        # Semantic version
    tier: RendererTier                  # Tier classification
    capabilities: Set[RendererCapability]  # Supported capabilities
    issued_at: datetime                 # Token issuance timestamp
    expires_at: Optional[datetime]      # Token expiration
    token_string: str                   # Encoded token string
    checksum: str                       # SHA256 checksum for validation

    def __str__(self) -> str:
        """Return human-readable token string."""
        return self.token_string

    def is_valid(self) -> bool:
        """Check if token is still valid (not expired)."""
        if self.expires_at is None:
            return True
        return datetime.utcnow() <= self.expires_at

    def has_capability(self, capability: RendererCapability) -> bool:
        """Check if renderer supports specific capability."""
        return capability in self.capabilities

    def to_dict(self) -> dict:
        """Serialize token for storage."""
        return {
            "token_id": str(self.token_id),
            "renderer_id": self.renderer_id,
            "version": self.version,
            "tier": self.tier.value,
            "capabilities": [c.value for c in self.capabilities],
            "issued_at": self.issued_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "token_string": self.token_string,
            "checksum": self.checksum,
        }


class TokenValidationError(Exception):
    """Raised when token validation fails."""
    pass


class TokenExpiredError(TokenValidationError):
    """Raised when token has expired."""
    pass


class TokenChecksumError(TokenValidationError):
    """Raised when token checksum doesn't match."""
    pass


def generate_token_checksum(renderer_id: str, version: str, tier: RendererTier) -> str:
    """Generate SHA256 checksum for token validation.

    Args:
        renderer_id: Unique renderer identifier
        version: Semantic version
        tier: Tier classification

    Returns:
        Hex-encoded SHA256 checksum (8 chars)
    """
    data = f"{renderer_id}-{version}-{tier.value}".encode('utf-8')
    full_hash = hashlib.sha256(data).hexdigest()
    return full_hash[:8]  # Return first 8 characters


def encode_token_string(renderer_id: str, version: str, tier: RendererTier, checksum: str) -> str:
    """Encode token string from components.

    Format: <renderer_id>-<version_prefix>-<tier_short>-<checksum>
    Example: quick_renderer-5d1-tier1-9f4e

    Args:
        renderer_id: Unique renderer identifier
        version: Semantic version (e.g., "5.1.0")
        tier: Tier classification
        checksum: Token checksum

    Returns:
        Encoded token string
    """
    version_prefix = version.split('.')[0]  # e.g., "5" from "5.1.0"
    tier_short = tier.value.split('_')[-1]  # e.g., "quick" from "tier_1_quick"
    return f"{renderer_id}-{version_prefix}{version.split('.')[1][0]}-{tier_short}-{checksum}"


def decode_token_string(token_string: str) -> dict:
    """Decode token string back to components.

    Args:
        token_string: Encoded token string

    Returns:
        Dictionary with {renderer_id, version, tier, checksum}

    Raises:
        TokenValidationError: If token format is invalid
    """
    parts = token_string.split('-')
    if len(parts) < 4:
        raise TokenValidationError(f"Invalid token format: {token_string}")

    renderer_id = '-'.join(parts[:-3])  # Handle renderer IDs with dashes
    version_prefix = parts[-3]
    tier_short = parts[-2]
    checksum = parts[-1]

    return {
        "renderer_id": renderer_id,
        "version_prefix": version_prefix,
        "tier_short": tier_short,
        "checksum": checksum,
    }
