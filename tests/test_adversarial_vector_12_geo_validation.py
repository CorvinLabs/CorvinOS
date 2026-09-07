"""Adversarial Test: Vector #12 — Map Geolocation Spoofing Attack.

Security Fix #12 Re-test with NEW mitigations in place.

Threat Model:
- Attacker claims fake GPS coordinates (e.g., "I'm in Tokyo" from Berlin)
- Attack goal: bypass geo-based routing, access restricted regions
- Defense: NEW GeoValidator with Cloudflare edge location validation

NEW Mitigations:
1. ✅ GeoData immutable (frozen) — prevents tampering
2. ✅ Haversine formula — accurate distance calculation
3. ✅ Severity classification — 5 distance thresholds
4. ✅ Confidence scoring — inverse of distance (0.0-1.0)
5. ✅ Audit logging — immutable, tenant-scoped events
6. ✅ GeoConfidenceWeighter — scales routing decisions
7. ✅ Tenant isolation — per-tenant validators
8. ✅ Fail-closed design — low confidence degrades gracefully
9. ✅ Audit-first pattern — event logged before any action

Attack Vectors to Test:
A1. Direct coordinate spoofing (fake lat/lon)
A2. Country-level spoofing (false country code)
A3. Region spoofing (false region within country)
A4. Gradient poisoning (corrupt confidence scores)
A5. Audit bypass (no event logged)
A6. Tenant isolation break (cross-tenant data leak)
A7. Immutability break (modify geo data after validation)
A8. Distance calculation bypass (skip Haversine)
A9. Confidence threshold bypass (ignore severity)
A10. Learning loop hijack (feed fake feedback to optimizer)

This test suite verifies ALL attack vectors are mitigated.

GDPR Art. 32, ADR-0647.
"""

import json
import math
import pytest
from datetime import datetime
from unittest.mock import Mock, MagicMock, patch, ANY
from uuid import uuid4
from dataclasses import dataclass, replace

# Import the actual modules under test
from core.learning.geo_validator import (
    GeoData,
    GeoValidator,
    GeoMismatchEvent,
    GeoMismatchSeverity,
    GeoConfidenceWeighter,
)


class TestAdversarialVector12_DirectCoordinateSpoofing:
    """Attack A1: Attacker provides fake lat/lon coordinates.

    Example: Claims to be in Tokyo (35.68°N, 139.69°E) but actually
    in Berlin (52.52°N, 13.41°E), with Cloudflare edge at Frankfurt.

    Mitigation: Haversine formula + severity classification
    """

    def test_tokyo_from_berlin_detected(self):
        """Attacker claims Tokyo from Berlin edge.

        Expected: distance > 1000 km, severity=CRITICAL, confidence < 0.1
        """
        validator = GeoValidator(tenant_id="_default")

        # Attacker claims Tokyo (precise lat/lon)
        claimed = GeoData(
            country="JP",
            region="Tokyo",
            city="Tokyo",
            latitude=35.6762,
            longitude=139.6503,
        )

        # Edge is Frankfurt
        edge_region = "FRA"

        confidence, event = validator.validate(claimed, edge_region=edge_region)

        # Assertions: Attack DETECTED
        assert event is not None, "Attack should produce mismatch event"
        assert event.distance_km > 1000, "Tokyo-Frankfurt > 1000 km"
        assert event.severity == GeoMismatchSeverity.CRITICAL.value
        assert confidence < 0.1, "Critical mismatch => low confidence"
        assert event.confidence_score == confidence

    def test_spoofed_coords_prevent_routing_hijack(self):
        """Attack consequence: spoofed coords should NOT hijack routing.

        Scenario: Attacker uses fake Tokyo coords to access geo-locked features.
        Defense: geo_confidence downweights routing decision.
        """
        validator = GeoValidator(tenant_id="_default")
        claimed = GeoData(country="JP", region="Tokyo", city="Tokyo")
        confidence, _ = validator.validate(claimed, edge_region="SFO")

        weighter = GeoConfidenceWeighter()

        # Suppose routing wants to send max gradient (1.0)
        routing_gradient = 1.0
        scaled = weighter.scale_gradient(routing_gradient, confidence)

        # With low confidence, gradient should be minimal
        assert scaled < 0.2, f"Expected scaled gradient < 0.2, got {scaled}"
        assert scaled >= 0, "Gradient must be non-negative"

    def test_lat_lon_boundary_values(self):
        """Test with extreme lat/lon values (boundary testing).

        Expected: Haversine handles poles/date line correctly.
        """
        validator = GeoValidator(tenant_id="_default")

        # North Pole
        claimed = GeoData(
            country="??",
            region="North Pole",
            city="North Pole",
            latitude=90.0,
            longitude=0.0,
        )

        confidence, event = validator.validate(claimed, edge_region="SFO")

        # Should calculate distance correctly (no NaN/Inf)
        assert 0.0 <= confidence <= 1.0, "Confidence must be valid number"
        if event:
            assert math.isfinite(event.distance_km), "Distance must be finite"


