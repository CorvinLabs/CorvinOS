"""
CorvinOS Vibe Engineering Platform v1.0

Autonomous task execution with Memory Palace + Skills + Brain + Context.
Integrated with CorvinOS native engine (spawning, state, recovery, plugins).

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
