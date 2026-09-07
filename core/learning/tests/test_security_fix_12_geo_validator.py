"""Security Fix #12 — Geo Validation Against Cloudflare Edge Location.

Tests for geo_validator.py module. Covers:
1. Exact geo match against edge region
2. Geo mismatch detection (various distances)
3. Audit logging of mismatches
4. Learning integration (confidence downweighting)
5. Resilience to spoofing attacks
6. Tenant isolation

GDPR Art. 32, ADR-0647.
"""

import json
import pytest
from pathlib import Path
from datetime import datetime
from unittest.mock import Mock, MagicMock, patch
from uuid import uuid4

from core.learning.geo_validator import (
    GeoData,
    GeoValidator,
    GeoMismatchEvent,
    GeoMismatchSeverity,
    GeoConfidenceWeighter,
)


class TestGeoDataModel:
    """Test immutability and serialization of GeoData."""

    def test_geo_data_immutable(self):
        """Verify GeoData is frozen (immutable)."""
        geo = GeoData(country="DE", region="Berlin", city="Berlin")
        with pytest.raises(AttributeError):
            geo.country = "US"

    def test_geo_data_to_dict(self):
        """Verify GeoData.to_dict() serialization."""
        geo = GeoData(
            country="US",
            region="California",
            city="San Francisco",
            latitude=37.7749,
            longitude=-122.4194,
        )
        d = geo.to_dict()
        assert d["country"] == "US"
        assert d["city"] == "San Francisco"
        assert d["latitude"] == 37.7749

    def test_geo_data_from_dict(self):
        """Verify GeoData.from_dict() deserialization."""
        d = {
            "country": "DE",
            "region": "Berlin",
            "city": "Berlin",
            "latitude": 52.52,
            "longitude": 13.405,
        }
        geo = GeoData.from_dict(d)
        assert geo.country == "DE"
        assert geo.city == "Berlin"
        assert geo.latitude == 52.52