class TestAdversarialVector12_CountrySpoofing:
    """Attack A2: Attacker claims false country code.

    Example: Claims to be in Germany (DE) but edge is in Australia (AU).

    Mitigation: Country-level distance classification (2000 km default).
    """

    def test_country_spoofing_detected(self):
        """Attacker claims Germany from Australian edge.

        Expected: distance = 2000 km, severity = CRITICAL
        """
        validator = GeoValidator(tenant_id="_default")

        # Attacker claims Germany
        claimed = GeoData(country="DE", region="Berlin", city="Berlin")

        # Edge is Australia
        confidence, event = validator.validate(claimed, edge_region="SYD")

        # Different countries => 2000 km distance
        assert event is not None
        assert event.distance_km == 2000.0
        assert event.severity == GeoMismatchSeverity.CRITICAL.value

    def test_region_spoofing_same_country(self):
        """Attack A3: Attacker claims different region in same country.

        Example: Claims Munich from Berlin edge (same country).

        Expected: distance = 300 km, severity = MODERATE
        """
        validator = GeoValidator(tenant_id="_default")

        # Attacker claims Munich (DE)
        claimed = GeoData(country="DE", region="Bavaria", city="Munich")

        # Edge is Frankfurt (DE)
        confidence, event = validator.validate(claimed, edge_region="FRA")

        # Same country, different region => 300 km
        assert event is not None
        assert event.distance_km == 300.0
        assert event.severity == GeoMismatchSeverity.MODERATE.value


class TestAdversarialVector12_ImmutabilityBreak:
    """Attack A7: Attacker tries to modify GeoData after creation.

    Example: Create immutable GeoData, then attempt to change country.

    Mitigation: GeoData is frozen (frozen=True in dataclass).
    """

    def test_geo_data_immutable_prevents_tampering(self):
        """Verify GeoData is truly immutable.

        Expected: AttributeError when trying to modify frozen dataclass.
        """
        geo = GeoData(country="DE", region="Berlin", city="Berlin")

        # Attempt 1: Modify country
        with pytest.raises(AttributeError):
            geo.country = "US"

        # Attempt 2: Modify region
        with pytest.raises(AttributeError):
            geo.region = "California"

        # Attempt 3: Modify city
        with pytest.raises(AttributeError):
            geo.city = "San Francisco"

    def test_geo_data_immutable_via_replace(self):
        """Verify replace() creates new instance (doesn't modify original).

        Expected: Original geo unchanged, new instance is separate.
        """
        geo1 = GeoData(country="DE", region="Berlin", city="Berlin")

        # replace() creates new instance, doesn't modify original
        geo2 = replace(geo1, country="US")

        # Original unchanged
        assert geo1.country == "DE"
        # New instance has change
        assert geo2.country == "US"


