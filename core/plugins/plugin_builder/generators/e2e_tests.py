"""Generate edge-case + wiring tests for a freshly-scaffolded plugin (ADR-0262).

Three template shapes, matched to how ``scaffold.py``'s own templates are
actually built — not one generic guess:

* **Class-based** (Hook / Provider / Integration / Custom, and any
  ``corvin plugin new`` provider template) — all target the
  ``corvin_plugins.CorvinPlugin`` protocol: a class with ``__init__``,
  ``on_load``/``on_unload``, ``health_check``. Edge-case tests instantiate it
  and call the lifecycle methods that exist; a WIRING test additionally
  proves the plugin can become the active instance via the REAL registry
  module named by the live Extension-Surface Map (ADR-0245) — not a mock.
* **MCP-Server** — no class at all (``mcp_server_plugin.py``'s template is
  module-level JSON-RPC dispatch functions); tests drive ``_dispatch``
  through ``capsys`` instead.
* **Skill** — a Markdown file, no executable code; the only honest test is
  that the doc exists and is non-empty.

**The wiring test never fakes a pass.** :func:`corvin_plugins.surface_map.surface_for`
is looked up LIVE at generation time — never a hardcoded list of "known dead"
types (that list has already gone stale once in this project's own history;
see PLUGIN_SYSTEM_ACTIVATION_PLAN.md). A ``plugin_type`` the map marks
unconsumed gets an honest ``pytest.mark.skip`` citing the live
``dead_reason``, not a test that "passes" against nothing.
"""
from __future__ import annotations

import ast
from pathlib import Path

from ..models import Classification, PluginKind


def _first_class_name(source: str) -> str | None:
    """The name of the first top-level class in ``source``, via AST — robust
    regardless of which template (Builder-owned or ``corvin plugin new``)
    produced the file, unlike guessing a fixed class name."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            return node.name
    return None


def _module_var_name(plugin_id: str) -> str:
    safe = plugin_id.replace(".", "_").replace("-", "_")
    return f"{safe}_scaffold"


def _wiring_block(classification: Classification, plugin_id: str) -> str:
    if classification.kind != PluginKind.PROVIDER or not classification.plugin_type:
        return (
            "# No registry wiring applies to this plugin kind "
            f"({classification.kind.value}) — only PROVIDER-kind plugins "
            "register with a corvin_plugins registry."
        )
    try:
        from corvin_plugins.surface_map import surface_for
    except ImportError:
        return (
            "# corvin_plugins was not importable when this test was "
            f"generated — no live wiring check for {classification.plugin_type!r} "
            "was possible. Re-run the Plugin-Builder in a full CorvinOS "
            "install to get a real wiring test."
        )
    surface = surface_for(classification.plugin_type)
    if surface.provider_module is None:
        reason = surface.dead_reason or (
            "this plugin_type has no corvin_plugins/providers/ module — see "
            "corvin_plugins.surface_map for where it registers instead."
        )
        return (
            f'@pytest.mark.skip(reason={reason!r})\n'
            'def test_registers_as_active_via_real_registry():\n'
            "    pass  # see the skip reason: looked up LIVE from "
            "corvin_plugins.surface_map at generation time, not hardcoded\n"
        )
    if not surface.consumed:
        reason = surface.dead_reason or "nothing currently invokes this plugin_type"
        return (
            f'@pytest.mark.skip(reason={reason!r})\n'
            'def test_registers_as_active_via_real_registry():\n'
            "    pass  # see the skip reason: looked up LIVE from "
            "corvin_plugins.surface_map at generation time, not hardcoded\n"
        )
    return (
        "def test_registers_as_active_via_real_registry():\n"
        f'    """Proves the plugin can become the active {classification.plugin_type} '
        "via the REAL registry module "
        f"(corvin_plugins.providers.{surface.provider_module}). This does NOT "
        f"prove {surface.consumed_by} actually calls get_active() at runtime "
        "(that needs a live CorvinOS boot) — it proves the plugin's own "
        'registration call-site is real and functional, which is the '
        'wiring the Plugin-Builder controls."""\n'
        f"    from corvin_plugins.providers.{surface.provider_module} import "
        "clear, get_active, set_active\n"
        "\n"
        "    module = _load_module()\n"
        f'    cls = getattr(module, "{_first_class_name_placeholder()}")\n'
        "    instance = cls()\n"
        "    set_active(instance)\n"
        "    try:\n"
        "        assert get_active() is instance\n"
        "    finally:\n"
        "        clear()\n"
    )


def _first_class_name_placeholder() -> str:
    # Replaced by the caller after formatting — kept as a named function so
    # the f-string above stays readable instead of a bare "%s".
    return "__CLASS_NAME__"


