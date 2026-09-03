"""Skill dispatcher — invoke skills for phase execution (ADR-0540)."""

from typing import List, Dict, Any, Callable
from dataclasses import dataclass


@dataclass
class SkillResult:
    """Result of skill execution."""
    skill_id: str
    success: bool
    output: Dict[str, Any]
    error: str = ""


class SkillDispatcher:
    """Dispatch skills in phase execution order."""

    def __init__(self):
        # Map skill_id -> callable
        self.skills: Dict[str, Callable] = {}

    def register_skill(self, skill_id: str, skill_fn: Callable):
        """Register a skill handler."""
        self.skills[skill_id] = skill_fn

    def dispatch(self, skill_ids: List[str], phase_input: Dict[str, Any]) -> List[SkillResult]:
        """Execute skills in order. Stop on first failure."""
        results = []
        current_input = phase_input.copy()

        for skill_id in skill_ids:
            if skill_id not in self.skills:
                results.append(SkillResult(
                    skill_id=skill_id,
                    success=False,
                    output={},
                    error=f"Skill {skill_id} not found",
                ))
                break

            try:
                skill_fn = self.skills[skill_id]
                output = skill_fn(current_input)
                result = SkillResult(skill_id=skill_id, success=True, output=output)
                results.append(result)
                current_input = output  # Pass output as input to next skill
            except Exception as e:
                results.append(SkillResult(
                    skill_id=skill_id,
                    success=False,
                    output={},
                    error=str(e),
                ))
                break

        return results

    def all_success(self, results: List[SkillResult]) -> bool:
        """Check if all skills succeeded."""
        return all(r.success for r in results)