class TestAdversarialVector12_AuditBypass:
    """Attack A5: Attacker tries to evade audit logging.

    Example: Trigger validation without logging, or suppress event creation.

    Mitigation: Audit-first pattern — all mismatches logged immutably.
    """

    def test_mismatch_always_produces_event(self):
        """Verify every mismatch produces immutable event.

        Expected: Even tiny distance produces event with correct structure.
        """
        validator = GeoValidator(tenant_id="_default")

        # Slight mismatch
        claimed = GeoData(country="US", region="California", city="Los Angeles")
        confidence, event = validator.validate(claimed, edge_region="SFO")

        # Event must be created (distance > 0)
        assert event is not None, "Mismatch must produce event"
        assert event.event_id, "Event must have UUID"
        assert event.tenant_id == "_default"
        assert event.timestamp, "Event must be timestamped"

    def test_audit_event_immutable(self):
        """Verify GeoMismatchEvent is frozen (immutable).

        Expected: Cannot modify event after creation.
        """
        event = GeoMismatchEvent(
            event_id=str(uuid4()),
            tenant_id="_default",
            timestamp=datetime.utcnow().isoformat() + "Z",
            claimed_geo={"country": "JP", "region": "Tokyo", "city": "Tokyo"},
            edge_geo={"country": "DE", "region": "Hesse", "city": "Frankfurt"},
            distance_km=1500.0,
            severity=GeoMismatchSeverity.CRITICAL.value,
            confidence_score=0.05,
        )

        # Attempt to modify
        with pytest.raises(AttributeError):
            event.distance_km = 100.0

        # Attempt to modify claimed_geo
        with pytest.raises(AttributeError):
            event.claimed_geo = {}


class TestAdversarialVector12_TenantIsolationBreak:
    """Attack A6: Attacker tries to cross tenant boundaries.

    Example: Validator for tenant_a somehow reads/writes tenant_b data.

    Mitigation: All validators are tenant-scoped, audit events carry tenant_id.
    """

    def test_validators_isolated_per_tenant(self):
        """Verify validators are independent per tenant.

        Expected: Different tenant_ids don't interfere.
        """
        validator_a = GeoValidator(tenant_id="attacker_tenant")
        validator_b = GeoValidator(tenant_id="victim_tenant")

        # Each validator has own tenant_id
        assert validator_a.tenant_id == "attacker_tenant"
        assert validator_b.tenant_id == "victim_tenant"

    def test_audit_events_carry_tenant_id(self):
        """Verify audit events are properly tenant-scoped.

        Expected: Event.tenant_id matches validator.tenant_id.
        """
        validator = GeoValidator(tenant_id="tenant_xyz")

        claimed = GeoData(country="AU", region="Sydney", city="Sydney")
        confidence, event = validator.validate(claimed, edge_region="SFO")

        # Event must carry correct tenant_id
        assert event is not None
        assert event.tenant_id == "tenant_xyz"

        # Serialized event must also carry tenant_id
        event_dict = event.to_dict()
        assert event_dict["tenant_id"] == "tenant_xyz"

    def test_tenant_isolation_invalid_tenant_rejected(self):
        """Verify validators reject invalid tenant_id.

        Expected: ValueError on validation with invalid tenant.
        """
        validator = GeoValidator(tenant_id="_default")

        claimed = GeoData(country="DE", region="Berlin", city="Berlin")

        # Force invalid tenant_id
        validator.tenant_id = ""

        # Should reject during validation
        with pytest.raises(ValueError, match="Invalid tenant_id"):
            validator.validate(claimed, edge_region="FRA")


