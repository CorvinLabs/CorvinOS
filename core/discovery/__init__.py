"""A2A Discovery Module — ADR-2059.

Implements zero-config A2A pairing with audit-first design.
"""
from core.discovery.discovery_coordinator import (
    DiscoveryCoordinator,
    InstanceIdentity,
    PairingRecord,
    PairingState,
    RetryStrategy,
    bootstrap_discovery_coordinator,
)

__all__ = [
    "DiscoveryCoordinator",
    "InstanceIdentity",
    "PairingRecord",
    "PairingState",
    "RetryStrategy",
    "bootstrap_discovery_coordinator",
]