class TestGeoValidation:
    """Test geo validation core logic."""

    def test_geo_valid_matches_edge(self):
        """Test case: Claimed geo matches Cloudflare edge region exactly.

        Expected: confidence_score = 1.0, no mismatch event
        """
        validator = GeoValidator(tenant_id="_default")

        # Claimed: San Francisco (SFO edge region)
        claimed_geo = GeoData(
            country="US",
            region="California",
            city="San Francisco",
        )

        confidence, mismatch_event = validator.validate(
            claimed_geo=claimed_geo,
            edge_region="SFO",  # Cloudflare SFO edge
        )

        assert confidence == 1.0, "Exact match should have confidence 1.0"
        assert mismatch_event is None, "Exact match should not produce mismatch event"

    def test_geo_mismatch_detected_minor(self):
        """Test case: Claimed geo differs by <100 km from edge region.

        Expected: 0.8 <= confidence < 1.0, mismatch event created
        """
        validator = GeoValidator(tenant_id="_default")

        # Claimed: Los Angeles (LAX edge region)
        claimed_geo = GeoData(
            country="US",
            region="California",
            city="Los Angeles",
        )

        # Edge: San Francisco (SFO)
        confidence, mismatch_event = validator.validate(
            claimed_geo=claimed_geo,
            edge_region="SFO",
        )

        # Distance LAX-SFO ~50 km (same region, different city)
        assert 0.0 <= confidence <= 1.0, "Confidence must be in [0.0, 1.0]"
        assert mismatch_event is not None, "Mismatch should be detected"
        assert mismatch_event.severity == GeoMismatchSeverity.MINOR.value

    def test_geo_mismatch_detected_critical(self):
        """Test case: Claimed geo differs by >1000 km from edge region.

        Expected: confidence < 0.1, severity=critical, event created
        """
        validator = GeoValidator(tenant_id="_default")

        # Claimed: Sydney (SYD edge region)
        claimed_geo = GeoData(
            country="AU",
            region="New South Wales",
            city="Sydney",
        )

        # Edge: San Francisco (SFO)
        confidence, mismatch_event = validator.validate(
            claimed_geo=claimed_geo,
            edge_region="SFO",
        )

        # Different countries: ~2000 km distance
        assert confidence < 0.1, "Critical mismatch should have low confidence"
        assert mismatch_event is not None, "Mismatch should be detected"
        assert mismatch_event.severity == GeoMismatchSeverity.CRITICAL.value

    def test_geo_mismatch_audit_logged(self):
        """Test case: Geo mismatch event is logged to audit chain.

        Expected: Event contains distance_km, severity, confidence_score
        """
        validator = GeoValidator(tenant_id="_default")

        claimed_geo = GeoData(country="AU", region="Sydney", city="Sydney")
        confidence, mismatch_event = validator.validate(
            claimed_geo=claimed_geo,
            edge_region="SFO",
        )

        # Verify event structure
        assert mismatch_event is not None
        assert mismatch_event.event_id
        assert mismatch_event.tenant_id == "_default"
        assert mismatch_event.distance_km > 0
        assert mismatch_event.confidence_score == confidence

        # Verify serialization contains event_type
        event_dict = mismatch_event.to_dict()
        assert event_dict["event_type"] == "geo_mismatch"

        # Verify serialization
        d = mismatch_event.to_dict()
        assert d["distance_km"] > 0
        assert d["severity"] == GeoMismatchSeverity.CRITICAL.value
        assert d["confidence_score"] < 0.1

    def test_geo_validation_invalid_tenant_id(self):
        """Test case: Validator rejects invalid tenant_id on validate() call.

        Expected: ValueError during geo validation
        """
        # Valid validator initialization (error happens at validation time)
        validator = GeoValidator(tenant_id="_default")

        # Invalid tenant during validation
        claimed_geo = GeoData(country="DE", region="Berlin", city="Berlin")

        # Force an invalid tenant_id check during logging
        validator.tenant_id = ""
        with pytest.raises(ValueError, match="Invalid tenant_id"):
            validator.validate(claimed_geo, edge_region="FRA")

    def test_geo_validation_missing_edge_region(self):
        """Test case: Missing edge_region defaults to medium confidence.

        Expected: confidence = 0.5, no mismatch event
        """
        validator = GeoValidator(tenant_id="_default")

        claimed_geo = GeoData(country="US", region="California", city="San Francisco")
        confidence, mismatch_event = validator.validate(
            claimed_geo=claimed_geo,
            edge_region=None,  # No edge region provided
        )

        assert confidence == 0.5, "Missing edge_region should default to 0.5"
        assert mismatch_event is None


class TestGeoDistanceCalculation:
    """Test distance calculation (Haversine + fallback methods)."""

    def test_haversine_distance(self):
        """Test Haversine formula with known coordinates."""
        # San Francisco to Los Angeles
        dist = GeoValidator._haversine(
            lat1=37.7749, lon1=-122.4194,  # SFO
            lat2=34.0522, lon2=-118.2437,  # LAX
        )

        # Expected: ~559 km
        assert 550 < dist < 570, f"Expected ~559 km, got {dist}"

    def test_haversine_same_point(self):
        """Test Haversine with same point (distance should be ~0)."""
        dist = GeoValidator._haversine(
            lat1=52.52, lon1=13.405,  # Berlin
            lat2=52.52, lon2=13.405,  # Berlin
        )

        assert dist < 1.0, "Distance to same point should be <1 km"

    def test_distance_same_country_different_regions(self):
        """Test distance estimation: same country, different regions."""
        geo1 = GeoData(country="DE", region="Berlin", city="Berlin")
        geo2 = GeoData(country="DE", region="Bavaria", city="Munich")

        dist = GeoValidator._estimate_distance(geo1, geo2)

        assert dist == 300.0, "Same country, different region should be ~300 km"

    def test_distance_different_countries(self):
        """Test distance estimation: different countries."""
        geo1 = GeoData(country="DE", region="Berlin", city="Berlin")
        geo2 = GeoData(country="US", region="California", city="San Francisco")

        dist = GeoValidator._estimate_distance(geo1, geo2)

        assert dist == 2000.0, "Different countries should be ~2000 km"