class TestAdversarialVector12_GradientPoisoning:
    """Attack A4: Attacker feeds corrupt confidence scores to downweight legit decisions.

    Example: Manipulate confidence_score in event to 0.0 even when legit.

    Mitigation: Confidence computed immutably from distance; can't be forged.
    """

    def test_confidence_computed_from_distance(self):
        """Verify confidence is deterministic from distance.

        Expected: Same distance => same confidence, always.
        """
        validator = GeoValidator(tenant_id="_default")

        claimed = GeoData(country="US", region="California", city="San Francisco")

        # Validate twice with same geo
        confidence_1, event_1 = validator.validate(claimed, edge_region="SFO")
        confidence_2, event_2 = validator.validate(claimed, edge_region="SFO")

        # Confidence must be identical (deterministic)
        assert confidence_1 == confidence_2
        assert confidence_1 == 1.0  # Exact match

    def test_confidence_ranges_by_severity(self):
        """Verify confidence ranges are strict per severity level.

        Expected: Can't get confidence=0.9 for CRITICAL distance.
        """
        for distance, expected_severity, min_conf, max_conf in [
            (0.0, GeoMismatchSeverity.EXACT_MATCH.value, 1.0, 1.0),
            (50.0, GeoMismatchSeverity.MINOR.value, 0.8, 1.0),
            (300.0, GeoMismatchSeverity.MODERATE.value, 0.4, 0.8),
            (750.0, GeoMismatchSeverity.MAJOR.value, 0.1, 0.4),
            (1500.0, GeoMismatchSeverity.CRITICAL.value, 0.0, 0.1),
        ]:
            severity, confidence = GeoValidator._classify_mismatch(distance)

            assert severity == expected_severity
            assert min_conf <= confidence <= max_conf, \
                f"Distance {distance} km: confidence {confidence} not in [{min_conf}, {max_conf}]"


class TestAdversarialVector12_DistanceCalculationBypass:
    """Attack A8: Attacker tries to skip Haversine or distance calculation.

    Example: Claim Berlin=Tokyo distance by spoofing edge_region parameter.

    Mitigation: All distance paths converge to _classify_mismatch.
    """

    def test_haversine_precision(self):
        """Verify Haversine formula is accurate.

        Expected: Known distances match calculated values (±1%).
        """
        test_cases = [
            # (lat1, lon1, lat2, lon2, expected_km, tolerance_km)
            (37.7749, -122.4194, 34.0522, -118.2437, 559, 10),  # SF to LA
            (52.52, 13.405, 52.52, 13.405, 0, 1),  # Berlin to Berlin
            (0, 0, 0, 1, 111.32, 1),  # Equator, 1 degree longitude
        ]

        for lat1, lon1, lat2, lon2, expected, tolerance in test_cases:
            calculated = GeoValidator._haversine(lat1, lon1, lat2, lon2)
            assert abs(calculated - expected) <= tolerance, \
                f"Expected ~{expected} km, got {calculated}"

    def test_distance_fallback_chain(self):
        """Verify all distance paths produce consistent results.

        Expected: Haversine + fallback both agree on same distance.
        """
        # Create two geos: one with lat/lon, one without
        geo_precise = GeoData(
            country="US",
            region="California",
            city="San Francisco",
            latitude=37.7749,
            longitude=-122.4194,
        )

        geo_fallback = GeoData(
            country="US",
            region="California",
            city="Los Angeles",
        )

        # Distance using Haversine (if both have coords)
        geo_la_precise = GeoData(
            country="US",
            region="California",
            city="Los Angeles",
            latitude=34.0522,
            longitude=-118.2437,
        )

        dist_haversine = GeoValidator._estimate_distance(geo_precise, geo_la_precise)
        dist_fallback = GeoValidator._estimate_distance(geo_precise, geo_fallback)

        # Haversine should be more precise
        assert 550 < dist_haversine < 570, "Haversine: SF-LA ~559 km"
        assert dist_fallback == 50.0, "Fallback: same region, different city = 50 km"


