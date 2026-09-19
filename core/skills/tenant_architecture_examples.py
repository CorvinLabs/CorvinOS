"""Integration Examples: Using Tenant-Skill-Architecture (ADR-0114, ADR-0174).

This module demonstrates practical usage patterns for the tenant-scoped skill
execution, versioning, and state management system.
"""

import asyncio
from typing import Dict, Any, Optional

from core.skills.tenant_architecture import (
    TenantSkillArchitecture,
    TenantSkillExecutionContext,
)
from core.skills.contract import SkillContract, SkillTier


# ============================================================================
# Example 1: Basic Skill Registration and Execution
# ============================================================================

async def example_basic_skill_execution():
    """Register a skill and execute it within tenant context."""

    # Create architecture for a tenant
    arch = TenantSkillArchitecture("_default")

    # Define skill contract
    contract = SkillContract(
        skill_id="os.router",
        version="1.0.0",
        tier=SkillTier.AUTONOMOUS,
        input_schema={
            "type": "object",
            "properties": {
                "request": {"type": "string"},
            },
            "required": ["request"],
        },
        output_schema={
            "type": "object",
            "properties": {
                "engine": {"type": "string"},
                "confidence": {"type": "number"},
            },
            "required": ["engine", "confidence"],
        },
        learning_enabled=True,
        learning_config_schema={
            "type": "object",
            "properties": {
                "confidence_threshold": {"type": "number"},
            },
        },
    )

    # Register skill version
    success, msg = arch.register_skill_version(
        skill_id="os.router",
        version="1.0.0",
        contract=contract,
        config_data={"confidence_threshold": 0.7},
        created_by="system",
    )
    print(f"Registration: {msg}")

    # Create execution context
    context = arch.create_execution_context(
        skill_id="os.router",
        task_id="task-20260919-001",
        input_data={"request": "Use GPT-4o for image analysis"},
    )

    # Define async skill function
    async def router_skill(input_data: Dict[str, Any], state_before):
        """Route request to appropriate model based on content."""
        request = input_data.get("request", "")

        # Simple routing logic
        if "image" in request.lower():
            engine = "claude-opus"
        elif "fast" in request.lower():
            engine = "claude-haiku"
        else:
            engine = "claude-opus"

        return {
            "engine": engine,
            "confidence": 0.95,
            "state": {
                "last_routed_to": engine,
                "last_request": request[:50],
            },
        }

    # Execute skill
    success, output, event = await arch.execute_skill(
        context=context,
        skill_fn=router_skill,
        timeout_seconds=5.0,
    )

    if success:
        print(f"Execution successful: {output}")
        print(f"Audit event: {event['status']}")
    else:
        print(f"Execution failed: {output}")


# ============================================================================
# Example 2: Multi-Tenant Isolation
# ============================================================================

async def example_multi_tenant_isolation():
    """Demonstrate tenant isolation - same skill, different tenants."""

    # Create separate architectures for two tenants
    arch_acme = TenantSkillArchitecture("acme-corp")
    arch_widgets = TenantSkillArchitecture("widgets-inc")

    # Verify isolation
    success_a, msg_a = arch_acme.verify_isolation()
    success_b, msg_b = arch_widgets.verify_isolation()
    print(f"ACME isolation: {msg_a}")
    print(f"Widgets isolation: {msg_b}")

    # Register same skill in each tenant (but with different configs)
    contract = SkillContract(
        skill_id="org.decision_maker",
        version="1.0.0",
        tier=SkillTier.AUTONOMOUS,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        learning_enabled=True,
    )

    # ACME's config (conservative)
    arch_acme.register_skill_version(
        skill_id="org.decision_maker",
        version="1.0.0",
        contract=contract,
        config_data={"approval_threshold": 0.95, "escalate_on_doubt": True},
        created_by="acme-admin",
    )

    # Widgets' config (aggressive)
    arch_widgets.register_skill_version(
        skill_id="org.decision_maker",
        version="1.0.0",
        contract=contract,
        config_data={"approval_threshold": 0.70, "escalate_on_doubt": False},
        created_by="widgets-admin",
    )

    # Create execution contexts (same skill, different tenants)
    context_acme = arch_acme.create_execution_context(
        skill_id="org.decision_maker",
        task_id="acme-decision-001",
        input_data={"decision": "approve_payment"},
    )

    context_widgets = arch_widgets.create_execution_context(
        skill_id="org.decision_maker",
        task_id="widgets-decision-001",
        input_data={"decision": "approve_payment"},
    )

    # Verify contexts are isolated
    print(f"\nACME context tenant: {context_acme.tenant_id}")
    print(f"Widgets context tenant: {context_widgets.tenant_id}")
    print(f"Contexts isolated: {context_acme.tenant_id != context_widgets.tenant_id}")

    # Execute the same skill with different tenants
    async def decision_maker_skill(input_data, state_before):
        return {"decision": "approved", "state": {"approved": True}}

    await arch_acme.execute_skill(context_acme, decision_maker_skill)
    await arch_widgets.execute_skill(context_widgets, decision_maker_skill)

    # Verify each tenant's state is separate
    state_mgr_acme = arch_acme.get_state_manager("org.decision_maker")
    state_mgr_widgets = arch_widgets.get_state_manager("org.decision_maker")

    state_acme = state_mgr_acme.get_current_state()
    state_widgets = state_mgr_widgets.get_current_state()

    print(f"\nACME state: {state_acme.state_data if state_acme else 'None'}")
    print(f"Widgets state: {state_widgets.state_data if state_widgets else 'None'}")
    print(f"States isolated: {state_acme != state_widgets}")