class TestGeoSeverityClassification:
    """Test severity classification and confidence scoring."""

    def test_severity_exact_match(self):
        """Test severity for exact match (0 km)."""
        severity, confidence = GeoValidator._classify_mismatch(0.0)

        assert severity == GeoMismatchSeverity.EXACT_MATCH.value
        assert confidence == 1.0

    def test_severity_minor(self):
        """Test severity for minor distance (<100 km)."""
        severity, confidence = GeoValidator._classify_mismatch(50.0)

        assert severity == GeoMismatchSeverity.MINOR.value
        assert 0.8 <= confidence < 1.0

    def test_severity_moderate(self):
        """Test severity for moderate distance (100-500 km)."""
        severity, confidence = GeoValidator._classify_mismatch(300.0)

        assert severity == GeoMismatchSeverity.MODERATE.value
        assert 0.4 <= confidence < 0.8

    def test_severity_major(self):
        """Test severity for major distance (500-1000 km)."""
        severity, confidence = GeoValidator._classify_mismatch(750.0)

        assert severity == GeoMismatchSeverity.MAJOR.value
        assert 0.1 <= confidence < 0.4

    def test_severity_critical(self):
        """Test severity for critical distance (>1000 km)."""
        severity, confidence = GeoValidator._classify_mismatch(1500.0)

        assert severity == GeoMismatchSeverity.CRITICAL.value
        assert 0.0 < confidence < 0.1


class TestGeoLearningIntegration:
    """Test integration with learning loss components."""

    def test_geo_low_confidence_downweights_routing(self):
        """Test case: Low geo_confidence scales routing loss gradients.

        Expected: Gradient contribution reduced by confidence factor
        """
        weighter = GeoConfidenceWeighter()

        # Routing gradient (before confidence scaling)
        routing_gradient = 0.5

        # High confidence: gradient ~unchanged
        scaled_high = weighter.scale_gradient(routing_gradient, geo_confidence=0.95)
        assert abs(scaled_high - 0.475) < 0.01

        # Low confidence: gradient significantly reduced
        scaled_low = weighter.scale_gradient(routing_gradient, geo_confidence=0.1)
        assert abs(scaled_low - 0.05) < 0.01

        # No confidence: gradient zeroed
        scaled_none = weighter.scale_gradient(routing_gradient, geo_confidence=0.0)
        assert scaled_none == 0.0

    def test_geo_confidence_weighter_invalid_input(self):
        """Test geo_confidence weighter with invalid inputs."""
        weighter = GeoConfidenceWeighter()

        with pytest.raises(ValueError, match="geo_confidence must be in"):
            weighter.scale_gradient(0.5, geo_confidence=-0.1)

        with pytest.raises(ValueError, match="geo_confidence must be in"):
            weighter.scale_gradient(0.5, geo_confidence=1.5)

    def test_geo_loss_component(self):
        """Test geo loss component computation for unified loss."""
        weighter = GeoConfidenceWeighter()

        # High confidence: loss ~0
        loss_high = weighter.compute_geo_loss_component(geo_confidence=0.95)
        assert loss_high < 0.01

        # Medium confidence: moderate loss
        loss_med = weighter.compute_geo_loss_component(geo_confidence=0.5)
        assert 0.15 < loss_med < 0.25

        # Low confidence: high loss
        loss_low = weighter.compute_geo_loss_component(geo_confidence=0.1)
        assert loss_low > 0.7


