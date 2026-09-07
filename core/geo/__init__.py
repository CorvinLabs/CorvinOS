"""Geo-Tracking Module (ADR-0206, ADR-0208).

Provides geographic resolution and aggregation for distributed instances.

Features:
  - Instance location resolution (instance_id → lat/lon/country/region/city)
  - 10km grid quantization (privacy-preserving)
  - Loss score mapping (visualization)
  - Tier 3 geo-tracking (country/region/city + 10km grid)
  - Tenant-scoped queries (GDPR Art. 6, 32)
  - Audit-logged resolutions

Public API:
  - InstanceLocator: Instance location resolver
  - GeoCoordinate: Immutable geographic coordinate
  - InstanceLocation: Instance location + loss metadata
"""

from .instance_locator import (
    InstanceLocator,
    GeoCoordinate,
    InstanceLocation,
)

__all__ = [
    "InstanceLocator",
    "GeoCoordinate",
    "InstanceLocation",
]
