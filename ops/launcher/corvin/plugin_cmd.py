"""`corvin plugin` — build-time plugin tooling (ADR-0244, ADR-0245, ADR-0247).

Three commands, all offline and all read-only with respect to the running system:

    corvin plugin types [--json]   what can I build, and will anything call it?
    corvin plugin check <path>     would the registry accept this?
    corvin plugin new <type> <id>  scaffold from the shipped template

The load-bearing constraint from ADR-0244: **this emits artifacts and never loads
them.** There is one registry, one loader, one lifecycle, and none of them live
here. Deleting this module must leave every generated plugin working — a generated
plugin depends on ``corvin_plugins``, never on the builder.

``corvin plugin list`` is deliberately absent. It would have to report which
plugins are live, and that state belongs to a running gateway process; answering
from this process would show an empty registry and read as "nothing installed",
which is worse than not answering. The Console plugin health route is the honest
source for that question.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Any

# Imported lazily inside the command functions: `corvin` must stay fast for
# `corvin status` on an install where corvin_plugins is not even present.


def _err(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)


def _plugins_importable() -> bool:
    try:
        import corvin_plugins  # noqa: F401
        return True
    except ImportError:
        _err(
            "corvin_plugins is not importable in this environment — plugin "
            "tooling needs the full install, not the launcher alone"
        )
        return False


# ── corvin plugin types ──────────────────────────────────────────────────────

def cmd_types(args: argparse.Namespace) -> int:
    """Print the extension-surface map, consumption status included.

    The `consumed` column is the reason this command exists. Six of the eleven
    plugin types currently register successfully and are never invoked; an author
    who picks one of those gets a plugin that loads, reports healthy, and does
    nothing, with no error anywhere. Printing it here is the earliest possible
    moment to learn that.
    """
    if not _plugins_importable():
        return 2
    from corvin_plugins.surface_map import SURFACES

    if getattr(args, "json", False):
        print(json.dumps(
            [
                {
                    "plugin_type": s.plugin_type,
                    "ctx_handle": s.ctx_handle,
                    "provider_module": s.provider_module,
                    "template": s.template,
                    "consumed": s.consumed,
                    "consumed_by": s.consumed_by,
                    "dead_reason": s.dead_reason,
                    "invariant": s.invariant,
                }
                for s in SURFACES
            ],
            indent=2,
        ))
        return 0

    live = [s for s in SURFACES if s.consumed]
    dead = [s for s in SURFACES if not s.consumed]

    print("\nExtension surfaces that are wired up and called:\n")
    for s in live:
        tmpl = s.template or "— no template —"
        print(f"  {s.plugin_type:<22} ctx.{s.ctx_handle}")
        print(f"  {'':<22} template: {tmpl}")
        print(f"  {'':<22} called by: {s.consumed_by}")
        print(f"  {'':<22} invariant: {s.invariant}")
        print()

    print("Surfaces that load and register but are NEVER invoked:\n")
    for s in dead:
        tmpl = s.template or "— no template —"
        print(f"  {s.plugin_type:<22} ctx.{s.ctx_handle}")
        print(f"  {'':<22} template: {tmpl}")
        print(f"  {'':<22} why dead: {s.dead_reason}")
        print()

    print(
        f"  {len(live)} of {len(SURFACES)} plugin types are actually consumed.\n"
        f"  A plugin of an unconsumed type will load, register, report healthy,\n"
        f"  and never be called. That is not a bug in your plugin.\n"
    )
    return 0


# ── corvin plugin check ──────────────────────────────────────────────────────

def cmd_check(args: argparse.Namespace) -> int:
    """Validate a plugin directory or a plugin.yaml against the real invariants.

    Exit code is 0 when nothing would make the registry reject the plugin.
    Warnings never affect it — see ADR-0247 on why there is no --strict.
    """
    if not _plugins_importable():
        return 2
    from corvin_plugins.validation import merge, validate_manifest_file

    target = Path(args.path).expanduser().resolve()
    manifest = target / "plugin.yaml" if target.is_dir() else target
    if not manifest.is_file():
        _err(f"no plugin.yaml at {manifest}")
        return 2

    report = validate_manifest_file(manifest)

    # Discovery: a plugin nobody can find is the failure with no signal at all.
    pkg_root = manifest.parent
    has_ep = _declares_entry_point(pkg_root / "pyproject.toml")
    report = merge(report, _discovery_report(has_entry_point=has_ep))

    # Code-level checks. These were implemented, tested, and never called by this
    # command — the same dead-mechanism shape this tooling exists to surface.
    # They need an import, so they are skippable; see _code_report.
    if not getattr(args, "no_import", False):
        report = merge(report, _code_report(pkg_root, manifest))
    else:
        print(
            "  note   [check.no_import] --no-import given: the manifest was "
            "checked, the code was not"
        )

    for finding in report.findings:
        print(f"  {finding}")

    if report.ok:
        n = len(report.warnings)
        print(
            "\nOK — the registry would accept this plugin"
            + (f" ({n} warning{'s' if n != 1 else ''})" if n else "")
        )
        return 0
    print(f"\nFAILED — {len(report.errors)} error(s)")
    return 1


def _declares_entry_point(pyproject: Path) -> bool:
    """True when pyproject.toml declares a `corvin.plugins` entry point."""
    if not pyproject.is_file():
        return False
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return False
    return "corvin.plugins" in text


def _discovery_report(*, has_entry_point: bool) -> Any:
    from corvin_plugins.validation import validate_discovery

    # A class_path lives in the operator's tenant.corvin.yaml, not in the plugin,
    # so it cannot be verified from here. Assume it is absent — that is the
    # conservative direction: it produces a warning telling the author to declare
    # one, never a false all-clear.
    return validate_discovery(
        has_entry_point=has_entry_point,
        has_class_path=False,
        auto_discover=False,
    )


def _code_report(pkg_root: Path, manifest: Path) -> Any:
    """Import the plugin module and check the class against the live protocol.

    **This executes the plugin's module-level code.** That is unavoidable — the
    protocol checks and the throwaway-registry registration both need a real
    class object, and no static analysis substitutes for `registry.register()`
    accepting it. It is also the reason `--no-import` exists: reviewing someone
    else's plugin before you trust it is exactly when you do not want to import
    it. ADR-0249 is blunt about this — a manifest is a declaration, not a sandbox.

    Everything here is best-effort. A plugin whose module cannot be imported gets
    a warning, not an error: the import may fail for reasons that have nothing to
    do with plugin correctness (a missing third-party dependency, most often), and
    an error must mean "the registry WILL reject this".
    """
    import importlib.util

    from corvin_plugins.validation import (
        ValidationReport,
        merge,
        validate_class,
        validate_registration,
    )

    report = ValidationReport()
    module_path = pkg_root / "plugin.py"
    if not module_path.is_file():
        report.add(
            "warning",
            "code.no_module",
            f"no plugin.py beside {manifest.name} — only the manifest was checked",
        )
        return report

    mod_name = f"_corvin_check_{pkg_root.name}"
    try:
        spec = importlib.util.spec_from_file_location(mod_name, module_path)
        if spec is None or spec.loader is None:
            raise ImportError("no loader")
        module = importlib.util.module_from_spec(spec)
        # Registered before exec: a dataclass resolves its annotations through
        # sys.modules[cls.__module__], so an unregistered module raises a
        # confusing AttributeError instead of loading.
        sys.modules[mod_name] = module
        try:
            spec.loader.exec_module(module)
        finally:
            sys.modules.pop(mod_name, None)
    except Exception as exc:
        report.add(
            "warning",
            "code.import_failed",
            f"could not import plugin.py ({type(exc).__name__}: {exc}) — the "
            f"class was not checked",
        )
        return report

    declared_type = _manifest_type(manifest)
    classes = [
        obj
        for name in dir(module)
        if isinstance(obj := getattr(module, name, None), type)
        and getattr(obj, "plugin_type", None)
        and obj.__module__ == mod_name
    ]
    if not classes:
        report.add(
            "warning",
            "code.no_plugin_class",
            "plugin.py defines no class with a plugin_type attribute",
        )
        return report

    for cls in classes:
        report = merge(report, validate_class(cls, expected_type=declared_type))
        try:
            instance = cls()
        except TypeError:
            report.add(
                "warning",
                "code.needs_ctor_args",
                f"{cls.__name__} could not be instantiated with no arguments — "
                f"registration was not exercised",
            )
            continue
        report = merge(report, validate_registration(instance))
    return report


def _manifest_type(manifest: Path) -> str | None:
    try:
        import yaml

        data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        return data.get("plugin_type") if isinstance(data, dict) else None
    except Exception:
        return None


# ── corvin plugin new ────────────────────────────────────────────────────────

_ID_RE = re.compile(r"^[a-z0-9]+([._-][a-z0-9]+)*$")


def cmd_new(args: argparse.Namespace) -> int:
    """Scaffold a plugin from the shipped template for its type."""
    if not _plugins_importable():
        return 2
    from corvin_plugins.surface_map import buildable_types, surface_for

    try:
        surface = surface_for(args.plugin_type)
    except KeyError as exc:
        _err(str(exc))
        return 2

    if surface.template is None:
        _err(
            f"no template ships for {args.plugin_type!r} "
            f"(buildable: {', '.join(buildable_types())})"
        )
        return 2

    if not _ID_RE.match(args.plugin_id):
        _err(
            f"invalid plugin id {args.plugin_id!r} — use lowercase segments "
            f"separated by . _ or - (e.g. com.example.my-router)"
        )
        return 2

    template = _template_path(surface.template)
    if template is None or not template.is_file():
        _err(f"template {surface.template!r} not found in this install")
        return 2

    dest = Path(args.output or ".").expanduser().resolve() / _dirname(args.plugin_id)
    if dest.exists():
        _err(f"{dest} already exists — refusing to overwrite")
        return 2

    pkg = _dirname(args.plugin_id)
    template_src = template.read_text(encoding="utf-8")
    cls_name = _plugin_class_name(template_src)
    dest.mkdir(parents=True)
    (dest / "plugin.py").write_text(template_src, encoding="utf-8")
    (dest / "plugin.yaml").write_text(
        _manifest_yaml(args.plugin_id, args.plugin_type), encoding="utf-8"
    )
    (dest / "pyproject.toml").write_text(
        _pyproject_toml(args.plugin_id, pkg, cls_name), encoding="utf-8"
    )
    (dest / "README.md").write_text(
        _readme(args.plugin_id, surface), encoding="utf-8"
    )

    print(f"\nScaffolded {args.plugin_type} plugin at {dest}\n")
    print("  plugin.py       the template — rename the class and fill in the TODOs")
    print("  plugin.yaml     manifest, least-privileged defaults")
    print("  pyproject.toml  packaging + the 'corvin.plugins' entry point")
    print("  README.md       how to install it, and the discovery step\n")

    if not surface.consumed:
        print(
            "  WARNING: nothing in this build calls a "
            f"{args.plugin_type}.\n"
            f"  {surface.dead_reason}\n"
            "  Your plugin will load, register, report healthy — and never run.\n"
        )
    print("  Next:  corvin plugin check " + str(dest) + "\n")
    return 0


def _plugin_class_name(src: str) -> str:
    """Name of the class in a template that is actually the plugin.

    The entry point must name the class that implements the plugin, and that is
    NOT reliably the first class in the file. Taking the first one — the previous
    implementation — produced a wrong entry point for four of the nine shipped
    templates, pointing at a private state dataclass (`_JobState`), an exception
    (`QuotaExceeded`) or a config holder (`BridgeChannelConfig`). The generated
    package would install and then fail to load, which is the exact silent-failure
    class this tooling exists to remove.

    The reliable marker is the ``plugin_type`` attribute: the registry requires it,
    so the plugin class always has it and helper classes never do. Parsed from the
    AST rather than matched with a regex, because a regex over class bodies cannot
    tell nesting from sequence.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return "MyPlugin"
    for node in tree.body:  # top-level classes only
        if not isinstance(node, ast.ClassDef):
            continue
        for stmt in node.body:
            targets = (
                stmt.targets if isinstance(stmt, ast.Assign)
                else [stmt.target] if isinstance(stmt, ast.AnnAssign)
                else []
            )
            if any(
                isinstance(t, ast.Name) and t.id == "plugin_type" for t in targets
            ):
                return node.name
    return "MyPlugin"


