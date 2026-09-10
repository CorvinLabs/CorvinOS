"""Middleware modules for console API.

Includes:
- RoutingDisclosureHeadersMiddleware: Adds X-Routed-By, X-User-Tier, etc.
"""

from .routing_disclosure_headers import (
    RoutingDisclosureHeadersMiddleware,
    add_routing_disclosure_headers,
)

__all__ = [
    "RoutingDisclosureHeadersMiddleware",
    "add_routing_disclosure_headers",
]
