"""corvin_compute.fabric — ADR-0026 Compute Fabric (Sections A, B, C).

Re-exports the public surface of all three sub-systems.
"""
from __future__ import annotations

from .config import FabricConfig, OracleConfig, ParallelConfig, RegistryConfig
from .audit_events import FABRIC_AUDIT_EVENTS

__all__ = [
    "FabricConfig",
    "OracleConfig",
    "ParallelConfig",
    "RegistryConfig",
    "FABRIC_AUDIT_EVENTS",
]
