"""ADR-0214: Tiered Delegation Engine (TDE) Components.

Phase 1 Core:
- RobustEngineDetector: Multi-signal ensemble for engine selection
- L34DelegationGate: Data-safe, fail-closed delegation check
- LossProfileTracker: In-session learning from outcomes
- SlashCommandParser: /use-engine command parsing

Phase 2 Integration:
- EngineRegistry: Central registry of all agentic engines (real engines)
- TieredDelegationEngine / ClaudeCodeLocalEngine / AcsEngineBridge
- AdaptiveDelegationExecutor: Parallel execution + three-gate delegation
- BudgetEnvelope: hard token budget per task
- SendIntegration: L22 send() hookpoint (select + execute)
- WorkerIPC: Mock (tests) / Subprocess (real LLM one-shot) / A2A (Phase 3)
- tde_audit: hash-chained content-free tde.* audit events

Phase 3 Streaming (partial):
- StreamingExecutor: Big-data streaming with L34 filtering (local path only)
"""

from .adaptive_delegation_executor import (
    AdaptiveDelegationExecutor,
    BudgetEnvelope,
    DelegationEnvelope,
    StepResult,
)
from .analysis_runner import (
    AnalysisUnavailable,
    run_initial_analysis,
    run_initial_analysis_sync,
)
from .engine_registry import EngineRegistry, get_registry, reset_registry
from .l34_delegation_gate import DelegationGateResult, L34DelegationGate
from .loss_profile_tracker import LossEntry, LossProfileTracker, get_session_tracker
from .robust_engine_detector import DetectionSignals, RobustEngineDetector
from .send_integration import SendIntegration
from .slash_command_parser import ParseResult, SlashCommandParser
from .streaming_executor import StreamingExecutor
from .tde_engine import (
    AcsEngineBridge,
    ClaudeCodeLocalEngine,
    TieredDelegationEngine,
    default_local_step_executor,
)
from .worker_ipc import (
    A2AWorkerIPC,
    MockWorkerIPC,
    SubprocessWorkerIPC,
    WorkerIPCInterface,
    get_worker_ipc,
    set_worker_ipc,
)

__all__ = [
    # Phase 1
    "RobustEngineDetector",
    "DetectionSignals",
    "L34DelegationGate",
    "DelegationGateResult",
    "LossProfileTracker",
    "LossEntry",
    "get_session_tracker",
    "SlashCommandParser",
    "ParseResult",
    # ADR-0210 Phase 1 (real LM call)
    "AnalysisUnavailable",
    "run_initial_analysis",
    "run_initial_analysis_sync",
    # Phase 2
    "EngineRegistry",
    "get_registry",
    "reset_registry",
    "AdaptiveDelegationExecutor",
    "BudgetEnvelope",
    "DelegationEnvelope",
    "StepResult",
    "SendIntegration",
    "TieredDelegationEngine",
    "ClaudeCodeLocalEngine",
    "AcsEngineBridge",
    "default_local_step_executor",
    # Phase 3
    "StreamingExecutor",
    "WorkerIPCInterface",
    "MockWorkerIPC",
    "SubprocessWorkerIPC",
    "A2AWorkerIPC",
    "get_worker_ipc",
    "set_worker_ipc",
]
