"""Tests for data-residency validation (ADR-0026 Section D).

15 test cases:
- Known region in matching zone → True
- Known region in wrong zone → False
- Unknown region → False (fail-closed)
- validate_residency raises DataResidencyViolation on mismatch
- Audit event emitted before raise
- None tenant zone → no check (pass-through)
- datasource_residency_strict=False → warning only, not exception
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from corvin_compute.fabric.datasources.residency import (
    _DEFAULT_REGION_ZONE_MAP,
    DataResidencyViolation,
    _region_in_zone,
    validate_residency,
)


class _MockManifest:
    def __init__(self, name: str, region: str):
        self.name = name
        self.source = MagicMock()
        self.source.region = region


class TestRegionInZone(unittest.TestCase):
    def test_eu_central_1_in_eu_zone(self):
        self.assertTrue(_region_in_zone("eu-central-1", "eu"))

    def test_eu_west_1_in_eu_zone(self):
        self.assertTrue(_region_in_zone("eu-west-1", "eu"))

    def test_us_east_1_in_us_zone(self):
        self.assertTrue(_region_in_zone("us-east-1", "us"))

    def test_ap_southeast_1_in_apac_zone(self):
        self.assertTrue(_region_in_zone("ap-southeast-1", "apac"))

    def test_eu_region_not_in_us_zone(self):
        self.assertFalse(_region_in_zone("eu-central-1", "us"))

    def test_us_region_not_in_eu_zone(self):
        self.assertFalse(_region_in_zone("us-east-1", "eu"))

    def test_unknown_region_returns_false_fail_closed(self):
        self.assertFalse(_region_in_zone("nonexistent-region-99", "eu"))

    def test_unknown_region_returns_false_for_any_zone(self):
        self.assertFalse(_region_in_zone("nonexistent-region-99", "us"))
        self.assertFalse(_region_in_zone("nonexistent-region-99", "apac"))

    def test_custom_zone_map(self):
        custom = {"custom-region-1": "custom_zone"}
        self.assertTrue(_region_in_zone("custom-region-1", "custom_zone", custom))
        self.assertFalse(_region_in_zone("eu-central-1", "eu", custom))  # not in custom map

    def test_at_least_20_default_mappings(self):
        self.assertGreaterEqual(len(_DEFAULT_REGION_ZONE_MAP), 20)


class TestValidateResidency(unittest.TestCase):
    def _make_audit_list(self):
        events = []
        def audit_fn(event, details):
            events.append((event, details))
        return events, audit_fn

    def test_none_tenant_config_passes(self):
        manifest = _MockManifest("ds1", "eu-central-1")
        events, audit_fn = self._make_audit_list()
        # Should not raise
        validate_residency(manifest, None, audit_fn)
        self.assertEqual(events, [])

    def test_no_zone_in_tenant_config_passes(self):
        manifest = _MockManifest("ds1", "eu-central-1")
        events, audit_fn = self._make_audit_list()
        validate_residency(manifest, {}, audit_fn)
        self.assertEqual(events, [])

    def test_matching_region_zone_passes(self):
        manifest = _MockManifest("ds1", "eu-central-1")
        events, audit_fn = self._make_audit_list()
        validate_residency(manifest, {"data_residency": "eu"}, audit_fn)
        self.assertEqual(events, [])

    def test_mismatched_region_raises(self):
        manifest = _MockManifest("ds1", "us-east-1")
        events, audit_fn = self._make_audit_list()
        with self.assertRaises(DataResidencyViolation):
            validate_residency(manifest, {"data_residency": "eu"}, audit_fn)

    def test_audit_emitted_before_raise(self):
        """Audit-first: event must be in events list even when exception is raised."""
        manifest = _MockManifest("ds1", "us-east-1")
        events, audit_fn = self._make_audit_list()
        try:
            validate_residency(manifest, {"data_residency": "eu"}, audit_fn)
        except DataResidencyViolation:
            pass
        self.assertEqual(len(events), 1)
        event_name, event_details = events[0]
        self.assertEqual(event_name, "datasource.residency_violation")
        self.assertEqual(event_details["datasource_name"], "ds1")
        self.assertEqual(event_details["declared_region"], "us-east-1")
        self.assertEqual(event_details["tenant_zone"], "eu")

    def test_strict_false_no_exception(self):
        """strict=False → audit emitted but no exception raised."""
        manifest = _MockManifest("ds1", "us-east-1")
        events, audit_fn = self._make_audit_list()
        # Should NOT raise
        validate_residency(
            manifest,
            {"data_residency": "eu", "datasource_residency_strict": False},
            audit_fn,
        )
        # Audit still emitted
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][0], "datasource.residency_violation")

    def test_unknown_region_strict_raises(self):
        """Unknown region → fail-closed → raises DataResidencyViolation."""
        manifest = _MockManifest("ds1", "unknown-region-xyz")
        events, audit_fn = self._make_audit_list()
        with self.assertRaises(DataResidencyViolation):
            validate_residency(manifest, {"data_residency": "eu"}, audit_fn)


if __name__ == "__main__":
    unittest.main(verbosity=2)
