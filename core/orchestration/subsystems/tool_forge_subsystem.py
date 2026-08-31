"""Tool Forge subsystem: Async wrapper + request handlers for runtime tool generation (ADR-0359).

Integrates ToolForge (Layer 6) with Brain v0.2 via:
- AsyncForgeRegistry: Thread-pool async wrapper around Forge Registry
- ToolForgeSubsystem: Subsystem interface (startup, handle_request, on_event)
- 4 request handlers: forge_tool, forge_exec, forge_promote, list_tools
- Event subscriptions: forge_requested, strategy_failed, error_detected
- Learning integration: Emits TOOL_EXECUTED events to EventEmitter (ADR-0321, Gap 1)
"""

import asyncio
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Type alias for tool IDs
ToolID = str

from .base import Subsystem
from .forge_apis import NamespacePolicy, ForgeQuota
from .forge_api_impl import ForgedToolAPIImpl
from core.context_engineering.context_api import ContextAPI
from core.learning.event_schema import LearningEvent, LearningEventType, ToolExecutedPayload
from core.learning.tool_ranking import ToolRankingManager, select_tool_for_reuse
from core.learning.event_store import EventStore

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

    async def register(
        self,
        tool_id: str,
        tool_spec: ToolSpec,
    ) -> ToolID:
        """Register a tool spec in the registry (public API for Brain).

        Args:
            tool_id: Unique tool ID for registration
            tool_spec: ToolSpec object to register

        Returns:
            ToolID string (same as tool_id on success)

        Raises:
            ValueError: If registration fails
        """
        if self.registry is None:
            # For testing: register in memory cache
            self._tools_cache[tool_spec.name] = tool_spec
            logger.info(f"Tool registered (in-memory): {tool_spec.name}")
            return tool_id

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self.executor,
            lambda: self._sync_register(tool_id, tool_spec),
        )

    def _sync_register(
        self,
        tool_id: str,
        tool_spec: ToolSpec,
    ) -> str:
        """Synchronous register for executor."""
        try:
            # Write tool spec to registry manifest
            # For now: store in cache (production: write to filesystem)
            self._tools_cache[tool_spec.name] = tool_spec
            logger.info(f"Tool registered: {tool_spec.name} (id={tool_id})")
            return tool_id
        except Exception as e:
            logger.error(f"Failed to register tool {tool_spec.name}: {e}")
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

    Phase C: Tenant-native persistence via ExecutionContext.tenant_id
    """

    def __init__(
        self,
        context: Optional[Any] = None,
        forge_registry: Optional[Any] = None,
        max_workers: int = 4,
        namespace_policy: Optional[NamespacePolicy] = None,
        forge_quota: Optional[ForgeQuota] = None,
        tenant_id: str = "_default",
    ):
        """Initialize ToolForgeSubsystem.

        Args:
            context: ExecutionContext (Phase C) with tenant_id for tenant-scoped operations
            forge_registry: Existing ToolForge Registry instance
            max_workers: ThreadPoolExecutor worker count
            namespace_policy: NamespacePolicy for validation (created if None)
            forge_quota: ForgeQuota for limits (created if None)
            tenant_id: Tenant ID for learning event isolation (fallback if context is None)
        """
        # Phase C: Store ExecutionContext for tenant-native operations
        self.context = context
        self.tenant_id = context.tenant_id if context else tenant_id

        self.forge_registry = forge_registry
        self.max_workers = max_workers
        self.async_registry: Optional[AsyncForgeRegistry] = None
        self.context_api: Optional[ContextAPI] = None
        self.hub: Optional[Any] = None
        self.forged_tools: Dict[str, ToolSpec] = {}
        self.last_forge_timestamp: Optional[str] = None

        # ADR-0361: Policy and quota (per-tenant)
        self.namespace_policy = namespace_policy or NamespacePolicy()
        self.forge_quota = forge_quota or ForgeQuota()

        # ADR-0321: Learning event emission (Gap 1) — per-tenant
        self.event_emitter: Optional[Any] = None
        self.instance_id = "tool_forge_subsystem"

        # ADR-0322: Tool ranking for reuse (Gap 2) — per-tenant
        self.event_store: Optional[EventStore] = None
        self.ranking_manager: Optional[ToolRankingManager] = None

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

        # ADR-0321: Initialize EventEmitter for learning event emission (Gap 1)
        try:
            # Try to get event_emitter from hub (if available)
            if hasattr(hub, 'get_service') and callable(hub.get_service):
                self.event_emitter = hub.get_service('event_emitter')
                if self.event_emitter:
                    logger.info("ToolForgeSubsystem: EventEmitter injected from hub")

            # Fallback: Create EventEmitter if not available from hub
            if not self.event_emitter:
                from core.learning.event_emitter import EventEmitter
                corvin_home = Path.home() / ".corvin"
                self.event_emitter = EventEmitter(corvin_home, self.tenant_id)
                logger.info("ToolForgeSubsystem: EventEmitter created locally")
        except Exception as e:
            logger.warning(f"ToolForgeSubsystem: Failed to initialize EventEmitter: {e}")
            self.event_emitter = None

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

        # ADR-0322: Initialize ToolRankingManager for tool reuse (Gap 2)
        try:
            # Get or create EventStore
            if hasattr(hub, 'get_service') and callable(hub.get_service):
                self.event_store = hub.get_service('event_store')

            if not self.event_store:
                corvin_home = Path.home() / ".corvin"
                db_path = corvin_home / "tenants" / self.tenant_id / "learning.db"
                self.event_store = EventStore(db_path)
                logger.info("ToolForgeSubsystem: EventStore created locally")

            # Initialize ToolRankingManager
            self.ranking_manager = ToolRankingManager(
                event_store=self.event_store,
                cache_ttl_seconds=300,
            )
            logger.info("ToolForgeSubsystem: ToolRankingManager initialized (Gap 2)")
        except Exception as e:
            logger.warning(f"ToolForgeSubsystem: Failed to initialize ToolRankingManager: {e}")
            self.ranking_manager = None

        # Subscribe to events
        hub.subscribe("forge_requested", self.on_forge_requested)
        hub.subscribe("strategy_failed", self.on_strategy_failed)
        hub.subscribe("error_detected", self.on_error_detected)
        hub.subscribe("operator_rated_tool", self.on_operator_rated_tool)

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
            case "select_tool":
                return await self._select_tool(kwargs)
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

        Raises:
            LicenseLimitError: If daily tool_forge quota exceeded.
        """
        try:
            # ADR-0365: Enforce tool_forge_per_day quota
            from pathlib import Path
            from core.orchestration.quota_gate import increment_and_check
            # Use _default tenant if not available from context
            tenant_id = getattr(self, 'tenant_id', '_default')
            # corvin_home resolved by the gate (honours CORVIN_HOME); hard-coding
            # Path.home() counted quota in a root the install may never read.
            increment_and_check(None, "tool_forge_per_day", tenant_id)

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
            task_id: str | None,
            turn_id: str | None,
            session_id: str | None,
        }

        Response:
        {
            output: dict,
            execution_time_ms: float,
        }

        Emits TOOL_EXECUTED learning events to EventEmitter (ADR-0321, Gap 1).
        """
        tool_name = payload["name"]
        start = time.time()
        start_dt = datetime.utcnow()

        # Extract execution context from payload
        task_id = payload.get("task_id", "unknown")
        turn_id = payload.get("turn_id")
        session_id = payload.get("session_id", "unknown")

        try:
            output = await self.async_registry.forge_exec(
                tool_name,
                payload["input_data"],
            )
            elapsed_ms = (time.time() - start) * 1000
            end_dt = datetime.utcnow()

            # Record decision (best effort)
            self._safe_record_decision(
                decision_type="tool_executed",
                value=tool_name,
                reasoning=f"Executed in {elapsed_ms:.1f}ms",
                confidence=1.0,
            )

            # Publish event (backward compatibility)
            self.publish_event(
                "tool_executed",
                {
                    "name": tool_name,
                    "success": True,
                    "execution_time_ms": elapsed_ms,
                },
            )

            # Emit learning event (ADR-0321, Gap 1)
            await self._emit_tool_executed_event(
                tool_name=tool_name,
                task_id=task_id,
                turn_id=turn_id,
                session_id=session_id,
                status="success",
                latency_ms=int(elapsed_ms),
                output=output,
                error=None,
                start_dt=start_dt,
                end_dt=end_dt,
            )

            return {
                "output": output,
                "execution_time_ms": elapsed_ms,
            }

        except Exception as e:
            elapsed_ms = (time.time() - start) * 1000
            end_dt = datetime.utcnow()
            logger.error(f"Failed to execute tool {tool_name}: {e}")

            # Classify error
            error_type, error_class = self._classify_error(e)
            sanitized_error = self._sanitize_error_message(str(e))

            # Publish event (backward compatibility)
            self.publish_event(
                "tool_executed",
                {
                    "name": tool_name,
                    "success": False,
                    "error": sanitized_error,
                    "execution_time_ms": elapsed_ms,
                },
            )

            # Emit learning event (ADR-0321, Gap 1)
            await self._emit_tool_executed_event(
                tool_name=tool_name,
                task_id=task_id,
                turn_id=turn_id,
                session_id=session_id,
                status="failure",
                latency_ms=int(elapsed_ms),
                output=None,
                error=sanitized_error,
                error_type=error_type,
                error_class=error_class,
                start_dt=start_dt,
                end_dt=end_dt,
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

    async def _select_tool(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Handle select_tool request (ADR-0322, Gap 2).

        Uses tool ranking to decide whether to reuse or generate a new tool.

        Request schema:
        {
            task_type: str | None,
            error_class: str | None,
            reuse_threshold: float = 0.7,
            limit: int = 5,
        }

        Response:
        {
            action: "reuse" | "generate",
            tool_id: str | None (if action="reuse"),
            ranked_tools: list[RankedTool],
            reason: str,
        }
        """
        if not self.ranking_manager:
            # ToolRankingManager not initialized; fall back to generate
            logger.warning("ToolForgeSubsystem: ToolRankingManager not available; falling back to generate")
            return {
                "action": "generate",
                "tool_id": None,
                "ranked_tools": [],
                "reason": "Tool ranking unavailable; generating new tool",
            }

        try:
            # Use ranking manager to make selection decision
            selection = await select_tool_for_reuse(
                ranking_manager=self.ranking_manager,
                tenant_id=self.tenant_id,
                task_type=payload.get("task_type"),
                error_class=payload.get("error_class"),
                reuse_threshold=payload.get("reuse_threshold", 0.7),
            )

            # Convert RankedTool objects to dictionaries for serialization
            ranked_tools_dict = []
            if selection.get("ranked_tools"):
                for rt in selection["ranked_tools"]:
                    ranked_tools_dict.append({
                        "tool_id": rt.tool_id,
                        "tool_name": rt.tool_name,
                        "score": rt.score,
                        "reason": rt.reason,
                        "success_rate": rt.success_rate,
                        "success_count": rt.success_count,
                        "total_count": rt.total_count,
                        "avg_latency_ms": rt.avg_latency_ms,
                        "p95_latency_ms": rt.p95_latency_ms,
                        "avg_cost_cents": rt.avg_cost_cents,
                        "confidence": rt.confidence,
                        "trend": rt.trend,
                        "is_cold_start": rt.is_cold_start,
                        "rank": rt.rank,
                    })

            return {
                "action": selection["action"],
                "tool_id": selection.get("tool_id"),
                "ranked_tools": ranked_tools_dict[:payload.get("limit", 5)],
                "reason": selection["reason"],
            }

        except Exception as e:
            logger.error(f"Tool selection failed: {e}", exc_info=True)
            return {
                "action": "generate",
                "tool_id": None,
                "ranked_tools": [],
                "reason": f"Tool selection error: {e}; generating new tool",
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

    async def on_operator_rated_tool(
        self, event_name: str, event_data: Dict[str, Any]
    ) -> None:
        """Handle operator rating of a tool (ADR-0321, Gap 1 future work).

        Usually triggered by rating UI or feedback collection.
        """
        try:
            tool_id = event_data.get("tool_id")
            rating = event_data.get("rating")
            feedback = event_data.get("feedback")
            session_id = event_data.get("session_id", "unknown")

            logger.debug(f"Tool {tool_id} rated {rating}/5: {feedback}")

            # Emit operator rating event (future: populate user_satisfaction field)
            # This will be used in Gap 7 for feedback loop integration
        except Exception as e:
            logger.error(f"on_operator_rated_tool failed: {e}")

    async def _emit_tool_executed_event(
        self,
        tool_name: str,
        task_id: str,
        turn_id: Optional[str],
        session_id: str,
        status: str,  # "success" | "failure" | "timeout" | "error"
        latency_ms: int,
        output: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
        error_type: Optional[str] = None,
        error_class: Optional[str] = None,
        start_dt: Optional[datetime] = None,
        end_dt: Optional[datetime] = None,
    ) -> None:
        """Emit TOOL_EXECUTED learning event to EventEmitter (ADR-0321, Gap 1).

        Wraps telemetry in LearningEvent and queues for async emission.
        Non-blocking: if queue is full, event is silently dropped.

        Args:
            tool_name: Name of executed tool
            task_id: Task ID from ExecutionContext
            turn_id: Turn ID from execution payload
            session_id: Session ID for learning context
            status: Execution status (success, failure, timeout, error)
            latency_ms: Execution latency in milliseconds
            output: Tool output (on success)
            error: Sanitized error message (on failure)
            error_type: Error classification (ValueError, TimeoutError, etc.)
            error_class: Error class for aggregation
            start_dt: Execution start timestamp
            end_dt: Execution end timestamp
        """
        if not self.event_emitter:
            logger.debug("EventEmitter not available, skipping learning event emission")
            return

        try:
            # Calculate estimated cost
            estimated_cost_cents = self._calculate_execution_cost(latency_ms)

            # Create telemetry payload
            payload = ToolExecutedPayload(
                tool_id=tool_name,  # Use tool name as ID (production: use internal ID)
                tool_name=tool_name,
                tool_type="generated",  # "generated" | "promoted" | "builtin"
                status=status,
                latency_ms=latency_ms,
                input_tokens=0,  # Future: extract from CostController
                output_tokens=0,  # Future: extract from CostController
                subsystem_tokens={},  # Future: token breakdown by subsystem
                estimated_cost_cents=estimated_cost_cents,
                error_type=error_type,
                error_message=error,
                error_class=error_class,
                user_satisfaction=-1,  # -1 = not available (Gap 7 will populate)
                required_followup=False,  # Future: operator feedback loop (Gap 7)
                error_resolved=None,  # Future: outcome signal
                model_id="claude-opus-5",
                task_type=None,  # Future: infer from task_id
                task_id=task_id,
                turn_id=turn_id,
                tags=[],  # Future: add tags like ["high_latency", "cost_overrun"]
            )

            # Create learning event
            event = LearningEvent(
                event_type=LearningEventType.TOOL_EXECUTED,
                tenant_id=self.tenant_id,
                instance_id=self.instance_id,
                skill_name="tool_forge",
                session_id=session_id,
                timestamp_utc=datetime.utcnow(),
                payload=payload.__dict__,  # Convert dataclass to dict
            )

            # Emit event (async, non-blocking)
            await self.event_emitter.emit(event)
            logger.debug(
                f"TOOL_EXECUTED event emitted: {tool_name} ({status}) in {latency_ms}ms"
            )

        except Exception as e:
            # Log but don't fail: learning events are optional
            logger.warning(f"Failed to emit TOOL_EXECUTED event: {e}")

    def _calculate_execution_cost(self, latency_ms: int) -> int:
        """Calculate estimated cost of tool execution in cents.

        Heuristic: ~0.01 cents per millisecond (adjustable per SLA).

        Args:
            latency_ms: Execution latency in milliseconds

        Returns:
            Estimated cost in cents (integer)
        """
        # Simple model: 0.01 cents per millisecond
        # Future: Use CostController for accurate pricing
        cost_cents = max(1, int(latency_ms * 0.01))
        return cost_cents

    def _sanitize_error_message(self, error_msg: str) -> str:
        """Sanitize error message for PII (paths, schema, credentials).

        Removes:
        - Absolute paths (/home/user/...)
        - Database schema names (database.table)
        - Stack traces and internal service names
        - Quoted strings that look like secrets

        Args:
            error_msg: Raw error message

        Returns:
            Sanitized error message safe for audit trail
        """
        sanitized = error_msg

        # Credential assignments FIRST, and independent of value length.
        # The quoted-string rule below only fires at >=20 chars, so a typical
        # secret slipped straight through into the audit trail:
        # `password="super_secret_12345"` is 18 chars, and most real passwords
        # and API keys are shorter still. The key name is kept (it is useful
        # for debugging and is not itself sensitive); only the value is
        # redacted. Runs before the path rule so secrets containing slashes
        # are masked as credentials rather than partially rewritten as paths.
        sanitized = re.sub(
            r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|"
            r"auth|credential|bearer)\b\s*[:=]\s*[\"']?[^\s\"',;)]+[\"']?",
            r"\1=[REDACTED]",
            sanitized,
        )

        # Remove absolute paths
        sanitized = re.sub(r"/[a-zA-Z0-9/_\-\.]+", "[PATH]", sanitized)

        # Remove database identifiers (schema.table)
        sanitized = re.sub(r"\b[a-z_]+\.[a-z_]+\b", "[DATABASE]", sanitized)

        # Remove quoted strings >20 chars (likely PII/secrets)
        sanitized = re.sub(r'["\']([\S]{20,})["\']', "[REDACTED]", sanitized)

        # Remove stack trace markers
        sanitized = re.sub(r"File .*?, line \d+", "[STACKTRACE]", sanitized)

        return sanitized

    def _classify_error(self, exception: Exception) -> tuple[Optional[str], Optional[str]]:
        """Classify error for learning aggregation.

        Returns:
            Tuple of (error_type, error_class)
            error_type: e.g., "ValueError", "TimeoutError"
            error_class: e.g., "validation_error", "infrastructure_error"
        """
        error_type = type(exception).__name__

        # Map exception types to error classes
        error_class = "unknown_error"

        if isinstance(exception, (ValueError, TypeError, KeyError)):
            error_class = "validation_error"
        elif isinstance(exception, TimeoutError):
            error_class = "timeout_error"
        elif isinstance(exception, (FileNotFoundError, PermissionError)):
            error_class = "infrastructure_error"
        elif isinstance(exception, RuntimeError):
            error_class = "runtime_error"
        elif isinstance(exception, Exception):
            # Generic error classification
            error_name = error_type.lower()
            if "permission" in error_name or "access" in error_name:
                error_class = "infrastructure_error"
            elif "timeout" in error_name:
                error_class = "timeout_error"
            elif "value" in error_name or "type" in error_name:
                error_class = "validation_error"

        return error_type, error_class

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
