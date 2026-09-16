"""Skill Forge v2.0 (Phase 2 Foundation) — Dynamic Skill Generation + Deployment

Builds on Notification Daemon: skills emit events, daemon routes them.
"""

from dataclasses import dataclass
from typing import Dict, List, Callable, Optional, Any
from datetime import datetime
import asyncio
from core.orchestration.subsystems.notification_daemon import (
    NotificationDaemon, NotificationEvent, EventTopic, EventPriority, emit_event
)


@dataclass
class SkillMetadata:
    """Skill definition (versioned, audited)."""
    skill_id: str
    version: str  # semver
    category: str  # "routing" / "learning" / "security" / "data"
    handler: Callable
    dependencies: List[str] = None
    required_checks: List[str] = None
    
    def __post_init__(self):
        if self.dependencies is None:
            self.dependencies = []
        if self.required_checks is None:
            self.required_checks = []


class SkillForgeV2:
    """Production skill generator + lifecycle manager."""
    
    def __init__(self, daemon: NotificationDaemon):
        self.daemon = daemon
        self.skills: Dict[str, SkillMetadata] = {}
        self.skill_cache: Dict[str, Any] = {}
    
    async def register_skill(self, metadata: SkillMetadata) -> bool:
        """Register skill with dependency checking."""
        # Check dependencies exist
        for dep in metadata.dependencies:
            if dep not in self.skills:
                await emit_event(
                    EventTopic.SKILL_ERROR,
                    EventPriority.HIGH,
                    {"error": f"Missing dependency: {dep}", "skill": metadata.skill_id},
                    "skill_forge_v2"
                )
                return False
        
        self.skills[metadata.skill_id] = metadata
        
        # Emit registration event
        await emit_event(
            EventTopic.SKILL_EXECUTED,
            EventPriority.NORMAL,
            {"action": "skill_registered", "skill_id": metadata.skill_id, "version": metadata.version},
            "skill_forge_v2"
        )
        
        return True
    
    async def execute_skill(
        self,
        skill_id: str,
        input_data: Dict[str, Any],
        task_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Execute skill, emit events, handle errors."""
        if skill_id not in self.skills:
            await emit_event(
                EventTopic.SKILL_ERROR,
                EventPriority.HIGH,
                {"error": f"Skill not found: {skill_id}"},
                "skill_forge_v2"
            )
            return {"error": "Skill not found"}
        
        metadata = self.skills[skill_id]
        start_time = datetime.utcnow()
        
        try:
            # Execute skill handler
            if asyncio.iscoroutinefunction(metadata.handler):
                result = await metadata.handler(input_data)
            else:
                result = metadata.handler(input_data)
            
            # Emit success event
            latency_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
            await emit_event(
                EventTopic.SKILL_EXECUTED,
                EventPriority.NORMAL,
                {
                    "skill_id": skill_id,
                    "version": metadata.version,
                    "latency_ms": latency_ms,
                    "task_id": task_id,
                    "success": True
                },
                "skill_forge_v2"
            )
            
            return result
        
        except Exception as e:
            # Emit error event
            await emit_event(
                EventTopic.SKILL_ERROR,
                EventPriority.HIGH,
                {
                    "skill_id": skill_id,
                    "error": str(e),
                    "task_id": task_id
                },
                "skill_forge_v2"
            )
            
            return {"error": str(e)}
    
    def get_skill(self, skill_id: str) -> Optional[SkillMetadata]:
        """Get skill metadata."""
        return self.skills.get(skill_id)
    
    def list_skills(self, category: Optional[str] = None) -> List[SkillMetadata]:
        """List all skills, optionally filtered by category."""
        skills = list(self.skills.values())
        if category:
            skills = [s for s in skills if s.category == category]
        return skills


# Global instance
_skill_forge_instance = None


def get_skill_forge(daemon: Optional[NotificationDaemon] = None) -> SkillForgeV2:
    """Get or create global Skill Forge."""
    global _skill_forge_instance
    if _skill_forge_instance is None:
        if daemon is None:
            from core.orchestration.subsystems.notification_daemon import get_daemon
            daemon = get_daemon()
        _skill_forge_instance = SkillForgeV2(daemon)
    return _skill_forge_instance