class TestAdversarialVector12_LearningLoopHijack:
    """Attack A10: Attacker tries to feed fake feedback to optimizer.

    Example: Claim geo_confidence was good when it was bad, to prevent learning.

    Mitigation: Audit-first logging; optimizer reads from immutable audit trail.
    """

    def test_geo_confidence_weighter_bounds(self):
        """Verify weighter rejects invalid confidence scores.

        Expected: ValueError for confidence outside [0.0, 1.0].
        """
        weighter = GeoConfidenceWeighter()

        # Test lower bound
        with pytest.raises(ValueError, match="geo_confidence must be in"):
            weighter.scale_gradient(0.5, geo_confidence=-0.1)

        # Test upper bound
        with pytest.raises(ValueError, match="geo_confidence must be in"):
            weighter.scale_gradient(0.5, geo_confidence=1.5)

    def test_geo_loss_component_computation(self):
        """Verify loss component computation is deterministic.

        Expected: Same confidence => same loss, always.
        """
        weighter = GeoConfidenceWeighter()

        # Test multiple times with same input
        for _ in range(5):
            loss = weighter.compute_geo_loss_component(geo_confidence=0.3)

            # Should be identical each time
            expected = (0.95 - 0.3) ** 2
            assert abs(loss - expected) < 1e-6


class TestAdversarialVector12_FailClosedDesign:
    """Verify fail-closed behavior: low confidence doesn't crash, just degrades.

    Expected: System continues operating even with zero confidence.
    """

    def test_zero_confidence_no_crash(self):
        """Attacker has zero confidence; system must not crash.

        Expected: Graceful degradation (gradient scaled to zero).
        """
        weighter = GeoConfidenceWeighter()

        # Routing gradient with zero confidence
        gradient = weighter.scale_gradient(1.0, geo_confidence=0.0)

        # Must be zero, not NaN/Inf/exception
        assert gradient == 0.0
        assert math.isfinite(gradient)

    def test_missing_edge_region_defaults_to_medium_confidence(self):
        """No edge_region provided; system assumes medium confidence.

        Expected: confidence = 0.5, no event (can't validate without edge).
        """
        validator = GeoValidator(tenant_id="_default")

        claimed = GeoData(country="US", region="California", city="San Francisco")
        confidence, event = validator.validate(claimed, edge_region=None)

        # Graceful fallback
        assert confidence == 0.5, "Missing edge_region => medium confidence"
        assert event is None, "Can't detect mismatch without edge region"


