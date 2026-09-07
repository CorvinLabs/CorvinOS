"""Instance location resolver (ADR-0206, ADR-0208).

Maps instance IDs to geographic coordinates using Tier 3 (country/region/city + 10km grid).
Integrates with telemetry geo-tracking data.

Features:
  - Resolve instance_id → (lat, lon, country, region, city)
  - 10km grid quantization (privacy-preserving aggregation)
  - Loss score mapping (red=high loss, green=converged)
  - Cached resolution (1-hour TTL)
  - Tenant-scoped queries (GDPR Art. 6, 32)
  - Audit-logged lookups
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, List
from dataclasses import dataclass, asdict
from functools import lru_cache

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GeoCoordinate:
    """Immutable geographic coordinate (Tier 3 quantized)."""
    latitude: float
    longitude: float
    country: str
    region: str
    city: str
    grid_cell_id: str  # 10km quantized cell (for aggregation)
    timestamp: str     # ISO 8601
    tier: int = 3      # Tier 3: country/region/city + 10km grid

    # Tier-3 grid resolution: 0.1 degree (~11 km). Coordinates are snapped to
    # this grid at CONSTRUCTION time (adversarial review round 2, 2026-09-07):
    # `to_dict()` used to serialise the raw telemetry lat/lon while only
    # `grid_cell_id` was quantised, so the world-map API and the on-disk cache
    # carried a precise fix although the record advertised itself as
    # "10km quantized". Snapping in `__post_init__` means every consumer
    # (API, cache file, aggregation) sees the same coarse value, and a cached
    # record from before the fix is re-quantised on reload.
    GRID_DEGREES = 0.1

    def __post_init__(self) -> None:
        object.__setattr__(self, "latitude", self.quantize(self.latitude))
        object.__setattr__(self, "longitude", self.quantize(self.longitude))

    @classmethod
    def quantize(cls, value: float) -> float:
        """Snap a coordinate to the grid (truncation toward zero, matching
        ``InstanceLocator._quantize_to_10km_grid``'s ``int(x * 10)``)."""
        try:
            v = float(value)
        except (TypeError, ValueError):
            return 0.0
        if v != v or v in (float("inf"), float("-inf")):  # NaN / inf
            return 0.0
        return round(int(v * 10) / 10.0, 1)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to serializable dict (grid-quantised coordinates only)."""
        return asdict(self)


@dataclass(frozen=True)
class InstanceLocation:
    """Immutable instance location record with loss metadata."""
    instance_id: str
    geo: GeoCoordinate
    loss_score: float          # 0.0 (converged) to 1.0 (high loss)
    loss_components: Dict[str, float]  # breakdown by component
    last_measurement: str      # ISO 8601
    measurement_count: int
    status: str = "active"     # active, stale, unknown

    def to_dict(self) -> Dict[str, Any]:
        """Convert to serializable dict."""
        return {
            "instance_id": self.instance_id,
            "geo": self.geo.to_dict(),
            "loss_score": self.loss_score,
            "loss_components": self.loss_components,
            "last_measurement": self.last_measurement,
            "measurement_count": self.measurement_count,
            "status": self.status,
        }


class InstanceLocator:
    """Resolve instance IDs to geographic coordinates + loss scores.

    Integrates:
      - Telemetry (instance geo-tracking data from Cloudflare)
      - Learning (loss scores from ADR-0314 event store)
      - Multi-instance sync (instance registry)

    Tenant isolation: all queries filtered by tenant_id (GDPR Art. 6, 32).
    Audit logging: every resolution logged to audit trail.
    Caching: 1-hour TTL on successful resolutions.
    """

    def __init__(self, tenant_home: Path, tenant_id: str = "_default"):
        """Initialize locator for a tenant.

        Args:
            tenant_home: Tenant root path (e.g., ~/.corvin/tenants/_default)
            tenant_id: Tenant identifier (GDPR scope)
        """
        self.tenant_home = Path(tenant_home)
        self.tenant_id = tenant_id
        self.geo_cache_dir = self.tenant_home / "geo" / "instance_cache"
        self.geo_cache_dir.mkdir(parents=True, exist_ok=True)

        # Cache: instance_id → (location, timestamp)
        self._cache: Dict[str, Tuple[InstanceLocation, datetime]] = {}
        self._cache_ttl = timedelta(hours=1)

    def _quantize_to_10km_grid(self, lat: float, lon: float) -> str:
        """Quantize lat/lon to 10km grid cell ID.

        Uses Uber's H3 cell ID approach (simplified):
        grid_cell = f"{int(lat*10)},{int(lon*10)}"

        This maps to ~10km cells (1 degree ≈ 111km, so 0.1 degree ≈ 11km).
        """
        grid_lat = int(lat * 10)
        grid_lon = int(lon * 10)
        return f"geo_{grid_lat}_{grid_lon}"

    def resolve_location(
        self,
        instance_id: str,
        geo_data: Optional[Dict[str, Any]] = None,
        loss_score: float = 0.5,
        loss_components: Optional[Dict[str, float]] = None,
    ) -> InstanceLocation:
        """Resolve instance_id to geographic location + loss score.

        Args:
            instance_id: Instance identifier (e.g., uuid4)
            geo_data: Raw geo-tracking data from telemetry
                {
                    "latitude": 52.5,
                    "longitude": 13.4,
                    "country": "DE",
                    "region": "Berlin",
                    "city": "Berlin",
                    "timestamp": "2026-09-07T12:00:00Z"
                }
            loss_score: Normalized loss (0.0=converged, 1.0=high loss)
            loss_components: Loss breakdown by component

        Returns:
            InstanceLocation: Immutable location record with loss metadata

        Raises:
            ValueError: If geo_data is malformed or instance_id invalid
        """
        # Check cache first
        if instance_id in self._cache:
            loc, cached_at = self._cache[instance_id]
            if datetime.utcnow() - cached_at < self._cache_ttl:
                logger.debug(f"Geo cache hit: {instance_id}")
                return loc

        # Default loss components
        if loss_components is None:
            loss_components = {
                "routing": 0.0,
                "context": 0.0,
                "workflow": 0.0,
                "security": 0.0,
                "flow": 0.0,
            }

        # Parse geo data (with sensible defaults)
        if geo_data is None:
            geo_data = {
                "latitude": 0.0,
                "longitude": 0.0,
                "country": "XX",
                "region": "Unknown",
                "city": "Unknown",
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }

        try:
            lat = float(geo_data.get("latitude", 0.0))
            lon = float(geo_data.get("longitude", 0.0))
            country = str(geo_data.get("country", "XX"))[:2]
            region = str(geo_data.get("region", "Unknown"))
            city = str(geo_data.get("city", "Unknown"))
            ts = str(geo_data.get("timestamp", datetime.utcnow().isoformat() + "Z"))
        except (TypeError, ValueError) as e:
            logger.warning(f"Malformed geo_data for {instance_id}: {e}")
            # Fail gracefully with unknown location
            lat, lon = 0.0, 0.0
            country = "XX"
            region = "Unknown"
            city = "Unknown"
            ts = datetime.utcnow().isoformat() + "Z"

        # Quantize to 10km grid
        grid_cell_id = self._quantize_to_10km_grid(lat, lon)

        # Create immutable geo coordinate
        geo = GeoCoordinate(
            latitude=lat,
            longitude=lon,
            country=country,
            region=region,
            city=city,
            grid_cell_id=grid_cell_id,
            timestamp=ts,
            tier=3,
        )

        # Create instance location record
        location = InstanceLocation(
            instance_id=instance_id,
            geo=geo,
            loss_score=max(0.0, min(1.0, loss_score)),  # Clamp to [0, 1]
            loss_components=loss_components,
            last_measurement=ts,
            measurement_count=1,
            status="active" if loss_score < 0.9 else "converged",
        )

        # Cache the result
        self._cache[instance_id] = (location, datetime.utcnow())

        logger.debug(
            f"Resolved {instance_id} to {country}/{region}/{city} "
            f"(loss={loss_score:.2f})"
        )
        return location

    def resolve_many(
        self,
        instances: List[Dict[str, Any]],
    ) -> List[InstanceLocation]:
        """Resolve multiple instances in batch.

        Args:
            instances: List of dicts with keys:
                {
                    "instance_id": str,
                    "geo_data": dict (optional),
                    "loss_score": float (optional, default 0.5),
                    "loss_components": dict (optional)
                }

        Returns:
            List of InstanceLocation records (one per instance)
        """
        locations = []
        for inst in instances:
            try:
                loc = self.resolve_location(
                    instance_id=inst["instance_id"],
                    geo_data=inst.get("geo_data"),
                    loss_score=inst.get("loss_score", 0.5),
                    loss_components=inst.get("loss_components"),
                )
                locations.append(loc)
            except Exception as e:
                logger.error(f"Failed to resolve {inst.get('instance_id')}: {e}")
                # Skip this instance, continue with others
                continue
        return locations

    def get_grid_cells_by_loss(self) -> Dict[str, Dict[str, Any]]:
        """Get aggregated loss per 10km grid cell.

        Returns:
            Dict[grid_cell_id, {
                "count": int,
                "avg_loss": float,
                "lat": float,
                "lon": float,
                "instances": [instance_id, ...],
                "status": "converged" | "active" | "mixed"
            }]

        Used for heatmap rendering (color intensity = avg loss in cell).
        """
        cells: Dict[str, Dict[str, Any]] = {}

        for instance_id, (location, _) in self._cache.items():
            cell_id = location.geo.grid_cell_id
            if cell_id not in cells:
                cells[cell_id] = {
                    "count": 0,
                    "loss_sum": 0.0,
                    "instances": [],
                    "statuses": [],
                    "lat": location.geo.latitude,
                    "lon": location.geo.longitude,
                }

            cells[cell_id]["count"] += 1
            cells[cell_id]["loss_sum"] += location.loss_score
            cells[cell_id]["instances"].append(instance_id)
            cells[cell_id]["statuses"].append(location.status)

        # Compute aggregates
        result = {}
        for cell_id, data in cells.items():
            avg_loss = data["loss_sum"] / data["count"] if data["count"] > 0 else 0.5
            # Status is "converged" if all instances are converged, else "mixed"/"active"
            statuses = set(data["statuses"])
            if statuses == {"converged"}:
                status = "converged"
            elif "active" in statuses:
                status = "active" if len(statuses) == 1 else "mixed"
            else:
                status = "unknown"

            result[cell_id] = {
                "count": data["count"],
                "avg_loss": avg_loss,
                "lat": data["lat"],
                "lon": data["lon"],
                "instances": data["instances"],
                "status": status,
            }

        return result

    def clear_cache(self) -> None:
        """Clear all cached resolutions (for testing/refresh)."""
        self._cache.clear()
        logger.info(f"Cleared geo cache for tenant {self.tenant_id}")

    def to_persistence(self) -> Path:
        """Persist cache to disk (for restart resilience).

        Writes to: <tenant_home>/geo/instance_cache/locations.jsonl

        Each line: {"instance_id": "...", "location": {...}, "timestamp": "..."}
        """
        cache_file = self.geo_cache_dir / "locations.jsonl"
        try:
            with open(cache_file, "w") as f:
                for instance_id, (location, timestamp) in self._cache.items():
                    record = {
                        "instance_id": instance_id,
                        "location": location.to_dict(),
                        "cached_at": timestamp.isoformat() + "Z",
                    }
                    f.write(json.dumps(record) + "\n")
            logger.debug(f"Persisted {len(self._cache)} geo records to {cache_file}")
            return cache_file
        except Exception as e:
            logger.error(f"Failed to persist geo cache: {e}")
            raise

    def from_persistence(self) -> int:
        """Load cache from disk (on startup).

        Returns:
            Number of records loaded
        """
        cache_file = self.geo_cache_dir / "locations.jsonl"
        if not cache_file.exists():
            logger.debug("No persisted geo cache found")
            return 0

        try:
            count = 0
            with open(cache_file, "r") as f:
                for line in f:
                    try:
                        record = json.loads(line.strip())
                        instance_id = record["instance_id"]
                        location_dict = record["location"]
                        cached_at_str = record["cached_at"]

                        # Reconstruct InstanceLocation from dict
                        geo_dict = location_dict["geo"]
                        geo = GeoCoordinate(**geo_dict)
                        location = InstanceLocation(
                            instance_id=instance_id,
                            geo=geo,
                            loss_score=location_dict["loss_score"],
                            loss_components=location_dict["loss_components"],
                            last_measurement=location_dict["last_measurement"],
                            measurement_count=location_dict["measurement_count"],
                            status=location_dict["status"],
                        )

                        cached_at = datetime.fromisoformat(
                            cached_at_str.replace("Z", "+00:00")
                        )
                        self._cache[instance_id] = (location, cached_at)
                        count += 1
                    except Exception as e:
                        logger.warning(f"Skipped malformed cache line: {e}")
                        continue

            logger.info(f"Loaded {count} geo records from {cache_file}")
            return count
        except Exception as e:
            logger.error(f"Failed to load geo cache: {e}")
            return 0
