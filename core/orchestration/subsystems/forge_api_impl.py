"""Concrete implementations of ForgedToolAPI and ForgedSkillAPI (ADR-0361).

ForgedToolAPIImpl delegates to ToolForgeSubsystem.
ForgedSkillAPIImpl delegates to SkillForgeSubsystem.

Both enforce namespace policy and quota limits.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from .forge_apis import (
    ForgedToolAPI,
    ForgedSkillAPI,
    NamespacePolicy,
    ForgeQuota,
    PermissionDenied,
    QuotaExceeded,
)

logger = logging.getLogger(__name__)


class ForgedToolAPIImpl(ForgedToolAPI):
    """Concrete implementation of ForgedToolAPI.

    Delegates to ToolForgeSubsystem while enforcing namespace policy and quota.
    """

    def __init__(
        self,
        subsystem: Any,  # ToolForgeSubsystem
        namespace_policy: NamespacePolicy,
        quota: ForgeQuota,
    ):
        """Initialize ForgedToolAPIImpl.

        Args:
            subsystem: ToolForgeSubsystem instance
            namespace_policy: NamespacePolicy instance
            quota: ForgeQuota instance
        """
        self.subsystem = subsystem
        self.namespace_policy = namespace_policy
        self.quota = quota

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
        """Generate a tool with namespace and quota enforcement.

        Args:
            name: Tool name
            description: Tool description
            input_schema: JSON schema for inputs
            impl: Implementation code
            runtime: 'python' or 'bash'
            meta: Optional metadata dict
            namespace: Namespace override

        Returns: {
            'tool_spec': dict,
            'cost_units': float,
            'namespace': str,
            'created_at': str,
        }

        Raises:
            PermissionDenied: namespace not allowed
            QuotaExceeded: forge quota exhausted
        """
        subsystem_name = self.subsystem.name

        # Check namespace policy
        ns = namespace or subsystem_name
        if not self.namespace_policy.is_allowed(subsystem_name, ns):
            raise PermissionDenied(f"Namespace {ns} not allowed for {subsystem_name}")

        # Check quota
        if not self.quota.check_tool_quota(subsystem_name):
            used = self.quota.get_tool_usage(subsystem_name)
            max_tools = self.quota.tool_quota.get(
                subsystem_name, self.quota.default_tool_quota
            )
            raise QuotaExceeded(
                f"Tool quota exceeded for {subsystem_name} ({used}/{max_tools})"
            )

        # Auto-prefix name
        prefixed_name = self.namespace_policy.auto_prefix_name(
            subsystem_name, name, namespace
        )

        # Delegate to subsystem
        result = await self.subsystem._forge_tool(
            {
                "name": prefixed_name,
                "description": description,
                "input_schema": input_schema,
                "impl": impl,
                "runtime": runtime,
                "meta": meta,
                "namespace": ns,
            }
        )

        # Record quota usage
        self.quota.record_tool_forge(subsystem_name)

        return result

    async def forge_exec(
        self,
        name: str,
        input_data: dict,
    ) -> Dict[str, Any]:
        """Execute a forged tool.

        Args:
            name: Tool name
            input_data: Input data matching tool's input_schema

        Returns:
            Execution result dictionary
        """
        return await self.subsystem._forge_exec(
            {
                "name": name,
                "input_data": input_data,
            }
        )

    async def forge_promote(
        self,
        name: str,
        from_scope: str,
        to_scope: str,
    ) -> None:
        """Promote tool across scopes.

        Args:
            name: Tool name
            from_scope: Current scope
            to_scope: Target scope
        """
        await self.subsystem._forge_promote(
            {
                "name": name,
                "from_scope": from_scope,
                "to_scope": to_scope,
            }
        )

    async def list_tools(
        self,
        namespace: Optional[str] = None,
        scope: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List tools matching criteria.

        Args:
            namespace: Filter by namespace
            scope: Filter by scope

        Returns:
            List of tool dicts
        """
        result = await self.subsystem._list_tools(
            {
                "namespace": namespace,
                "scope": scope,
            }
        )
        return result.get("tools", [])


