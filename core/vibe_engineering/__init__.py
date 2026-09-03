"""
⚠️ DEPRECATED — CorvinOS Vibe Engineering Platform v1.0

Autonomous task execution with Memory Palace + Skills + Brain + Context.
Integrated with CorvinOS native engine (spawning, state, recovery, plugins).

**DEPRECATION NOTICE (ADR-0538):** This module is being phased out in favor of ACP Skills:
- Routing logic → `os.delegation_router` Skill (ADR-0532 Phase 1)
- Context management → HybridContextModel + `os.context_adapter` Skill (ADR-0555)
- State/recovery → SkillAuditEvent + plugin lifecycle (ADR-0314)

**Timeline:**
- Phase A (weeks 1–2): Audit + mark deprecated (NOW)
- Phase B (weeks 3–4): Compat layer routing old APIs → Skills transparently
- Phase C (weeks 5–8): Measured deletion (after telemetry confirms 0 live calls)

**Migration:** See docs/implementation/MIGRATION_GUIDE_TO_SKILLS.md

**For new code:** Use ACP Skills instead. For existing code: compat layer (Phase B) maintains API compatibility transparently.

Phase 1 (MVP): Core subsystems only. Foundation for v1.0 GA.
"""

__version__ = "1.0.0-alpha"
__author__ = "CorvinOS Team"

from .memory_palace import MemoryPalace, MemoryEntry
from .skills_engine import SkillsEngine, Skill, SkillResult
from .brain import Brain, Decision, Recovery, Subtask
from .context import TaskContext, ContextEnricher, TaskProgress
from .vibe_engine import VibeEngine
from .plugin_manager import PluginRegistry, PluginManifest, LoadedPlugin
from .state_contract import (
    SerializableTaskContext, SerializableTaskProgress, CheckpointState,
    StateStore, InMemoryStateStore, serialize_for_spawn, deserialize_from_spawn
)
from .hermes_bridge import HermesBridge, HermesResponse, HermesRequest
from .event_broadcaster import EventBroadcaster, StatusLevel, ConsoleNotifier, DiscordNotifier

__all__ = [
    "MemoryPalace",
    "SkillsEngine",
    "Brain",
    "TaskContext",
    "VibeEngine",
    "PluginRegistry",
    "PluginManifest",
    "LoadedPlugin",
    "Subtask",
    "SerializableTaskContext",
    "CheckpointState",
    "StateStore",
    "InMemoryStateStore",
    "serialize_for_spawn",
    "deserialize_from_spawn",
]
