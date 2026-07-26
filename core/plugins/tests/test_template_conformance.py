"""Every shipped template must still satisfy the live protocol (ADR-0246).

Nine templates ship in ``core/plugins/templates/`` and, before this file, nothing
in the suite imported a single one. They are the first thing an author copies, so
a template that drifted from the protocol would hand every new plugin the same
defect — and the failure would surface on the author's machine at boot, where they
cannot tell whether they broke it or inherited it.

The protocol is not frozen: ``PluginRecord`` gained ``layer`` and ``replaces`` in
ADR-0243, and ``PluginContext`` gained ``stt_registry`` / ``data_connector_registry``
after ADR-0033 shipped types that had nowhere to register. Each of those changes
could have invalidated a template silently. This file makes that failure loud, in
CorvinOS's own test run, with the template named.

Note on importing: templates are standalone files, not package modules, and two of
them define dataclasses. ``importlib`` alone is not enough — a dataclass resolves
its annotations through ``sys.modules[cls.__module__]``, so a module that is
executed but never registered raises ``'NoneType' object has no attribute
'__dict__'``. That looked like two broken templates until the import was done
properly; the helper below registers the module first.
"""
from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path

import pytest
from corvin_plugins.protocol import CorvinPlugin, HealthStatus, PluginContext
from corvin_plugins.surface_map import SURFACES

_TEMPLATES = Path(__file__).resolve().parents[3] / "core" / "plugins" / "templates"

#: (plugin_type, template filename) for every surface that ships one.
_WITH_TEMPLATE = [
    (s.plugin_type, s.template) for s in SURFACES if s.template is not None
]


def _load_template(filename: str):
    """Import a template file as a module, registered in sys.modules.

    Registration is required, not tidiness — see the module docstring.
    """
    path = _TEMPLATES / filename
    mod_name = f"_corvin_template_{path.stem}"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    assert spec and spec.loader, f"cannot build a spec for {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(mod_name, None)
        raise
    return module


def _plugin_classes(module) -> list[type]:
    """Classes in the template that look like a plugin (declare plugin_type)."""
    return [
        obj
        for _, obj in inspect.getmembers(module, inspect.isclass)
        if obj.__module__ == module.__name__ and getattr(obj, "plugin_type", None)
    ]


@pytest.mark.parametrize("plugin_type,filename", _WITH_TEMPLATE)
def test_template_imports(plugin_type, filename):
    """A template that cannot be imported cannot be copied into a working plugin."""
    assert _load_template(filename) is not None


@pytest.mark.parametrize("plugin_type,filename", _WITH_TEMPLATE)
def test_template_defines_a_plugin_class(plugin_type, filename):
    module = _load_template(filename)
    classes = _plugin_classes(module)
    assert classes, (
        f"{filename} defines no class with a plugin_type attribute — an author "
        f"copying it has nothing to rename"
    )


@pytest.mark.parametrize("plugin_type,filename", _WITH_TEMPLATE)
def test_template_declares_the_type_it_is_filed_under(plugin_type, filename):
    """A router template declaring plugin_type='recall_backend' would register
    with the wrong registry and be silently useless."""
    module = _load_template(filename)
    declared = {c.plugin_type for c in _plugin_classes(module)}
    assert plugin_type in declared, (
        f"{filename} is the template for {plugin_type!r} but its classes declare "
        f"{sorted(declared)}"
    )


@pytest.mark.parametrize("plugin_type,filename", _WITH_TEMPLATE)
def test_template_satisfies_the_corvin_plugin_protocol(plugin_type, filename):
    """The structural check the registry itself performs."""
    module = _load_template(filename)
    for cls in _plugin_classes(module):
        if cls.plugin_type != plugin_type:
            continue
        assert isinstance(cls, type)
        for method in ("on_load", "on_unload", "health_check"):
            assert callable(getattr(cls, method, None)), (
                f"{filename}:{cls.__name__} does not implement {method}()"
            )
        for attr in ("plugin_id", "version", "display_name"):
            assert getattr(cls, attr, None), (
                f"{filename}:{cls.__name__} does not set {attr}"
            )


@pytest.mark.parametrize("plugin_type,filename", _WITH_TEMPLATE)
def test_template_instance_is_a_runtime_checkable_corvin_plugin(plugin_type, filename):
    """isinstance against the runtime_checkable Protocol — what discovery uses."""
    module = _load_template(filename)
    for cls in _plugin_classes(module):
        if cls.plugin_type != plugin_type:
            continue
        try:
            instance = cls()
        except TypeError:
            pytest.skip(f"{cls.__name__} needs constructor args")
        assert isinstance(instance, CorvinPlugin), (
            f"{filename}:{cls.__name__} does not satisfy CorvinPlugin"
        )


@pytest.mark.parametrize("plugin_type,filename", _WITH_TEMPLATE)
def test_template_health_check_returns_a_health_status(plugin_type, filename):
    """health_check() is polled by the collector; a wrong return type breaks it."""
    module = _load_template(filename)
    for cls in _plugin_classes(module):
        if cls.plugin_type != plugin_type:
            continue
        try:
            instance = cls()
        except TypeError:
            pytest.skip(f"{cls.__name__} needs constructor args")
        try:
            status = instance.health_check()
        except Exception as exc:
            pytest.fail(f"{filename}: health_check() raised {type(exc).__name__}: {exc}")
        assert isinstance(status, HealthStatus), (
            f"{filename}: health_check() returned {type(status).__name__}, "
            f"expected HealthStatus"
        )


@pytest.mark.parametrize("plugin_type,filename", _WITH_TEMPLATE)
def test_template_on_load_tolerates_a_context_with_no_registries(plugin_type, filename):
    """The situation every template actually meets today.

    Three ctx handles are never populated by the gateway (ADR-0245), so on_load()
    routinely receives None where it expects a registry. A template that raises
    there would abort registration for the whole plugin — the registry rolls back
    the slot and re-raises. Templates must degrade, and this asserts they do.
    """
    module = _load_template(filename)
    ctx = PluginContext(
        plugin_id="conformance",
        tenant_id="_test",
        corvin_home=Path("/nonexistent"),
        config={},
        audit_emit=lambda _e, _d: None,
    )
    for cls in _plugin_classes(module):
        if cls.plugin_type != plugin_type:
            continue
        try:
            instance = cls()
        except TypeError:
            pytest.skip(f"{cls.__name__} needs constructor args")
        try:
            instance.on_load(ctx)
        except Exception as exc:
            pytest.fail(
                f"{filename}: on_load() raised {type(exc).__name__} on a context "
                f"with unpopulated registries: {exc}"
            )


def test_every_template_on_disk_is_covered_here():
    """A template added without a map row would otherwise never be tested."""
    on_disk = {p.name for p in _TEMPLATES.glob("*_plugin.py")}
    covered = {f for _, f in _WITH_TEMPLATE}
    assert on_disk == covered, (
        f"untested templates: {sorted(on_disk - covered)}; "
        f"mapped but absent: {sorted(covered - on_disk)}"
    )
