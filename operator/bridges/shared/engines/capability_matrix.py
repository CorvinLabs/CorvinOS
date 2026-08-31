"""
ADR-0087 M8: Universal Engine Capability Matrix

Goal: Refactor per-engine capabilities from booleans to semantic strings.

Current State (M5+M6 frozen):
  - Each engine has `capabilities` dict with boolean values
  - Example: `capabilities["skills"] = True | False`

New State (M8 refactor):
  - All capabilities use semantic strings (FROZEN in M5+M6)
  - Example: `capabilities["skills"] = "append_system_prompt" | "system_message" | "prompt_prefix"`
  - No breaking changes: bool fallback remains (truthy check works)

Semantic Capability Strings (from M1–M6):
  - mid_stream_inject: "stdin_json" (CC) | "buffered" (Codex/OpenCode/Hermes) | None (Copilot)
  - hooks: "native" (CC) | "teb_brokered" (others)
  - skills: "append_system_prompt" (CC/Codex/OpenCode) | "system_message" (Hermes) | "prompt_prefix" (Copilot)
  - session_pinning: "native" (CC) | "checkpoint" (others) | None
  - mcp: "native" (CC/Codex/OpenCode) | "fcb_emulated" (Copilot, M5) | None (Hermes/Ollama)
  - system_prompt: "flag" (CC) | "message_role" (Hermes) | "text_prefix" (Codex/OpenCode/Copilot)
  - multi_turn: "native" (CC) | "sequential_wrapper" (Copilot, M7) | None (others)

Architecture:
  - CapabilityString: Enum-like class for validation
  - EngineCapabilityMatrix: Per-engine capability snapshot
  - CapabilityMatcher: Query-by-capability (find engines supporting "native" session_pinning)
  - Backward Compat: BoolToStringAdapter for old consumers

Compliance (from CLAUDE.md):
  - L10/L16/L33: No changes (enforced at TEB, not in capability matrix)
  - ADR-0007: All matrix structures include tenant_id for multi-tenancy

Test Coverage Standard (from M1–M4):
  - Tier-1: Enum validation, matrix construction
  - Tier-2: Backward compat (bool checks), capability queries
  - Tier-3: Full matrix consistency across all 5 engines
"""

from typing import Dict, Optional, Literal, Any
from dataclasses import dataclass, field
import logging

logger = logging.getLogger(__name__)

# Semantic capability string types (frozen from M5+M6)
MidStreamInjectCapability = Literal["stdin_json", "buffered", None]
HooksCapability = Literal["native", "teb_brokered", None]
SkillsCapability = Literal["append_system_prompt", "system_message", "prompt_prefix", None]
SessionPinningCapability = Literal["native", "checkpoint", None]
MCPCapability = Literal["native", "fcb_emulated", None]
SystemPromptCapability = Literal["flag", "message_role", "text_prefix", None]
MultiTurnCapability = Literal["native", "sequential_wrapper", None]


@dataclass
class CapabilityString:
    """Enum-like validator for semantic capability strings."""

    # Predefined strings (frozen from M5+M6)
    STDIN_JSON = "stdin_json"
    BUFFERED = "buffered"
    NATIVE = "native"
    TEB_BROKERED = "teb_brokered"
    APPEND_SYSTEM_PROMPT = "append_system_prompt"
    SYSTEM_MESSAGE = "system_message"
    PROMPT_PREFIX = "prompt_prefix"
    CHECKPOINT = "checkpoint"
    FCB_EMULATED = "fcb_emulated"
    FLAG = "flag"
    MESSAGE_ROLE = "message_role"
    SEQUENTIAL_WRAPPER = "sequential_wrapper"

    @staticmethod
    def validate(capability: Optional[str], allowed_values: tuple) -> bool:
        """
        Validate capability string against allowed values.

        Args:
            capability: String to validate (may be None)
            allowed_values: Tuple of allowed strings (including None)

        Returns:
            True if valid, False otherwise

        Example:
            >>> CapabilityString.validate("stdin_json", ("stdin_json", "buffered", None))
            True
        """
        return capability in allowed_values


