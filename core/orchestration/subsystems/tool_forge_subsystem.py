"""Tool Forge subsystem: Async wrapper + request handlers for runtime tool generation (ADR-0359).

Integrates ToolForge (Layer 6) with Brain v0.2 via:
- AsyncForgeRegistry: Thread-pool async wrapper around Forge Registry
- ToolForgeSubsystem: Subsystem interface (startup, handle_request, on_event)
- 4 request handlers: forge_tool, forge_exec, forge_promote, list_tools
- Event subscriptions: forge_requested, strategy_failed, error_detected
"""

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import Subsystem
from .forge_apis import NamespacePolicy, ForgeQuota
from .forge_api_impl import ForgedToolAPIImpl
from core.context_engineering.context_api import ContextAPI

logger = logging.getLogger(__name__)


# ============================================================================
# Part 1: AsyncForgeRegistry — Async wrapper for ToolForge.Registry
# ============================================================================


class ToolSpec:
    """Simple ToolSpec wrapper (mirrors forge.registry.ToolSpec)."""

    def __init__(
        self,
        name: str,
        description: str,
        input_schema: dict,
        runtime: str,
        impl_path: str,
        scope: str = "session",
        sha256: str = "",
        call_count: int = 0,
        promoted: bool = False,
        meta: Optional[Dict[str, Any]] = None,
    ):
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.runtime = runtime
        self.impl_path = impl_path
        self.scope = scope
        self.sha256 = sha256
        self.call_count = call_count
        self.promoted = promoted
        self.meta = meta or {}

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "runtime": self.runtime,
            "impl_path": self.impl_path,
            "scope": self.scope,
            "sha256": self.sha256,
            "call_count": self.call_count,
            "promoted": self.promoted,
            "meta": self.meta,
        }


