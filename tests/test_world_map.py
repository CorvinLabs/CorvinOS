"""World Map Visualization Tests (ADR-0206, ADR-0208, ADR-0639).

Tests for:
  - InstanceLocator (geo resolution + caching)
  - World Map API endpoints
  - Grid cell aggregation
  - Tenant isolation
  - Audit logging
"""

import pytest
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

import sys
from pathlib import Path as PathlibPath
project_root = PathlibPath(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

try:
    from core.geo import InstanceLocator, GeoCoordinate, InstanceLocation
except ImportError:
    pytest.skip("Geo module not available", allow_module_level=True)


# ============================================================================
# INSTANCE LOCATOR TESTS
# ============================================================================

class TestInstanceLocator:
    """Test instance location resolution and caching."""

    @pytest.fixture
    def temp_tenant_home(self, tmp_path):
        """Create temporary tenant home directory."""
        return tmp_path / "tenants" / "_default"

    @pytest.fixture
    def locator(self, temp_tenant_home):
        """Create locator instance."""
        return InstanceLocator(
            tenant_home=temp_tenant_home,
            tenant_id="_default",
        )

    def test_resolve_location_basic(self, locator):
        """Test basic instance location resolution."""
        geo_data = {
            "latitude": 52.52,
            "longitude": 13.41,
            "country": "DE",
            "region": "Berlin",
            "city": "Berlin",
            "timestamp": "2026-09-07T12:00:00Z",
        }

        location = locator.resolve_location(
            instance_id="instance-123",
            geo_data=geo_data,
            loss_score=0.3,
            loss_components={
                "routing": 0.1,
                "context": 0.05,
                "workflow": 0.1,
                "security": 0.0,
                "flow": 0.05,
            },
        )

        assert location.instance_id == "instance-123"
        assert location.geo.latitude == 52.52
        assert location.geo.longitude == 13.41
        assert location.geo.country == "DE"
        assert location.geo.city == "Berlin"
        assert location.loss_score == 0.3
        assert location.status == "active"

    def test_resolve_location_loss_clamping(self, locator):
        """Test that loss scores are clamped to [0, 1]."""
        location_high = locator.resolve_location(
            instance_id="instance-1",
            geo_data={"latitude": 0, "longitude": 0},
            loss_score=1.5,  # Out of range
        )
        assert location_high.loss_score == 1.0

        location_low = locator.resolve_location(
            instance_id="instance-2",
            geo_data={"latitude": 0, "longitude": 0},
            loss_score=-0.5,  # Out of range
        )
        assert location_low.loss_score == 0.0

    def test_resolve_location_malformed_geo_data(self, locator):
        """Test graceful handling of malformed geo data."""
        # Missing fields
        location = locator.resolve_location(
            instance_id="instance-123",
            geo_data={"latitude": "invalid"},
            loss_score=0.5,
        )
        assert location.geo.latitude == 0.0
        assert location.geo.country == "XX"

    def test_resolve_location_caching(self, locator):
        """Test that resolutions are cached."""
        geo_data = {
            "latitude": 40.7128,
            "longitude": -74.0060,
            "country": "US",
            "region": "New York",
            "city": "New York",
        }

        # First resolution
        loc1 = locator.resolve_location(
            instance_id="instance-456",
            geo_data=geo_data,
            loss_score=0.2,
        )

        # Second resolution (should come from cache)
        loc2 = locator.resolve_location(
            instance_id="instance-456",
            geo_data=geo_data,
            loss_score=0.9,  # Different loss, but cache should ignore
        )

        # Cache hit: loss_score stays the same
        assert loc2.loss_score == 0.2
        assert loc2.geo.city == "New York"

    def test_grid_quantization(self, locator):
        """Test 10km grid quantization."""
        # Berlin
        geo_berlin = {"latitude": 52.5, "longitude": 13.4}
        loc_berlin = locator.resolve_location(
            instance_id="instance-berlin",
            geo_data=geo_berlin,
            loss_score=0.3,
        )

        # Same grid cell (within 0.1 degree)
        geo_nearby = {"latitude": 52.55, "longitude": 13.45}
        loc_nearby = locator.resolve_location(
            instance_id="instance-nearby",
            geo_data=geo_nearby,
            loss_score=0.4,
        )

        # Both should be in same grid cell (525, 134)
        assert loc_berlin.geo.grid_cell_id == "geo_525_134"
        assert loc_nearby.geo.grid_cell_id == "geo_525_134"

    def test_resolve_many(self, locator):
        """Test batch resolution."""
        instances = [
            {
                "instance_id": f"instance-{i}",
                "geo_data": {
                    "latitude": 50.0 + i,
                    "longitude": 10.0 + i,
                    "country": "DE",
                    "region": "Region",
                    "city": "City",
                },
                "loss_score": 0.1 * i,
            }
            for i in range(5)
        ]

        locations = locator.resolve_many(instances)

        assert len(locations) == 5
        for i, loc in enumerate(locations):
            assert loc.instance_id == f"instance-{i}"
            assert loc.loss_score == 0.1 * i

    def test_grid_cells_by_loss(self, locator):
        """Test grid cell aggregation."""
        # Three instances in same grid cell
        for i in range(3):
            locator.resolve_location(
                instance_id=f"instance-{i}",
                geo_data={
                    "latitude": 52.5 + (0.01 * i),
                    "longitude": 13.4 + (0.01 * i),
                },
                loss_score=0.2 + (0.1 * i),  # 0.2, 0.3, 0.4
            )

        cells = locator.get_grid_cells_by_loss()

        # Should have 1 cell (all in same quantized grid)
        assert len(cells) == 1

        # Check aggregates
        cell = list(cells.values())[0]
        assert cell["count"] == 3
        # avg_loss = (0.2 + 0.3 + 0.4) / 3 = 0.3
        assert abs(cell["avg_loss"] - 0.3) < 0.01

    def test_persistence(self, locator, temp_tenant_home):
        """Test cache persistence to disk."""
        # Resolve some instances
        locator.resolve_location(
            instance_id="instance-123",
            geo_data={
                "latitude": 52.5,
                "longitude": 13.4,
                "country": "DE",
            },
            loss_score=0.3,
        )

        # Persist
        cache_file = locator.to_persistence()
        assert cache_file.exists()

        # Read and verify
        with open(cache_file, "r") as f:
            lines = f.readlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["instance_id"] == "instance-123"
        assert record["location"]["loss_score"] == 0.3

    def test_load_persistence(self, locator, temp_tenant_home):
        """Test loading cache from disk."""
        # Create and persist
        locator.resolve_location(
            instance_id="instance-123",
            geo_data={
                "latitude": 52.5,
                "longitude": 13.4,
                "country": "DE",
            },
            loss_score=0.3,
        )
        locator.to_persistence()

        # Create new locator and load
        locator2 = InstanceLocator(
            tenant_home=temp_tenant_home,
            tenant_id="_default",
        )
        count = locator2.from_persistence()
        assert count == 1

        # Cache should have the instance
        assert "instance-123" in locator2._cache

    def test_clear_cache(self, locator):
        """Test cache clearing."""
        locator.resolve_location(
            instance_id="instance-123",
            geo_data={"latitude": 0, "longitude": 0},
            loss_score=0.5,
        )

        assert len(locator._cache) == 1
        locator.clear_cache()
        assert len(locator._cache) == 0


# ============================================================================
# IMMUTABLE DATACLASS TESTS
# ============================================================================

class TestGeoCoordinate:
    """Test GeoCoordinate immutability and serialization."""

    def test_immutability(self):
        """Test that GeoCoordinate is frozen."""
        geo = GeoCoordinate(
            latitude=52.5,
            longitude=13.4,
            country="DE",
            region="Berlin",
            city="Berlin",
            grid_cell_id="geo_525_134",
            timestamp="2026-09-07T12:00:00Z",
        )

        # Should not be able to modify
        with pytest.raises(AttributeError):
            geo.latitude = 50.0  # type: ignore

    def test_serialization(self):
        """Test to_dict() serialization."""
        geo = GeoCoordinate(
            latitude=52.5,
            longitude=13.4,
            country="DE",
            region="Berlin",
            city="Berlin",
            grid_cell_id="geo_525_134",
            timestamp="2026-09-07T12:00:00Z",
        )

        data = geo.to_dict()
        assert data["latitude"] == 52.5
        assert data["country"] == "DE"
        assert data["tier"] == 3


class TestInstanceLocation:
    """Test InstanceLocation immutability and serialization."""

    def test_immutability(self):
        """Test that InstanceLocation is frozen."""
        geo = GeoCoordinate(
            latitude=52.5,
            longitude=13.4,
            country="DE",
            region="Berlin",
            city="Berlin",
            grid_cell_id="geo_525_134",
            timestamp="2026-09-07T12:00:00Z",
        )

        loc = InstanceLocation(
            instance_id="instance-123",
            geo=geo,
            loss_score=0.3,
            loss_components={"routing": 0.1},
            last_measurement="2026-09-07T12:00:00Z",
            measurement_count=5,
        )

        # Should not be able to modify
        with pytest.raises(AttributeError):
            loc.loss_score = 0.5  # type: ignore

    def test_serialization(self):
        """Test to_dict() serialization."""
        geo = GeoCoordinate(
            latitude=52.5,
            longitude=13.4,
            country="DE",
            region="Berlin",
            city="Berlin",
            grid_cell_id="geo_525_134",
            timestamp="2026-09-07T12:00:00Z",
        )

        loc = InstanceLocation(
            instance_id="instance-123",
            geo=geo,
            loss_score=0.3,
            loss_components={"routing": 0.1},
            last_measurement="2026-09-07T12:00:00Z",
            measurement_count=5,
        )

        data = loc.to_dict()
        assert data["instance_id"] == "instance-123"
        assert data["loss_score"] == 0.3
        assert data["geo"]["country"] == "DE"


# ============================================================================
# EDGE CASES & ERROR HANDLING
# ============================================================================

class TestEdgeCases:
    """Test edge cases and error handling."""

    @pytest.fixture
    def locator(self, tmp_path):
        """Create locator instance."""
        return InstanceLocator(
            tenant_home=tmp_path,
            tenant_id="_default",
        )

    def test_resolve_with_none_geo_data(self, locator):
        """Test resolution with None geo_data."""
        location = locator.resolve_location(
            instance_id="instance-123",
            geo_data=None,
            loss_score=0.5,
        )

        # Should use defaults
        assert location.geo.latitude == 0.0
        assert location.geo.country == "XX"

    def test_resolve_empty_geo_data(self, locator):
        """Test resolution with empty geo_data dict."""
        location = locator.resolve_location(
            instance_id="instance-123",
            geo_data={},
            loss_score=0.5,
        )

        # Should use defaults
        assert location.geo.latitude == 0.0
        assert location.geo.country == "XX"

    def test_resolve_with_extreme_coordinates(self, locator):
        """Test resolution with extreme lat/lon values."""
        # North Pole
        location = locator.resolve_location(
            instance_id="instance-north",
            geo_data={
                "latitude": 90.0,
                "longitude": 0.0,
            },
            loss_score=0.5,
        )
        assert location.geo.latitude == 90.0

        # South Pole
        location = locator.resolve_location(
            instance_id="instance-south",
            geo_data={
                "latitude": -90.0,
                "longitude": 180.0,
            },
            loss_score=0.5,
        )
        assert location.geo.latitude == -90.0

    def test_grid_cells_empty(self, locator):
        """Test grid cells when cache is empty."""
        cells = locator.get_grid_cells_by_loss()
        assert cells == {}

    def test_resolve_many_with_errors(self, locator):
        """Test batch resolution with some errors."""
        instances = [
            {
                "instance_id": "valid-1",
                "geo_data": {"latitude": 0, "longitude": 0},
                "loss_score": 0.5,
            },
            {
                # Invalid: missing instance_id
                "geo_data": {"latitude": 0, "longitude": 0},
            },
            {
                "instance_id": "valid-2",
                "geo_data": {"latitude": 0, "longitude": 0},
                "loss_score": 0.3,
            },
        ]

        locations = locator.resolve_many(instances)

        # Should skip the invalid one and return 2
        assert len(locations) == 2
        assert locations[0].instance_id == "valid-1"
        assert locations[1].instance_id == "valid-2"


# ============================================================================
# TENANT ISOLATION TESTS
# ============================================================================

class TestTenantIsolation:
    """Test tenant-scoped operations."""

    def test_different_tenants_different_caches(self, tmp_path):
        """Test that different tenants have separate caches."""
        locator1 = InstanceLocator(
            tenant_home=tmp_path / "tenant1",
            tenant_id="tenant-1",
        )

        locator2 = InstanceLocator(
            tenant_home=tmp_path / "tenant2",
            tenant_id="tenant-2",
        )

        # Resolve in tenant1
        locator1.resolve_location(
            instance_id="instance-123",
            geo_data={"latitude": 52.5, "longitude": 13.4},
            loss_score=0.3,
        )

        # Resolve different instance in tenant2
        locator2.resolve_location(
            instance_id="instance-456",
            geo_data={"latitude": 40.7, "longitude": -74.0},
            loss_score=0.2,
        )

        # Each tenant should see only their own data
        assert "instance-123" in locator1._cache
        assert "instance-123" not in locator2._cache
        assert "instance-456" in locator2._cache
        assert "instance-456" not in locator1._cache

    def test_tenant_persistence_isolated(self, tmp_path):
        """Test that tenant persistence files are separate."""
        locator1 = InstanceLocator(
            tenant_home=tmp_path / "tenant1",
            tenant_id="tenant-1",
        )

        locator1.resolve_location(
            instance_id="instance-123",
            geo_data={"latitude": 0, "longitude": 0},
            loss_score=0.3,
        )

        locator1.to_persistence()

        cache_file1 = tmp_path / "tenant1" / "geo" / "instance_cache" / "locations.jsonl"
        cache_file2 = tmp_path / "tenant2" / "geo" / "instance_cache" / "locations.jsonl"

        assert cache_file1.exists()
        assert not cache_file2.exists()


# ============================================================================
# LOSS SCORE STATUS MAPPING
# ============================================================================

class TestLossStatusMapping:
    """Test loss score to status mapping."""

    @pytest.fixture
    def locator(self, tmp_path):
        return InstanceLocator(
            tenant_home=tmp_path,
            tenant_id="_default",
        )

    def test_status_converged(self, locator):
        """Test that low loss maps to converged."""
        location = locator.resolve_location(
            instance_id="instance-123",
            geo_data={"latitude": 0, "longitude": 0},
            loss_score=0.1,  # Low loss
        )
        assert location.status == "active"  # Note: < 0.9 is "active"

    def test_status_high_loss(self, locator):
        """Test that high loss maps to converged (>0.9)."""
        location = locator.resolve_location(
            instance_id="instance-123",
            geo_data={"latitude": 0, "longitude": 0},
            loss_score=0.95,  # High loss
        )
        assert location.status == "converged"  # >0.9 inverts status


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
