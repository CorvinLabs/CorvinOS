"""
Sentry Integration for CorvinOS Console Production Monitoring

Tracks:
- Routing decisions (model_selected, complexity_tier, cost, confidence)
- Model API failures (fallback logic)
- Token estimation errors
- Budget exceeded scenarios
- Audit chain failures (CRITICAL)

Breadcrumb categories:
- routing_decision: model selection, tier, confidence
- model_selected: which model was chosen (haiku/sonnet/opus)
- cost_estimated: estimated cost in USD
- tenant_id: tenant scope
- task_id: task identifier
- audit_chain: audit chain events (CRITICAL)
"""

import os
import logging
from typing import Optional, Dict, Any
from datetime import datetime
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration

logger = logging.getLogger(__name__)


def initialize_sentry(
    dsn: Optional[str] = None,
    environment: str = "production",
    release: str = "v5.1.1-hotfix",
    traces_sample_rate: float = 0.1,
    profiles_sample_rate: float = 0.1,
) -> None:
    """
    Initialize Sentry for error tracking and performance monitoring.

    Args:
        dsn: Sentry DSN (defaults to SENTRY_DSN env var)
        environment: Environment name (production/staging/development)
        release: Release version tag
        traces_sample_rate: Performance monitoring sample rate (0.0-1.0)
        profiles_sample_rate: Profiling sample rate (0.0-1.0)

    CRITICAL: Must be called at app startup before any request handling.
    """
    dsn = dsn or os.environ.get("SENTRY_DSN")

    if not dsn:
        logger.warning(
            "Sentry DSN not provided (SENTRY_DSN env var not set). "
            "Error tracking will be disabled."
        )
        return

    try:
        sentry_sdk.init(
            dsn=dsn,
            environment=environment,
            release=release,
            traces_sample_rate=traces_sample_rate,
            profiles_sample_rate=profiles_sample_rate,
            # Integrations
            integrations=[
                FastApiIntegration(),
                StarletteIntegration(),
            ],
            # Error filtering (don't send debug-level issues)
            before_send=_before_send,
            # Attach additional context
            before_send_transaction=_before_send_transaction,
        )
        logger.info(f"✅ Sentry initialized (env={environment}, release={release})")
    except Exception as e:
        logger.error(f"Failed to initialize Sentry: {e}")


