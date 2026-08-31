"""Skills Engine: Versioned, auto-graded, user-extensible."""

from dataclasses import dataclass
from typing import Dict, List, Callable, Optional, Any
from enum import Enum

@dataclass
class SkillResult:
    """Outcome from skill invocation."""
    status: str  # "success", "failure", "partial"
    output: Any
    cost_actual: float  # compute units
    time_actual: float  # seconds
    error_trace: Optional[str] = None

@dataclass
class Skill:
    """Skill definition (versioned, typed, user-definable)."""
    id: str
    version: str
    description: str
    task_types: List[str]  # which tasks use this?
    entry_point: Callable  # async function
    # Which learned STRATEGY this skill realises (MemoryPalace.StrategyWeights
    # is keyed by strategy: "decompose" / "direct_fix" / "backtrack", not by
    # skill id). Without this the Brain looked up `weights[skill.id]`, always
    # missed, and every skill scored the uniform default — so nothing the
    # memory learned ever reached the decision it exists to inform. None means
    # "the id IS the strategy name".
    strategy: Optional[str] = None
    parameters: Dict[str, Any] = None
    cost_estimate: float = 1.0
    time_estimate: float = 5.0
    success_rate: float = 0.5  # learned from past runs
    confidence: int = 0  # how many samples?
    is_user_defined: bool = False
    plugin_id: str = None
    deprecated_in: Optional[str] = None

class SkillsEngine:
    """Registry + invocation + auto-grading."""

    def __init__(self):
        self.skills: Dict[str, Skill] = {}
        self._register_builtins()

    def _register_builtins(self):
        """Register built-in skills (v1.0 MVP)."""
        self.skills["code_analysis"] = Skill(
            id="code_analysis",
            version="1.0.0",
            description="Analyze code for issues",
            task_types=["refactoring", "testing"],
            entry_point=self._builtin_code_analysis,
            strategy="direct_fix",
        )
        self.skills["decompose_task"] = Skill(
            id="decompose_task",
            version="1.0.0",
            description="Break task into subtasks",
            task_types=["any"],
            entry_point=self._builtin_decompose,
            strategy="decompose",
        )
        self.skills["direct_fix"] = Skill(
            id="direct_fix",
            version="1.0.0",
            description="Apply direct code fix",
            task_types=["bug_fix"],
            entry_point=self._builtin_direct_fix,
            strategy="direct_fix",
        )

    async def _builtin_code_analysis(self, context: Any) -> SkillResult:
        """MVP: mock analysis."""
        return SkillResult(
            status="success",
            output={"issues": ["code_style", "unused_var"]},
            cost_actual=0.5,
            time_actual=1.0
        )

    async def _builtin_decompose(self, context: Any) -> SkillResult:
        """MVP: mock decompose."""
        return SkillResult(
            status="success",
            output={"subtasks": ["batch_1", "batch_2", "batch_3"]},
            cost_actual=0.1,
            time_actual=0.5
        )

    async def _builtin_direct_fix(self, context: Any) -> SkillResult:
        """MVP: mock fix."""
        return SkillResult(
            status="success",
            output={"fixed": True},
            cost_actual=1.0,
            time_actual=2.0
        )

    async def invoke(self, skill_id: str, context: Any, params: Dict = None) -> SkillResult:
        """Execute skill + capture outcome."""
        if skill_id not in self.skills:
            return SkillResult(
                status="failure",
                output=None,
                cost_actual=0,
                time_actual=0,
                error_trace=f"Skill {skill_id} not found"
            )

        skill = self.skills[skill_id]
        try:
            result = await skill.entry_point(context)
            return result
        except Exception as e:
            return SkillResult(
                status="failure",
                output=None,
                cost_actual=0,
                time_actual=0,
                error_trace=str(e)
            )

    def get_skill(self, skill_id: str, version: str = "latest") -> Optional[Skill]:
        """Retrieve skill (MVP: version pinning deferred)."""
        return self.skills.get(skill_id)

    def register_skill(self, skill: Skill):
        """User registers custom skill."""
        skill.is_user_defined = True
        self.skills[skill.id] = skill

    def list_skills(self, task_type: str = None) -> List[Skill]:
        """List available skills, optionally filtered."""
        if task_type:
            return [s for s in self.skills.values() if task_type in s.task_types or "any" in s.task_types]
        return list(self.skills.values())
