"""Unit tests for Phase 1: OTEL Exporter + Geo Privacy Validator.

Test coverage:
- OTELExporter (heartbeat dual-write, JSON fallback)
- GeoPrivacyValidator (fail-closed filtering, whitelist enforcement)
- E2E: Export path (OTEL → JSON fallback)
- Audit logging (all exports logged)
"""

import json
import logging
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.observability.otel_exporter import (
    OTELExporter,
    OTELExportError,
    GeoAttributes,
    HeartbeatSignal,
)
from core.observability.geo_privacy import GeoPrivacyValidator, PrivacyViolationError


class TestOTELExporter:
    """Tests for OTELExporter class."""

    def test_init_requires_tenant_id(self):
        """tenant_id is mandatory (fail-closed)."""
        with pytest.raises(ValueError, match="tenant_id is mandatory"):
            OTELExporter(tenant_id="", instance_id="uuid-1")

    def test_init_valid(self):
        """OTELExporter initializes with valid tenant_id."""
        exporter = OTELExporter(
            tenant_id="acme-corp",
            instance_id="uuid-1",
            geo_granularity="country",
        )
        assert exporter.tenant_id == "acme-corp"
        assert exporter.instance_id == "uuid-1"
        assert exporter.geo_granularity == "country"

    def test_export_heartbeat_json_fallback(self):
        """export_heartbeat falls back to JSON when OTEL fails."""
        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = OTELExporter(
                tenant_id="acme-corp",
                instance_id="uuid-1",
                json_fallback_dir=Path(tmpdir),
            )

            # Mock audit logger
            exporter.audit_logger = MagicMock()

            # Export heartbeat (OTEL will fail because SDK not initialized)
            success, message = exporter.export_heartbeat(
                is_alive=True,
                uptime_seconds=3600,
                plugin_count=5,
                memory_usage_bytes=512 * 1024 * 1024,
                platform="linux",
                python_version="3.11",
                geo_attrs=GeoAttributes(country="DE", granularity="country"),
            )

            # Should have fallen back to JSON
            assert success is False
            assert "fell back to JSON" in message

            # Verify JSON fallback file was written
            fallback_file = Path(tmpdir) / "heartbeat-acme-corp-uuid-1.jsonl"
            assert fallback_file.exists()

            # Verify JSON content
            with open(fallback_file) as f:
                record = json.loads(f.readline())
                assert record["tenant_id"] == "acme-corp"
                assert record["instance_id"] == "uuid-1"
                assert record["is_alive"] is True
                assert record["uptime_seconds"] == 3600
                assert record["geo"]["country"] == "DE"

            # Verify audit log was called
            exporter.audit_logger.warning.assert_called_once()
            call_args = exporter.audit_logger.warning.call_args
            assert "telemetry_fallback_activated" in str(call_args)

    def test_export_heartbeat_without_geo(self):
        """export_heartbeat works without geo attributes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = OTELExporter(
                tenant_id="acme-corp",
                instance_id="uuid-1",
                json_fallback_dir=Path(tmpdir),
            )
            exporter.audit_logger = MagicMock()

            success, message = exporter.export_heartbeat(
                is_alive=True,
                uptime_seconds=1000,
                plugin_count=3,
                memory_usage_bytes=256 * 1024 * 1024,
                platform="darwin",
                python_version="3.10",
                geo_attrs=None,  # No geo
            )

            assert success is False  # Will fallback (no OTEL)
            fallback_file = Path(tmpdir) / "heartbeat-acme-corp-uuid-1.jsonl"
            with open(fallback_file) as f:
                record = json.loads(f.readline())
                assert record["geo"] is None


class TestGeoPrivacyValidator:
    """Tests for GeoPrivacyValidator class."""

    def test_init_requires_tenant_id(self):
        """tenant_id is mandatory."""
        with pytest.raises(PrivacyViolationError, match="tenant_id is mandatory"):
            GeoPrivacyValidator(tenant_id="")

    def test_init_invalid_granularity(self):
        """Invalid granularity raises error."""
        with pytest.raises(PrivacyViolationError, match="Invalid granularity"):
            GeoPrivacyValidator(tenant_id="acme-corp", granularity="invalid")

    def test_init_valid(self):
        """GeoPrivacyValidator initializes with valid params."""
        validator = GeoPrivacyValidator(
            tenant_id="acme-corp", granularity="country"
        )
        assert validator.tenant_id == "acme-corp"
        assert validator.granularity == "country"

    def test_filter_country_level(self):
        """Filter at country level removes region/city/coordinates."""
        validator = GeoPrivacyValidator(
            tenant_id="acme-corp", granularity="country"
        )
        validator.audit_logger = MagicMock()

        attributes = {
            "geo.country": "DE",
            "geo.region": "BW",  # Should be removed
            "geo.city": "Stuttgart",  # Should be removed
            "geo.lat": 48.7758,  # Should be removed
            "geo.lon": 9.1734,  # Should be removed
            "geo.granularity": "country",
            "geo.source": "cloudflare",
        }

        filtered = validator.validate_and_filter(attributes)

        # Only country-level attrs should remain
        assert filtered == {
            "geo.country": "DE",
            "geo.granularity": "country",
            "geo.source": "cloudflare",
        }

        # Verify audit log
        validator.audit_logger.info.assert_called_once()
        call_extra = validator.audit_logger.info.call_args[1]["extra"]
        assert "geo_attributes_exported" in str(validator.audit_logger.info.call_args)
        assert call_extra["granularity"] == "country"
        assert "geo.region" in call_extra["hidden_keys"]

    def test_filter_region_level(self):
        """Filter at region level includes region, hides city/coordinates."""
        validator = GeoPrivacyValidator(
            tenant_id="acme-corp", granularity="region"
        )
        validator.audit_logger = MagicMock()

        attributes = {
            "geo.country": "DE",
            "geo.region": "BW",  # Allowed at region level
            "geo.city": "Stuttgart",  # Should be removed
            "geo.lat": 48.7758,  # Should be removed
            "geo.granularity": "region",
            "geo.source": "cloudflare",
            "geo.metro_code": "Stuttgart",  # Allowed
        }

        filtered = validator.validate_and_filter(attributes)

        assert "geo.region" in filtered
        assert "geo.city" not in filtered
        assert "geo.lat" not in filtered
        assert "geo.metro_code" in filtered

    def test_filter_city_level(self):
        """Filter at city level includes city, hides coordinates."""
        validator = GeoPrivacyValidator(
            tenant_id="acme-corp", granularity="city"
        )

        attributes = {
            "geo.country": "DE",
            "geo.region": "BW",
            "geo.city": "Stuttgart",  # Allowed at city level
            "geo.grid_10km": "48.7_9.1",  # Allowed
            "geo.lat": 48.7758,  # Should be removed
            "geo.lon": 9.1734,  # Should be removed
            "geo.granularity": "city",
            "geo.source": "cloudflare",
        }

        filtered = validator.validate_and_filter(attributes)

        assert "geo.city" in filtered
        assert "geo.grid_10km" in filtered
        assert "geo.lat" not in filtered
        assert "geo.lon" not in filtered

    def test_filter_coordinates_level(self):
        """Filter at coordinates level includes everything."""
        validator = GeoPrivacyValidator(
            tenant_id="acme-corp", granularity="coordinates"
        )

        attributes = {
            "geo.country": "DE",
            "geo.region": "BW",
            "geo.city": "Stuttgart",
            "geo.lat": 48.7758,  # Allowed at coordinates level
            "geo.lon": 9.1734,  # Allowed
            "geo.grid_10km": "48.7_9.1",
            "geo.granularity": "coordinates",
            "geo.source": "cloudflare",
        }

        filtered = validator.validate_and_filter(attributes)

        # Everything should be present
        assert len(filtered) == len(attributes)
        assert "geo.lat" in filtered
        assert "geo.lon" in filtered

    def test_unknown_attributes_removed(self):
        """Unknown geo attributes are removed (whitelist enforcement)."""
        validator = GeoPrivacyValidator(
            tenant_id="acme-corp", granularity="country"
        )
        validator.audit_logger = MagicMock()

        attributes = {
            "geo.country": "DE",
            "geo.ip_address": "203.0.113.1",  # Unknown, sneaky
            "geo.novel_attribute": "secret",  # Unknown
            "geo.granularity": "country",
            "geo.source": "cloudflare",
        }

        filtered = validator.validate_and_filter(attributes)

        # Unknown attrs should be stripped
        assert "geo.ip_address" not in filtered
        assert "geo.novel_attribute" not in filtered
        assert "geo.country" in filtered

    def test_check_consent_city_requires_consent(self):
        """city granularity requires consent (Phase 2)."""
        validator = GeoPrivacyValidator(
            tenant_id="acme-corp", granularity="city"
        )
        # Phase 1: city requires explicit consent (not implemented yet)
        assert validator.check_consent("city") is False

    def test_check_consent_country_default(self):
        """country granularity is default (no consent required)."""
        validator = GeoPrivacyValidator(
            tenant_id="acme-corp", granularity="country"
        )
        assert validator.check_consent("country") is True


class TestHeartbeatSignal:
    """Tests for HeartbeatSignal dataclass (immutable)."""

    def test_heartbeat_signal_frozen(self):
        """HeartbeatSignal is immutable (frozen)."""
        signal = HeartbeatSignal(
            tenant_id="acme",
            instance_id="uuid-1",
            is_alive=True,
            uptime_seconds=3600,
            timestamp="2026-09-12T12:00:00Z",
            platform="linux",
            python_version="3.11",
            plugin_count=5,
            memory_usage_bytes=512 * 1024 * 1024,
        )

        # Try to modify (should raise)
        with pytest.raises(AttributeError):
            signal.uptime_seconds = 7200

    def test_heartbeat_signal_asdict(self):
        """HeartbeatSignal can be converted to dict."""
        signal = HeartbeatSignal(
            tenant_id="acme",
            instance_id="uuid-1",
            is_alive=True,
            uptime_seconds=3600,
            timestamp="2026-09-12T12:00:00Z",
            platform="linux",
            python_version="3.11",
            plugin_count=5,
            memory_usage_bytes=512 * 1024 * 1024,
        )

        signal_dict = {k: v for k, v in signal.__dict__.items()}
        assert signal_dict["tenant_id"] == "acme"
        assert signal_dict["is_alive"] is True


class TestGeoAttributes:
    """Tests for GeoAttributes dataclass (immutable)."""

    def test_geo_attributes_frozen(self):
        """GeoAttributes is immutable."""
        geo = GeoAttributes(
            country="DE", region="BW", city="Stuttgart", granularity="city"
        )

        with pytest.raises(AttributeError):
            geo.country = "US"

    def test_geo_attributes_optional_fields(self):
        """GeoAttributes region/city are optional."""
        geo_country_only = GeoAttributes(country="DE")
        assert geo_country_only.country == "DE"
        assert geo_country_only.region is None
        assert geo_country_only.city is None
        assert geo_country_only.granularity == "country"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
