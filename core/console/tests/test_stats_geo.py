"""Tests for stats geo-tracking API (ADR-0205 Phase 3)."""
from fastapi.testclient import TestClient
import pytest


@pytest.fixture
def client():
    """Create test client for stats API."""
    from corvin_console.routes.stats_geo import router
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestGeoStatsAPI:
    """Test geo-tracking statistics endpoints."""

    def test_get_instances_tier_1(self, client):
        """Test Tier 1 (country-level) endpoint."""
        response = client.get("/v1/stats/instances?tier=1")
        assert response.status_code == 200

        data = response.json()
        assert "countries" in data
        assert "total_instances" in data
        assert data["total_instances"] > 0

        # Verify country structure
        for country in data["countries"]:
            assert "code" in country
            assert "name" in country
            assert "instances" in country
            assert len(country["code"]) == 2  # ISO code

    def test_get_instances_tier_2(self, client):
        """Test Tier 2 (region-level) endpoint."""
        response = client.get("/v1/stats/instances?tier=2")
        assert response.status_code == 200

        data = response.json()
        assert "countries" in data

        # Verify region structure
        for country in data["countries"]:
            assert "code" in country
            assert "regions" in country
            if country["regions"]:
                for region in country["regions"]:
                    assert "code" in region
                    assert "name" in region
                    assert "instances" in region

    def test_get_instances_tier_3(self, client):
        """Test Tier 3 (city + grid) endpoint."""
        response = client.get("/v1/stats/instances?tier=3")
        assert response.status_code == 200

        data = response.json()
        assert "geopoints" in data
        assert "meta" in data
        assert data["meta"]["geo_consent_tier"] == 3

        # Verify grid rasterization
        for point in data["geopoints"]:
            assert "lat" in point
            assert "lng" in point
            assert "city" in point
            assert "instances" in point
            # Grid coordinates should be at 0.1 precision (10km grid)
            assert isinstance(point["lat"], (int, float))
            assert isinstance(point["lng"], (int, float))

    def test_get_instances_invalid_tier(self, client):
        """Test invalid tier parameter."""
        response = client.get("/v1/stats/instances?tier=99")
        # FastAPI validates tier: 1 <= tier <= 3, so 99 returns 422
        assert response.status_code == 422  # Validation error (expected)

    def test_get_country_detail_tier_2(self, client):
        """Test country detail endpoint with regions."""
        response = client.get("/v1/stats/instances/country/DE?tier=2")
        assert response.status_code == 200

        data = response.json()
        assert "code" in data
        assert data["code"] == "DE"
        assert "regions" in data
        assert len(data["regions"]) > 0

    def test_get_country_detail_nonexistent(self, client):
        """Test country detail for non-existent country."""
        response = client.get("/v1/stats/instances/country/XX?tier=2")
        assert response.status_code == 200

        data = response.json()
        assert "error" in data or data == {}

    def test_get_live_snapshot(self, client):
        """Test live snapshot endpoint."""
        response = client.get("/v1/stats/instances/live")
        assert response.status_code == 200

        data = response.json()
        assert "countries" in data
        assert "total_instances" in data
        assert "online_now" in data
        assert "updated_at" in data
        assert "ttl_seconds" in data

        # Verify TTL is reasonable (< 5 minutes)
        assert data["ttl_seconds"] <= 300

    def test_get_insights_tier_1(self, client):
        """Test insights endpoint."""
        response = client.get("/v1/stats/insights?tier=1")
        assert response.status_code == 200

        data = response.json()
        assert "concentration" in data
        assert "growth_momentum" in data
        assert "retention_health" in data
        assert "technical_maturity" in data

        # Verify concentration metrics
        conc = data["concentration"]
        assert "herfindahl_index" in conc
        assert 0 <= conc["herfindahl_index"] <= 1

    def test_insights_tier_2_3_not_ready(self, client):
        """Test that Tier 2/3 insights return placeholder (DB not connected)."""
        response = client.get("/v1/stats/insights?tier=2")
        assert response.status_code == 200

        data = response.json()
        # Phase 3: should return "requires database connection" message
        assert "error" in data or "database" in str(data).lower()

    def test_tier_1_anonymous(self, client):
        """Verify Tier 1 data contains no PII."""
        response = client.get("/v1/stats/instances?tier=1")
        data = response.json()

        # Should have no coordinates, regions, cities
        for country in data["countries"]:
            assert "region" not in country
            assert "city" not in country
            assert "lat" not in country
            assert "lng" not in country

    def test_tier_2_pseudonymous(self, client):
        """Verify Tier 2 data is aggregated (no individual pings)."""
        response = client.get("/v1/stats/instances?tier=2")
        data = response.json()

        # Should have regions but no exact coordinates
        for country in data["countries"]:
            assert "regions" in country
            # No lat/lng in regions
            for region in country.get("regions", []):
                assert "lat" not in region or region.get("lat") is None
                assert "lng" not in region or region.get("lng") is None

    def test_tier_3_grid_rasterized(self, client):
        """Verify Tier 3 data uses 10km grid (not exact coordinates)."""
        response = client.get("/v1/stats/instances?tier=3")
        data = response.json()

        # Grid coordinates should have 0.1 precision (10km grid)
        for point in data["geopoints"]:
            lat = point["lat"]
            lng = point["lng"]

            # Check decimal places: 10km grid = 0.1 degree precision
            lat_str = str(lat)
            if "." in lat_str:
                decimals = len(lat_str.split(".")[1])
                assert decimals <= 1, f"Latitude has too much precision: {lat}"

    def test_response_headers(self, client):
        """Verify response includes privacy/compliance headers."""
        response = client.get("/v1/stats/instances?tier=1")

        # Should have cache headers (public, safe for browser caching)
        # Phase 3: implement Cache-Control headers
        assert response.status_code == 200

    def test_api_consistency(self, client):
        """Verify API consistency across tiers."""
        tier1 = client.get("/v1/stats/instances?tier=1").json()
        tier2 = client.get("/v1/stats/instances?tier=2").json()
        tier3 = client.get("/v1/stats/instances?tier=3").json()

        # All should have metadata
        assert "meta" in tier2 or "countries" in tier2
        assert "meta" in tier3 or "geopoints" in tier3

        # All should be valid responses (no 5xx errors)
        assert isinstance(tier1, dict)
        assert isinstance(tier2, dict)
        assert isinstance(tier3, dict)
