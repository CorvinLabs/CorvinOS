"""
Phase 6 Deployment Orchestration — Blue-Green deployment, traffic switching, rollback.

Coordinates deployment of Phase 6 code alongside Phase 5 (Blue-Green pattern).

Key components:
- BlueGreenDeployer: orchestrates deployment + traffic switching
- HealthCheck: validates deployed version
- RollbackExecutor: implements emergency rollback
- DeploymentCoordinator: manages the full deployment lifecycle
"""

__version__ = "1.0.0"
__all__ = [
    "BlueGreenDeployer",
    "HealthCheck",
    "RollbackExecutor",
    "DeploymentCoordinator",
]
