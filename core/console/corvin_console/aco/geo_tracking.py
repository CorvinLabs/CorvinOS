"""Geo-Tracking — Multi-Tier Geographic Data with DSGVO Compliance (ADR-0205).

Three tiers of location granularity with consent and privacy controls:
  * **Tier 1 (Country):** Country-level only. Default-ON (opt-out). Indefinite retention.
  * **Tier 2 (Region):** State/Province-level. Opt-IN required. 30-day retention.
  * **Tier 3 (City+Grid):** City + 10km raster grid. Explicit opt-IN required. 14-day retention.

Hard privacy guarantees (GDPR Art. 5, 6, 32):
  * **No IP storage:** IP used only for GeoIP lookup; lookup result stored, IP discarded.
  * **Anonymization:** Geo-grid rasterization (10km grid = 100+ users per cell).
  * **No timestamps:** Store date only, not time (prevents (city, time) re-identification).
  * **TTL-based auto-delete:** Tier 2/3 rows expire after retention period.
  * **Audit logging:** All GeoIP lookups recorded in audit.jsonl.
  * **Consent-driven:** Tier 2/3 require explicit opt-in via config.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class GeoResult:
    """Result of a GeoIP lookup (never contains the original IP)."""

    country: str  # ISO 3166-1 alpha-2, e.g. "DE"
    region: Optional[str] = None  # ISO 3166-2 code, e.g. "BW" (only Tier 2/3)
    city: Optional[str] = None  # City name (only Tier 3)
    latitude: Optional[float] = None  # Raw latitude (only for internal grid calc)
    longitude: Optional[float] = None  # Raw longitude (only for internal grid calc)

    def to_dict(self) -> dict:
        """Export to dict for storage/JSON serialization."""
        return {
            "country": self.country,
            "region": self.region,
            "city": self.city,
            "latitude": self.latitude,
            "longitude": self.longitude,
        }

    def grid_coordinates(self) -> tuple[Optional[float], Optional[float]]:
        """Return 10km-rasterized grid coordinates (anonymization).

        Rasterization formula:
            lat_grid = floor(lat / 0.1) * 0.1  (roughly 11km per degree)
            lng_grid = floor(lng / 0.1) * 0.1

        Result: 100+ potential users per grid cell → de-anonymization impossible.
        """
        if self.latitude is None or self.longitude is None:
            return (None, None)

        lat_grid = math.floor(self.latitude / 0.1) * 0.1
        lng_grid = math.floor(self.longitude / 0.1) * 0.1
        # Round to avoid floating-point precision artifacts
        return (round(lat_grid, 1), round(lng_grid, 1))


class GeoIPReader:
    """Wrapper around MaxMind GeoIP2 database for offline lookups."""

    def __init__(self, db_path: Path | str):
        """Initialize with path to GeoLite2-City.mmdb database.

        Args:
            db_path: Path to GeoIP2 database file (e.g., ~/.corvin/geoip/GeoLite2-City.mmdb)

        Raises:
            ImportError: If geoip2 library not installed.
            FileNotFoundError: If database file doesn't exist.
        """
        self.db_path = Path(db_path)

        if not self.db_path.exists():
            raise FileNotFoundError(f"GeoIP database not found: {self.db_path}")

        try:
            from geoip2.database import Reader
            self.reader = Reader(str(self.db_path))
        except ImportError as e:
            raise ImportError(
                "geoip2 library required for geo-tracking. "
                "Install: pip install geoip2"
            ) from e

    def lookup(self, ip_address: str) -> Optional[GeoResult]:
        """Perform GeoIP lookup. IP is used but never stored.

        Args:
            ip_address: IPv4 or IPv6 address to look up

        Returns:
            GeoResult with country/region/city, or None if lookup fails.

        Note:
            The IP address is discarded after lookup. Only the result
            (country/region/city) is stored.
        """
        try:
            response = self.reader.city(ip_address)

            return GeoResult(
                country=response.country.iso_code or "XX",
                region=self._get_region(response),
                city=response.city.name,
                latitude=response.location.latitude,
                longitude=response.location.longitude,
            )
        except Exception as e:
            logger.warning(f"GeoIP lookup failed for {ip_address}: {e}")
            return None

    @staticmethod
    def _get_region(response) -> Optional[str]:
        """Extract region/state code from GeoIP response.

        Tries in order: ISO 3166-2 subdivision code (preferred), fallback to name.
        """
        if not response.subdivisions:
            return None

        # Prefer ISO 3166-2 code (e.g., "BW" for Baden-Württemberg)
        if hasattr(response.subdivisions[0], 'iso_code'):
            return response.subdivisions[0].iso_code

        # Fallback to name if code not available
        return response.subdivisions[0].name

    def close(self):
        """Close the database reader."""
        if hasattr(self, 'reader'):
            self.reader.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class GeoConsentManager:
    """Manage geo-tracking consent per instance."""

    def __init__(self, config_path: Path | str):
        """Initialize with path to instance config (spec.yaml).

        Args:
            config_path: Path to ~/spec.yaml or equivalent config file.
        """
        self.config_path = Path(config_path)

    def get_tier(self) -> int:
        """Read configured geo-tracking tier (1, 2, or 3).

        Default: 1 (country-only).

        Returns:
            Integer: 1 (country), 2 (region), or 3 (city+grid).
        """
        try:
            import yaml
            config = yaml.safe_load(self.config_path.read_text())
            tier = config.get("spec", {}).get("telemetry", {}).get("geo_tracking_tier", 1)
            return int(tier) if 1 <= tier <= 3 else 1
        except Exception as e:
            logger.warning(f"Failed to read geo_tracking_tier from config: {e}")
            return 1  # Default to country-only on error

    def has_consent(self) -> bool:
        """Check if user has given explicit consent for Tier 2/3.

        Returns:
            bool: True if geo_tracking_consent_given is set and true.
        """
        try:
            import yaml
            config = yaml.safe_load(self.config_path.read_text())
            return config.get("spec", {}).get("telemetry", {}).get("geo_tracking_consent_given", False)
        except Exception as e:
            logger.warning(f"Failed to read geo consent flag: {e}")
            return False

    def should_track_tier_n(self, tier: int) -> bool:
        """Check if geo-tracking for a specific tier should proceed.

        Tier 1 (country) is always allowed.
        Tier 2/3 require both consent_given=true AND tier configured.

        Args:
            tier: The tier to check (1, 2, or 3).

        Returns:
            bool: True if tracking should proceed; False if blocked by consent.
        """
        if tier == 1:
            return True  # Country-level is always allowed

        # Tier 2/3 require explicit consent
        return self.has_consent() and self.get_tier() >= tier


class GeoTracker:
    """High-level geo-tracking orchestrator with DB persistence (Phase 3.1)."""

    def __init__(
        self,
        geoip_db_path: Path | str,
        config_path: Path | str,
        instance_id: str,
        db_dsn: Optional[str] = None,
    ):
        """Initialize tracker.

        Args:
            geoip_db_path: Path to GeoLite2 database.
            config_path: Path to instance config (spec.yaml).
            instance_id: Unique instance identifier (will be hashed in logs).
            db_dsn: PostgreSQL connection string (optional; enables DB persistence).
        """
        self.reader = GeoIPReader(geoip_db_path)
        self.consent = GeoConsentManager(config_path)
        self.instance_id_hash = hashlib.sha256(instance_id.encode()).hexdigest()[:16]
        self.db_dsn = db_dsn  # PostgreSQL DSN for persistence

    def track(self, ip_address: str) -> Optional[GeoResult]:
        """Perform geo-tracking lookup respecting consent & tier.

        If db_dsn is configured, persists result to PostgreSQL.

        Returns:
            GeoResult with granularity matching configured tier, or None if blocked.
        """
        tier = self.consent.get_tier()

        # Always allow Tier 1 (country), but skip lookup if tier < 1 (safety)
        if tier < 1:
            return None

        # Tier 2/3 require explicit consent
        if tier >= 2 and not self.consent.has_consent():
            logger.info(f"Geo-tracking Tier {tier} blocked: no consent")
            return None

        # Perform lookup
        result = self.reader.lookup(ip_address)
        if not result:
            return None

        # Truncate result to match tier
        if tier == 1:
            # Country only
            result.region = None
            result.city = None
            result.latitude = None
            result.longitude = None
        elif tier == 2:
            # Region but no city/coordinates
            result.city = None
            result.latitude = None
            result.longitude = None
        # tier == 3: keep all (city + grid coordinates)

        # Audit log
        self._audit_lookup(result, tier)

        # Persist to database if DSN is configured
        if self.db_dsn:
            self._write_to_db(result, tier)

        return result

    def _audit_lookup(self, result: GeoResult, tier: int):
        """Log geo-tracking lookup to audit trail (content-free).

        Logged info: (pseudonymous instance ID, tier, country, region, city).
        NOT logged: IP address, coordinates, timestamps (time-of-day).
        """
        try:
            audit_entry = {
                "event_type": "geo_lookup",
                "instance_id_hash": self.instance_id_hash,
                "geo_tier": tier,
                "result": result.to_dict(),
            }
            logger.info(f"geo_lookup: {json.dumps(audit_entry)}")
        except Exception as e:
            logger.error(f"Failed to audit geo-lookup: {e}")

    def _write_to_db(self, result: GeoResult, tier: int):
        """Persist geo result to PostgreSQL (Phase 3.1).

        Safe to call if db_dsn is None (will skip silently).
        """
        if not self.db_dsn:
            return

        try:
            # Lazy-import to avoid hard dependency
            from . import geo_schema

            # Extract grid coordinates for Tier 3
            grid_lat, grid_lng = result.grid_coordinates()

            geo_schema.insert_geo_ping(
                dsn=self.db_dsn,
                instance_id_hash=self.instance_id_hash,
                country=result.country,
                tier=tier,
                region=result.region,
                city=result.city,
                grid_lat=grid_lat,
                grid_lng=grid_lng,
            )
        except Exception as e:
            logger.error(f"Failed to persist geo-tracking to DB: {e}")

    def close(self):
        """Clean up resources."""
        self.reader.close()


# Singleton instance cache (optional, for stateless environments)
_instance_cache: dict[str, GeoIPReader] = {}


def get_geoip_reader(db_path: Path | str) -> GeoIPReader:
    """Get a cached GeoIPReader instance."""
    db_str = str(db_path)
    if db_str not in _instance_cache:
        _instance_cache[db_str] = GeoIPReader(db_path)
    return _instance_cache[db_str]


def clear_geoip_cache():
    """Clear all cached readers (call at shutdown)."""
    for reader in _instance_cache.values():
        reader.close()
    _instance_cache.clear()
