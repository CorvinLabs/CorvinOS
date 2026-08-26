"""
Consolidation layer: self-healing, error recovery, dead-code detection, module analysis, and performance profiling.

Phase 4 ADRs:
- ADR-0332: Self-Healing (Non-Blocking Recovery)
- ADR-0333: Error Recovery and Retry Logic
- ADR-0421: Dead-Code Detection + Module Dependencies Analysis
- ADR-XXXX: Performance Profiling + SLO Tracking (Phase 4)
"""

from .self_healing import (
    CircuitState,
    BackoffConfig,
    ExponentialBackoff,
    CircuitBreakerConfig,
    CircuitBreaker,
    DegradationConfig,
    GracefulDegradation,
    report_recovery_attempt,
)

from .error_recovery import (
    ErrorClass,
    ErrorClassifier,
    Checkpoint,
    StateRollback,
    FallbackConfig,
    FallbackStrategy,
    RetryLogic,
)

from .dead_code_detector import (
    DeadCodeDetector,
    DeadCodeFinding,
    DeadCodeReport,
)

from .module_analyzer import (
    ModuleAnalyzer,
    CircularDependency,
    BoundaryViolation,
    ModuleDependencyReport,
)

from .profiler import (
    SLOStatus,
    SLOThreshold,
    SLOAlert,
    MetricPoint,
    CheckpointStats,
    Profiler,
    get_profiler,
    reset_profiler,
)
# Import profiler Checkpoint with alias to avoid name conflict with error_recovery.Checkpoint
from .profiler import Checkpoint as ProfilingCheckpoint

__all__ = [
    # self_healing
    "CircuitState",
    "BackoffConfig",
    "ExponentialBackoff",
    "CircuitBreakerConfig",
    "CircuitBreaker",
    "DegradationConfig",
    "GracefulDegradation",
    "report_recovery_attempt",
    # error_recovery
    "ErrorClass",
    "ErrorClassifier",
    "Checkpoint",
    "StateRollback",
    "FallbackConfig",
    "FallbackStrategy",
    "RetryLogic",
    # dead_code_detector (ADR-0421)
    "DeadCodeDetector",
    "DeadCodeFinding",
    "DeadCodeReport",
    # module_analyzer (ADR-0421)
    "ModuleAnalyzer",
    "CircularDependency",
    "BoundaryViolation",
    "ModuleDependencyReport",
    # profiler (Phase 4 Performance ADRs)
    "SLOStatus",
    "SLOThreshold",
    "SLOAlert",
    "MetricPoint",
    "CheckpointStats",
    "Profiler",
    "ProfilingCheckpoint",
    "get_profiler",
    "reset_profiler",
]