# ============================================================================
# Example 3: Skill Versioning and Rollback
# ============================================================================

async def example_versioning_and_rollback():
    """Demonstrate skill versioning with rollback."""

    arch = TenantSkillArchitecture("_default")

    # Version 1.0.0 - Initial version
    contract_v1 = SkillContract(
        skill_id="ml.classifier",
        version="1.0.0",
        tier=SkillTier.AUTONOMOUS,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        learning_enabled=True,
    )

    arch.register_skill_version(
        skill_id="ml.classifier",
        version="1.0.0",
        contract=contract_v1,
        config_data={"model": "gpt-3.5", "accuracy": 0.92},
        created_by="ml-team",
    )

    # Execute with v1.0.0
    context_v1 = arch.create_execution_context(
        skill_id="ml.classifier",
        task_id="classify-001",
        input_data={"text": "The sky is blue"},
    )

    async def classifier_v1(input_data, state_before):
        return {"category": "observation", "confidence": 0.92, "state": {}}

    success_v1, output_v1, event_v1 = await arch.execute_skill(context_v1, classifier_v1)
    print(f"v1.0.0 execution: {output_v1}, event version: {event_v1['version']}")

    # Version 2.0.0 - Improved version (with bug)
    contract_v2 = SkillContract(
        skill_id="ml.classifier",
        version="2.0.0",
        tier=SkillTier.AUTONOMOUS,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        learning_enabled=True,
    )

    arch.register_skill_version(
        skill_id="ml.classifier",
        version="2.0.0",
        contract=contract_v2,
        config_data={"model": "gpt-4", "accuracy": 0.96},
        created_by="ml-team",
    )

    # Execute with v2.0.0
    context_v2 = arch.create_execution_context(
        skill_id="ml.classifier",
        task_id="classify-002",
        input_data={"text": "The sky is blue"},
    )

    async def classifier_v2(input_data, state_before):
        # Bug in v2.0.0: returns wrong category sometimes
        return {"category": "BROKEN", "confidence": 0.0, "state": {}}

    success_v2, output_v2, event_v2 = await arch.execute_skill(context_v2, classifier_v2)
    print(f"v2.0.0 execution: {output_v2}, event version: {event_v2['version']}")

    # Discover bug and rollback
    print("\nRolling back due to bug in v2.0.0...")
    success, msg = arch.rollback_skill_version(
        skill_id="ml.classifier",
        target_version="1.0.0",
        reason="v2.0.0 classifier returns wrong categories",
    )
    print(f"Rollback: {msg}")

    # Execute again with rolled-back version
    context_v1_again = arch.create_execution_context(
        skill_id="ml.classifier",
        task_id="classify-003",
        input_data={"text": "The sky is blue"},
    )

    success_v1_again, output_v1_again, event_v1_again = await arch.execute_skill(
        context_v1_again,
        classifier_v1,
    )
    print(f"v1.0.0 re-execution: {output_v1_again}, event version: {event_v1_again['version']}")

    # Verify version history
    version_mgr = arch.get_version_manager("ml.classifier")
    versions = version_mgr.list_versions()

    print(f"\nVersion history:")
    for v in versions:
        print(f"  {v.version}: state={v.state}, created_by={v.created_by}")


