"""Tests for corvin_plugins.manifest — registry records, deps, settings (ADR-0233).

These replace the retired prototype's ``test_models.py``, which consisted of 22
``pytest.skip("Awaiting models.py implementation")`` calls against a 527-line
module — it asserted nothing.  Every case here exercises real behaviour, and the
dependency-operator and round-trip cases pin the two defects the salvage fixed.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

# ── Adjust path so tests can be run standalone ───────────────────────────────
_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[3]            # CorvinOS repo root
_PKG = _HERE.parents[1]             # core/plugins (holds the corvin_plugins package)
for _p in (str(_PKG), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from corvin_plugins.manifest import (  # noqa: E402
    BreakingChange,
    CircularDependencyError,
    DependencyConflictError,
    DependencyResolver,
    PIIRisk,
    PluginDependency,
    PluginError,
    PluginManifest,
    PluginOrigin,
    PluginRecord,
    SettingsValidator,
    UnknownPluginType,
    UpdatePolicy,
    ValidationError,
    plan_settings_migration,
)


def _record(pid: str = "acme-notify", **kw) -> PluginRecord:
    """A minimal valid record; keyword args override any field."""
    base = dict(
        plugin_id=pid,
        version="1.0.0",
        display_name="Acme Notify",
        plugin_type="notification_backend",
    )
    base.update(kw)
    return PluginRecord(**base)


# ── PluginRecord ──────────────────────────────────────────────────────────────


class TestPluginRecord(unittest.TestCase):
    def test_minimal_record(self):
        rec = _record()
        self.assertEqual(rec.plugin_id, "acme-notify")
        self.assertEqual(rec.full_id, "acme-notify/1.0.0")
        self.assertFalse(rec.enabled, "a fresh record must not be enabled")

    def test_unknown_plugin_type_is_rejected(self):
        """plugin_type must name a real extension point, not a second taxonomy."""
        with self.assertRaises(UnknownPluginType):
            _record(plugin_type="skill")  # the prototype's parallel enum
        with self.assertRaises(UnknownPluginType):
            _record(plugin_type="")

    def test_known_plugin_types_are_accepted(self):
        for ptype in ("audit_backend", "worker_engine", "router_backend"):
            self.assertEqual(_record(plugin_type=ptype).plugin_type, ptype)

    def test_empty_plugin_id_is_rejected(self):
        with self.assertRaises(PluginError):
            _record(pid="")

    def test_with_enabled_returns_a_copy(self):
        rec = _record()
        now = datetime(2026, 7, 26, tzinfo=timezone.utc)
        on = rec.with_enabled(True, now=now)
        self.assertTrue(on.enabled)
        self.assertEqual(on.enabled_at, now)
        self.assertFalse(rec.enabled, "the original record must not be mutated")
        off = on.with_enabled(False)
        self.assertFalse(off.enabled)
        self.assertIsNone(off.enabled_at, "disable must clear the timestamp")


class TestConsentGate(unittest.TestCase):
    """consent_required() is deny-by-default; callers must not narrow it."""

    def test_builtin_without_pii_needs_no_consent(self):
        rec = _record(origin=PluginOrigin.BUILTIN, pii_risk=PIIRisk.NONE)
        self.assertFalse(rec.consent_required())

    def test_community_origin_always_needs_consent(self):
        rec = _record(origin=PluginOrigin.COMMUNITY, pii_risk=PIIRisk.NONE)
        self.assertTrue(rec.consent_required())

    def test_high_pii_needs_consent_even_when_builtin(self):
        rec = _record(origin=PluginOrigin.BUILTIN, pii_risk=PIIRisk.HIGH)
        self.assertTrue(rec.consent_required())

    def test_explicit_flag_needs_consent(self):
        rec = _record(
            origin=PluginOrigin.VETTED, pii_risk=PIIRisk.LOW, requires_consent=True
        )
        self.assertTrue(rec.consent_required())


class TestRoundTrip(unittest.TestCase):
    """The prototype dropped four fields on save; this pins the full round-trip."""

    def test_every_field_survives_a_round_trip(self):
        rec = _record(
            origin=PluginOrigin.VETTED,
            pii_risk=PIIRisk.MEDIUM,
            requires_consent=True,
            audit_required=True,
            installed_at=datetime(2026, 7, 26, 10, 30, tzinfo=timezone.utc),
            installed_by="operator",
            enabled=True,
            enabled_at=datetime(2026, 7, 26, 10, 31, tzinfo=timezone.utc),
            update_policy=UpdatePolicy.PATCH,
            settings={"channel": "ops"},
            settings_schema={"type": "object", "properties": {"channel": {"type": "string"}}},
            settings_schema_version="2.0",
            class_path="acme.plugins:Notify",
            dependencies=["acme-core>=1.0.0"],
            version_history=[PluginManifest("acme-notify", "0.9.0")],
            last_error_type="TimeoutError",
            error_count=2,
        )
        restored = PluginRecord.from_dict(rec.to_dict())
        self.assertEqual(restored.to_dict(), rec.to_dict())
        # The four fields the prototype silently lost:
        self.assertEqual(restored.settings_schema, rec.settings_schema)
        self.assertEqual(restored.settings_schema_version, "2.0")
        self.assertTrue(restored.audit_required)
        self.assertEqual(len(restored.version_history), 1)

    def test_unknown_field_fails_closed(self):
        """A registry from a newer CorvinOS must not be half-read."""
        data = _record().to_dict()
        data["quota_from_the_future"] = {"tokens": 5}
        with self.assertRaises(PluginError):
            PluginRecord.from_dict(data)

    def test_missing_required_field_raises(self):
        data = _record().to_dict()
        del data["version"]
        with self.assertRaises(PluginError):
            PluginRecord.from_dict(data)

    def test_display_name_falls_back_to_id(self):
        data = _record().to_dict()
        data["display_name"] = ""
        self.assertEqual(PluginRecord.from_dict(data).display_name, "acme-notify")


# ── Dependency constraints ────────────────────────────────────────────────────


class TestPluginDependency(unittest.TestCase):
    def test_parse_all_operators(self):
        cases = {
            "acme>=1.0.0": (">=", "1.0.0"),
            "acme<=2.0.0": ("<=", "2.0.0"),
            "acme==1.2.3": ("==", "1.2.3"),
            "acme!=1.2.3": ("!=", "1.2.3"),
            "acme~=1.2": ("~=", "1.2"),
            "acme>1.0.0": (">", "1.0.0"),
            "acme<2.0.0": ("<", "2.0.0"),
        }
        for spec, (op, ver) in cases.items():
            dep = PluginDependency.parse(spec)
            self.assertEqual(dep.plugin_id, "acme", spec)
            self.assertEqual(dep.version_range, op + ver, spec)

    def test_parse_longest_operator_wins(self):
        """'>=' must not be parsed as '>' with a stray '=' in the version."""
        self.assertEqual(PluginDependency.parse("acme>=1.0").version_range, ">=1.0")
        self.assertEqual(PluginDependency.parse("acme<=1.0").version_range, "<=1.0")

    def test_parse_without_constraint_means_any(self):
        dep = PluginDependency.parse("  acme  ")
        self.assertEqual(dep.plugin_id, "acme")
        self.assertEqual(dep.version_range, "")
        self.assertTrue(dep.satisfies("0.0.1"))

    def test_strict_greater_than_is_honoured(self):
        """The prototype parsed '>' but evaluated every such constraint to False."""
        dep = PluginDependency.parse("acme>1.0.0")
        self.assertTrue(dep.satisfies("1.0.1"))
        self.assertFalse(dep.satisfies("1.0.0"))

    def test_strict_less_than_is_honoured(self):
        dep = PluginDependency.parse("acme<2.0.0")
        self.assertTrue(dep.satisfies("1.9.9"))
        self.assertFalse(dep.satisfies("2.0.0"))

    def test_at_least_and_exact(self):
        self.assertTrue(PluginDependency.parse("acme>=1.0.0").satisfies("1.4.0"))
        self.assertFalse(PluginDependency.parse("acme>=1.5.0").satisfies("1.4.0"))
        self.assertTrue(PluginDependency.parse("acme==1.4.0").satisfies("1.4.0"))
        self.assertFalse(PluginDependency.parse("acme==1.4.0").satisfies("1.4.1"))

    def test_not_equal(self):
        dep = PluginDependency.parse("acme!=1.4.0")
        self.assertFalse(dep.satisfies("1.4.0"))
        self.assertTrue(dep.satisfies("1.4.1"))

    def test_dotted_x_shorthand_pins_the_stated_precision(self):
        """"1.x" pins the major; "1.2.x" pins the minor."""
        major = PluginDependency("acme", "1.x")
        self.assertTrue(major.satisfies("1.9.0"))
        self.assertTrue(major.satisfies("1.0.0"))
        self.assertFalse(major.satisfies("2.0.0"))
        self.assertFalse(major.satisfies("0.9.0"))

        minor = PluginDependency("acme", "1.2.x")
        self.assertTrue(minor.satisfies("1.2.7"))
        self.assertFalse(minor.satisfies("1.3.0"), "1.2.x must not admit 1.3.0")
        self.assertFalse(minor.satisfies("1.1.9"))

    def test_malformed_constraint_raises_rather_than_denying(self):
        """An unsatisfiable constraint is an error, not a silent 'not installed'."""
        with self.assertRaises(DependencyConflictError):
            PluginDependency("acme", ">=not-a-version").satisfies("1.0.0")
        with self.assertRaises(DependencyConflictError):
            PluginDependency("acme", ">=1.0.0").satisfies("not-a-version")


# ── Load order ────────────────────────────────────────────────────────────────


class TestDependencyResolver(unittest.TestCase):
    def test_linear_chain_orders_dependencies_first(self):
        records = {
            "c": _record("c", dependencies=["b>=1.0.0"]),
            "b": _record("b", dependencies=["a>=1.0.0"]),
            "a": _record("a"),
        }
        self.assertEqual(DependencyResolver(records).load_order(), ["a", "b", "c"])

    def test_order_is_deterministic_across_insert_orders(self):
        """The prototype's order depended on dict insertion order."""
        forward = {"a": _record("a"), "b": _record("b"), "c": _record("c")}
        backward = {"c": _record("c"), "b": _record("b"), "a": _record("a")}
        self.assertEqual(
            DependencyResolver(forward).load_order(),
            DependencyResolver(backward).load_order(),
        )
        self.assertEqual(DependencyResolver(backward).load_order(), ["a", "b", "c"])

    def test_diamond_graph(self):
        records = {
            "top": _record("top", dependencies=["left>=1.0.0", "right>=1.0.0"]),
            "left": _record("left", dependencies=["base>=1.0.0"]),
            "right": _record("right", dependencies=["base>=1.0.0"]),
            "base": _record("base"),
        }
        order = DependencyResolver(records).load_order()
        self.assertEqual(order[0], "base")
        self.assertEqual(order[-1], "top")
        self.assertLess(order.index("left"), order.index("top"))
        self.assertLess(order.index("right"), order.index("top"))

    def test_missing_dependency(self):
        records = {"a": _record("a", dependencies=["absent>=1.0.0"])}
        with self.assertRaises(DependencyConflictError) as ctx:
            DependencyResolver(records).load_order()
        self.assertIn("absent", str(ctx.exception))

    def test_version_mismatch(self):
        records = {
            "a": _record("a", dependencies=["b>=2.0.0"]),
            "b": _record("b", version="1.0.0"),
        }
        with self.assertRaises(DependencyConflictError):
            DependencyResolver(records).load_order()

    def test_cycle_is_detected(self):
        records = {
            "a": _record("a", dependencies=["b>=1.0.0"]),
            "b": _record("b", dependencies=["a>=1.0.0"]),
        }
        with self.assertRaises(CircularDependencyError) as ctx:
            DependencyResolver(records).load_order()
        self.assertIn("a", str(ctx.exception))
        self.assertIn("b", str(ctx.exception))

    def test_empty_registry(self):
        self.assertEqual(DependencyResolver({}).load_order(), [])


