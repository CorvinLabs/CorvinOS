"""
Plugin Swarms — Phase 4

Multi-plugin orchestration + LLM synthesis.
Parallel execution, confidence tracking, fallback safety.
"""

from typing import Dict, List, Any, Optional
from dataclasses import dataclass
import asyncio


@dataclass
class SwarmResult:
    """Swarm reasoning result."""
    answer: str
    confidence: float  # 0.0-1.0
    reasoning: str
    contributors: List[str]
    latency_ms: float


class PluginSwarm:
    """Orchestrates multiple plugins for collaborative reasoning."""

    def __init__(self, registry: Dict[str, Any]):
        self.registry = registry
        self.metrics = {"swarms_executed": 0, "fallbacks_used": 0}

    async def solve(self, problem: str, plugins: List[str] = None) -> SwarmResult:
        """
        Solve problem with multiple plugins.
        Returns: Synthesized result with confidence.
        """
        import time
        start_ms = time.time() * 1000

        if not plugins:
            plugins = list(self.registry.keys())[:5]  # Up to 5 plugins

        # Execute plugins in parallel
        tasks = []
        for plugin_id in plugins:
            if plugin_id in self.registry:
                plugin = self.registry[plugin_id]
                # Mock reasoning
                async def mock_reasoning(p_id, prob):
                    await asyncio.sleep(0.01)
                    return {
                        "plugin": p_id,
                        "analysis": f"Plugin {p_id} analysis of: {prob[:30]}...",
                    }
                tasks.append(mock_reasoning(plugin_id, problem))

        # Handle exceptions gracefully
        results = await asyncio.gather(*tasks, return_exceptions=True)
        contributors = [r.get("plugin") for r in results if isinstance(r, dict) and not isinstance(r, Exception)]

        # Pre-check for empty contributors before accessing [0]
        if not contributors:
            contributors = ["fallback"]

        # Synthesize (mock LLM)
        synthesis = f"Synthesized solution from {len(contributors)} plugins"

        elapsed_ms = time.time() * 1000 - start_ms
        self.metrics["swarms_executed"] += 1

        return SwarmResult(
            answer=synthesis,
            confidence=0.87 if len(contributors) >= 2 else 0.65,
            reasoning=f"Analyzed by: {', '.join(contributors)}",
            contributors=contributors,
            latency_ms=elapsed_ms,
        )

    async def solve_with_fallback(self, problem: str) -> SwarmResult:
        """Solve with fallback to single plugin if confidence low."""
        result = await self.solve(problem)

        if result.confidence < 0.70:
            self.metrics["fallbacks_used"] += 1
            # Fallback: use first plugin only (with safety check)
            if result.contributors and len(result.contributors) > 0:
                first_plugin = result.contributors[0]
            else:
                first_plugin = "fallback"
            result.answer = f"Single-plugin fallback: {first_plugin}"
            result.confidence = 0.75

        return result


class SkillIntegration:
    """Skill 2.0 feedback loop."""

    def __init__(self):
        self.grades: Dict[str, list] = {}  # {plugin_id: [grades]}

    async def record_grade(
        self,
        plugin_id: str,
        decision_id: str,
        score: float,
        feedback: str,
    ):
        """Record skill grade for learning."""
        if plugin_id not in self.grades:
            self.grades[plugin_id] = []

        self.grades[plugin_id].append({
            "decision_id": decision_id,
            "score": score,
            "feedback": feedback,
        })

    def get_plugin_accuracy(self, plugin_id: str) -> float:
        """Calculate plugin accuracy from grades."""
        if plugin_id not in self.grades or not self.grades[plugin_id]:
            return 0.5

        grades = self.grades[plugin_id]
        avg = sum(g["score"] for g in grades) / len(grades)
        return avg

    def get_convergence_improvement(self, plugin_id: str, window: int = 50) -> float:
        """Calculate improvement over last N grades."""
        if plugin_id not in self.grades or len(self.grades[plugin_id]) < window:
            return 0.0

        grades = self.grades[plugin_id]
        old_avg = sum(g["score"] for g in grades[-window*2:-window]) / window
        new_avg = sum(g["score"] for g in grades[-window:]) / window

        return (new_avg - old_avg) / old_avg if old_avg > 0 else 0.0


# Global swarm + skills
_swarm: Optional[PluginSwarm] = None
_skills: Optional[SkillIntegration] = None


def get_swarm(registry=None) -> PluginSwarm:
    """Get global swarm."""
    global _swarm
    if _swarm is None:
        _swarm = PluginSwarm(registry or {})
    return _swarm


def get_skills() -> SkillIntegration:
    """Get global skills."""
    global _skills
    if _skills is None:
        _skills = SkillIntegration()
    return _skills
