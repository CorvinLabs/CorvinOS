"""ADR-0214: Tiered Delegation Engine (TDE) Components.

Phase 1 Core:
- RobustEngineDetector: Multi-signal ensemble for engine selection
- L34DelegationGate: Data-safe, fail-closed delegation check
- LossProfileTracker: In-session learning from outcomes
- SlashCommandParser: /use-engine command parsing

Phase 2 Integration:
- EngineRegistry: Central registry of all agentic engines
- AdaptiveDelegationExecutor: Parallel execution with sampling-loss-measurement
- SendIntegration: L22 send() hookpoint (select + execute)
- WorkerIPC: Interface for delegating to remote workers

Phase 3 Streaming (Stubs):
- StreamingExecutor: Big-data streaming with L34 filtering
"""

from .adaptive_delegation_executor import (
    AdaptiveDelegationExecutor,
    DelegationEnvelope,
    StepResult,
)
from .engine_registry import EngineRegistry, get_registry
from .l34_delegation_gate import L34DelegationGate, DelegationGateResult
from .loss_profile_tracker import LossProfileTracker, LossEntry
from .robust_engine_detector import RobustEngineDetector, DetectionSignals
from .send_integration import SendIntegration
from .slash_command_parser import SlashCommandParser, ParseResult
from .streaming_executor import StreamingExecutor
from .worker_ipc import WorkerIPCInterface, MockWorkerIPC, A2AWorkerIPC, get_worker_ipc

__all__ = [
    # Phase 1
    "RobustEngineDetector",
    "DetectionSignals",
    "L34DelegationGate",
    "DelegationGateResult",
    "LossProfileTracker",
    "LossEntry",
    "SlashCommandParser",
    "ParseResult",
    # Phase 2
    "EngineRegistry",
    "get_registry",
    "AdaptiveDelegationExecutor",
    "DelegationEnvelope",
    "StepResult",
    "SendIntegration",
    # Phase 3
    "StreamingExecutor",
    "WorkerIPCInterface",
    "MockWorkerIPC",
    "A2AWorkerIPC",
    "get_worker_ipc",
]