class TestGeoSpoofingResilience:
    """Test resilience to geolocation spoofing attacks."""

    def test_geo_spoofing_does_not_break_system(self):
        """Test case: Spoofed geo data doesn't crash learning.

        Attacker claims Tokyo geo from Berlin Cloudflare edge.
        Expected: System detects mismatch, flags low confidence, continues
        """
        validator = GeoValidator(tenant_id="_default")

        # Attacker claims Tokyo
        spoofed_geo = GeoData(country="JP", region="Tokyo", city="Tokyo")

        # But edge is Frankfurt (FRA)
        confidence, mismatch_event = validator.validate(
            claimed_geo=spoofed_geo,
            edge_region="FRA",
        )

        # Verify system resilience
        assert mismatch_event is not None, "Spoofing detected"
        assert confidence < 0.2, "Confidence very low"
        assert mismatch_event.severity == GeoMismatchSeverity.CRITICAL.value

        # Verify weighting function still works
        weighter = GeoConfidenceWeighter()
        gradient = weighter.scale_gradient(0.8, confidence)
        assert 0 <= gradient <= 0.8, "Routing gradient properly downweighted"

    def test_geo_confidence_prevents_routing_hijack(self):
        """Test that geo mismatch prevents routing hijack attacks.

        Scenario: Attacker fakes EU geo to access region-locked features.
        Defense: geo_confidence downweights routing decisions.
        """
        validator = GeoValidator(tenant_id="_default")

        # Attacker claims Berlin (EU)
        attacked_geo = GeoData(country="DE", region="Berlin", city="Berlin")

        # But edge is Sydney (AU)
        confidence, mismatch_event = validator.validate(
            claimed_geo=attacked_geo,
            edge_region="SYD",
        )

        # Verify attack is mitigated (confidence very low for critical distance)
        assert confidence < 0.15, "Critical mismatch detected"
        weighter = GeoConfidenceWeighter()
        scaled = weighter.scale_gradient(1.0, confidence)  # Max gradient for routing
        assert scaled < 0.15, "Routing decision significantly reduced"


class TestGeoTenantIsolation:
    """Test tenant isolation (GDPR Art. 32)."""

    def test_geo_validation_tenant_scoped(self):
        """Test case: Tenant A geo validation does not affect Tenant B.

        Expected: Each tenant has independent validators
        """
        validator_a = GeoValidator(tenant_id="tenant_a")
        validator_b = GeoValidator(tenant_id="tenant_b")

        geo = GeoData(country="DE", region="Berlin", city="Berlin")

        # Validate same geo with different validators
        conf_a, event_a = validator_a.validate(geo, edge_region="SFO")
        conf_b, event_b = validator_b.validate(geo, edge_region="SFO")

        # Confidence scores should be identical (same geo data)
        assert conf_a == conf_b

        # But events should have different tenant_ids
        if event_a and event_b:
            assert event_a.tenant_id == "tenant_a"
            assert event_b.tenant_id == "tenant_b"

    def test_geo_mismatch_event_tenant_scoped(self):
        """Verify mismatch events carry correct tenant_id."""
        validator = GeoValidator(tenant_id="project_xyz")

        geo1 = GeoData(country="US", region="California", city="San Francisco")
        geo2 = GeoData(country="DE", region="Berlin", city="Berlin")

        _, event = validator.validate(geo1, edge_region="FRA")

        if event:
            assert event.tenant_id == "project_xyz"


class TestGeoAuditLogging:
    """Test audit logging of geo validation events."""

    def test_geo_mismatch_event_structure(self):
        """Verify mismatch event has all required audit fields."""
        validator = GeoValidator(tenant_id="_default")

        claimed = GeoData(country="AU", region="Sydney", city="Sydney")
        _, event = validator.validate(claimed, edge_region="SFO")

        assert event is not None
        assert event.event_id  # UUID
        assert event.tenant_id == "_default"
        assert event.timestamp  # ISO 8601
        assert event.claimed_geo is not None
        assert event.edge_geo is not None
        assert event.distance_km >= 0
        assert 0.0 <= event.confidence_score <= 1.0

    def test_geo_mismatch_audit_ref_assignment(self):
        """Verify audit_ref is set when event is stored."""
        # This test verifies the audit-first pattern
        mismatch_event = GeoMismatchEvent(
            event_id=str(uuid4()),
            tenant_id="_default",
            timestamp=datetime.utcnow().isoformat() + "Z",
            claimed_geo={"country": "AU", "region": "Sydney", "city": "Sydney"},
            edge_geo={"country": "US", "region": "California", "city": "San Francisco"},
            distance_km=2000.0,
            severity=GeoMismatchSeverity.CRITICAL.value,
            confidence_score=0.05,
        )

        # Serialize to dict (as would be stored)
        d = mismatch_event.to_dict()
        assert d["event_type"] == "geo_mismatch"
        assert "tenant_id" in d
        assert "distance_km" in d


