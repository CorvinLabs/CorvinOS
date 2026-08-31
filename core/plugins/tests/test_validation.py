"""The validator must agree with the registry, and must stay out of the way (ADR-0247).

Two properties are load-bearing and both are tested here rather than asserted in
a docstring:

1. **It reuses enforcement.** A record the registry rejects must be rejected here
   too. The test for that constructs the failure through the REAL invariants
   rather than a hand-written expectation, so the two cannot drift apart.
2. **It touches nothing.** Validation runs on a developer's machine as a
   build-time action. If it wrote to the hash-chained audit trail, every lint run
   would put developer noise into a GDPR Art. 30 record.
"""
from __future__ import annotations

from corvin_plugins.protocol import HealthStatus, PluginContext
from corvin_plugins.validation import (
    ERROR,
    WARNING,
    ValidationReport,
    merge,
    validate_class,
    validate_discovery,
    validate_manifest_file,
    validate_record_dict,
    validate_registration,
)


def _record_dict(**over) -> dict:
    base = {
        "plugin_id": "com.example.router",
        "version": "1.0.0",
        "display_name": "Example Router",
        "plugin_type": "router_backend",
    }
    base.update(over)
    return base


class _GoodPlugin:
    plugin_id = "com.example.router"
    plugin_type = "router_backend"
    version = "1.0.0"
    display_name = "Example Router"

    def on_load(self, ctx: PluginContext) -> None:
        if ctx.router_registry is not None:
            ctx.router_registry.set_active(self)

    def on_unload(self) -> None:
        pass

    def health_check(self) -> HealthStatus:
        return HealthStatus(ok=True, message="ok")


# ── Record validation ────────────────────────────────────────────────────────

def test_a_well_formed_record_passes():
    report = validate_record_dict(_record_dict())
    assert report.ok, report.findings


def test_missing_required_field_is_an_error():
    data = _record_dict()
    del data["plugin_type"]
    report = validate_record_dict(data)
    assert not report.ok
    assert any("plugin_type" in f.message for f in report.errors)


def test_unknown_plugin_type_is_an_error_and_lists_the_known_ones():
    report = validate_record_dict(_record_dict(plugin_type="nonsense_backend"))
    assert not report.ok
    msg = " ".join(f.message for f in report.errors)
    assert "router_backend" in msg


def test_community_origin_may_not_claim_a_privileged_boot_layer():
    """The refusal comes from PluginRecord.__post_init__, not from a copy here.

    This is the anti-drift property: if that invariant were relaxed in the
    manifest module, this test would go green on its own and tell us the
    validator followed the registry rather than contradicting it.
    """
    report = validate_record_dict(
        _record_dict(boot_layer="compliance", origin="community")
    )
    assert not report.ok
    assert any(f.code == "record.invalid" for f in report.errors)


def test_unconsumed_type_warns_but_does_not_block():
    """A user_backend is well-formed and the registry accepts it — but nothing
    calls it. That is a warning with a loud message, never an error."""
    report = validate_record_dict(_record_dict(plugin_type="user_backend"))
    assert report.ok, "an unconsumed type must not be an error"
    codes = [f.code for f in report.warnings]
    assert "type.unconsumed" in codes
    text = " ".join(f.message for f in report.warnings)
    assert "never be called" in text


def test_consumed_type_does_not_warn_about_consumption():
    report = validate_record_dict(_record_dict(plugin_type="router_backend"))
    assert "type.unconsumed" not in [f.code for f in report.warnings]


# ── Class validation ─────────────────────────────────────────────────────────

def test_good_class_passes():
    assert validate_class(_GoodPlugin, expected_type="router_backend").ok


def test_missing_lifecycle_method_is_an_error():
    class NoHealth:
        plugin_id = "x.y"
        plugin_type = "router_backend"
        version = "1.0.0"
        display_name = "X"

        def on_load(self, ctx): ...
        def on_unload(self): ...

    report = validate_class(NoHealth)
    assert not report.ok
    assert any("health_check" in f.message for f in report.errors)


def test_class_type_mismatch_against_manifest_is_an_error():
    report = validate_class(_GoodPlugin, expected_type="recall_backend")
    assert not report.ok
    assert any(f.code == "class.type_mismatch" for f in report.errors)


def test_on_load_that_never_registers_warns():
    class NeverRegisters:
        plugin_id = "x.y"
        plugin_type = "router_backend"
        version = "1.0.0"
        display_name = "X"

        def on_load(self, ctx):
            # NOTE: deliberately does not self-register. The handle name must not
            # appear anywhere in this method's source — the check is a substring
            # scan and would match it even inside a comment.
            self._cfg = ctx.config

        def on_unload(self): ...
        def health_check(self): return HealthStatus(ok=True)

    report = validate_class(NeverRegisters)
    assert report.ok, "not registering is not a registry-level rejection"
    assert "class.no_self_registration" in [f.code for f in report.warnings]