# ── Settings validation ───────────────────────────────────────────────────────


_SCHEMA = {
    "type": "object",
    "properties": {
        "model": {"type": "string", "enum": ["haiku", "sonnet"], "default": "sonnet"},
        "depth": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3},
    },
    "required": ["model"],
    "additionalProperties": False,
}


class TestSettingsValidator(unittest.TestCase):
    def test_valid_settings(self):
        self.assertTrue(SettingsValidator(_SCHEMA).validate({"model": "haiku", "depth": 2}))

    def test_wrong_type_rejected(self):
        with self.assertRaises(ValidationError):
            SettingsValidator(_SCHEMA).validate({"model": "haiku", "depth": "deep"})

    def test_out_of_range_rejected(self):
        with self.assertRaises(ValidationError):
            SettingsValidator(_SCHEMA).validate({"model": "haiku", "depth": 9})

    def test_value_outside_enum_rejected(self):
        with self.assertRaises(ValidationError):
            SettingsValidator(_SCHEMA).validate({"model": "opus"})

    def test_missing_required_rejected(self):
        with self.assertRaises(ValidationError):
            SettingsValidator(_SCHEMA).validate({"depth": 3})

    def test_unknown_key_rejected(self):
        with self.assertRaises(ValidationError):
            SettingsValidator(_SCHEMA).validate({"model": "haiku", "nope": 1})

    def test_empty_schema_accepts_anything(self):
        self.assertTrue(SettingsValidator({}).validate({"whatever": True}))

    def test_invalid_schema_is_reported_as_such(self):
        with self.assertRaises(ValidationError) as ctx:
            SettingsValidator({"type": "not-a-type"}).validate({"a": 1})
        self.assertIn("invalid schema", str(ctx.exception))

    def test_defaults_are_collected(self):
        self.assertEqual(
            SettingsValidator(_SCHEMA).defaults(), {"model": "sonnet", "depth": 3}
        )

    def test_defaults_on_schema_without_properties(self):
        self.assertEqual(SettingsValidator({"type": "object"}).defaults(), {})


