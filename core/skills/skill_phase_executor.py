"""SkillPhaseExecutor: Bridge Skills composability to TaskOrchestrator DAGs.

ADR-0571 (L4 Context Isolation) integration with ADR-0402 (TaskOrchestrator).

A SkillPhaseExecutor wraps a sequence of Skills and produces a Phase handler
that executes them with isolated contexts, using SkillExecutor.execute_isolated().
"""

from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional
import asyncio
import logging

from core.skills.executor import SkillExecutor, ExecutionResult
from core.skills.contract import SkillRegistry, SKILL_REGISTRY

logger = logging.getLogger(__name__)


@dataclass
class SkillPhaseSpec:
    """Spec for a skill-based phase."""
    phase_id: str
    skill_ids: List[str]  # Ordered sequence of skills to execute
    tenant_id: str = "_default"
    task_id: str = "unknown"
    input_context: Dict[str, Any] = field(default_factory=dict)
    registry: Optional[SkillRegistry] = None


class SkillPhaseExecutor:
    """Execute a sequence of skills as a DAG phase with context isolation."""

    def __init__(self, executor: Optional[SkillExecutor] = None):
        """Initialize executor.

        Args:
            executor: SkillExecutor to use (or creates one)
        """
        self.executor = executor or SkillExecutor()
        self.registry = SKILL_REGISTRY

    async def execute_phase(
        self,
        spec: SkillPhaseSpec,
    ) -> Dict[str, Any]:
        """Execute skill sequence as a single phase.

        Each skill receives an isolated context, mutations merge back.
        Sequence stops on first failure (hard-fail).

        Args:
            spec: SkillPhaseSpec with skills, context, tenant

        Returns:
            Dict with keys:
              - skills_executed: list of skill_ids
              - context_final: final merged context
              - results: list of ExecutionResult per skill
              - state_hashes: {"before": hash, "after": hash}
              - mutations: accumulated deltas across all skills

        Raises:
            RuntimeError: If a skill fails or isolation is violated
        """
        if not spec.skill_ids:
            return {
                "skills_executed": [],
                "context_final": spec.input_context.copy(),
                "results": [],
                "state_hashes": {"before": None, "after": None},
                "mutations": {},
            }

        # Track execution
        results = []
        context = spec.input_context.copy()
        all_mutations = {}

        # Execute skills in sequence (each gets isolated context)
        for i, skill_id in enumerate(spec.skill_ids):
            logger.info(f"[SkillPhase {spec.phase_id}] Executing skill {i+1}/{len(spec.skill_ids)}: {skill_id}")

            # Get skill callable (note: contract validation happens at composition time,
            # not at execution time, so we skip that check here)
            skill_func = self._get_skill_callable(skill_id)
            if not skill_func:
                raise RuntimeError(f"Skill {skill_id} not callable or not registered")

            # Execute with isolation
            try:
                result = await self.executor.execute_isolated(
                    tenant_id=spec.tenant_id,
                    skill_id=skill_id,
                    skill=skill_func,
                    context=context,
                    task_id=spec.task_id,
                )
            except Exception as e:
                logger.error(f"[SkillPhase {spec.phase_id}] Skill {skill_id} execution error: {e}")
                raise

            results.append(result)

            # Check for execution success
            if result.status != "success":
                error_msg = result.error_message or "Unknown error"
                logger.error(
                    f"[SkillPhase {spec.phase_id}] Skill {skill_id} failed: {error_msg}"
                )
                raise RuntimeError(
                    f"Skill {skill_id} failed in phase {spec.phase_id}: {error_msg}"
                )

            # Merge mutations back to context for next skill
            if result.mutations:
                all_mutations.update(result.mutations)
                # Apply mutations to context (simple merge)
                for path, delta_dict in result.mutations.items():
                    new_value = delta_dict.get("new_value")
                    parts = path.split(".")
                    current = context
                    for part in parts[:-1]:
                        if part not in current:
                            current[part] = {}
                        current = current[part]
                    current[parts[-1]] = new_value

            logger.info(
                f"[SkillPhase {spec.phase_id}] Skill {skill_id} success "
                f"({result.execution_time_ms:.2f}ms, {len(result.mutations or {})} mutations)"
            )

        return {
            "skills_executed": spec.skill_ids,
            "context_final": context,
            "results": results,
            "state_hashes": {
                "before": results[0].context_state_before_hash if results else None,
                "after": results[-1].context_state_after_hash if results else None,
            },
            "mutations": all_mutations,
        }

    def _get_skill_callable(self, skill_id: str):
        """Get callable for a skill.

        TODO: This should look up the actual skill function from a registry.
        For now, return None to indicate not implemented.
        """
        # Placeholder: real implementation would look up skill from os_skills or similar
        return None


async def create_skill_phase_handler(
    spec: SkillPhaseSpec,
    executor: Optional[SkillExecutor] = None,
) -> callable:
    """Create a phase handler for TaskOrchestrator that executes skills.

    Usage:
        phase_handler = await create_skill_phase_handler(
            SkillPhaseSpec(
                phase_id="phase_1",
                skill_ids=["os.delegation_router", "os.context_adapter"],
                tenant_id="_default",
                task_id="task_123",
            )
        )

        # Use with TaskOrchestrator:
        phase = Phase(
            phase_id="phase_1",
            handler=phase_handler,
            timeout_s=60,
        )

    Args:
        spec: SkillPhaseSpec
        executor: SkillExecutor (or creates one)

    Returns:
        Async callable that can be used as Phase.handler
    """
    skill_executor = SkillPhaseExecutor(executor=executor)

    async def phase_handler():
        """Handler that executes skills and returns result dict."""
        return await skill_executor.execute_phase(spec)

    return phase_handler