# ── Registration against the real registry ───────────────────────────────────

def test_registration_into_a_throwaway_registry_succeeds():
    assert validate_registration(_GoodPlugin()).ok


def test_registration_failure_is_reported_not_raised():
    class Exploding:
        plugin_id = "x.boom"
        plugin_type = "router_backend"
        version = "1.0.0"
        display_name = "Boom"

        def on_load(self, ctx):
            raise RuntimeError("no")

        def on_unload(self): ...
        def health_check(self): return HealthStatus(ok=True)

    report = validate_registration(Exploding())
    assert not report.ok
    assert any(f.code == "registry.refused" for f in report.errors)
    assert any("RuntimeError" in f.message for f in report.errors)


def test_validation_never_writes_to_the_audit_trail(monkeypatch, tmp_path):
    """The load-bearing compliance property of this module.

    ``bootstrap.build_context`` binds a REAL audit sink. If validation used it,
    every `corvin plugin check` run would append to the hash-chained trail. This
    asserts the sandbox context is used instead, by failing loudly if anything
    reaches the real emitter.
    """
    import corvin_plugins.bootstrap as bootstrap

    def _boom(*a, **kw):  # pragma: no cover - must never run
        raise AssertionError("validation reached the real audit sink")

    monkeypatch.setattr(bootstrap, "_default_audit_emit", _boom, raising=False)
    assert validate_registration(_GoodPlugin(), corvin_home=tmp_path).ok


def test_registration_leaves_no_state_behind():
    """Two runs in a row must both succeed — a leaked registry would collide."""
    assert validate_registration(_GoodPlugin()).ok
    assert validate_registration(_GoodPlugin()).ok


# ── Discovery ────────────────────────────────────────────────────────────────

def test_no_entry_point_and_no_class_path_warns_but_does_not_block():
    """Cannot be an error: class_path lives in tenant.corvin.yaml, which a
    plugin directory cannot see. Making it an error meant every freshly
    scaffolded plugin failed its own check — caught by running new → check
    end to end, not by any unit test."""
    report = validate_discovery(
        has_entry_point=False, has_class_path=False, auto_discover=False
    )
    assert report.ok
    assert any(f.code == "discovery.unreachable" for f in report.warnings)


def test_entry_point_only_with_autodiscover_off_warns():
    """The silent trap: correct plugin, default config, never loaded."""
    report = validate_discovery(
        has_entry_point=True, has_class_path=False, auto_discover=False
    )
    assert report.ok
    assert "discovery.entry_point_not_scanned" in [f.code for f in report.warnings]


def test_entry_point_with_autodiscover_on_is_clean():
    report = validate_discovery(
        has_entry_point=True, has_class_path=False, auto_discover=True
    )
    assert report.ok and not report.warnings


def test_class_path_alone_is_enough():
    report = validate_discovery(
        has_entry_point=False, has_class_path=True, auto_discover=False
    )
    assert report.ok and not report.warnings


# ── Manifest file + report mechanics ─────────────────────────────────────────

def test_missing_manifest_file_is_an_error(tmp_path):
    report = validate_manifest_file(tmp_path / "nope.yaml")
    assert not report.ok
    assert any(f.code == "manifest.missing" for f in report.errors)


def test_unparseable_manifest_is_an_error(tmp_path):
    p = tmp_path / "plugin.yaml"
    p.write_text("key: [unclosed\n", encoding="utf-8")
    report = validate_manifest_file(p)
    assert not report.ok


def test_non_mapping_manifest_is_an_error(tmp_path):
    p = tmp_path / "plugin.yaml"
    p.write_text("- just\n- a\n- list\n", encoding="utf-8")
    report = validate_manifest_file(p)
    assert not report.ok
    assert any(f.code == "manifest.not_a_mapping" for f in report.errors)


def test_valid_manifest_file_passes(tmp_path):
    import yaml

    p = tmp_path / "plugin.yaml"
    p.write_text(yaml.safe_dump(_record_dict()), encoding="utf-8")
    assert validate_manifest_file(p).ok


def test_ok_ignores_warnings_entirely():
    """There is no --strict. A warning must never flip ok to False."""
    r = ValidationReport()
    r.add(WARNING, "w", "just a warning")
    assert r.ok
    r.add(ERROR, "e", "now it is not")
    assert not r.ok


def test_merge_preserves_all_findings():
    a, b = ValidationReport(), ValidationReport()
    a.add(ERROR, "a", "x")
    b.add(WARNING, "b", "y")
    m = merge(a, b)
    assert len(m.findings) == 2 and not m.ok
