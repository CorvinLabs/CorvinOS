"""elr_capabilities_m2.py — M2 Capability Definitions (ADR-0167 M2).

10 entangled capabilities with plaintext envelope schemas. Each capability
is AEAD-wrapped with a tile_k derived from the ratchet.

Capabilities (8 + room for 2 more in M3):
  1. egress-paid-preset (L35, M1 base) — allowlist for paid EU_PRODUCTION
  2. a2a-sestoken (ADR-0103) — network membership credential
  3–5. mcp-auth-imagegen, mcp-auth-slack, mcp-auth-custom (paid MCP)
  6–7. cls-tier-b-key, cls-tier-c-key (ADR-0156 CLS activation)
  8. [reserved for future]

Each capability dict includes: version, expiry (ttl from derivation), and
capability-specific fields. Unwrap failure → None, caller falls back to default.

Must NOT:
  - Put plaintext key material in audit details (hashes only)
  - Fail open (unwrap failure → None, not exception)
  - Reuse capability labels across milestones (versioned schemas)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# L35 — Egress-allowlist signature (M1 extended, now paid-tier specific)

@dataclass
class EgressPaidPresetCapability:
    """Egress-allowlist policy for paid EU_PRODUCTION tier.

    Plaintext schema (AEAD-wrapped):
    {
      "capability_id": "egress-paid-preset",
      "version": 1,
      "expires_at_epoch_k": <k>,  # ratchet epoch when this expires
      "allowed_hosts": ["localhost", "ollama.lan", ...],
      "forbidden_hosts": ["api.anthropic.com", ...],
      "default_action": "deny"
    }

    Unwrap failure → use static tenant policy (fail-closed).
    """
    capability_id: str = "egress-paid-preset"
    version: int = 1
    expires_at_epoch_k: int = 0
    allowed_hosts: list[str] = None
    forbidden_hosts: list[str] = None
    # Fail-CLOSED default: an unmatched host is DENIED unless the descriptor
    # explicitly opts into "allow". A missing/forgotten field must never silently
    # open egress (L35 posture; matches the docstring example above). Security
    # review 2026-06-27 (ADR-0167) — was "allow" (fail-open).
    default_action: str = "deny"

    def __post_init__(self) -> None:
        if self.allowed_hosts is None:
            self.allowed_hosts = []
        if self.forbidden_hosts is None:
            self.forbidden_hosts = []

    def to_dict(self) -> dict[str, Any]:
        """Serialize to plaintext envelope (pre-AEAD-wrap)."""
        return {
            "capability_id": self.capability_id,
            "version": self.version,
            "expires_at_epoch_k": self.expires_at_epoch_k,
            "allowed_hosts": self.allowed_hosts,
            "forbidden_hosts": self.forbidden_hosts,
            "default_action": self.default_action,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EgressPaidPresetCapability | None:
        """Deserialize from unwrapped plaintext (post-AEAD-unwrap)."""
        try:
            return cls(
                capability_id=data.get("capability_id", "egress-paid-preset"),
                version=int(data.get("version", 1)),
                expires_at_epoch_k=int(data.get("expires_at_epoch_k", 0)),
                allowed_hosts=list(data.get("allowed_hosts", [])),
                forbidden_hosts=list(data.get("forbidden_hosts", [])),
                default_action=data.get("default_action", "deny"),  # fail-closed
            )
        except (KeyError, ValueError, TypeError):
            return None


# ADR-0103 — Network membership credential (A2A/SesT)

@dataclass
class A2ASesTokenCapability:
    """A2A / network-membership SesT (Session Establishing Token).

    Plaintext schema:
    {
      "capability_id": "a2a-sestoken",
      "version": 1,
      "expires_at_epoch_k": <k>,
      "membership_nonce": "<base64 or hex>",  # ADR-0103 nonce
      "network_id": "corvin-prod",
      "allowed_origins": ["https://example.com", ...],
      "allowed_endpoints": ["endpoint-id-1", ...]
    }

    Unwrap failure → deny A2A spawn (fail-closed).
    """
    capability_id: str = "a2a-sestoken"
    version: int = 1
    expires_at_epoch_k: int = 0
    membership_nonce: str = ""
    network_id: str = ""
    allowed_origins: list[str] = None
    allowed_endpoints: list[str] = None

    def __post_init__(self) -> None:
        if self.allowed_origins is None:
            self.allowed_origins = []
        if self.allowed_endpoints is None:
            self.allowed_endpoints = []

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "version": self.version,
            "expires_at_epoch_k": self.expires_at_epoch_k,
            "membership_nonce": self.membership_nonce,
            "network_id": self.network_id,
            "allowed_origins": self.allowed_origins,
            "allowed_endpoints": self.allowed_endpoints,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> A2ASesTokenCapability | None:
        try:
            return cls(
                capability_id=data.get("capability_id", "a2a-sestoken"),
                version=int(data.get("version", 1)),
                expires_at_epoch_k=int(data.get("expires_at_epoch_k", 0)),
                membership_nonce=str(data.get("membership_nonce", "")),
                network_id=str(data.get("network_id", "")),
                allowed_origins=list(data.get("allowed_origins", [])),
                allowed_endpoints=list(data.get("allowed_endpoints", [])),
            )
        except (KeyError, ValueError, TypeError):
            return None


# Paid-tier MCP auth (3 services)

@dataclass
class MCPAuthCapability:
    """Paid-tier MCP-server authentication credentials.

    Plaintext schema:
    {
      "capability_id": "mcp-auth-<service>",
      "version": 1,
      "expires_at_epoch_k": <k>,
      "service": "imagegen" | "slack" | "custom",
      "auth_token": "<base64-encoded or encrypted>",
      "auth_header": "Authorization",
      "api_endpoint": "https://api.service.com"
    }

    Unwrap failure → MCP service unavailable (fail-closed, no fallback).
    """
    capability_id: str = ""
    version: int = 1
    expires_at_epoch_k: int = 0
    service: str = ""
    auth_token: str = ""
    auth_header: str = "Authorization"
    api_endpoint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "version": self.version,
            "expires_at_epoch_k": self.expires_at_epoch_k,
            "service": self.service,
            "auth_token": self.auth_token,
            "auth_header": self.auth_header,
            "api_endpoint": self.api_endpoint,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MCPAuthCapability | None:
        try:
            return cls(
                capability_id=str(data.get("capability_id", "")),
                version=int(data.get("version", 1)),
                expires_at_epoch_k=int(data.get("expires_at_epoch_k", 0)),
                service=str(data.get("service", "")),
                auth_token=str(data.get("auth_token", "")),
                auth_header=str(data.get("auth_header", "Authorization")),
                api_endpoint=str(data.get("api_endpoint", "")),
            )
        except (KeyError, ValueError, TypeError):
            return None


# ADR-0156 — Custom-Layer (CLS) Tier-B/C activation

@dataclass
class CLSActivationCapability:
    """Custom-Layer activation key for Tier-B/C features.

    Plaintext schema:
    {
      "capability_id": "cls-tier-<b|c>-key",
      "version": 1,
      "expires_at_epoch_k": <k>,
      "tier": "B" | "C",
      "max_concurrent_layers": <N>,
      "allowed_tools": ["list", "of", "tool", "namespaces"],
      "allowed_skills": ["list", "of", "skill", "namespaces"]
    }

    Unwrap failure → fall back to free tier (fail-closed).
    """
    capability_id: str = ""
    version: int = 1
    expires_at_epoch_k: int = 0
    tier: str = "B"
    max_concurrent_layers: int = 1
    allowed_tools: list[str] = None
    allowed_skills: list[str] = None

    def __post_init__(self) -> None:
        if self.allowed_tools is None:
            self.allowed_tools = []
        if self.allowed_skills is None:
            self.allowed_skills = []

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "version": self.version,
            "expires_at_epoch_k": self.expires_at_epoch_k,
            "tier": self.tier,
            "max_concurrent_layers": self.max_concurrent_layers,
            "allowed_tools": self.allowed_tools,
            "allowed_skills": self.allowed_skills,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CLSActivationCapability | None:
        try:
            return cls(
                capability_id=str(data.get("capability_id", "")),
                version=int(data.get("version", 1)),
                expires_at_epoch_k=int(data.get("expires_at_epoch_k", 0)),
                tier=str(data.get("tier", "B")),
                max_concurrent_layers=int(data.get("max_concurrent_layers", 1)),
                allowed_tools=list(data.get("allowed_tools", [])),
                allowed_skills=list(data.get("allowed_skills", [])),
            )
        except (KeyError, ValueError, TypeError):
            return None


# Capability factory (dispatch by label)

def create_capability_from_dict(
    capability_label: str,
    plaintext: dict[str, Any],
) -> Any | None:
    """Factory to deserialize any capability by label.

    Returns the appropriate capability dataclass, or None on failure.
    """
    if capability_label == "egress-paid-preset":
        return EgressPaidPresetCapability.from_dict(plaintext)
    elif capability_label == "a2a-sestoken":
        return A2ASesTokenCapability.from_dict(plaintext)
    elif capability_label.startswith("mcp-auth-"):
        return MCPAuthCapability.from_dict(plaintext)
    elif capability_label.startswith("cls-tier-"):
        return CLSActivationCapability.from_dict(plaintext)
    else:
        return None