# ============================================================================
# Example 4: State Persistence and Recovery
# ============================================================================

async def example_state_persistence():
    """Demonstrate persistent state and recovery."""

    arch = TenantSkillArchitecture("_default")

    # Register skill
    contract = SkillContract(
        skill_id="ml.optimizer",
        version="1.0.0",
        tier=SkillTier.AUTONOMOUS,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        learning_enabled=True,
    )

    arch.register_skill_version(
        skill_id="ml.optimizer",
        version="1.0.0",
        contract=contract,
        config_data={},
        created_by="system",
    )

    # Skill that learns (updates internal state)
    iteration = 0

    async def learning_skill(input_data, state_before):
        nonlocal iteration
        iteration += 1

        # Accumulate learning from previous state
        previous_score = 0.0
        if state_before:
            previous_score = state_before.state_data.get("best_score", 0.0)

        # Improve
        new_score = previous_score + 0.01

        return {
            "score": new_score,
            "state": {
                "best_score": new_score,
                "iterations": iteration,
                "learning_rate": 0.01,
            },
        }

    # Execute multiple times to build up state
    for i in range(3):
        context = arch.create_execution_context(
            skill_id="ml.optimizer",
            task_id=f"optimize-{i+1:03d}",
            input_data={"iteration": i + 1},
        )

        success, output, event = await arch.execute_skill(context, learning_skill)
        print(f"Iteration {i+1}: score={output['score']}, state={output['state']}")

    # Recover state from history
    state_mgr = arch.get_state_manager("ml.optimizer")
    history = state_mgr.get_state_history()

    print(f"\nState history ({len(history)} snapshots):")
    for i, state in enumerate(history):
        print(f"  {i+1}. Iteration {state.state_data['iterations']}: score={state.state_data['best_score']}")

    # Verify chain integrity
    success, msg = state_mgr.verify_chain_integrity()
    print(f"\nChain integrity: {msg}")


# ============================================================================
# Example 5: Error Handling and Isolation Violations
# ============================================================================

async def example_error_handling():
    """Demonstrate error handling and isolation violation detection."""

    arch = TenantSkillArchitecture("_default")

    # Try to create context with no registered versions
    try:
        context = arch.create_execution_context(
            skill_id="nonexistent.skill",
            task_id="task-001",
            input_data={},
        )
    except RuntimeError as e:
        print(f"✓ Caught expected error: {e}")

    # Register a skill
    contract = SkillContract(
        skill_id="test.skill",
        version="1.0.0",
        tier=SkillTier.PRIMITIVE,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )

    arch.register_skill_version("test.skill", "1.0.0", contract, {}, "system")

    # Create context
    context = arch.create_execution_context(
        skill_id="test.skill",
        task_id="task-001",
        input_data={},
    )

    # Try to tamper with context
    original_tenant = context.tenant_id
    context.tenant_id = "attacker"

    if not context.verify_isolation():
        print(f"✓ Isolation tampering detected (tenant changed from {original_tenant} to attacker)")

    # Restore for proper execution
    context.tenant_id = original_tenant

    # Execute with timeout
    async def slow_skill(input_data, state_before):
        await asyncio.sleep(10)  # Longer than timeout
        return {"result": "done"}

    success, output, event = await arch.execute_skill(
        context=context,
        skill_fn=slow_skill,
        timeout_seconds=0.1,  # Very short timeout
    )

    if not success:
        print(f"✓ Timeout detected: {event}")


# ============================================================================
# Main: Run Examples
# ============================================================================

async def main():
    """Run all examples."""

    print("=" * 70)
    print("Example 1: Basic Skill Registration and Execution")
    print("=" * 70)
    await example_basic_skill_execution()

    print("\n" + "=" * 70)
    print("Example 2: Multi-Tenant Isolation")
    print("=" * 70)
    await example_multi_tenant_isolation()

    print("\n" + "=" * 70)
    print("Example 3: Skill Versioning and Rollback")
    print("=" * 70)
    await example_versioning_and_rollback()

    print("\n" + "=" * 70)
    print("Example 4: State Persistence and Recovery")
    print("=" * 70)
    await example_state_persistence()

    print("\n" + "=" * 70)
    print("Example 5: Error Handling and Isolation Violations")
    print("=" * 70)
    await example_error_handling()


if __name__ == "__main__":
    asyncio.run(main())