def _before_send(event: Dict[str, Any], hint: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Filter and enrich events before sending to Sentry.

    Rules:
    - Drop debug/info level logs (only WARNING and ERROR)
    - Drop PII-suspected events
    - Attach routing context if available
    """
    # Skip debug/info level logs
    if event.get("level") in ("debug", "info"):
        return None

    # Drop events with PII patterns (email, API key, token)
    message = str(event.get("message", "")).lower()
    for pii_pattern in ["@example.com", "api_key", "password", "secret", "token"]:
        if pii_pattern in message:
            return None

    # Enrich with breadcrumb context
    breadcrumbs = event.get("breadcrumbs", [])
    if breadcrumbs:
        last_breadcrumb = breadcrumbs[-1]
        if last_breadcrumb.get("category") == "routing_decision":
            event.setdefault("contexts", {})["routing"] = {
                "model": last_breadcrumb.get("data", {}).get("model_selected"),
                "confidence": last_breadcrumb.get("data", {}).get("confidence"),
                "cost_estimate": last_breadcrumb.get("data", {}).get("cost_estimate"),
            }

    return event


def _before_send_transaction(event: Dict[str, Any], hint: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Filter performance transactions before sending.

    Rules:
    - Drop health check transactions
    - Keep marketplace transactions at 100% (SLO-critical)
    """
    name = event.get("transaction", "")

    # Skip health checks
    if "health" in name.lower() or "ping" in name.lower():
        return None

    # Keep marketplace at 100% sampling
    if "/marketplace/" in name:
        return event

    return event


class SentryBreadcrumbManager:
    """Helper to add structured breadcrumbs for audit trail."""

    @staticmethod
    def routing_decision(
        model_selected: str,
        complexity_tier: str,
        confidence: float,
        cost_estimate: float,
        latency_estimate_ms: int,
        reasoning: str,
    ) -> None:
        """Record a routing decision breadcrumb."""
        sentry_sdk.add_breadcrumb(
            category="routing_decision",
            message=f"Routing: {model_selected} (tier={complexity_tier}, confidence={confidence:.2f})",
            level="info",
            data={
                "model_selected": model_selected,
                "complexity_tier": complexity_tier,
                "confidence": confidence,
                "cost_estimate": cost_estimate,
                "latency_estimate_ms": latency_estimate_ms,
                "reasoning": reasoning,
                "timestamp_utc": datetime.utcnow().isoformat() + "Z",
            },
        )

    @staticmethod
    def model_api_failure(
        model: str,
        error: str,
        fallback_model: Optional[str] = None,
        retry_count: int = 0,
    ) -> None:
        """Record a model API failure."""
        sentry_sdk.add_breadcrumb(
            category="model_api_error",
            message=f"Model API failure: {model}",
            level="warning",
            data={
                "model": model,
                "error": error,
                "fallback_model": fallback_model,
                "retry_count": retry_count,
                "timestamp_utc": datetime.utcnow().isoformat() + "Z",
            },
        )

    @staticmethod
    def token_estimation_error(
        estimated_tokens: int,
        actual_tokens: int,
        error_percent: float,
        task_id: str,
    ) -> None:
        """Record a token estimation accuracy issue."""
        sentry_sdk.add_breadcrumb(
            category="token_estimation_error",
            message=f"Token estimation off by {error_percent:.1f}%",
            level="warning" if abs(error_percent) > 50 else "info",
            data={
                "estimated_tokens": estimated_tokens,
                "actual_tokens": actual_tokens,
                "error_percent": error_percent,
                "task_id": task_id,
                "timestamp_utc": datetime.utcnow().isoformat() + "Z",
            },
        )

    @staticmethod
    def budget_exceeded(
        estimated_cost: float,
        daily_budget: float,
        percentage_used: float,
        tenant_id: str,
    ) -> None:
        """Record budget threshold breach."""
        sentry_sdk.add_breadcrumb(
            category="budget_exceeded",
            message=f"Cost approaching budget: {percentage_used:.1f}%",
            level="error" if percentage_used > 100 else "warning",
            data={
                "estimated_cost": estimated_cost,
                "daily_budget": daily_budget,
                "percentage_used": percentage_used,
                "tenant_id": tenant_id,
                "timestamp_utc": datetime.utcnow().isoformat() + "Z",
            },
        )

    @staticmethod
    def audit_chain_failure(
        error_type: str,
        message: str,
        chain_height: int,
        last_hash: str,
    ) -> None:
        """Record audit chain failure (CRITICAL)."""
        sentry_sdk.add_breadcrumb(
            category="audit_chain_failure",
            message=f"CRITICAL: Audit chain failure - {error_type}",
            level="error",
            data={
                "error_type": error_type,
                "message": message,
                "chain_height": chain_height,
                "last_hash": last_hash,
                "timestamp_utc": datetime.utcnow().isoformat() + "Z",
            },
        )

    @staticmethod
    def circuit_breaker_triggered(
        endpoint: str,
        reason: str,
        recovery_delay_sec: int,
    ) -> None:
        """Record circuit breaker activation."""
        sentry_sdk.add_breadcrumb(
            category="circuit_breaker",
            message=f"Circuit breaker triggered: {endpoint}",
            level="error",
            data={
                "endpoint": endpoint,
                "reason": reason,
                "recovery_delay_sec": recovery_delay_sec,
                "timestamp_utc": datetime.utcnow().isoformat() + "Z",
            },
        )

    @staticmethod
    def learning_loop_event(
        skill_id: str,
        event_type: str,
        signal: Any,
        confidence_delta: float = 0.0,
    ) -> None:
        """Record a learning loop event."""
        sentry_sdk.add_breadcrumb(
            category="learning_event",
            message=f"Learning: {skill_id} - {event_type}",
            level="info",
            data={
                "skill_id": skill_id,
                "event_type": event_type,
                "signal": str(signal),
                "confidence_delta": confidence_delta,
                "timestamp_utc": datetime.utcnow().isoformat() + "Z",
            },
        )


def capture_routing_error(
    model: str,
    tier: str,
    error: Exception,
    context: Dict[str, Any] = None,
) -> None:
    """
    Capture a routing-related error with full context.

    CRITICAL: Should only be used for actual errors, not debug info.
    """
    with sentry_sdk.push_scope() as scope:
        scope.set_tag("component", "intelligent_router")
        scope.set_tag("model", model)
        scope.set_tag("tier", tier)
        if context:
            scope.set_context("routing_context", context)

        sentry_sdk.capture_exception(error)


def capture_audit_chain_error(error: Exception, chain_info: Dict[str, Any]) -> None:
    """
    Capture audit chain error (CRITICAL for GDPR compliance).

    CRITICAL: This must alert immediately to security team.
    """
    with sentry_sdk.push_scope() as scope:
        scope.set_level("error")
        scope.set_tag("component", "audit_chain")
        scope.set_tag("severity", "CRITICAL")
        scope.set_context("audit_chain_info", chain_info)

        sentry_sdk.capture_exception(error)


# Middleware for FastAPI app integration
from fastapi import FastAPI, Request


def add_sentry_middleware(app: FastAPI) -> None:
    """
    Add Sentry middleware to FastAPI app.

    This captures exceptions and attaches request context automatically.
    """
    from starlette.middleware.errors import ServerErrorMiddleware

    @app.middleware("http")
    async def sentry_middleware(request: Request, call_next):
        """Attach request context to Sentry."""
        with sentry_sdk.push_scope() as scope:
            # Attach request metadata
            scope.set_context("request", {
                "method": request.method,
                "url": str(request.url),
                "path": request.url.path,
                "query_params": dict(request.query_params),
            })

            # Extract tenant_id if present
            if hasattr(request.state, "tenant_id"):
                scope.set_tag("tenant_id", request.state.tenant_id)

            # Extract task_id if present
            if hasattr(request.state, "task_id"):
                scope.set_tag("task_id", request.state.task_id)

            try:
                response = await call_next(request)

                # Record latency
                if hasattr(response, "headers"):
                    response.headers["X-Sentry-Request-Id"] = sentry_sdk.get_current_scope().get_request_context()

                return response
            except Exception as e:
                sentry_sdk.capture_exception(e)
                raise


if __name__ == "__main__":
    # Demo: initialize and test breadcrumb collection
    initialize_sentry(dsn="https://example@sentry.io/12345")

    # Record a routing decision
    SentryBreadcrumbManager.routing_decision(
        model_selected="claude-opus-5",
        complexity_tier="complex",
        confidence=0.95,
        cost_estimate=0.0045,
        latency_estimate_ms=2500,
        reasoning="Task complexity > 250 tokens",
    )

    # Record a model API failure
    SentryBreadcrumbManager.model_api_failure(
        model="claude-opus-5",
        error="Rate limit exceeded",
        fallback_model="claude-sonnet-5",
        retry_count=1,
    )

    print("✅ Sentry demo complete")