# ── Breaking-change migration ─────────────────────────────────────────────────


class TestSettingsMigration(unittest.TestCase):
    def test_rename_carries_the_value(self):
        proposed, notes = plan_settings_migration(
            {"legacy_rules": "{}", "keep": 1},
            [BreakingChange("legacy_rules", "custom_rules", "copy JSON")],
        )
        self.assertEqual(proposed, {"custom_rules": "{}", "keep": 1})
        self.assertIn("legacy_rules -> custom_rules", notes[0])

    def test_absent_source_key_is_reported_not_invented(self):
        proposed, notes = plan_settings_migration(
            {"keep": 1}, [BreakingChange("legacy_rules", "custom_rules")]
        )
        self.assertEqual(proposed, {"keep": 1})
        self.assertNotIn("custom_rules", proposed)
        self.assertIn("not present", notes[0])

    def test_no_changes_is_identity(self):
        proposed, notes = plan_settings_migration({"a": 1}, [])
        self.assertEqual(proposed, {"a": 1})
        self.assertEqual(notes, [])

    def test_input_is_not_mutated(self):
        original = {"legacy_rules": "{}"}
        plan_settings_migration(original, [BreakingChange("legacy_rules", "custom_rules")])
        self.assertEqual(original, {"legacy_rules": "{}"})

    def test_manifest_round_trip(self):
        manifest = PluginManifest(
            "acme", "2.0.0", "2.0", (BreakingChange("old", "new", "copy"),)
        )
        restored = PluginManifest.from_dict(manifest.to_dict())
        self.assertEqual(restored, manifest)


if __name__ == "__main__":
    unittest.main()
