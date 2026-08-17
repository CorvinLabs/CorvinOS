"""Forge subsystem APIs: Abstract interfaces for tool/skill generation (ADR-0361).

Defines:
- ForgedToolAPI: Standard interface for requesting tool generation
- ForgedSkillAPI: Standard interface for requesting skill generation
- NamespacePolicy: Enforce namespace ownership per subsystem
- ForgeQuota: Enforce per-subsystem resource limits
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ============================================================================
# Part 1: ForgedToolAPI Interface
# ============================================================================


class ForgedToolAPI(ABC):
    """Standard interface for requesting tool generation.

    Custom subsystems use hub.get_api("forged_tool") to forge tools
    without directly importing ToolForgeSubsystem.
    """

    @abstractmethod
    async def forge_tool(
        self,
        name: str,
        description: str,
        input_schema: dict,
        impl: str,
        runtime: str = "python",
        meta: Optional[dict] = None,
        namespace: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate a tool.

        Args:
            name: Tool name (auto-prefixed if namespace set)
            description: Tool description
            input_schema: JSON schema for inputs
            impl: Implementation code
            runtime: 'python' or 'bash'
            meta: Optional metadata dict
            namespace: Namespace override (checked against policy)

        Returns: {
            'tool_spec': dict | ToolSpec,
            'cost_units': float,
            'namespace': str,
            'created_at': str,
        }

        Raises:
            PermissionDenied: namespace not allowed
            SandboxError: code fails sandbox checks
            QuotaExceeded: forge quota exhausted
        """
        pass

    @abstractmethod
    async def forge_exec(
        self,
        name: str,
        input_data: dict,
    ) -> Dict[str, Any]:
        """Execute a forged tool."""
        pass

    @abstractmethod
    async def forge_promote(
        self,
        name: str,
        from_scope: str,
        to_scope: str,
    ) -> None:
        """Promote tool across scopes."""
        pass

    @abstractmethod
    async def list_tools(
        self,
        namespace: Optional[str] = None,
        scope: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List tools matching criteria."""
        pass


# ============================================================================
# Part 2: ForgedSkillAPI Interface
# ============================================================================


class ForgedSkillAPI(ABC):
    """Standard interface for requesting skill generation.

    Custom subsystems use hub.get_api("forged_skill") to create skills
    without directly importing SkillForgeSubsystem.
    """

    @abstractmethod
    async def skill_create(
        self,
        name: str,
        body_md: str,
        description: Optional[str] = None,
        skill_type: str = "learned-experience",
        claim: Optional[dict] = None,
        namespace: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a skill.

        Args:
            name: Skill name (auto-prefixed if namespace set)
            body_md: Markdown body (linter-checked)
            description: Short description
            skill_type: 'learned-experience', 'debug-pattern', etc.
            claim: Optional claim dict for evidence tracking
            namespace: Namespace override

        Returns: {
            'skill_record': dict | SkillRecord,
            'created_at': str,
            'namespace': str,
        }

        Raises:
            LinterError: markdown has prompt injection pattern
            PermissionDenied: namespace not allowed
            QuotaExceeded: skill creation quota exhausted
        """
        pass

    @abstractmethod
    async def skill_grade(
        self,
        name: str,
        score: float,
        feedback: Optional[str] = None,
    ) -> None:
        """Grade a skill (for manual feedback)."""
        pass

    @abstractmethod
    async def skill_promote(
        self,
        name: str,
        from_scope: str,
        to_scope: str,
    ) -> None:
        """Promote skill across scopes."""
        pass

    @abstractmethod
    async def list_skills(
        self,
        namespace: Optional[str] = None,
        scope: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List skills matching criteria."""
        pass


# ============================================================================
# Part 3: NamespacePolicy — Enforce namespace ownership
# ============================================================================


@dataclass
class NamespacePolicy:
    """Enforce namespace ownership per subsystem."""

    # Subsystem namespace declarations: subsystem_name -> [namespace_patterns]
    # Pattern can be "namespace.*" (wildcard) or exact match
    subsystem_namespaces: Dict[str, List[str]] = field(
        default_factory=lambda: {
            "tool_forge": ["tool_forge.*"],
            "skill_forge": ["skill_forge.*"],
        }
    )

    # Custom namespaces: namespace -> owner_subsystem_name
    custom_namespaces: Dict[str, str] = field(default_factory=dict)

    def is_allowed(self, subsystem_name: str, namespace: str) -> bool:
        """Check if subsystem can forge in namespace.

        Args:
            subsystem_name: Name of subsystem making the request
            namespace: Namespace to forge/create in

        Returns:
            True if allowed, False otherwise
        """
        # Subsystem always owns its own namespace
        for allowed_pattern in self.subsystem_namespaces.get(subsystem_name, []):
            if allowed_pattern.endswith(".*"):
                prefix = allowed_pattern[:-2]  # Remove .*
                if namespace.startswith(prefix + ".") or namespace == prefix:
                    return True
            elif namespace == allowed_pattern:
                return True

        # Check custom namespaces
        if namespace in self.custom_namespaces:
            return self.custom_namespaces[namespace] == subsystem_name

        return False

    def auto_prefix_name(
        self, subsystem_name: str, name: str, namespace: Optional[str]
    ) -> str:
        """Auto-prefix tool/skill name with namespace.

        Args:
            subsystem_name: Name of subsystem
            name: Tool/skill name
            namespace: Optional namespace (if None, use subsystem_name)

        Returns:
            Prefixed name (e.g., "error_recovery.recover_ImportError")
        """
        ns = namespace or subsystem_name
        return f"{ns}.{name}"

    def add_custom_namespace(self, namespace: str, owner: str) -> None:
        """Register a custom namespace.

        Args:
            namespace: Namespace name
            owner: Owner subsystem name
        """
        if not namespace or "." not in namespace:
            raise ValueError(f"Invalid namespace: {namespace}")
        self.custom_namespaces[namespace] = owner
        logger.info(f"Added custom namespace: {namespace} owned by {owner}")

    def remove_custom_namespace(self, namespace: str) -> None:
        """Unregister a custom namespace.

        Args:
            namespace: Namespace name
        """
        if namespace in self.custom_namespaces:
            del self.custom_namespaces[namespace]
            logger.info(f"Removed custom namespace: {namespace}")


# ============================================================================
# Part 4: ForgeQuota — Enforce per-subsystem resource limits
# ============================================================================


@dataclass
class ForgeQuota:
    """Per-subsystem resource limits for tools and skills."""

    # Per-subsystem limits
    tool_quota: Dict[str, int] = field(default_factory=lambda: {})
    skill_quota: Dict[str, int] = field(default_factory=lambda: {})

    # Usage tracking (reset per session)
    tool_usage: Dict[str, int] = field(default_factory=lambda: {})
    skill_usage: Dict[str, int] = field(default_factory=lambda: {})

    # Defaults
    default_tool_quota: int = 10
    default_skill_quota: int = 5

    def check_tool_quota(self, subsystem_name: str) -> bool:
        """Check if subsystem can forge another tool.

        Args:
            subsystem_name: Name of subsystem

        Returns:
            True if quota available, False if exhausted
        """
        max_tools = self.tool_quota.get(subsystem_name, self.default_tool_quota)
        used = self.tool_usage.get(subsystem_name, 0)
        return used < max_tools

    def check_skill_quota(self, subsystem_name: str) -> bool:
        """Check if subsystem can create another skill.

        Args:
            subsystem_name: Name of subsystem

        Returns:
            True if quota available, False if exhausted
        """
        max_skills = self.skill_quota.get(subsystem_name, self.default_skill_quota)
        used = self.skill_usage.get(subsystem_name, 0)
        return used < max_skills

    def record_tool_forge(self, subsystem_name: str) -> None:
        """Increment tool usage counter.

        Args:
            subsystem_name: Name of subsystem
        """
        self.tool_usage[subsystem_name] = self.tool_usage.get(subsystem_name, 0) + 1

    def record_skill_create(self, subsystem_name: str) -> None:
        """Increment skill usage counter.

        Args:
            subsystem_name: Name of subsystem
        """
        self.skill_usage[subsystem_name] = self.skill_usage.get(subsystem_name, 0) + 1

    def get_tool_usage(self, subsystem_name: str) -> int:
        """Get current tool usage.

        Args:
            subsystem_name: Name of subsystem

        Returns:
            Current usage count
        """
        return self.tool_usage.get(subsystem_name, 0)

    def get_skill_usage(self, subsystem_name: str) -> int:
        """Get current skill usage.

        Args:
            subsystem_name: Name of subsystem

        Returns:
            Current usage count
        """
        return self.skill_usage.get(subsystem_name, 0)

    def set_tool_quota(self, subsystem_name: str, quota: int) -> None:
        """Set tool quota for a subsystem.

        Args:
            subsystem_name: Name of subsystem
            quota: New quota (must be > 0)

        Raises:
            ValueError: If quota <= 0
        """
        if quota <= 0:
            raise ValueError(f"Quota must be > 0, got {quota}")
        self.tool_quota[subsystem_name] = quota

    def set_skill_quota(self, subsystem_name: str, quota: int) -> None:
        """Set skill quota for a subsystem.

        Args:
            subsystem_name: Name of subsystem
            quota: New quota (must be > 0)

        Raises:
            ValueError: If quota <= 0
        """
        if quota <= 0:
            raise ValueError(f"Quota must be > 0, got {quota}")
        self.skill_quota[subsystem_name] = quota

    def reset_usage(self) -> None:
        """Reset all usage counters (call at start of session)."""
        self.tool_usage.clear()
        self.skill_usage.clear()
        logger.info("ForgeQuota: Reset all usage counters")


# ============================================================================
# Part 5: Exceptions
# ============================================================================


class PermissionDenied(Exception):
    """Namespace permission denied."""

    pass


class SandboxError(Exception):
    """Code fails sandbox/security checks."""

    pass


class QuotaExceeded(Exception):
    """Forge quota exhausted."""

    pass


class LinterError(Exception):
    """Skill markdown has prompt injection pattern."""

    pass
