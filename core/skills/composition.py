"""Skill Composition — pipeline multiple skills (ADR-0311)."""

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
            skill_fn: Async or sync callable
            tags: Optional tags
        """
        self.steps.append(CompositionStep(name=name, skill_fn=skill_fn, tags=tags or []))

    async def execute(self, input_data: Any) -> Any:
        """Execute the pipeline sequentially.

        Args:
            input_data: Initial input

        Returns:
            Output of the last step
        """
        result = input_data

        for step in self.steps:
            try:
                # Try async first
                import asyncio
                if asyncio.iscoroutinefunction(step.skill_fn):
                    result = await step.skill_fn(result)
                else:
                    result = step.skill_fn(result)
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
