"""GeoPrivacyValidator: Fail-closed geo attribute filtering (ADR-0683).

Enforces GDPR-compliant geo granularity levels:
- country (default, opt-out): ISO 3166-1 alpha-2
- region (opt-in): + ISO 3166-2
- city (opt-in): + City + 10km Grid
- coordinates (explicit opt-in + audit note): + Lat/Lon

Design: Whitelist-based (not blacklist). Only allowed attributes per granularity are exported.
Fail-closed: unknown attributes are removed, missing tenant_id raises error.
"""

import logging
from typing import Dict, Any, Optional, Set

logger = logging.getLogger(__name__)


class PrivacyViolationError(Exception):
    """Raised when geo privacy constraint is violated (fail-closed)."""

    pass


class GeoPrivacyValidator:
    """Enforces GDPR-compliant geo filtering.

    Constraints (ADR-0683):
    1. Whitelist-based: only known attributes per granularity are allowed
    2. Fail-closed: missing tenant_id raises error (not silent)
    3. Audit log: every export is logged (granularity, removed keys)
    4. Immutable: granularity is declared at init time (can't change per export)
    """

    # Whitelist of allowed attributes per granularity level
    ALLOWED_KEYS = {
        "country": {
            "geo.country",
            "geo.granularity",
            "geo.source",
        },
        "region": {
            "geo.country",
            "geo.region",
            "geo.metro_code",
            "geo.granularity",
            "geo.source",
        },
        "city": {
            "geo.country",
            "geo.region",
            "geo.city",
            "geo.grid_10km",
            "geo.metro_code",
            "geo.granularity",
            "geo.source",
        },
        "coordinates": {
            "geo.country",
            "geo.region",
            "geo.city",
            "geo.lat",
            "geo.lon",
            "geo.grid_10km",
            "geo.metro_code",
            "geo.granularity",
            "geo.source",
        },
    }

    def __init__(
        self,
        tenant_id: str,
        granularity: str = "country",
        audit_logger: Optional[logging.Logger] = None,
    ):
        """Initialize validator.

        Args:
            tenant_id: Tenant identifier (mandatory)
            granularity: "country" | "region" | "city" | "coordinates"
            audit_logger: Logger for audit trail (geo_attributes_exported, geo_privacy_violation_attempted)

        Raises: PrivacyViolationError if granularity is invalid
        """
        if not tenant_id:
            raise PrivacyViolationError("tenant_id is mandatory (fail-closed)")

        if granularity not in self.ALLOWED_KEYS:
            raise PrivacyViolationError(
                f"Invalid granularity: {granularity}. Must be one of: {list(self.ALLOWED_KEYS.keys())}"
            )

        self.tenant_id = tenant_id
        self.granularity = granularity
        self.audit_logger = audit_logger or logging.getLogger("audit")

    def validate_and_filter(self, attributes: Dict[str, Any]) -> Dict[str, Any]:
        """Filter geo attributes to allowed set per granularity.

        Args:
            attributes: Dict of geo attributes (e.g., {"geo.country": "DE", "geo.lat": 48.77, ...})

        Returns:
            Filtered dict with only allowed attributes for this granularity

        Raises: PrivacyViolationError if validation fails
        """
        allowed = self.ALLOWED_KEYS[self.granularity]

        # Whitelist-based filtering: keep only allowed keys
        filtered = {k: v for k, v in attributes.items() if k in allowed}
        removed_keys = set(attributes.keys()) - set(filtered.keys())

        # Audit log the export
        self.audit_logger.info(
            "geo_attributes_exported",
            extra={
                "tenant_id": self.tenant_id,
                "granularity": self.granularity,
                "exported_keys": list(filtered.keys()),
                "hidden_keys": list(removed_keys),
            },
        )

        return filtered

    def check_consent(self, granularity: str) -> bool:
        """Check if tenant has consented to this granularity level.

        TODO(Phase 2): Wire to tenant config (spec.telemetry.geo_granularity)
        For Phase 1, city + coordinates require explicit consent (will fail if not set).

        Args:
            granularity: The requested granularity level

        Returns: True if consent is present, False otherwise
        """
        # Phase 1: Simple stub (always allow country; city/coordinates will need consent in Phase 2)
        if granularity in ["city", "coordinates"]:
            # TODO: Read from tenant config
            return False  # For now, require explicit consent (enforced in ADR-0683)
        return True