def _class_based_test_source(
    classification: Classification, plugin_id: str, scaffold_filename: str, class_name: str
) -> str:
    wiring = _wiring_block(classification, plugin_id).replace(
        "__CLASS_NAME__", class_name
    )
    module_var = _module_var_name(plugin_id)
    return f'''"""Generated by the Plugin-Builder (ADR-0262) for {plugin_id}.

Edge-case tests always run; the wiring test at the bottom is generated from
a LIVE Extension-Surface-Map lookup (ADR-0245) at generation time — see its
docstring/skip-reason for what that proved (or didn't) for this plugin_type.

Generated once — this file is not regenerated on a later Plugin-Builder run
for the same plugin_id; edit it freely.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCAFFOLD_PATH = Path(__file__).resolve().parent.parent / "{scaffold_filename}"


def _load_module():
    spec = importlib.util.spec_from_file_location("{module_var}", _SCAFFOLD_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_scaffold_imports_without_error():
    _load_module()


def test_plugin_class_instantiates():
    module = _load_module()
    cls = getattr(module, "{class_name}")
    instance = cls()
    assert instance.plugin_id == "{plugin_id}"


def test_health_check_reports_ok():
    module = _load_module()
    cls = getattr(module, "{class_name}")
    instance = cls()
    if not hasattr(instance, "health_check"):
        pytest.skip("this scaffold does not implement health_check() yet")
    status = instance.health_check()
    assert status.ok is True


def test_on_unload_does_not_raise():
    module = _load_module()
    cls = getattr(module, "{class_name}")
    instance = cls()
    if not hasattr(instance, "on_unload"):
        pytest.skip("this scaffold does not implement on_unload() yet")
    instance.on_unload()  # must not raise


# ── Wiring ────────────────────────────────────────────────────────────────
{wiring}'''


def _mcp_server_test_source(plugin_id: str, scaffold_filename: str) -> str:
    module_var = _module_var_name(plugin_id)
    return f'''"""Generated by the Plugin-Builder (ADR-0262) for {plugin_id}.

MCP-Server plugins have no CorvinPlugin class (Tier C, ADR-0156 — they run
out of process over stdio JSON-RPC) — these tests drive the same
``_dispatch`` entry point the real subprocess uses, via stdout capture,
instead of importing a class that does not exist here.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_SCAFFOLD_PATH = Path(__file__).resolve().parent.parent / "{scaffold_filename}"


def _load_module():
    spec = importlib.util.spec_from_file_location("{module_var}", _SCAFFOLD_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_scaffold_imports_without_error():
    _load_module()


def test_tools_list_reports_the_example_tool():
    module = _load_module()
    result = module._handle_tools_list({{}})
    names = [t["name"] for t in result["tools"]]
    assert names, "TOOLS is empty — no tool has been defined yet"


def test_dispatch_unknown_method_returns_json_rpc_error(capsys):
    module = _load_module()
    module._dispatch({{"jsonrpc": "2.0", "id": 1, "method": "not_a_real_method", "params": {{}}}})
    out = capsys.readouterr().out.strip()
    response = json.loads(out)
    assert response["error"]["code"] == module.METHOD_NOT_FOUND
'''


def _skill_test_source(plugin_id: str, scaffold_filename: str) -> str:
    return f'''"""Generated by the Plugin-Builder (ADR-0262) for {plugin_id}.

A Skill is a Markdown file (Tier A, prompt-only) — there is no executable
code to import or wire. The only honest test is that the doc exists and
says something.
"""
from __future__ import annotations

from pathlib import Path

_SCAFFOLD_PATH = Path(__file__).resolve().parent.parent / "{scaffold_filename}"


def test_skill_doc_exists_and_is_not_empty():
    assert _SCAFFOLD_PATH.is_file()
    assert len(_SCAFFOLD_PATH.read_text(encoding="utf-8").strip()) > 0
'''


def generate_e2e_tests(
    classification: Classification, plugin_id: str, scaffold_path: Path, dest: Path
) -> Path | None:
    """Write a generated pytest file next to the scaffold, under ``dest/tests/``.

    Returns the written path, or ``None`` when ``scaffold_path`` doesn't
    exist (defensive — should not happen in the normal flow, but a
    generator must never crash the turn that's about to report success).
    """
    scaffold_path = Path(scaffold_path)
    dest = Path(dest)
    if not scaffold_path.is_file():
        return None

    tests_dir = dest / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    test_filename = f"test_{plugin_id.replace('.', '_').replace('-', '_')}_e2e.py"
    out_path = tests_dir / test_filename

    if scaffold_path.suffix == ".md":
        source = _skill_test_source(plugin_id, scaffold_path.name)
    elif classification.kind == PluginKind.MCP_SERVER:
        source = _mcp_server_test_source(plugin_id, scaffold_path.name)
    else:
        code = scaffold_path.read_text(encoding="utf-8")
        class_name = _first_class_name(code) or "MyProviderPlugin"
        source = _class_based_test_source(
            classification, plugin_id, scaffold_path.name, class_name
        )

    out_path.write_text(source, encoding="utf-8")
    return out_path


__all__ = ["generate_e2e_tests"]
