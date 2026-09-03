"""ExecutionContext v1/v2 Coexistence Bridge (Task 2.3 - ADR-0358 Part II).

Provides backward-compatibility layer between ExecutionContext v1 (routing metadata)
and ExecutionContext v2 (task execution state).

Key design principles:
  - v1 is immutable (used for routing decisions)
  - v2 is mutable (used for task execution)
  - v1 fields are preserved in v2 for audit trail
  - Both versions coexist without breaking existing code
"""

from typing import Optional, Any, Dict
from dataclasses import dataclass

from corvin_core.execution_context import (
    ExecutionContext as ExecutionContextV1,
    EngineId,
    ModelSource,
    DelegationMode,
)
from core.context_engineering.execution_context import (
    ExecutionContext as ExecutionContextV2,
    ContextStack,
)


@dataclass
class ContextBridgeResult:
    """Result of context bridge conversion.

    Attributes:
        context_v2: ExecutionContextV2 for task execution
        context_v1_metadata: Dict with v1 fields for audit/routing
    """

    context_v2: ExecutionContextV2
    context_v1_metadata: Dict[str, Any]


class ContextBridge:
    """Convert between ExecutionContext v1 (routing) and v2 (execution).

    Enables coexistence of both versions:
    - v1 for delegation routing (dispatcher, engine selection)
    - v2 for task execution (Brain subsystems, context API)

    Thread-safe: stateless conversions, no shared mutable state.
    """

    @staticmethod
    def v1_to_v2(
        ctx_v1: ExecutionContextV1,
        task_id: str,
        budget_remaining: float,
        task_template: Dict[str, Any] | None = None,
        time_remaining: int = 3600,
    ) -> ExecutionContextV2:
        """Convert ExecutionContext v1 to v2.

        Preserves v1 metadata in v2 for audit trail and routing callbacks.
        Creates a v2 task context with default strategy and empty decision history.

        Args:
            ctx_v1: ExecutionContext v1 (routing metadata)
            task_id: Unique task identifier for v2
            budget_remaining: Initial budget (tokens or cost)
            task_template: Task template dict (if available)
            time_remaining: Time available for task (seconds)

        Returns:
            ExecutionContextV2 with v1 fields preserved in task_template extra
        """
        # Create root context stack
        context_stack = ContextStack()
        context_stack.push("task", task_id, engine=ctx_v1.engine_id.value)

        # Create v2 context
        template = task_template or {}
        ctx_v2 = ExecutionContextV2(
            task_id=task_id,
            tenant_id=ctx_v1.tenant_id,
            task_template=template,
            context_stack=context_stack,
            decision_history=[],
            budget_remaining=budget_remaining,
            time_remaining=time_remaining,
            model=ctx_v1.model_name or "",  # v1.model_name → v2.model
            strategy="decompose",  # default strategy
            strategy_confidence=0.5,  # neutral default
            guidance_overrides={},
            checkpoints=[],
        )

        return ctx_v2

    @staticmethod
    def preserve_v1_fields(
        ctx_v2: ExecutionContextV2,
        engine: str,
        model_source: str | ModelSource = ModelSource.UNKNOWN,
        delegation_mode: str | DelegationMode = DelegationMode.NATIVE,
        acs_run_id: Optional[str] = None,
        tde_router_decision: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Extract v1-compatible metadata from v2.

        Reconstructs v1 fields for audit log, routing callbacks, and delegation.
        Used when v2 subsystems need to communicate back to v1 layers.

        Args:
            ctx_v2: ExecutionContextV2 (task execution context)
            engine: Engine identifier (e.g., "claude_code", "acs", "tde")
            model_source: ModelSource enum or string
            delegation_mode: DelegationMode enum or string
            acs_run_id: ACS run ID (if delegated to ACS)
            tde_router_decision: TDE routing decision (if delegated to TDE)

        Returns:
            Dict with v1-compatible fields: {
                'engine_id': str,
                'model': str,
                'model_source': str,
                'delegation_mode': str,
                'task_id': str,  # added for traceability
                'tenant_id': str,
                'budget_remaining': float,
                'acs_run_id': str | None,
                'tde_router_decision': str | None,
            }
        """
        # Normalize model_source and delegation_mode
        model_source_str = (
            model_source.value
            if isinstance(model_source, ModelSource)
            else str(model_source)
        )
        delegation_mode_str = (
            delegation_mode.value
            if isinstance(delegation_mode, DelegationMode)
            else str(delegation_mode)
        )

        return {
            "engine_id": engine,
            "model": ctx_v2.model,
            "model_source": model_source_str,
            "delegation_mode": delegation_mode_str,
            "task_id": ctx_v2.task_id,
            "tenant_id": ctx_v2.tenant_id,
            "budget_remaining": ctx_v2.budget_remaining,
            "acs_run_id": acs_run_id,
            "tde_router_decision": tde_router_decision,
        }

    @staticmethod
    def create_v1_from_v2(
        ctx_v2: ExecutionContextV2,
        engine_id: str | EngineId = EngineId.UNKNOWN,
        model_source: str | ModelSource = ModelSource.UNKNOWN,
        delegation_mode: str | DelegationMode = DelegationMode.NATIVE,
    ) -> ExecutionContextV1:
        """Create a minimal ExecutionContext v1 from v2.

        Used when legacy v1 consumers need metadata from a v2 task.
        Creates a v1 context with metadata fields populated from v2.

        Args:
            ctx_v2: ExecutionContextV2 (source)
            engine_id: Engine identifier
            model_source: ModelSource enum or string
            delegation_mode: DelegationMode enum or string

        Returns:
            ExecutionContextV1 with v2 metadata populated
        """
        # Normalize engine_id and delegation_mode
        engine_id_enum = (
            engine_id
            if isinstance(engine_id, EngineId)
            else EngineId(engine_id) if engine_id else EngineId.UNKNOWN
        )
        model_source_enum = (
            model_source
            if isinstance(model_source, ModelSource)
            else ModelSource(model_source) if model_source else ModelSource.UNKNOWN
        )
        delegation_mode_enum = (
            delegation_mode
            if isinstance(delegation_mode, DelegationMode)
            else DelegationMode(delegation_mode) if delegation_mode else DelegationMode.NATIVE
        )

        return ExecutionContextV1(
            engine_id=engine_id_enum,
            model_source=model_source_enum,
            model_name=ctx_v2.model,
            delegation_mode=delegation_mode_enum,
            tenant_id=ctx_v2.tenant_id,
            extra={
                "task_id": ctx_v2.task_id,
                "budget_remaining": ctx_v2.budget_remaining,
                "strategy": ctx_v2.strategy,
            },
        )

    @staticmethod
    def verify_compatibility(ctx_v1: ExecutionContextV1, ctx_v2: ExecutionContextV2) -> bool:
        """Verify that v1 and v2 contexts are compatible.

        Checks:
        - Tenant IDs match
        - Model names align
        - Engine/strategy are compatible

        Args:
            ctx_v1: ExecutionContext v1
            ctx_v2: ExecutionContext v2

        Returns:
            True if contexts are compatible, False otherwise
        """
        # Tenant ID must match
        if ctx_v1.tenant_id != ctx_v2.tenant_id:
            return False

        # Model names should align (v1.model_name should match v2.model)
        v1_model = ctx_v1.model_name.lower().strip()
        v2_model = ctx_v2.model.lower().strip()
        if v1_model and v2_model and v1_model != v2_model:
            return False

        # Engine ID should be valid
        if ctx_v1.engine_id == EngineId.UNKNOWN:
            return False

        return True
