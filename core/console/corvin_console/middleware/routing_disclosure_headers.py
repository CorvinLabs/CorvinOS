"""Routing Disclosure Response Headers Middleware (CRITICAL — EU AI Act Art. 50).

Adds HTTP response headers that disclose:
1. The AI system that made the routing decision (os.delegation_router)
2. The user's license tier (free, paid, enterprise)
3. The routing confidence score (0.0–1.0)
4. The selected engine (haiku, sonnet, opus)
5. The routing reason (cost_optimized, quality_priority, latency_optimized)

GDPR Art. 13/14 (Transparency at Collection):
  Every routing decision is disclosed to the user via audit trail + response headers.

EU AI Act Art. 50 (Bot Disclosure):
  The user can immediately see which AI system made the routing decision
  (header X-Routed-By) and with what confidence (header X-Routing-Confidence).

COMPLIANCE NOTE:
  These headers are MANDATORY. Do not weaken or remove them.
  They are the primary user-facing disclosure of tier-based routing.
"""

from __future__ import annotations

import logging
from typing import Optional
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)


class RoutingDisclosureHeadersMiddleware(BaseHTTPMiddleware):
    """Adds routing decision disclosure headers to all responses.

    Headers added:
    - X-Routed-By: "os.delegation_router" (AI system)
    - X-User-Tier: "free" | "paid" | "enterprise" (user's license tier)
    - X-Routing-Confidence: "0.78" (0.0–1.0 confidence score)
    - X-Routing-Engine: "haiku" | "sonnet" | "opus" (selected model)
    - X-Routing-Reason: "cost_optimized" | "quality_priority" | "latency_optimized"
    - X-Tier-Based-Optimization: "true" (explicit statement)

    GDPR Compliance: Discloses to user that tier-based optimization is active.
    EU AI Act Compliance: Discloses AI system + confidence + decision.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        """Add routing disclosure headers to response."""
        response = await call_next(request)

        # Extract routing decision from request scope (if available)
        # In production, this would be populated by the routing skill
        routing_decision = request.scope.get("routing_decision", {})
        user_tier = request.scope.get("user_tier", "unknown")

        # Add mandatory disclosure headers
        response.headers["X-Routed-By"] = "os.delegation_router"
        response.headers["X-User-Tier"] = user_tier
        response.headers["X-Tier-Based-Optimization"] = "true"

        # Add optional decision details (if available)
        if routing_decision:
            confidence = routing_decision.get("confidence", 0.0)
            response.headers["X-Routing-Confidence"] = str(round(confidence, 2))

            engine = routing_decision.get("engine", "unknown")
            response.headers["X-Routing-Engine"] = engine

            reason = routing_decision.get("reason", "unknown")
            response.headers["X-Routing-Reason"] = reason

        return response


async def add_routing_disclosure_headers(
    request: Request,
    user_tier: str,
    routing_decision: Optional[dict] = None
) -> None:
    """Add routing disclosure to request scope (for downstream middleware).

    Call this from your routing skill or delegation policy to populate
    the request scope with routing decision data.

    Args:
        request: FastAPI Request
        user_tier: "free" | "paid" | "enterprise"
        routing_decision: Optional dict with keys:
            - confidence: float (0.0–1.0)
            - engine: str ("haiku" | "sonnet" | "opus")
            - reason: str ("cost_optimized" | "quality_priority" | "latency_optimized")
            - token_savings_percent: float (optional, e.g., 12.3)
    """
    request.scope["user_tier"] = user_tier
    if routing_decision:
        request.scope["routing_decision"] = routing_decision


# Example usage in a route handler:
#
# @router.post("/v1/run")
# async def run_task(request: Request, task: TaskRequest):
#     # Determine user tier
#     user_tier = get_user_tier(request)  # e.g., "paid"
#
#     # Get routing decision from os.delegation_router skill
#     routing_decision = await os_delegation_router.execute(task)
#
#     # Add to request scope (middleware will pick it up)
#     await add_routing_disclosure_headers(
#         request,
#         user_tier=user_tier,
#         routing_decision={
#             "confidence": routing_decision.confidence,
#             "engine": routing_decision.engine,
#             "reason": routing_decision.reason,
#             "token_savings_percent": 12.3
#         }
#     )
#
#     # Process request normally
#     return await execute_task(routing_decision.engine, task)
