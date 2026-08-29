"""Feature tier system (ADR-0423).

Complete implementation of automatic feature graduation from ALPHA → BETA → STABLE → PRODUCTION.

**Modules:**
- `telemetry_collector`: Collects GDPR-safe usage events
- `promotion_analytics`: Computes promotion eligibility scores
- `promotion_audit`: Hash-chained audit trail for all tier transitions
"""

__all__ = [
    "telemetry_collector",
    "promotion_analytics",
    "promotion_audit",
]