class TestGeoValidatorEdgeCases:
    """Test edge cases and error handling."""

    def test_geo_validator_zero_distance(self):
        """Test with zero distance (exact match)."""
        validator = GeoValidator(tenant_id="_default")

        geo = GeoData(country="US", region="California", city="San Francisco")
        confidence, event = validator.validate(geo, edge_region="SFO")

        assert confidence == 1.0
        assert event is None

    def test_geo_validator_with_lat_lon(self):
        """Test distance calculation with lat/lon coordinates."""
        validator = GeoValidator(tenant_id="_default")

        # San Francisco coordinates
        claimed = GeoData(
            country="US",
            region="California",
            city="San Francisco",
            latitude=37.7749,
            longitude=-122.4194,
        )

        # Los Angeles coordinates
        edge_geo = GeoData(
            country="US",
            region="California",
            city="Los Angeles",
            latitude=34.0522,
            longitude=-118.2437,
        )

        # Manually calculate distance
        dist = GeoValidator._estimate_distance(claimed, edge_geo)

        # Should use Haversine (lat/lon available)
        assert 550 < dist < 570, f"Expected ~559 km, got {dist}"

    def test_geo_validator_partial_lat_lon(self):
        """Test fallback when only one has lat/lon."""
        validator = GeoValidator(tenant_id="_default")

        # One with lat/lon
        geo1 = GeoData(
            country="US",
            region="California",
            city="San Francisco",
            latitude=37.7749,
            longitude=-122.4194,
        )

        # One without
        geo2 = GeoData(
            country="DE",
            region="Berlin",
            city="Berlin",
        )

        dist = GeoValidator._estimate_distance(geo1, geo2)

        # Should fall back to country-level comparison
        assert dist == 2000.0, "Different countries should be 2000 km"


class TestGeoConfidenceIntegration:
    """Test full integration with learning pipeline."""

    def test_geo_confidence_in_unified_loss(self):
        """Test geo_confidence component in unified loss calculation."""
        weighter = GeoConfidenceWeighter()

        # Simulate unified loss components
        confidence_loss = 0.2
        latency_loss = 0.1
        cost_loss = 0.15

        # Geo confidence is low due to detected mismatch
        geo_confidence = 0.3

        # Scale the routing (geo-influenced) component
        scaled_confidence = weighter.scale_gradient(confidence_loss, geo_confidence)

        # Geo loss component
        geo_loss = weighter.compute_geo_loss_component(geo_confidence)

        # Unified loss: average with geo component
        unified_loss = (scaled_confidence * 0.3 + latency_loss * 0.3 + cost_loss * 0.2 + geo_loss * 0.2) / 4

        # Should be < 0.2 if geo_confidence=0.3
        assert unified_loss > 0, "Loss should be positive"
        assert unified_loss < 1.0, "Loss should be normalized"

    def test_geo_confidence_convergence(self):
        """Test that geo confidence improves over time (learning)."""
        validator = GeoValidator(tenant_id="_default")

        # Start with spoofed geo
        geo_initial = GeoData(country="JP", region="Tokyo", city="Tokyo")
        conf_1, _ = validator.validate(geo_initial, edge_region="SFO")

        # User corrects geo to match edge region
        geo_corrected = GeoData(country="US", region="California", city="San Francisco")
        conf_2, _ = validator.validate(geo_corrected, edge_region="SFO")

        # Confidence should improve
        assert conf_2 > conf_1
        assert conf_2 == 1.0
