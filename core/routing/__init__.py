"""Route safety validation for fail-closed routing (ADR-0324)."""

from core.routing.safety import RouteDecision, RouteValidator

__all__ = [
    "RouteDecision",
    "RouteValidator",
]
