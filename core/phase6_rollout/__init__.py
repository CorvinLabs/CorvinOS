"""
Phase 6 Production Rollout Framework — ADR-0423

Implements canary deployment, traffic ramping, monitoring, incident detection,
and automated rollback for the unified 7-layer architecture.

Key components:
- Orchestrator: drives canary→50%→100% rollout with health gates
- Simulation: generates realistic production metrics for testing
- Monitoring: collects health signals (throughput, latency, errors, audit)
- RampController: manages traffic split + auto-promotion
- IncidentDetector: identifies anomalies + triggers playbooks
- BlueGreenDeploy: orchestrates deployment coordination
- IncidentPlaybooks: automated + manual response procedures
"""

__version__ = "1.0.0"
__all__ = [
    "Orchestrator",
    "SimulationFramework",
    "HealthMonitor",
    "RampController",
    "IncidentDetector",
    "BlueGreenDeployment",
    "IncidentPlaybooks",
]
