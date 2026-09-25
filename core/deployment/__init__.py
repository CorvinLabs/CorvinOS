"""
Deployment State Management — Phase 1: Drift Prevention

Ensures all instances run identical code version and detects divergence.
Central to 100% drift prevention architecture.
"""

from .state_sync import DeploymentStateManager
from .manifest import ManifestManager

__all__ = [
    "DeploymentStateManager",
    "ManifestManager",
]
