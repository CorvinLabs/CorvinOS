"""``os.learning_daemon`` - the Data Hub learning daemon as a registry Skill (PLAN-0948 D5).

The daemon itself is a long-running host (``core.background.learning_host``); this Skill is its
audited, read-only face in the Skills registry: what has the loop learned, what is queued, what was
regenerated. It reads the host's persisted state (any process can), it never starts, stops or steers
the daemon, and its output is ids and numbers only.
"""
from __future__ import annotations

from typing import Any, Dict

from ..skill_registry_phase1 import Skill, SkillMetadata, SkillOrigin, SkillTier


class LearningDaemonSkill(Skill):
    def __init__(self) -> None:
        super().__init__(SkillMetadata(
            id="os.learning_daemon",
            name="Learning Daemon",
            description="Status of the Data Hub learning loop: learned source weights, regeneration queue, outcomes",
            version="1.0.0",
            origin=SkillOrigin.BUILTIN,
            tier=SkillTier.CORE,
            owner="corvin-os-team",
            tags=["learning", "datahub", "os-core"],
            learn=False,  # a status read is audited, not a learning signal
        ))

    def execute(self, input: Dict[str, Any]) -> Dict[str, Any]:
        from core.background.learning_host import read_state_summary

        tenant_id = input.get("tenant_id") or "_default"
        summary = read_state_summary(str(tenant_id))
        return {
            "mode": "status",
            "state": summary["state"],
            "decisions": summary["decisions"],
            "queued": len(summary["queue"]),
            "sources_watched": summary.get("sources_watched", 0),
            "pending_inbox_files": summary["pending_inbox_files"],
        }