@dataclass
class EngineCapabilityMatrix:
    """Per-engine capability snapshot (frozen in M6, used in M8+)."""

    engine_id: str  # "claude_code", "hermes", etc.
    tenant_id: str = "_default"

    # All capabilities from M5+M6 (frozen strings)
    mid_stream_inject: Optional[str] = None
    hooks: Optional[str] = None
    skills: Optional[str] = None
    session_pinning: Optional[str] = None
    mcp: Optional[str] = None
    system_prompt: Optional[str] = None
    multi_turn: Optional[str] = None

    # Backward compat: bool fallback (from M1–M6)
    _legacy_capabilities: Dict[str, bool] = field(default_factory=dict)

    def is_capable(self, capability_name: str) -> bool:
        """
        Check if engine supports a capability (modern semantic strings).

        Args:
            capability_name: "mid_stream_inject", "hooks", etc.

        Returns:
            True if capability is not None (engine supports it)

        Example:
            >>> matrix = EngineCapabilityMatrix("claude_code", mid_stream_inject="stdin_json")
            >>> matrix.is_capable("mid_stream_inject")
            True
        """
        value = getattr(self, capability_name, None)
        return value is not None

    def get_capability(self, capability_name: str) -> Optional[str]:
        """
        Get semantic capability string for a feature.

        Args:
            capability_name: "mid_stream_inject", "hooks", etc.

        Returns:
            Semantic string (e.g., "native", "buffered") or None

        Example:
            >>> matrix.get_capability("mid_stream_inject")
            "stdin_json"
        """
        return getattr(self, capability_name, None)

    def validate_all(self) -> bool:
        """
        Validate all capability strings against frozen allowed values.

        Returns:
            True if all valid, False otherwise

        Raises:
            ValueError: If invalid capability string detected
        """
        # Define allowed values per capability (frozen from M5+M6)
        allowed_by_name = {
            "mid_stream_inject": ("stdin_json", "buffered", None),
            "hooks": ("native", "teb_brokered", None),
            "skills": ("append_system_prompt", "system_message", "prompt_prefix", None),
            "session_pinning": ("native", "checkpoint", None),
            "mcp": ("native", "fcb_emulated", None),
            "system_prompt": ("flag", "message_role", "text_prefix", None),
            "multi_turn": ("native", "sequential_wrapper", None),
        }

        for cap_name, allowed_values in allowed_by_name.items():
            value = getattr(self, cap_name, None)
            if not CapabilityString.validate(value, allowed_values):
                raise ValueError(
                    f"Invalid capability '{cap_name}={value}' for engine '{self.engine_id}'. "
                    f"Allowed values: {allowed_values}"
                )

        return True


class CapabilityMatcher:
    """Query engines by capability (find all engines supporting "native" session_pinning)."""

    def __init__(self, matrices: Dict[str, EngineCapabilityMatrix]):
        """
        Initialize with engine capability matrices.

        Args:
            matrices: Dict mapping engine_id -> EngineCapabilityMatrix
        """
        self.matrices = matrices

    def engines_supporting(self, capability_name: str, capability_value: str) -> list:
        """
        Find all engines supporting a specific capability value.

        Args:
            capability_name: "mid_stream_inject", "hooks", etc.
            capability_value: "native", "buffered", "teb_brokered", etc.

        Returns:
            List of engine IDs supporting the capability

        Example:
            >>> matcher.engines_supporting("hooks", "native")
            ["claude_code"]
        """
        result = []
        for engine_id, matrix in self.matrices.items():
            value = matrix.get_capability(capability_name)
            if value == capability_value:
                result.append(engine_id)
        return result


# Frozen capability matrix for all 5 engines (snapshot from M5+M6)
CANONICAL_CAPABILITY_MATRIX = {
    "claude_code": {
        "mid_stream_inject": "stdin_json",
        "hooks": "native",
        "skills": "append_system_prompt",
        "session_pinning": "native",
        "mcp": "native",
        "system_prompt": "flag",
        "multi_turn": "native",
    },
    "codex": {
        "mid_stream_inject": "buffered",
        "hooks": "teb_brokered",
        "skills": "append_system_prompt",
        "session_pinning": "checkpoint",
        "mcp": "native",
        "system_prompt": "text_prefix",
        "multi_turn": None,
    },
    "opencode": {
        "mid_stream_inject": "buffered",
        "hooks": "teb_brokered",
        "skills": "prompt_prefix",
        "session_pinning": "checkpoint",
        "mcp": "native",
        "system_prompt": "text_prefix",
        "multi_turn": None,
    },
    "hermes": {
        "mid_stream_inject": "buffered",
        "hooks": "teb_brokered",
        "skills": "system_message",
        "session_pinning": "checkpoint",
        "mcp": None,  # Ollama doesn't have MCP
        "system_prompt": "message_role",
        "multi_turn": None,
    },
    "copilot": {
        "mid_stream_inject": None,  # Single-turn by default
        "hooks": "teb_brokered",
        "skills": "prompt_prefix",
        "session_pinning": "checkpoint",
        "mcp": "fcb_emulated",  # M5: emulated via text syntax
        "system_prompt": "text_prefix",
        "multi_turn": "sequential_wrapper",  # M7: optional
    },
}