class AsyncForgeRegistry:
    """Async wrapper for Tool Forge Registry (Layer 6).

    Wraps synchronous Registry in ThreadPoolExecutor for non-blocking operations.
    """

    def __init__(self, registry: Optional[Any] = None, max_workers: int = 4):
        """Initialize async wrapper.

        Args:
            registry: Existing ToolForge Registry instance (or None for testing)
            max_workers: ThreadPoolExecutor worker count
        """
        self.registry = registry
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self._tools_cache: Dict[str, ToolSpec] = {}

    async def forge_tool(
        self,
        name: str,
        description: str,
        input_schema: dict,
        impl: str,
        runtime: str = "python",
        meta: Optional[Dict[str, Any]] = None,
    ) -> ToolSpec:
        """Forge a new tool in thread pool.

        Args:
            name: Tool name
            description: Tool description
            input_schema: JSON schema for input validation
            impl: Tool implementation source code
            runtime: 'python' or 'bash'
            meta: Optional metadata dictionary

        Returns:
            ToolSpec of the created tool

        Raises:
            PermissionDenied: If policy prevents forging
            SandboxError: If AST/security check fails
            ValueError: If input validation fails
        """
        if self.registry is None:
            # For testing: create in-memory spec
            spec = ToolSpec(
                name=name,
                description=description,
                input_schema=input_schema,
                runtime=runtime,
                impl_path=f"/tmp/{name}.{runtime[0]}",
                scope="session",
                sha256=self._compute_sha(impl),
            )
            self._tools_cache[name] = spec
            return spec

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self.executor,
            lambda: self._sync_forge_tool(
                name, description, input_schema, impl, runtime, meta
            ),
        )

    def _sync_forge_tool(
        self,
        name: str,
        description: str,
        input_schema: dict,
        impl: str,
        runtime: str,
        meta: Optional[Dict[str, Any]],
    ) -> ToolSpec:
        """Synchronous forge_tool for executor."""
        try:
            spec = self.registry.create(
                name=name,
                description=description,
                input_schema=input_schema,
                impl=impl,
                runtime=runtime,
                scope="session",
                overwrite=False,
                meta=meta,
            )
            # Convert forge.registry.ToolSpec to our ToolSpec
            return ToolSpec(
                name=spec.name,
                description=spec.description,
                input_schema=spec.input_schema,
                runtime=spec.runtime,
                impl_path=spec.impl_path,
                scope=spec.scope,
                sha256=spec.sha256,
                call_count=spec.call_count,
                promoted=spec.promoted,
                meta=spec.meta,
            )
        except Exception as e:
            logger.error(f"Failed to forge tool {name}: {e}")
            raise

    async def forge_exec(
        self, name: str, input_data: dict
    ) -> Dict[str, Any]:
        """Execute tool in sandbox.

        Args:
            name: Tool name
            input_data: Input data matching the tool's input_schema

        Returns:
            Execution result dictionary

        Raises:
            ValueError: If tool not found or input validation fails
            Exception: If execution fails
        """
        if self.registry is None:
            # For testing: check cache first
            if name not in self._tools_cache:
                raise ValueError(f"Tool {name} not found")
            # Mock execution
            return {
                "success": True,
                "output": {"result": f"mock output for {name}"},
                "execution_time_ms": 10.5,
            }

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self.executor,
            lambda: self._sync_forge_exec(name, input_data),
        )

    def _sync_forge_exec(self, name: str, input_data: dict) -> Dict[str, Any]:
        """Synchronous forge_exec for executor."""
        try:
            from forge.runner import run_tool

            spec = self.registry.get(name)
            if not spec:
                raise ValueError(f"Tool {name} not found")

            result = run_tool(
                self.registry,
                name,
                input_data,
                timeout=30,
                permission_mode="yes",
                use_sandbox=True,
            )
            return result.to_dict()
        except Exception as e:
            logger.error(f"Failed to execute tool {name}: {e}")
            raise

    async def forge_promote(
        self, name: str, from_scope: str, to_scope: str
    ) -> None:
        """Move tool to next scope.

        Args:
            name: Tool name
            from_scope: Current scope (session/project/user)
            to_scope: Target scope

        Raises:
            ValueError: If tool not found or scope invalid
        """
        if self.registry is None:
            # For testing: just validate
            if name not in self._tools_cache:
                raise ValueError(f"Tool {name} not found")
            self._tools_cache[name].scope = to_scope
            return

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self.executor,
            lambda: self._sync_forge_promote(name, from_scope, to_scope),
        )

    def _sync_forge_promote(
        self, name: str, from_scope: str, to_scope: str
    ) -> None:
        """Synchronous forge_promote for executor."""
        try:
            spec = self.registry.get(name)
            if not spec:
                raise ValueError(f"Tool {name} not found")
            # Update scope in registry
            from dataclasses import replace
            updated = replace(spec, scope=to_scope)
            # Write back to manifest
            data = self.registry._load()
            from dataclasses import asdict
            data[name] = asdict(updated)
            self.registry._save(data)
        except Exception as e:
            logger.error(f"Failed to promote tool {name}: {e}")
            raise

    async def list_tools(
        self,
        namespace: Optional[str] = None,
        scope: Optional[str] = None,
    ) -> List[ToolSpec]:
        """List tools matching criteria.

        Args:
            namespace: Filter by namespace (prefix before first dot)
            scope: Filter by scope (session/project/user)

        Returns:
            List of ToolSpec objects
        """
        if self.registry is None:
            # For testing: return cache
            tools = list(self._tools_cache.values())
        else:
            loop = asyncio.get_event_loop()
            tools = await loop.run_in_executor(
                self.executor,
                lambda: self._sync_list_tools(namespace, scope),
            )

        # Apply filters
        result = []
        for tool in tools:
            if namespace and not tool.name.startswith(namespace + "."):
                continue
            if scope and tool.scope != scope:
                continue
            result.append(tool)

        return result

    def _sync_list_tools(
        self,
        namespace: Optional[str],
        scope: Optional[str],
    ) -> List[ToolSpec]:
        """Synchronous list_tools for executor."""
        try:
            tools = self.registry.list()
            # Convert to our ToolSpec
            return [
                ToolSpec(
                    name=t.name,
                    description=t.description,
                    input_schema=t.input_schema,
                    runtime=t.runtime,
                    impl_path=t.impl_path,
                    scope=t.scope,
                    sha256=t.sha256,
                    call_count=t.call_count,
                    promoted=t.promoted,
                    meta=t.meta,
                )
                for t in tools
            ]
        except Exception as e:
            logger.error(f"Failed to list tools: {e}")
            raise

    @staticmethod
    def _compute_sha(impl: str) -> str:
        """Compute SHA256 of implementation."""
        import hashlib
        return hashlib.sha256(impl.encode()).hexdigest()[:16]

    def shutdown(self) -> None:
        """Shutdown thread pool executor."""
        self.executor.shutdown(wait=True)


