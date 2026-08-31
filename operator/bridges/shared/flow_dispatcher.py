"""flow_dispatcher.py — CorvinFlow M2: Capability-aware node Dispatcher stub.

ADR-0121 M2.  The Dispatcher selects a registered endpoint for a flow step's
node slot based on capability requirements declared in the FlowBundle's
nodes.yaml.  This module ships as a stub for M1 — wire real endpoint
resolution in M2 when flow_bundle.py and corvin-a2a endpoint registry are
integrated.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class NodeRequirements:
    """Capability requirements declared in FlowBundle nodes.yaml for a slot."""

    capabilities: list[str] = field(default_factory=list)
    compliance_zone: str = ""
    max_classification: str = "PUBLIC"
    min_tier: str = "free"


@dataclass(frozen=True)
class RegisteredEndpoint:
    """A registered A2A endpoint that can satisfy a node slot."""

    endpoint_id: str
    instance_id: str
    url: str
    capabilities: list[str] = field(default_factory=list)
    compliance_zone: str = ""
    locality: str = "local"


class NoCapableNodeError(Exception):
    """No registered endpoint satisfies the given NodeRequirements."""


class Dispatcher:
    """Select a RegisteredEndpoint for a flow step.

    M1 stub: returns the local pseudo-endpoint for any slot; raises
    NoCapableNodeError if requirements specify a remote zone without
    any registered endpoints.  Real endpoint registry integration is M2.
    """

    _LOCAL_ENDPOINT = RegisteredEndpoint(
        endpoint_id="local",
        instance_id="local",
        url="",
        capabilities=["*"],
        compliance_zone="local",
        locality="local",
    )

    def __init__(self, registered: list[RegisteredEndpoint] | None = None) -> None:
        self._registered: list[RegisteredEndpoint] = registered or []

    def resolve(
        self, slot: str, requirements: NodeRequirements | None = None
    ) -> RegisteredEndpoint:
        """Return the best endpoint for slot, or raise NoCapableNodeError."""
        req = requirements or NodeRequirements()

        candidates = [
            ep for ep in self._registered
            if self._satisfies(ep, req)
        ]
        if candidates:
            return candidates[0]

        # Fall through to local if zone allows
        if req.compliance_zone in ("", "local"):
            return self._LOCAL_ENDPOINT

        raise NoCapableNodeError(
            f"No registered endpoint satisfies slot '{slot}' "
            f"with requirements {req}"
        )

    @staticmethod
    def _satisfies(ep: RegisteredEndpoint, req: NodeRequirements) -> bool:
        if req.compliance_zone and ep.compliance_zone != req.compliance_zone:
            return False
        if "*" not in ep.capabilities:
            if not all(c in ep.capabilities for c in req.capabilities):
                return False
        return True
