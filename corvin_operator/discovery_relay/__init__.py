# Discovery Relay Service
# Enables multi-instance CorvinOS deployments to find and catalog each other.
# ADR-2061: Instance Discovery Relay Service

from .relay import DiscoveryRelay, create_relay_app

__all__ = ["DiscoveryRelay", "create_relay_app"]
