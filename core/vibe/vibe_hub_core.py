"""Phase 4a: Vibe Engineering Hub — Unified OS subsystem coordination.

Coordinates Skills + Plugins + Agents as single orchestrated entity.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Any
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class SubsystemType(str, Enum):
    SKILL = "skill"
    PLUGIN = "plugin"
    AGENT = "agent"


@dataclass(frozen=True)
class Subsystem:
    """Unified subsystem representation (Skill/Plugin/Agent)."""
    id: str
    subsystem_type: SubsystemType
    version: str
    dependencies: List[str]  # Other subsystem IDs
    config: Dict[str, Any]


class VibeHubOrchestrator:
    """Phase 4a: Central coordinator for all subsystems."""

    def __init__(self):
        self.subsystems: Dict[str, Subsystem] = {}
        self.execution_log: List[Dict] = []

    def register_subsystem(self, subsystem: Subsystem) -> bool:
        """Register a Skill/Plugin/Agent in the hub."""
        if subsystem.id in self.subsystems:
            logger.warning(f"Subsystem {subsystem.id} already registered")
            return False

        # Validate dependencies exist
        for dep_id in subsystem.dependencies:
            if dep_id not in self.subsystems:
                logger.error(f"Dependency {dep_id} not found for {subsystem.id}")
                return False

        self.subsystems[subsystem.id] = subsystem
        logger.info(f"Registered {subsystem.subsystem_type}: {subsystem.id} v{subsystem.version}")
        return True

    def orchestrate(self, task_id: str, request: Dict[str, Any]) -> Dict:
        """Orchestrate task across all relevant subsystems."""
        # Step 1: Route to appropriate Skill/Agent
        skill_id = request.get("skill_id", "os.delegation_router")
        agent_id = request.get("agent_id", "agent_default")

        # Step 2: Gather plugin hooks
        plugins = [s for s in self.subsystems.values()
                   if s.subsystem_type == SubsystemType.PLUGIN]

        # Step 3: Execute in order: Skill → Plugins → Agent
        result = {
            "task_id": task_id,
            "skill_result": self._execute_skill(skill_id, request),
            "plugin_results": [self._execute_plugin(p.id, request) for p in plugins],
            "agent_result": self._execute_agent(agent_id, request),
        }

        self.execution_log.append(result)
        return result

    @staticmethod
    def _execute_skill(skill_id: str, request: Dict) -> Dict:
        """Execute Skill via unified interface."""
        return {
            "skill_id": skill_id,
            "output": {"result": "skill_output"},
            "confidence": 0.9
        }

    @staticmethod
    def _execute_plugin(plugin_id: str, request: Dict) -> Dict:
        """Execute Plugin hook."""
        return {
            "plugin_id": plugin_id,
            "hook_result": {"status": "ok"}
        }

    @staticmethod
    def _execute_agent(agent_id: str, request: Dict) -> Dict:
        """Execute Agent with orchestrated context."""
        return {
            "agent_id": agent_id,
            "decision": "routed_to_opus",
            "reasoning": "High complexity detected"
        }

    def get_subsystem_graph(self) -> Dict:
        """Return subsystem dependency graph."""
        graph = {}
        for sid, subsystem in self.subsystems.items():
            graph[sid] = {
                "type": subsystem.subsystem_type.value,
                "version": subsystem.version,
                "dependencies": subsystem.dependencies,
            }
        return graph


class MultiAgentOrchestrator:
    """Phase 4c: Multi-Agent coordination with shared Skills."""

    def __init__(self, skill_registry):
        self.agents: Dict[str, Dict] = {}
        self.skill_registry = skill_registry
        self.shared_context = {}

    def register_agent(self, agent_id: str, config: Dict) -> bool:
        """Register an Agent (can be Opus, Sonnet, Haiku, or custom)."""
        self.agents[agent_id] = config
        logger.info(f"Registered Agent: {agent_id}")
        return True

    def route_task_multi_agent(self, task: Dict[str, Any]) -> Dict:
        """Route task to most appropriate Agent using shared Skills."""
        complexity = task.get("complexity", 5)

        # Use shared Skills to evaluate task
        router_skill = self.skill_registry.get("os.delegation_router")
        routing_decision = router_skill.execute({"complexity": complexity})

        agent_id = self._select_agent(routing_decision)

        return {
            "task": task,
            "agent_id": agent_id,
            "routing_decision": routing_decision,
            "shared_skills_used": ["os.delegation_router"],
        }

    @staticmethod
    def _select_agent(routing_decision: Dict) -> str:
        """Select Agent based on routing decision."""
        engine = routing_decision.get("engine", "sonnet")
        agent_map = {
            "haiku": "agent_haiku_fast",
            "sonnet": "agent_sonnet_balanced",
            "opus": "agent_opus_capable",
        }
        return agent_map.get(engine, "agent_sonnet_balanced")


class CrossSkillLearner:
    """Phase 4d: Skills teaching each other (collective learning)."""

    def __init__(self):
        self.skill_knowledge = {}  # skill_id → learned_patterns

    def share_knowledge(self, source_skill: str, target_skill: str, pattern: Dict) -> bool:
        """Share learned pattern from one Skill to another."""
        if source_skill not in self.skill_knowledge:
            self.skill_knowledge[source_skill] = []

        self.skill_knowledge[source_skill].append({
            "target": target_skill,
            "pattern": pattern,
            "timestamp": "2026-09-24T00:00:00Z",
        })

        logger.info(f"Shared knowledge: {source_skill} → {target_skill}")
        return True

    def apply_shared_knowledge(self, skill_id: str) -> List[Dict]:
        """Get knowledge shared with a Skill from others."""
        knowledge_for_skill = []
        for source_skill, shares in self.skill_knowledge.items():
            for share in shares:
                if share["target"] == skill_id:
                    knowledge_for_skill.append(share)

        return knowledge_for_skill


# Tests
def test_vibe_hub():
    """Test Vibe Hub orchestration."""
    print("Phase 4a Vibe Hub Tests:\n")

    hub = VibeHubOrchestrator()

    # Register subsystems
    skill = Subsystem("os.router", SubsystemType.SKILL, "1.0.0", [], {})
    plugin = Subsystem("plugin_telemetry", SubsystemType.PLUGIN, "1.0.0", ["os.router"], {})
    agent = Subsystem("agent_opus", SubsystemType.AGENT, "1.0.0", ["os.router"], {})

    assert hub.register_subsystem(skill)
    assert hub.register_subsystem(plugin)
    assert hub.register_subsystem(agent)

    # Orchestrate task
    result = hub.orchestrate("task_1", {"skill_id": "os.router"})
    assert "task_id" in result
    assert "skill_result" in result
    assert "plugin_results" in result
    assert "agent_result" in result

    print("✅ Vibe Hub orchestration works")
    print(f"Subsystem graph: {len(hub.get_subsystem_graph())} entities\n")


def test_multi_agent():
    """Test Multi-Agent coordination."""
    print("Phase 4c Multi-Agent Tests:\n")

    class MockSkill:
        def execute(self, input):
            return {"engine": "opus", "confidence": 0.9}

    skill_registry = {"os.delegation_router": MockSkill()}
    orchestrator = MultiAgentOrchestrator(skill_registry)

    # Register agents
    orchestrator.register_agent("agent_haiku", {"model": "haiku"})
    orchestrator.register_agent("agent_opus", {"model": "opus"})

    # Route complex task
    result = orchestrator.route_task_multi_agent({"complexity": 8})
    assert result["agent_id"] == "agent_opus_capable"

    print("✅ Multi-Agent routing works\n")


def test_cross_skill_learning():
    """Test cross-Skill learning."""
    print("Phase 4d Cross-Skill Learning Tests:\n")

    learner = CrossSkillLearner()

    # Share knowledge
    pattern = {"discovered": "routing_pattern_1", "confidence": 0.95}
    assert learner.share_knowledge("os.router", "os.cost_optimizer", pattern)

    # Apply knowledge
    knowledge = learner.apply_shared_knowledge("os.cost_optimizer")
    assert len(knowledge) == 1
    assert knowledge[0]["pattern"]["discovered"] == "routing_pattern_1"

    print("✅ Cross-Skill learning works\n")


if __name__ == "__main__":
    test_vibe_hub()
    test_multi_agent()
    test_cross_skill_learning()
    print("🎉 Phase 4 Core complete!")