# ============================================================================
# Part 2: ToolForgeSubsystem — Brain subsystem interface
# ============================================================================


class ToolForgeSubsystem(Subsystem):
    """Tool Forge subsystem: Async wrapper + request handlers for runtime tool generation.

    Implements Subsystem interface from ADR-0349:
    - startup(): Initialize AsyncForgeRegistry, inject ContextAPI
    - handle_request(): Route forge_tool, forge_exec, forge_promote, list_tools
    - on_event(): React to forge_requested, strategy_failed, error_detected
    - shutdown(): Clean up resources
    """

    def __init__(
        self,
        forge_registry: Optional[Any] = None,
        max_workers: int = 4,
        namespace_policy: Optional[NamespacePolicy] = None,
        forge_quota: Optional[ForgeQuota] = None,
    ):
        """Initialize ToolForgeSubsystem.

        Args:
            forge_registry: Existing ToolForge Registry instance
            max_workers: ThreadPoolExecutor worker count
            namespace_policy: NamespacePolicy for validation (created if None)
            forge_quota: ForgeQuota for limits (created if None)
        """
        self.forge_registry = forge_registry
        self.max_workers = max_workers
        self.async_registry: Optional[AsyncForgeRegistry] = None
        self.context_api: Optional[ContextAPI] = None
        self.hub: Optional[Any] = None
        self.forged_tools: Dict[str, ToolSpec] = {}
        self.last_forge_timestamp: Optional[str] = None

        # ADR-0361: Policy and quota
        self.namespace_policy = namespace_policy or NamespacePolicy()
        self.forge_quota = forge_quota or ForgeQuota()

    @property
    def name(self) -> str:
        return "tool_forge"

    @property
    def version(self) -> str:
        return "0.1.0"

    def startup(self, hub: Any) -> None:
        """Initialize subsystem and subscribe to events.

        Args:
            hub: SubsystemHub instance
        """
        self.hub = hub

        # Create AsyncForgeRegistry wrapper
        self.async_registry = AsyncForgeRegistry(
            registry=self.forge_registry,
            max_workers=self.max_workers,
        )

        # Inject ContextAPI for decision recording
        try:
            self.context_api = ContextAPI(self.name, hub.context_bus)
            logger.info("ToolForgeSubsystem: ContextAPI injected")
        except Exception as e:
            logger.warning(f"ToolForgeSubsystem: Failed to inject ContextAPI: {e}")
            self.context_api = None

        # ADR-0361: Register ForgedToolAPI for loose coupling
        try:
            api_impl = ForgedToolAPIImpl(
                subsystem=self,
                namespace_policy=self.namespace_policy,
                quota=self.forge_quota,
            )
            hub.register_api("forged_tool", api_impl)
            logger.info("ToolForgeSubsystem: ForgedToolAPI registered")
        except Exception as e:
            logger.warning(f"ToolForgeSubsystem: Failed to register API: {e}")

        # Subscribe to events
        hub.subscribe("forge_requested", self.on_forge_requested)
        hub.subscribe("strategy_failed", self.on_strategy_failed)
        hub.subscribe("error_detected", self.on_error_detected)

        logger.info(f"ToolForgeSubsystem started (workers={self.max_workers})")

    async def handle_request(self, request_type: str, **kwargs) -> Any:
        """Process requests: forge_tool, forge_exec, forge_promote, list_tools.

        Args:
            request_type: Type of request ('forge_tool', 'forge_exec', etc.)
            **kwargs: Request-specific parameters (name, description, etc.)

        Returns:
            Response dictionary specific to the request type

        Raises:
            ValueError: If request_type is unknown
        """
        match request_type:
            case "forge_tool":
                return await self._forge_tool(kwargs)
            case "forge_exec":
                return await self._forge_exec(kwargs)
            case "forge_promote":
                return await self._forge_promote(kwargs)
            case "list_tools":
                return await self._list_tools(kwargs)
            case _:
                raise ValueError(f"Unknown request type: {request_type}")

    async def _forge_tool(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Handle forge_tool request.

        Request schema:
        {
            name: str,
            description: str,
            input_schema: dict,
            impl: str,
            namespace: str | None,
            runtime: str = "python",
        }

        Response:
        {
            tool_spec: ToolSpec,
            cost_units: float,
            created_at: str,
        }
        """
        try:
            # Estimate cost
            impl = payload.get("impl", "")
            cost = self._estimate_forge_cost(impl)

            # Record decision (best effort)
            self._safe_record_decision(
                decision_type="forge_cost_estimate",
                value=str(cost),
                reasoning=f"Tool {payload['name']} size {len(impl)} chars",
                confidence=0.9,
            )

            # Forge tool
            tool_spec = await self.async_registry.forge_tool(
                name=payload["name"],
                description=payload["description"],
                input_schema=payload["input_schema"],
                impl=impl,
                runtime=payload.get("runtime", "python"),
            )

            # Store in cache
            self.forged_tools[tool_spec.name] = tool_spec
            self.last_forge_timestamp = datetime.now().isoformat()

            # Publish event
            self.publish_event(
                "tool_forged",
                {
                    "name": tool_spec.name,
                    "namespace": payload.get("namespace"),
                    "cost_units": cost,
                    "created_at": self.last_forge_timestamp,
                },
            )

            # Record success (best effort)
            self._safe_record_decision(
                decision_type="tool_forged",
                value=tool_spec.name,
                reasoning=f"Successfully forged tool for {payload['name']}",
                confidence=1.0,
            )

            return {
                "tool_spec": tool_spec.to_dict(),
                "cost_units": cost,
                "created_at": self.last_forge_timestamp,
            }

        except Exception as e:
            logger.error(f"Failed to forge tool: {e}")
            self._safe_record_decision(
                decision_type="forge_failed",
                value=str(e),
                reasoning=f"Error forging tool {payload.get('name')}: {e}",
                confidence=0.0,
            )
            raise

    async def _forge_exec(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Handle forge_exec request.

        Request schema:
        {
            name: str,
            input_data: dict,
        }

        Response:
        {
            output: dict,
            execution_time_ms: float,
        }
        """
        tool_name = payload["name"]
        start = time.time()

        try:
            output = await self.async_registry.forge_exec(
                tool_name,
                payload["input_data"],
            )
            elapsed_ms = (time.time() - start) * 1000

            # Record decision (best effort)
            self._safe_record_decision(
                decision_type="tool_executed",
                value=tool_name,
                reasoning=f"Executed in {elapsed_ms:.1f}ms",
                confidence=1.0,
            )

            # Publish event
            self.publish_event(
                "tool_executed",
                {
                    "name": tool_name,
                    "success": True,
                    "execution_time_ms": elapsed_ms,
                },
            )

            return {
                "output": output,
                "execution_time_ms": elapsed_ms,
            }

        except Exception as e:
            elapsed_ms = (time.time() - start) * 1000
            logger.error(f"Failed to execute tool {tool_name}: {e}")

            # Publish event
            self.publish_event(
                "tool_executed",
                {
                    "name": tool_name,
                    "success": False,
                    "error": str(e),
                    "execution_time_ms": elapsed_ms,
                },
            )
            raise

    async def _forge_promote(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Handle forge_promote request.

        Request schema:
        {
            name: str,
            from_scope: str,
            to_scope: str,
        }

        Response:
        {
            success: bool,
            message: str,
        }
        """
        tool_name = payload["name"]
        from_scope = payload["from_scope"]
        to_scope = payload["to_scope"]

        try:
            await self.async_registry.forge_promote(
                tool_name, from_scope, to_scope
            )

            # Record decision (best effort)
            self._safe_record_decision(
                decision_type="tool_promoted",
                value=f"{tool_name}: {from_scope} → {to_scope}",
                reasoning=f"Tool {tool_name} promoted by learning engine",
                confidence=0.95,
            )

            # Publish event
            self.publish_event(
                "tool_promoted",
                {
                    "name": tool_name,
                    "from_scope": from_scope,
                    "to_scope": to_scope,
                },
            )

            return {
                "success": True,
                "message": f"Tool {tool_name} promoted to {to_scope}",
            }

        except Exception as e:
            logger.error(f"Failed to promote tool {tool_name}: {e}")
            raise

    async def _list_tools(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Handle list_tools request.

        Request schema:
        {
            namespace: str | None,
            scope: str | None,
            limit: int = 100,
        }

        Response:
        {
            tools: list[dict],
            count: int,
        }
        """
        tools = await self.async_registry.list_tools(
            namespace=payload.get("namespace"),
            scope=payload.get("scope"),
        )

        limited = tools[: payload.get("limit", 100)]
        return {
            "tools": [t.to_dict() for t in limited],
            "count": len(tools),
        }

    async def on_event(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """React to published events (fire-and-forget).

        Args:
            event_name: Event type
            event_data: Event payload
        """
        if event_name == "forge_requested":
            await self.on_forge_requested(event_name, event_data)
        elif event_name == "strategy_failed":
            await self.on_strategy_failed(event_name, event_data)
        elif event_name == "error_detected":
            await self.on_error_detected(event_name, event_data)

    async def on_forge_requested(
        self, event_name: str, event_data: Dict[str, Any]
    ) -> None:
        """User/Brain requests tool generation.

        Usually triggered by GuidanceClassifier or MidstreamRouter.
        """
        try:
            payload = {
                "name": event_data["tool_name"],
                "description": event_data["description"],
                "input_schema": event_data["input_schema"],
                "impl": event_data["implementation"],
                "namespace": event_data.get("namespace"),
            }
            await self.handle_request("forge_tool", **payload)
        except Exception as e:
            logger.error(f"on_forge_requested failed: {e}")

    async def on_strategy_failed(
        self, event_name: str, event_data: Dict[str, Any]
    ) -> None:
        """Strategy failed; might need recovery tool."""
        try:
            error_type = event_data.get("error_type", "unknown")
            self._safe_record_decision(
                decision_type="error_recovery_needed",
                value=error_type,
                reasoning="Strategy failed; forging recovery tool",
                confidence=0.7,
            )
        except Exception as e:
            logger.error(f"on_strategy_failed failed: {e}")

    async def on_error_detected(
        self, event_name: str, event_data: Dict[str, Any]
    ) -> None:
        """Error detected; forge error-specific tool."""
        try:
            error_type = event_data.get("error_type", "unknown")
            logger.info(f"Error detected: {error_type}, recording for recovery")
        except Exception as e:
            logger.error(f"on_error_detected failed: {e}")

    def shutdown(self) -> None:
        """Cleanup resources."""
        if self.async_registry:
            self.async_registry.shutdown()
        logger.info("ToolForgeSubsystem shut down")

    def _safe_record_decision(
        self,
        decision_type: str,
        value: str,
        reasoning: str = "",
        confidence: float = 0.5,
    ) -> None:
        """Record decision safely, ignoring errors if ContextAPI not available.

        Args:
            decision_type: Type of decision
            value: Decision value
            reasoning: Reasoning for the decision
            confidence: Confidence level (0.0-1.0)
        """
        if not self.context_api:
            return

        try:
            self.context_api.record_decision(
                decision_type=decision_type,
                value=value,
                reasoning=reasoning,
                confidence=confidence,
            )
        except Exception as e:
            logger.debug(f"Failed to record decision: {e}")

    @staticmethod
    def _estimate_forge_cost(impl: str) -> float:
        """Estimate cost of forging a tool.

        Simple heuristic: 1 cost unit per 1000 characters.
        """
        return max(1.0, len(impl) / 1000.0)