def _dirname(plugin_id: str) -> str:
    return plugin_id.replace(".", "_").replace("-", "_")


def _template_path(name: str) -> Path | None:
    """Locate the shipped template directory in source tree or wheel install."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "core" / "plugins" / "templates" / name
        if candidate.is_file():
            return candidate
    try:
        import corvin_plugins

        pkg = Path(corvin_plugins.__file__).resolve().parent
        for candidate in (
            pkg.parent / "templates" / name,
            pkg / "templates" / name,
        ):
            if candidate.is_file():
                return candidate
    except ImportError:
        pass
    return None


def _manifest_yaml(plugin_id: str, plugin_type: str) -> str:
    """Generate a manifest at the least-privileged values, all written out.

    Nothing is omitted and left to a default: an author changing one of these
    should see what they are changing rather than discovering that an absent key
    had a meaning. `layer` and `origin` are the two that cannot be raised here at
    all — PluginRecord refuses origin=community on a privileged layer, and a
    tenant-scope claim is downgraded and audited (ADR-0243).
    """
    return (
        f"# Generated by `corvin plugin new` (ADR-0246).\n"
        f"# Every value below is the least-privileged one the registry accepts.\n"
        f"plugin_id: {plugin_id}\n"
        f"plugin_type: {plugin_type}\n"
        f"version: 0.1.0\n"
        f'display_name: "{plugin_id}"\n'
        f"\n"
        f"# Boot layer. `installed` is the only value a third-party plugin may\n"
        f"# claim; compliance/core require shipping in the CorvinOS wheel.\n"
        f"layer: installed\n"
        f"origin: community\n"
        f"\n"
        f"# Compliance declarations. These are DECLARATIONS, not a sandbox —\n"
        f"# they gate prompts and inform review, they do not confine the code.\n"
        f"pii_risk: low\n"
        f"requires_consent: false\n"
    )


def _pyproject_toml(plugin_id: str, pkg: str, cls_name: str) -> str:
    """Packaging metadata with the entry point that makes the plugin findable.

    ADR-0246 lists this file in the scaffold and the first implementation omitted
    it, which meant every generated plugin was undiscoverable and failed its own
    `corvin plugin check`. The entry-point group is `corvin.plugins`, matching
    `loader.load_from_entry_points`.

    Note the entry point alone is not sufficient at runtime:
    `auto_discover_entry_points` defaults to false. The README says so; repeating
    it here keeps the two from drifting.
    """
    return (
        f"[project]\n"
        f'name = "{plugin_id.replace(".", "-").replace("_", "-")}"\n'
        f'version = "0.1.0"\n'
        f'description = "A CorvinOS plugin"\n'
        f'requires-python = ">=3.11"\n'
        f"\n"
        f"# Discovery: CorvinOS scans this group (loader.load_from_entry_points).\n"
        f"# Remember that spec.plugins.auto_discover_entry_points defaults to\n"
        f"# false — see README.md.\n"
        f'[project.entry-points."corvin.plugins"]\n'
        f'{pkg} = "plugin:{cls_name}"  # TODO: rename if you rename the class\n'
        f"\n"
        f"[build-system]\n"
        f'requires = ["hatchling"]\n'
        f'build-backend = "hatchling.build"\n'
    )


def _readme(plugin_id: str, surface: Any) -> str:
    dead = (
        ""
        if surface.consumed
        else (
            f"\n## ⚠ Nothing calls this plugin type\n\n"
            f"{surface.dead_reason}\n\n"
            f"This plugin will load, register and report healthy — and never be\n"
            f"invoked. Verify with `corvin plugin types` before investing work.\n"
        )
    )
    called_by = surface.consumed_by or "nothing — see the warning above"
    return (
        f"# {plugin_id}\n\n"
        f"A `{surface.plugin_type}` plugin for CorvinOS.\n"
        f"{dead}\n"
        f"## Invariant\n\n"
        f"{surface.invariant}\n\n"
        f"## Installing\n\n"
        f"Building and installing the package is **not enough**. Discovery needs\n"
        f"one of these, or the plugin is skipped at debug level with no visible\n"
        f"error:\n\n"
        f"1. Declare it in the tenant config — the reviewable option:\n\n"
        f"   ```yaml\n"
        f"   spec:\n"
        f"     plugins:\n"
        f"       installed:\n"
        f"         - id: {plugin_id}\n"
        f"           class_path: \"your_package.plugin:YourPluginClass\"\n"
        f"   ```\n\n"
        f"2. Or ship a `corvin.plugins` entry point AND enable\n"
        f"   `spec.plugins.auto_discover_entry_points` — it defaults to `false`.\n\n"
        f"## Checking\n\n"
        f"```bash\n"
        f"corvin plugin check .\n"
        f"```\n\n"
        f"## Where it is called from\n\n"
        f"{called_by}\n"
    )


# ── parser wiring ────────────────────────────────────────────────────────────

def add_parser(sub: Any) -> None:
    """Attach the `plugin` subcommand group to the top-level parser."""
    p = sub.add_parser("plugin", help="Build, inspect and validate plugins")
    ps = p.add_subparsers(dest="plugin_cmd", metavar="subcommand")

    t = ps.add_parser("types", help="List extension surfaces and whether they are called")
    t.add_argument("--json", action="store_true", help="Machine-readable output")

    c = ps.add_parser("check", help="Validate a plugin against the real registry rules")
    c.add_argument("path", metavar="PATH", help="Plugin directory or plugin.yaml")
    c.add_argument(
        "--no-import", action="store_true",
        help="Check the manifest only; do not import and execute plugin.py",
    )

    n = ps.add_parser("new", help="Scaffold a plugin from the shipped template")
    n.add_argument("plugin_type", metavar="TYPE", help="e.g. router_backend")
    n.add_argument("plugin_id", metavar="ID", help="e.g. com.example.my-router")
    n.add_argument("-o", "--output", metavar="DIR", help="Where to create it (default: .)")


def dispatch(args: argparse.Namespace) -> int:
    if args.plugin_cmd == "types":
        return cmd_types(args)
    if args.plugin_cmd == "check":
        return cmd_check(args)
    if args.plugin_cmd == "new":
        return cmd_new(args)
    _err("usage: corvin plugin {types|check|new}")
    return 2
