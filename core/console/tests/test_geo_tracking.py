"""Unit tests for geo-tracking (ADR-0205)."""
import json
import math
import sys
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from corvin_console.aco.geo_tracking import (
    GeoConsentManager,
    GeoIPReader,
    GeoResult,
    GeoTracker,
    get_geoip_reader,
    clear_geoip_cache,
)


class TestGeoResult:
    """Test GeoResult dataclass and methods."""

    def test_to_dict(self):
        """Test conversion to dict."""
        result = GeoResult(
            country="DE",
            region="BW",
            city="Stuttgart",
            latitude=48.7758,
            longitude=9.1829,
        )
        d = result.to_dict()
        assert d["country"] == "DE"
        assert d["region"] == "BW"
        assert d["city"] == "Stuttgart"
        assert d["latitude"] == 48.7758
        assert d["longitude"] == 9.1829

    def test_grid_coordinates(self):
        """Test 10km grid rasterization."""
        result = GeoResult(
            country="DE",
            latitude=51.3412,
            longitude=12.1532,
        )
        lat_grid, lng_grid = result.grid_coordinates()

        # floor(51.3412 / 0.1) * 0.1 = floor(513.412) * 0.1 = 510 * 0.1 = 51.0
        assert lat_grid == 51.3
        # floor(12.1532 / 0.1) * 0.1 = floor(121.532) * 0.1 = 121 * 0.1 = 12.1
        assert lng_grid == 12.1

    def test_grid_coordinates_no_coords(self):
        """Test grid when no coordinates available."""
        result = GeoResult(country="DE")
        lat_grid, lng_grid = result.grid_coordinates()
        assert lat_grid is None
        assert lng_grid is None


class TestGeoIPReader:
    """Test GeoIPReader class."""

    def test_init_missing_db(self):
        """Test initialization with missing database."""
        with pytest.raises(FileNotFoundError):
            GeoIPReader("/nonexistent/path/to/db.mmdb")

    def test_init_missing_geoip2_library(self, tmp_path):
        """Test ImportError when geoip2 not installed."""
        db_path = tmp_path / "test.mmdb"
        db_path.touch()

        with patch.dict("sys.modules", {"geoip2": None}):
            with pytest.raises(ImportError, match="geoip2 library required"):
                GeoIPReader(db_path)

    def test_lookup_success(self, tmp_path):
        """Test successful GeoIP lookup."""
        # Create a mock GeoIPReader
        db_path = tmp_path / "test.mmdb"
        db_path.touch()  # Create dummy file

        reader = GeoIPReader.__new__(GeoIPReader)
        reader.db_path = db_path

        # Mock the internal reader
        mock_response = Mock()
        mock_response.country.iso_code = "DE"
        mock_response.subdivisions = [Mock(iso_code="BW")]
        mock_response.city.name = "Stuttgart"
        mock_response.location.latitude = 48.7758
        mock_response.location.longitude = 9.1829

        reader.reader = Mock()
        reader.reader.city.return_value = mock_response

        result = reader.lookup("1.1.1.1")

        assert result.country == "DE"
        assert result.region == "BW"
        assert result.city == "Stuttgart"
        assert result.latitude == 48.7758
        assert result.longitude == 9.1829

    def test_lookup_failure(self, tmp_path, caplog):
        """Test lookup failure returns None."""
        db_path = tmp_path / "test.mmdb"
        db_path.touch()

        reader = GeoIPReader.__new__(GeoIPReader)
        reader.db_path = db_path
        reader.reader = Mock()
        reader.reader.city.side_effect = Exception("Invalid IP")

        result = reader.lookup("invalid-ip")

        assert result is None
        assert "GeoIP lookup failed" in caplog.text

    def test_get_region_with_iso_code(self, tmp_path):
        """Test _get_region extracts ISO code."""
        db_path = tmp_path / "test.mmdb"
        db_path.touch()

        reader = GeoIPReader.__new__(GeoIPReader)

        mock_response = Mock()
        mock_response.subdivisions = [Mock(iso_code="BW")]

        region = reader._get_region(mock_response)
        assert region == "BW"

    def test_get_region_fallback_to_name(self, tmp_path):
        """Test _get_region falls back to name when iso_code missing."""
        db_path = tmp_path / "test.mmdb"
        db_path.touch()

        reader = GeoIPReader.__new__(GeoIPReader)

        mock_response = Mock()
        mock_subdivision = Mock(spec=[])  # No iso_code attribute
        mock_subdivision.name = "Baden-Württemberg"
        mock_response.subdivisions = [mock_subdivision]

        region = reader._get_region(mock_response)
        assert region == "Baden-Württemberg"

    def test_get_region_no_subdivisions(self, tmp_path):
        """Test _get_region returns None when no subdivisions."""
        db_path = tmp_path / "test.mmdb"
        db_path.touch()

        reader = GeoIPReader.__new__(GeoIPReader)

        mock_response = Mock()
        mock_response.subdivisions = []

        region = reader._get_region(mock_response)
        assert region is None


