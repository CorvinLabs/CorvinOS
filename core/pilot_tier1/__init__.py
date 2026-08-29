"""
Tier 1 Pilot — Minimal 4-tier system model.

Components:
- Tier 0: bootstrap (config, audit, core registry)
- Tier 1: session_manager (session lifecycle, storage)
- Tier 2: task_engine (task queue, execution)
- Tier 3: brain_core (decision logic, metrics)

Single feature: Extract & manage slack-notifier via CLI.
Reproducible: All state in SQLite, no manual config.
Compliant: Audit hash-chain, consent gate, boot tripwire.
"""

__version__ = "0.1.0"
__tier_level__ = "pilot"

from .tier0_bootstrap import BootstrapManager, Config
from .tier1_session import SessionManager, SessionStorage
from .tier2_task_engine import TaskEngine, TaskQueue, TaskExecutor
from .tier3_brain_core import BrainCore, DecisionEngine

__all__ = [
    "BootstrapManager",
    "Config",
    "SessionManager",
    "SessionStorage",
    "TaskEngine",
    "TaskQueue",
    "TaskExecutor",
    "BrainCore",
    "DecisionEngine",
]
