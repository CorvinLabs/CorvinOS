"""Tests for DSI v1 manifest validation (ADR-0106).

Covers a confirmed blind spot in ``validate_dsiv1_manifest``:

  ``name = raw.get("name", "")`` is passed straight into
  ``_DSI_NAME_RE.match(name)`` with no ``isinstance(name, str)`` guard.
  A client-supplied manifest with a type-confused ``name`` field
  (int, explicit JSON ``null``, list, dict) makes ``re.match()`` raise
  ``TypeError`` instead of the documented ``DSIv1PolicyError`` (a
  ``ValueError`` subclass) the caller (and the console HTTP route) expects.

Before this file existed, ``validate_dsiv1_manifest`` had zero direct
unit-test coverage anywhere in the repo.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from corvin_compute.fabric.datasources.manifest import (
    DSIv1ConnectionManifest,
    DSIv1PolicyError,
    validate_dsiv1_manifest,
)


def _valid_manifest(**overrides) -> dict:
    base = {
        "dsi_version": "1",
        "name": "my-source",
        "adapter": "postgres",
        "config": {"host": "db"},
        "data_classification": "INTERNAL",
    }
    base.update(overrides)
    return base


class TestValidateDsiv1ManifestHappyPath(unittest.TestCase):
    def test_minimal_valid_manifest_returns_manifest(self):
        result = validate_dsiv1_manifest(_valid_manifest())
        self.assertIsInstance(result, DSIv1ConnectionManifest)
        self.assertEqual(result.name, "my-source")
        self.assertEqual(result.adapter, "postgres")
        self.assertEqual(result.data_classification, "INTERNAL")
        # Defaults
        self.assertEqual(result.data_residency, "any")
        self.assertTrue(result.read_only)


class TestValidateDsiv1ManifestNameTypeConfusion(unittest.TestCase):
    """Blind spot: type-confused 'name' must raise DSIv1PolicyError, not TypeError."""

    def test_integer_name_raises_policy_error_not_type_error(self):
        manifest = _valid_manifest(name=123)
        with self.assertRaises(DSIv1PolicyError):
            validate_dsiv1_manifest(manifest)

    def test_explicit_json_null_name_raises_policy_error_not_type_error(self):
        # raw.get("name", "") returns None here (not the "" default) because
        # the key is present with an explicit null value — this is the
        # trickiest case since the default-value fallback does not kick in.
        manifest = _valid_manifest(name=None)
        with self.assertRaises(DSIv1PolicyError):
            validate_dsiv1_manifest(manifest)

    def test_list_name_raises_policy_error_not_type_error(self):
        manifest = _valid_manifest(name=["not", "a", "string"])
        with self.assertRaises(DSIv1PolicyError):
            validate_dsiv1_manifest(manifest)

    def test_dict_name_raises_policy_error_not_type_error(self):
        manifest = _valid_manifest(name={"nested": "object"})
        with self.assertRaises(DSIv1PolicyError):
            validate_dsiv1_manifest(manifest)

    def test_float_name_raises_policy_error_not_type_error(self):
        manifest = _valid_manifest(name=1.5)
        with self.assertRaises(DSIv1PolicyError):
            validate_dsiv1_manifest(manifest)

    def test_bool_name_raises_policy_error_not_type_error(self):
        # bool is a subclass of int in Python; still not a valid manifest name.
        manifest = _valid_manifest(name=True)
        with self.assertRaises(DSIv1PolicyError):
            validate_dsiv1_manifest(manifest)


class TestValidateDsiv1ManifestNameStillRejectsBadStrings(unittest.TestCase):
    """Sanity: the existing string-based validation path is unaffected."""

    def test_empty_string_name_raises_policy_error(self):
        with self.assertRaises(DSIv1PolicyError):
            validate_dsiv1_manifest(_valid_manifest(name=""))

    def test_uppercase_name_raises_policy_error(self):
        with self.assertRaises(DSIv1PolicyError):
            validate_dsiv1_manifest(_valid_manifest(name="MySource"))

    def test_name_starting_with_digit_raises_policy_error(self):
        with self.assertRaises(DSIv1PolicyError):
            validate_dsiv1_manifest(_valid_manifest(name="1source"))


if __name__ == "__main__":
    unittest.main()