class TestGeoConsentManager:
    """Test consent management."""

    def test_get_tier_default(self, tmp_path):
        """Test default tier is 1."""
        config_file = tmp_path / "spec.yaml"
        config_file.write_text("")  # Empty config

        manager = GeoConsentManager(config_file)
        assert manager.get_tier() == 1

    def test_get_tier_from_config(self, tmp_path):
        """Test reading tier from config."""
        config_file = tmp_path / "spec.yaml"
        config_file.write_text(
            "spec:\n"
            "  telemetry:\n"
            "    geo_tracking_tier: 2\n"
        )

        manager = GeoConsentManager(config_file)
        assert manager.get_tier() == 2

    def test_get_tier_invalid_bounds_to_default(self, tmp_path):
        """Test invalid tier clamps to 1."""
        config_file = tmp_path / "spec.yaml"
        config_file.write_text(
            "spec:\n"
            "  telemetry:\n"
            "    geo_tracking_tier: 99\n"
        )

        manager = GeoConsentManager(config_file)
        assert manager.get_tier() == 1

    def test_has_consent_false_by_default(self, tmp_path):
        """Test consent defaults to false."""
        config_file = tmp_path / "spec.yaml"
        config_file.write_text("")

        manager = GeoConsentManager(config_file)
        assert manager.has_consent() is False

    def test_has_consent_from_config(self, tmp_path):
        """Test reading consent from config."""
        config_file = tmp_path / "spec.yaml"
        config_file.write_text(
            "spec:\n"
            "  telemetry:\n"
            "    geo_tracking_consent_given: true\n"
        )

        manager = GeoConsentManager(config_file)
        assert manager.has_consent() is True

    def test_should_track_tier_1_always(self, tmp_path):
        """Test Tier 1 is always allowed."""
        config_file = tmp_path / "spec.yaml"
        config_file.write_text("")  # No config

        manager = GeoConsentManager(config_file)
        assert manager.should_track_tier_n(1) is True

    def test_should_track_tier_2_requires_consent(self, tmp_path):
        """Test Tier 2 requires explicit consent."""
        config_file = tmp_path / "spec.yaml"
        config_file.write_text(
            "spec:\n"
            "  telemetry:\n"
            "    geo_tracking_tier: 2\n"
            "    geo_tracking_consent_given: false\n"
        )

        manager = GeoConsentManager(config_file)
        assert manager.should_track_tier_n(2) is False

    def test_should_track_tier_2_with_consent(self, tmp_path):
        """Test Tier 2 allowed with consent."""
        config_file = tmp_path / "spec.yaml"
        config_file.write_text(
            "spec:\n"
            "  telemetry:\n"
            "    geo_tracking_tier: 2\n"
            "    geo_tracking_consent_given: true\n"
        )

        manager = GeoConsentManager(config_file)
        assert manager.should_track_tier_n(2) is True


