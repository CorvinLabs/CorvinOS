"""Route Safety Validation — Deny-by-Default (ADR-0324).

Every route decision is fail-closed: unknown routes are denied and audited.
Cross-tenant isolation enforced at validation time.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class RouteDecision:
    """Result of a route validation decision."""

    def __init__(self, allowed: bool, reason: str, route: str):
        """Initialize decision.

        Args:
            allowed: Whether route is allowed
            reason: Human-readable reason for decision
            route: The route being decided
        """
        self.allowed = allowed
        self.reason = reason
        self.route = route

    def __repr__(self) -> str:
        status = "ALLOW" if self.allowed else "DENY"
        return f"RouteDecision({status}: {self.route} — {self.reason})"


class RouteValidator:
    """Validates routes at runtime with fail-closed deny-by-default policy."""

    def __init__(self, permit_set: Optional[set[str]] = None):
        """Initialize validator with permit set.

        Args:
            permit_set: Routes that are allowed. Empty/None = only genesis + default routes.
        """
        # Genesis + default routes always allowed
        self._permit_set = {
            "/health",
            "/status",
            "/init",
            "/shutdown",
        }
        if permit_set:
            self._permit_set.update(permit_set)

        self._audit_log: list[RouteDecision] = []

    def validate_route(
        self,
        route: str,
        *,
        source: str,
        action: str,
        tenant_id: str,
    ) -> RouteDecision:
        """Validate a route request (deny-by-default).

        Args:
            route: Route path to validate (e.g., "/api/users")
            source: Source of request (e.g., "console", "bridge")
            action: Action being performed (e.g., "read", "write")
            tenant_id: Tenant ID for isolation check

        Returns:
            RouteDecision with allowed/denied status

        Raises:
            ValueError: If tenant_id is invalid
        """
        # Validate tenant_id
        if not tenant_id or not isinstance(tenant_id, str):
            decision = RouteDecision(
                allowed=False,
                reason=f"Invalid tenant_id: {tenant_id}",
                route=route,
            )
            self._audit_log.append(decision)
            raise ValueError(f"Invalid tenant_id: {tenant_id}")

        # Normalize route
        normalized_route = route.rstrip("/") or "/"

        # Check permit set (fail-closed: not in set = deny)
        allowed = normalized_route in self._permit_set

        reason = (
            f"Route '{normalized_route}' is whitelisted (source={source}, action={action}, tenant={tenant_id})"
            if allowed
            else f"Route '{normalized_route}' is NOT whitelisted (source={source}, action={action}, tenant={tenant_id})"
        )

        decision = RouteDecision(allowed=allowed, reason=reason, route=normalized_route)
        self._audit_log.append(decision)

        return decision

    def audit_decision(self, route: str, allowed: bool) -> None:
        """Record a route decision to audit trail (manual entry).

        Args:
            route: Route that was decided
            allowed: Whether route was allowed
        """
        reason = "whitelisted" if allowed else "denied"
        decision = RouteDecision(allowed=allowed, reason=reason, route=route)
        self._audit_log.append(decision)
        logger.info(f"Route audit: {decision}")

    def get_allowed_routes(self, tenant_id: str) -> set[str]:
        """List all permitted routes for a tenant.

        Args:
            tenant_id: Tenant ID (for audit)

        Returns:
            Set of permitted routes

        Raises:
            ValueError: If tenant_id is invalid
        """
        if not tenant_id or not isinstance(tenant_id, str):
            raise ValueError(f"Invalid tenant_id: {tenant_id}")

        logger.debug(f"Tenant {tenant_id} querying permitted routes: {self._permit_set}")
        return self._permit_set.copy()

    def add_route(self, route: str, tenant_id: str) -> None:
        """Add a route to the permit set.

        Args:
            route: Route to add
            tenant_id: Tenant ID (for audit)

        Raises:
            ValueError: If route or tenant_id is invalid
        """
        if not route or not isinstance(route, str):
            raise ValueError(f"Invalid route: {route}")
        if not tenant_id or not isinstance(tenant_id, str):
            raise ValueError(f"Invalid tenant_id: {tenant_id}")

        normalized = route.rstrip("/") or "/"
        self._permit_set.add(normalized)
        logger.debug(f"Route added: {normalized} (tenant={tenant_id})")

    def remove_route(self, route: str, tenant_id: str) -> None:
        """Remove a route from the permit set.

        Args:
            route: Route to remove
            tenant_id: Tenant ID (for audit)

        Raises:
            ValueError: If route is genesis/default or tenant_id invalid
        """
        if not tenant_id or not isinstance(tenant_id, str):
            raise ValueError(f"Invalid tenant_id: {tenant_id}")

        normalized = route.rstrip("/") or "/"

        # Prevent removal of genesis routes
        if normalized in {"/health", "/status", "/init", "/shutdown"}:
            raise ValueError(f"Cannot remove genesis route: {normalized}")

        self._permit_set.discard(normalized)
        logger.debug(f"Route removed: {normalized} (tenant={tenant_id})")

    def get_audit_log(self) -> list[RouteDecision]:
        """Return full audit log of route decisions.

        Returns:
            List of all route decisions made
        """
        return self._audit_log.copy()

    def clear_audit_log(self) -> None:
        """Clear audit log (test cleanup only)."""
        self._audit_log.clear()
