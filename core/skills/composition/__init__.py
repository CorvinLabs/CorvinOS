"""Skill Composition — pipeline multiple skills (ADR-0311).

This is a PACKAGE ``__init__``, not a sibling module. ``composition.py`` and
``composition/`` coexisted, and a module beats a package of the same name on
``sys.path``: ``core.skills.composition.video_producer_model_selector`` was
therefore unimportable ("'core.skills.composition' is not a package") and every
test of the Tier-3 model selector was a collection error. Same failure shape as
the documented frontend ``pages/foo.tsx`` vs ``pages/foo/`` shadow — keep the
package form so both the composition API and its submodules resolve.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class CompositionStep:
    """A single step in a skill pipeline."""

    name: str
    skill_fn: Callable[[Any], Any]
    tags: list[str] = field(default_factory=list)


@dataclass
class SkillComposition:
    """Compose multiple skills into a pipeline."""

    name: str
    steps: list[CompositionStep] = field(default_factory=list)

    def add_step(self, name: str, skill_fn: Callable[[Any], Any], tags: list[str] | None = None) -> None:
        """Add a step to the pipeline.

        Args:
            name: Step name
            skill_fn: Async or sync callable (should be @skill_learnable decorated)
            tags: Optional tags

        Raises:
            ValueError: If skill doesn't have _skill_metadata attribute (contract violation)
        """
        # K3-001 Fix: Validate skill contract
        if not hasattr(skill_fn, "_skill_metadata"):
            raise ValueError(
                f"Skill '{name}' must be decorated with @skill_learnable. "
                f"Missing _skill_metadata attribute. "
                f"Use: @skill_learnable decorator on the skill function."
            )

        self.steps.append(CompositionStep(name=name, skill_fn=skill_fn, tags=tags or []))

    async def execute(self, input_data: Any, step_timeout_s: float = 30.0) -> Any:
        """Execute the pipeline sequentially with timeout protection.

        Args:
            input_data: Initial input
            step_timeout_s: Max time per step (prevents sync blocking)

        Returns:
            Output of the last step
        """
        import asyncio
        from functools import partial

        result = input_data

        for step in self.steps:
            try:
                if asyncio.iscoroutinefunction(step.skill_fn):
                    result = await asyncio.wait_for(step.skill_fn(result), timeout=step_timeout_s)
                else:
                    # K2-004 Fix: Wrap sync in executor to prevent blocking event loop
                    loop = asyncio.get_event_loop()
                    result = await asyncio.wait_for(
                        loop.run_in_executor(None, partial(step.skill_fn, result)),
                        timeout=step_timeout_s,
                    )
            except asyncio.TimeoutError:
                raise RuntimeError(f"Step '{step.name}' exceeded timeout {step_timeout_s}s") from None
            except Exception as e:
                raise RuntimeError(f"Step '{step.name}' failed: {e}") from e

        return result

    def get_pipeline_info(self) -> dict[str, Any]:
        """Get pipeline metadata."""
        return {
            "name": self.name,
            "steps": len(self.steps),
            "step_names": [s.name for s in self.steps],
            "all_tags": list(set(tag for s in self.steps for tag in s.tags)),
        }