class TestGeoTracker:
    """Test high-level GeoTracker orchestrator."""

    def test_track_tier_1_country_only(self, tmp_path):
        """Test Tier 1 returns only country."""
        db_path = tmp_path / "test.mmdb"
        db_path.touch()

        config_file = tmp_path / "spec.yaml"
        config_file.write_text(
            "spec:\n"
            "  telemetry:\n"
            "    geo_tracking_tier: 1\n"
        )

        tracker = GeoTracker.__new__(GeoTracker)
        tracker.reader = Mock()
        tracker.consent = GeoConsentManager(config_file)
        tracker.instance_id_hash = "test_instance"

        # Mock lookup result
        mock_result = GeoResult(
            country="DE",
            region="BW",
            city="Stuttgart",
            latitude=48.7758,
            longitude=9.1829,
        )
        tracker.reader.lookup.return_value = mock_result

        result = tracker.track("1.1.1.1")

        # Verify truncation: Tier 1 should have no region/city/coords
        assert result.country == "DE"
        assert result.region is None
        assert result.city is None
        assert result.latitude is None
        assert result.longitude is None

    def test_track_tier_2_blocked_without_consent(self, tmp_path):
        """Test Tier 2 blocked when consent not given."""
        db_path = tmp_path / "test.mmdb"
        db_path.touch()

        config_file = tmp_path / "spec.yaml"
        config_file.write_text(
            "spec:\n"
            "  telemetry:\n"
            "    geo_tracking_tier: 2\n"
            "    geo_tracking_consent_given: false\n"
        )

        tracker = GeoTracker.__new__(GeoTracker)
        tracker.reader = Mock()
        tracker.consent = GeoConsentManager(config_file)
        tracker.instance_id_hash = "test_instance"

        result = tracker.track("1.1.1.1")

        assert result is None
        tracker.reader.lookup.assert_not_called()

    def test_track_tier_3_with_consent_keeps_grid(self, tmp_path, caplog):
        """Test Tier 3 with consent keeps all data including grid."""
        import logging

        db_path = tmp_path / "test.mmdb"
        db_path.touch()

        config_file = tmp_path / "spec.yaml"
        config_file.write_text(
            "spec:\n"
            "  telemetry:\n"
            "    geo_tracking_tier: 3\n"
            "    geo_tracking_consent_given: true\n"
        )

        tracker = GeoTracker.__new__(GeoTracker)
        tracker.reader = Mock()
        tracker.consent = GeoConsentManager(config_file)
        tracker.instance_id_hash = "test_instance"

        mock_result = GeoResult(
            country="DE",
            region="BW",
            city="Stuttgart",
            latitude=48.7758,
            longitude=9.1829,
        )
        tracker.reader.lookup.return_value = mock_result

        with caplog.at_level(logging.INFO):
            result = tracker.track("1.1.1.1")

        # Tier 3 keeps everything
        assert result.country == "DE"
        assert result.region == "BW"
        assert result.city == "Stuttgart"
        assert result.latitude == 48.7758
        assert result.longitude == 9.1829

        # Verify audit log
        assert "geo_lookup" in caplog.text

    def test_audit_lookup_logs_content_free_info(self, tmp_path, caplog):
        """Test audit logs only content-free info."""
        import logging

        db_path = tmp_path / "test.mmdb"
        db_path.touch()

        config_file = tmp_path / "spec.yaml"
        config_file.write_text("")

        tracker = GeoTracker.__new__(GeoTracker)
        tracker.reader = Mock()
        tracker.consent = GeoConsentManager(config_file)
        tracker.instance_id_hash = "test_instance_hash"

        result = GeoResult(country="DE", region="BW", city="Stuttgart")

        with caplog.at_level(logging.INFO):
            tracker._audit_lookup(result, tier=2)

        # Verify audit entry is logged
        assert "geo_lookup" in caplog.text
        assert "test_instance_hash" in caplog.text
        assert "BW" in caplog.text
        # IP should NOT be logged
        assert "1.1.1.1" not in caplog.text


class TestGeoIPReaderCache:
    """Test caching functions."""

    @patch("corvin_console.aco.geo_tracking.GeoIPReader")
    def test_get_geoip_reader_cache(self, mock_reader_class, tmp_path):
        """Test that get_geoip_reader caches instances."""
        clear_geoip_cache()

        db_path = tmp_path / "test.mmdb"

        # Mock the constructor
        mock_reader_class.return_value = Mock()

        reader1 = get_geoip_reader(db_path)
        reader2 = get_geoip_reader(db_path)

        # Should be the same instance (cached)
        assert reader1 is reader2
        # Constructor should only be called once (due to caching)
        mock_reader_class.assert_called_once()

    @patch("corvin_console.aco.geo_tracking.GeoIPReader")
    def test_clear_geoip_cache(self, mock_reader_class, tmp_path):
        """Test cache clearing."""
        clear_geoip_cache()

        db_path = tmp_path / "test.mmdb"
        # Each call returns a different Mock instance
        mock_reader_class.side_effect = [Mock(spec=GeoIPReader), Mock(spec=GeoIPReader)]

        reader1 = get_geoip_reader(db_path)
        clear_geoip_cache()
        reader2 = get_geoip_reader(db_path)

        # Should be different instances after clear
        assert reader1 is not reader2
        # Constructor should be called twice (once before clear, once after)
        assert mock_reader_class.call_count == 2