class ForgedSkillAPIImpl(ForgedSkillAPI):
    """Concrete implementation of ForgedSkillAPI.

    Delegates to SkillForgeSubsystem while enforcing namespace policy and quota.
    """

    def __init__(
        self,
        subsystem: Any,  # SkillForgeSubsystem
        namespace_policy: NamespacePolicy,
        quota: ForgeQuota,
    ):
        """Initialize ForgedSkillAPIImpl.

        Args:
            subsystem: SkillForgeSubsystem instance
            namespace_policy: NamespacePolicy instance
            quota: ForgeQuota instance
        """
        self.subsystem = subsystem
        self.namespace_policy = namespace_policy
        self.quota = quota

    async def skill_create(
        self,
        name: str,
        body_md: str,
        description: Optional[str] = None,
        skill_type: str = "learned-experience",
        claim: Optional[dict] = None,
        namespace: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a skill with namespace and quota enforcement.

        Args:
            name: Skill name
            body_md: Markdown body
            description: Short description
            skill_type: Type of skill
            claim: Optional claim dict
            namespace: Namespace override

        Returns: {
            'skill_record': dict,
            'created_at': str,
            'namespace': str,
        }

        Raises:
            PermissionDenied: namespace not allowed
            QuotaExceeded: skill creation quota exhausted
        """
        subsystem_name = self.subsystem.name

        # Check namespace policy
        ns = namespace or subsystem_name
        if not self.namespace_policy.is_allowed(subsystem_name, ns):
            raise PermissionDenied(f"Namespace {ns} not allowed for {subsystem_name}")

        # Check quota
        if not self.quota.check_skill_quota(subsystem_name):
            used = self.quota.get_skill_usage(subsystem_name)
            max_skills = self.quota.skill_quota.get(
                subsystem_name, self.quota.default_skill_quota
            )
            raise QuotaExceeded(
                f"Skill quota exceeded for {subsystem_name} ({used}/{max_skills})"
            )

        # Auto-prefix name
        prefixed_name = self.namespace_policy.auto_prefix_name(
            subsystem_name, name, namespace
        )

        # Delegate to subsystem
        result = await self.subsystem.async_registry.skill_create(
            name=prefixed_name,
            body_md=body_md,
            description=description or f"Skill: {prefixed_name}",
            skill_type=skill_type,
            claim=claim,
        )

        # Record quota usage
        self.quota.record_skill_create(subsystem_name)

        # Add metadata
        if isinstance(result, dict):
            result["namespace"] = ns
            result["created_at"] = datetime.now().isoformat()

        return result

    async def skill_grade(
        self,
        name: str,
        score: float,
        feedback: Optional[str] = None,
    ) -> None:
        """Grade a skill.

        Args:
            name: Skill name
            score: Score 0.0-1.0
            feedback: Optional feedback
        """
        await self.subsystem.async_registry.skill_grade(
            name=name,
            run_id=f"manual_grade_{datetime.now().isoformat()}",
            score=score,
            notes=feedback or "",
        )

    async def skill_promote(
        self,
        name: str,
        from_scope: str,
        to_scope: str,
    ) -> None:
        """Promote skill across scopes.

        Args:
            name: Skill name
            from_scope: Current scope
            to_scope: Target scope
        """
        await self.subsystem.async_registry.skill_promote(
            name=name,
            from_scope=from_scope,
            to_scope=to_scope,
        )

    async def list_skills(
        self,
        namespace: Optional[str] = None,
        scope: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List skills matching criteria.

        Args:
            namespace: Filter by namespace
            scope: Filter by scope

        Returns:
            List of skill dicts
        """
        skills = await self.subsystem.async_registry.list_skills(
            namespace=namespace,
            scope=scope,
        )
        return skills
