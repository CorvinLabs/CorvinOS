"""Phase 2.2 Monitor Subsystems for autonomous session management.

5 Monitor Subsystems:
1. GoalAlignmentMonitor — Detect semantic drift from original goal
2. ConsistencyValidator — Detect contradictions in decisions
3. AssumptionTracker — Validate unvalidated assumptions
4. ExplorationScheduler — Detect and escape local optima
5. SelfMonitoringSubsystem — Detect cognitive overload

ADR-0407: Session Manager Phase 2.2 Monitor Subsystems
Depends on: ADR-0406 (Phase 2.1 Core)
Integration: SubsystemHub (ADR-0347), EventBus (ADR-0348), ContextPipeline v2 (ADR-0399)
"""

from .base import MonitorBase, MonitorAlert, AlertType
from .goal_alignment import GoalAlignmentMonitor
from .consistency_validator import ConsistencyValidator
from .assumption_tracker import AssumptionTracker
from .exploration_scheduler import ExplorationScheduler
from .self_monitoring import SelfMonitoringSubsystem

__all__ = [
    "MonitorBase",
    "MonitorAlert",
    "AlertType",
    "GoalAlignmentMonitor",
    "ConsistencyValidator",
    "AssumptionTracker",
    "ExplorationScheduler",
    "SelfMonitoringSubsystem",
]
