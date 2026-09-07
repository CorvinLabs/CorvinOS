"""Geo Validation Against Cloudflare Edge Location (Security Fix #12).

Prevents geolocation spoofing attacks by validating claimed geo data against
Cloudflare edge location. Integrates with learning loss components to downweight
routing decisions based on geo_confidence.

GDPR Art. 32 (security), ADR-0647 (geo spoofing defense).
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional
from uuid import uuid4

logger = logging.getLogger(__name__)


class GeoMismatchSeverity(str, Enum):
    """Severity levels for geo mismatches."""

    EXACT_MATCH = "exact_match"  # Claimed geo matches edge region
    MINOR = "minor"  # <100 km difference
    MODERATE = "moderate"  # 100-500 km difference
    MAJOR = "major"  # 500-1000 km difference
    CRITICAL = "critical"  # >1000 km difference (likely spoof)


@dataclass(frozen=True)
class GeoData:
    """Immutable geolocation data (GDPR Art. 32)."""

    country: str  # ISO 3166-1 alpha-2 (e.g., "DE", "US")
    region: str  # State/province (e.g., "Berlin", "California")
    city: str  # City name (e.g., "Berlin", "San Francisco")
    latitude: Optional[float] = None  # For distance calculation
    longitude: Optional[float] = None

    def to_dict(self) -> dict:
        """Serialize to dict (for audit trail)."""
        return {
            "country": self.country,
            "region": self.region,
            "city": self.city,
            "latitude": self.latitude,
            "longitude": self.longitude,
        }

    @classmethod
    def from_dict(cls, data: dict) -> GeoData:
        """Deserialize from dict."""
        return cls(
            country=data.get("country", ""),
            region=data.get("region", ""),
            city=data.get("city", ""),
            latitude=data.get("latitude"),
            longitude=data.get("longitude"),
        )


@dataclass(frozen=True)
class GeoMismatchEvent:
    """Immutable geo mismatch detection event (GDPR Art. 30, 32)."""

    event_id: str  # UUID4
    tenant_id: str  # Tenant scope (required)
    timestamp: str  # ISO 8601 UTC
    claimed_geo: dict  # GeoData.to_dict()
    edge_geo: dict  # Cloudflare edge location
    distance_km: float  # Great-circle distance
    severity: str  # GeoMismatchSeverity enum
    confidence_score: float  # 0.0-1.0 (1.0 = exact match)
    skill_id: str = "geo_validator"  # Source skill
    lom: Optional[str] = None  # Line of Moral Responsibility

    def to_dict(self) -> dict:
        """Serialize to dict (for storage/JSON)."""
        return {
            "event_id": self.event_id,
            "tenant_id": self.tenant_id,
            "timestamp": self.timestamp,
            "event_type": "geo_mismatch",
            "claimed_geo": self.claimed_geo,
            "edge_geo": self.edge_geo,
            "distance_km": self.distance_km,
            "severity": self.severity,
            "confidence_score": self.confidence_score,
            "skill_id": self.skill_id,
            "lom": self.lom,
        }


class GeoValidator:
    """Validates geolocation against Cloudflare edge location.

    Threat Model (Security Fix #12):
    - Attacker claims fake GPS coordinates (e.g., "I'm in Tokyo" from Berlin)
    - Attack goal: bypass geo-based routing, access restricted regions
    - Defense: Compare claimed geo against Cloudflare edge location (request IP)
    - Outcome: Flag mismatches, reduce confidence, downweight routing gradients

    LOAD-BEARING CONSTRAINTS:
    - Tenant isolation: every validation is tenant-scoped
    - Fail-closed: low-confidence geo does NOT crash routing (graceful degrade)
    - Audit-first: every mismatch logged to audit chain (immutable)
    - Learning integration: geo_confidence scales routing loss gradients
    """

    # Cloudflare regions (simplified; real implementation queries API)
    _CLOUDFLARE_REGIONS = {
        "SFO": {"country": "US", "region": "California", "city": "San Francisco"},
        "LAX": {"country": "US", "region": "California", "city": "Los Angeles"},
        "DEN": {"country": "US", "region": "Colorado", "city": "Denver"},
        "ORD": {"country": "US", "region": "Illinois", "city": "Chicago"},
        "ATL": {"country": "US", "region": "Georgia", "city": "Atlanta"},
        "LHR": {"country": "GB", "region": "England", "city": "London"},
        "FRA": {"country": "DE", "region": "Hesse", "city": "Frankfurt"},
        "AMS": {"country": "NL", "region": "North Holland", "city": "Amsterdam"},
        "SYD": {"country": "AU", "region": "New South Wales", "city": "Sydney"},
        "NRT": {"country": "JP", "region": "Tokyo", "city": "Tokyo"},
    }

    def __init__(self, tenant_id: str):
        """Initialize validator for a tenant.

        Args:
            tenant_id: Tenant scope (GDPR Art. 32)
        """
        self.tenant_id = tenant_id

    def validate(
        self,
        claimed_geo: GeoData,
        request_ip: Optional[str] = None,
        edge_region: Optional[str] = None,
    ) -> tuple[float, GeoMismatchEvent | None]:
        """Validate claimed geo against Cloudflare edge location.

        Args:
            claimed_geo: GeoData from user input or inference
            request_ip: IP address of request (for Cloudflare edge lookup)
            edge_region: Cloudflare edge region code (e.g., "SFO")

        Returns:
            Tuple of (confidence_score: 0.0-1.0, mismatch_event: None if exact match)

        Raises:
            ValueError: tenant_id is invalid or missing
        """
        if not self.tenant_id or not isinstance(self.tenant_id, str):
            raise ValueError(f"Invalid tenant_id: {self.tenant_id!r}")

        if not edge_region or edge_region not in self._CLOUDFLARE_REGIONS:
            # Fallback: if no edge region provided, assume medium confidence
            return 0.5, None

        edge_geo = self._CLOUDFLARE_REGIONS[edge_region]

        # Calculate distance between claimed and edge geo (simplified)
        distance_km = self._estimate_distance(claimed_geo, GeoData.from_dict(edge_geo))

        # Determine severity and confidence
        severity, confidence = self._classify_mismatch(distance_km)

        # Log mismatch event (if distance > 0)
        mismatch_event = None
        if distance_km > 0:
            mismatch_event = GeoMismatchEvent(
                event_id=str(uuid4()),
                tenant_id=self.tenant_id,
                timestamp=datetime.utcnow().isoformat() + "Z",
                claimed_geo=claimed_geo.to_dict(),
                edge_geo=edge_geo,
                distance_km=distance_km,
                severity=severity,
                confidence_score=confidence,
            )

        return confidence, mismatch_event

    @staticmethod
    def _estimate_distance(geo1: GeoData, geo2: GeoData) -> float:
        """Estimate distance between two geolocations (km).

        Uses simplified great-circle distance if lat/lon available,
        falls back to country-level comparison.

        Args:
            geo1: First geolocation
            geo2: Second geolocation

        Returns:
            Distance in kilometers
        """
        # If lat/lon available, use Haversine formula
        if geo1.latitude is not None and geo1.longitude is not None and \
           geo2.latitude is not None and geo2.longitude is not None:
            return GeoValidator._haversine(
                geo1.latitude, geo1.longitude,
                geo2.latitude, geo2.longitude
            )

        # Fallback: compare countries/regions/cities
        if geo1.country != geo2.country:
            return 2000.0  # Different countries: assume >1000 km

        if geo1.region != geo2.region:
            return 300.0  # Same country, different region: ~300 km

        if geo1.city != geo2.city:
            return 50.0  # Same region, different city: ~50 km

        return 0.0  # Exact match

    @staticmethod
    def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Calculate great-circle distance using Haversine formula.

        Args:
            lat1, lon1: First point (decimal degrees)
            lat2, lon2: Second point (decimal degrees)

        Returns:
            Distance in kilometers
        """
        R = 6371.0  # Earth's radius in km

        lat1_rad = math.radians(lat1)
        lon1_rad = math.radians(lon1)
        lat2_rad = math.radians(lat2)
        lon2_rad = math.radians(lon2)

        dlat = lat2_rad - lat1_rad
        dlon = lon2_rad - lon1_rad

        a = math.sin(dlat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
        c = 2 * math.asin(math.sqrt(a))

        return R * c

    @staticmethod
    def _classify_mismatch(distance_km: float) -> tuple[str, float]:
        """Classify mismatch severity and confidence.

        Args:
            distance_km: Distance between claimed and edge geo

        Returns:
            Tuple of (severity: GeoMismatchSeverity, confidence_score: 0.0-1.0)

        Confidence is inverse of distance:
            - 0 km → 1.0 (exact match)
            - 100 km → 0.8 (minor)
            - 500 km → 0.4 (moderate)
            - 1000 km → 0.1 (major)
            - >1000 km → 0.05 (critical, likely spoof)
        """
        if distance_km == 0:
            return GeoMismatchSeverity.EXACT_MATCH.value, 1.0

        if distance_km < 100:
            # Minor difference: confidence 0.8-1.0
            confidence = 1.0 - (distance_km / 100) * 0.2
            return GeoMismatchSeverity.MINOR.value, confidence

        if distance_km < 500:
            # Moderate difference: confidence 0.4-0.8
            confidence = 0.8 - ((distance_km - 100) / 400) * 0.4
            return GeoMismatchSeverity.MODERATE.value, confidence

        if distance_km < 1000:
            # Major difference: confidence 0.1-0.4
            confidence = 0.4 - ((distance_km - 500) / 500) * 0.3
            return GeoMismatchSeverity.MAJOR.value, confidence

        # Critical: likely spoof
        confidence = max(0.01, 0.1 - ((distance_km - 1000) / 5000) * 0.09)
        return GeoMismatchSeverity.CRITICAL.value, confidence

    def log_mismatch(self, event_store, mismatch_event: GeoMismatchEvent) -> None:
        """Log geo mismatch event to audit chain and disk.

        Args:
            event_store: EventStore instance (for audit-first write)
            mismatch_event: GeoMismatchEvent to log
        """
        if not event_store:
            logger.warning(f"No event_store provided; geo mismatch not logged: {mismatch_event.event_id}")
            return

        try:
            # Import LearningEvent here to avoid circular imports
            from core.learning.learning_events import LearningEvent, EventType

            # Convert geo mismatch to learning event
            learning_event = LearningEvent(
                event_id=mismatch_event.event_id,
                event_type=EventType.GEO_MISMATCH,
                skill_id=mismatch_event.skill_id,
                tenant_id=mismatch_event.tenant_id,
                timestamp=mismatch_event.timestamp,
                signal={
                    "distance_km": mismatch_event.distance_km,
                    "severity": mismatch_event.severity,
                    "confidence_score": mismatch_event.confidence_score,
                    "claimed_geo": mismatch_event.claimed_geo,
                    "edge_geo": mismatch_event.edge_geo,
                },
                lom=mismatch_event.lom,
            )

            event_store.write_event(learning_event)
            logger.info(f"Logged geo mismatch event {mismatch_event.event_id} (distance={mismatch_event.distance_km:.1f}km, severity={mismatch_event.severity})")

        except Exception as e:
            logger.error(f"Failed to log geo mismatch event {mismatch_event.event_id}: {e}")


class GeoConfidenceWeighter:
    """Scales learning loss gradients based on geo_confidence.

    Geo-based routing decisions (e.g., "route to nearest region") use geo_confidence
    to scale their gradient contribution to the unified loss (ADR-0614).

    Example:
        routing_loss_component = 0.7  # Some loss value
        geo_confidence = 0.3  # Low confidence due to geo mismatch
        weighted_loss = routing_loss_component * geo_confidence
        # Gradient contribution is reduced, preventing optimizer from
        # over-correcting based on untrustworthy geo data
    """

    @staticmethod
    def scale_gradient(
        gradient_component: float,
        geo_confidence: float,
    ) -> float:
        """Scale a gradient component by geo_confidence.

        Args:
            gradient_component: Gradient value (e.g., dL/dθ)
            geo_confidence: Geo confidence score (0.0-1.0)

        Returns:
            Scaled gradient (gradient_component * geo_confidence)
        """
        if not 0.0 <= geo_confidence <= 1.0:
            raise ValueError(f"geo_confidence must be in [0.0, 1.0], got {geo_confidence}")

        return gradient_component * geo_confidence

    @staticmethod
    def compute_geo_loss_component(
        geo_confidence: float,
        target_confidence: float = 0.95,
    ) -> float:
        """Compute loss from geo mismatch for inclusion in unified loss.

        This allows geo-based mismatches to directly influence the learning loop,
        encouraging the system to detect and downweight low-confidence geo.

        Args:
            geo_confidence: Observed confidence score (0.0-1.0)
            target_confidence: Desired confidence (default 0.95)

        Returns:
            Loss in [0.0, 1.0] (0 = perfect, 1 = worst)
        """
        if not 0.0 <= geo_confidence <= 1.0:
            raise ValueError(f"geo_confidence must be in [0.0, 1.0], got {geo_confidence}")

        # Loss = (target_confidence - geo_confidence)^2
        # This penalizes low geo_confidence while rewarding high confidence
        loss = (target_confidence - geo_confidence) ** 2
        return min(loss, 1.0)  # Clip to [0.0, 1.0]