class TestAdversarialVector12_ComprehensiveScenarios:
    """End-to-end scenarios combining multiple attack vectors.

    Tests that mitigations work together holistically.
    """

    def test_scenario_1_sophisticated_spoof_with_audit_trail(self):
        """Scenario: Attacker spoof Tokyo from Berlin, tries to hide in audit trail.

        Expected:
        1. Geo mismatch detected (distance > 1000 km)
        2. Event immutably logged with all metadata
        3. Confidence < 0.1
        4. Routing gradient downweighted to < 0.1
        5. Event carries correct tenant_id
        """
        validator = GeoValidator(tenant_id="user_account")
        weighter = GeoConfidenceWeighter()

        # Attack: claim Tokyo
        claimed = GeoData(country="JP", region="Tokyo", city="Tokyo")

        # Validate against Berlin edge
        confidence, event = validator.validate(claimed, edge_region="FRA")

        # Verify all mitigations engaged
        assert event is not None, "Mismatch detected"
        assert event.distance_km > 1000, "Long distance recognized"
        assert event.severity == GeoMismatchSeverity.CRITICAL.value
        assert confidence < 0.1, "Confidence very low"
        assert event.tenant_id == "user_account", "Audit carries tenant_id"

        # Verify routing doesn't get hijacked
        routing_gradient = weighter.scale_gradient(1.0, confidence)
        assert routing_gradient < 0.1, "Routing gradient downweighted"

    def test_scenario_2_subtle_regional_spoof_detected(self):
        """Scenario: Attacker claims nearby city (subtle spoof).

        Expected:
        1. Still detected (distance > 0)
        2. Severity = MINOR, not CRITICAL
        3. Confidence reduced but not zero
        4. Routing still slightly affected
        """
        validator = GeoValidator(tenant_id="_default")
        weighter = GeoConfidenceWeighter()

        # Attack: claim LA from SF edge (subtle, same region)
        claimed = GeoData(country="US", region="California", city="Los Angeles")
        confidence, event = validator.validate(claimed, edge_region="SFO")

        # Verify detection even for subtle spoof
        assert event is not None, "Subtle spoof still detected"
        assert event.severity == GeoMismatchSeverity.MINOR.value
        assert 0.8 <= confidence <= 1.0, "Confidence reduced but not critical"

        # Verify learning still works (confidence affects routing)
        gradient = weighter.scale_gradient(1.0, confidence)
        assert 0.8 <= gradient <= 1.0, "Routing slightly affected"

    def test_scenario_3_exact_match_no_false_positive(self):
        """Scenario: Legitimate user, geo matches exactly.

        Expected:
        1. No mismatch event created
        2. Confidence = 1.0
        3. Routing unaffected
        """
        validator = GeoValidator(tenant_id="_default")
        weighter = GeoConfidenceWeighter()

        # Legitimate: claim SF from SF edge
        claimed = GeoData(country="US", region="California", city="San Francisco")
        confidence, event = validator.validate(claimed, edge_region="SFO")

        # Verify no false positive
        assert event is None, "Exact match must not create event"
        assert confidence == 1.0, "Exact match => confidence 1.0"

        # Verify routing unaffected
        gradient = weighter.scale_gradient(1.0, confidence)
        assert gradient == 1.0, "Exact match => routing unaffected"


class TestAdversarialVector12_ContractValidation:
    """Validate critical contracts that must hold for security.

    These are the non-negotiable invariants.
    """

    def test_contract_confidence_always_in_bounds(self):
        """INVARIANT: confidence must ALWAYS be in [0.0, 1.0].

        Expected: No exceptions, no NaN, no Inf.
        """
        validator = GeoValidator(tenant_id="_default")

        # Test with various (extreme) distances
        for distance in [0.0, 1.0, 100.0, 500.0, 1000.0, 5000.0, 10000.0]:
            severity, confidence = GeoValidator._classify_mismatch(distance)

            assert 0.0 <= confidence <= 1.0, \
                f"Distance {distance}: confidence {confidence} out of bounds"
            assert math.isfinite(confidence), \
                f"Distance {distance}: confidence is {confidence} (not finite)"

    def test_contract_event_never_without_tenant_id(self):
        """INVARIANT: every GeoMismatchEvent must carry tenant_id.

        Expected: tenant_id never null/empty/missing.
        """
        validator = GeoValidator(tenant_id="required_tenant")

        claimed = GeoData(country="JP", region="Tokyo", city="Tokyo")
        confidence, event = validator.validate(claimed, edge_region="FRA")

        if event:
            assert event.tenant_id, "Event must have non-empty tenant_id"
            assert event.tenant_id == "required_tenant"

    def test_contract_distance_always_non_negative(self):
        """INVARIANT: distance must ALWAYS be >= 0.

        Expected: No negative distances (no backward time travel!).
        """
        geos = [
            (GeoData("US", "CA", "SF"), GeoData("US", "CA", "LA")),
            (GeoData("US", "CA", "SF"), GeoData("DE", "Berlin", "Berlin")),
            (GeoData("JP", "Tokyo", "Tokyo"), GeoData("AU", "NSW", "Sydney")),
        ]

        for geo1, geo2 in geos:
            distance = GeoValidator._estimate_distance(geo1, geo2)
            assert distance >= 0, f"Negative distance: {distance} km"


if __name__ == "__main__":
    # Run all tests
    pytest.main([__file__, "-v"])
