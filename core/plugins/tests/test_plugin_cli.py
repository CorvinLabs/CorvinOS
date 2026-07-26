"""`corvin plugin` regression tests (ADR-0244).

Both tests here exist because the adversarial pass found the defect they pin, and
both defects share a shape: the code was correct in the repo and broken for the
person actually using it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import tomllib

_REPO = Path(__file__).resolve().parents[3]
_LAUNCHER = _REPO / "ops" / "launcher"
_TEMPLATES = _REPO / "core" / "plugins" / "templates"

if str(_LAUNCHER) not in sys.path:
    sys.path.insert(0, str(_LAUNCHER))

from corvin.plugin_cmd import _plugin_class_name  # noqa: E402


def _templates() -> list[Path]:
    return sorted(_TEMPLATES.glob("*_plugin.py"))


@pytest.mark.parametrize("template", _templates(), ids=lambda p: p.name)
def test_entry_point_names_the_class_that_declares_plugin_type(template):
    """The generated entry point must name the PLUGIN class, not the first class.

    The first implementation took the first `class` in the file. That is wrong for
    four of the nine shipped templates, where the first class is a private state
    dataclass (`_JobState`), an exception (`QuotaExceeded`) or a config holder
    (`BridgeChannelConfig`). The generated package installs and then fails to load
    — a silent failure of exactly the kind this tooling exists to remove.
    """
    src = template.read_text(encoding="utf-8")
    name = _plugin_class_name(src)

    assert name != "MyPlugin", f"{template.name}: no plugin class found"

    # The named class must exist AND be the one carrying plugin_type.
    import ast

    tree = ast.parse(src)
    found = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            found = node
            break
    assert found is not None, f"{template.name}: {name} is not a top-level class"

    declares = False
    for stmt in found.body:
        targets = (
            stmt.targets if isinstance(stmt, ast.Assign)
            else [stmt.target] if isinstance(stmt, ast.AnnAssign)
            else []
        )
        if any(isinstance(t, ast.Name) and t.id == "plugin_type" for t in targets):
            declares = True
    assert declares, (
        f"{template.name}: entry point would name {name}, which does not declare "
        f"plugin_type — it is not the plugin class"
    )


def _scaffold(tmp_path: Path, plugin_type: str = "router_backend") -> Path:
    import argparse

    from corvin.plugin_cmd import cmd_new

    args = argparse.Namespace(
        plugin_type=plugin_type, plugin_id="com.example.t", output=str(tmp_path)
    )
    assert cmd_new(args) == 0
    return tmp_path / "com_example_t"


def test_check_actually_exercises_the_code_not_just_the_manifest(tmp_path, capsys):
    """Call-site test for the validator's own code checks.

    `validate_class` and `validate_registration` were implemented and unit-tested
    while `cmd_check` called neither — a plugin missing `health_check()` passed
    the check. That is precisely the dead-mechanism failure this tooling was built
    to expose, reproduced inside the tool itself. A unit test on the validator
    could never have caught it; only asking "does the CLI call this?" does.
    """
    import argparse

    from corvin.plugin_cmd import cmd_check

    pkg = _scaffold(tmp_path)
    capsys.readouterr()

    src = (pkg / "plugin.py").read_text(encoding="utf-8")
    broken = src.replace(
        '    def health_check(self) -> HealthStatus:\n'
        '        return HealthStatus(ok=True, message="ok")',
        "",
    )
    assert broken != src, "fixture did not actually remove health_check"
    (pkg / "plugin.py").write_text(broken, encoding="utf-8")

    rc = cmd_check(argparse.Namespace(path=str(pkg), no_import=False))
    out = capsys.readouterr().out
    assert rc == 1, "a class missing health_check() must fail the check"
    assert "health_check" in out


def test_check_passes_on_a_freshly_scaffolded_plugin(tmp_path, capsys):
    """new → check must succeed, or the tool contradicts itself.

    The first implementation failed here: `new` emitted no pyproject.toml and
    `discovery.unreachable` was an error, so every scaffold failed its own check.
    """
    import argparse

    from corvin.plugin_cmd import cmd_check

    pkg = _scaffold(tmp_path)
    capsys.readouterr()
    assert cmd_check(argparse.Namespace(path=str(pkg), no_import=False)) == 0


def test_no_import_skips_the_code_checks(tmp_path, capsys):
    """--no-import must not silently report a clean bill of health.

    Reviewing an untrusted plugin is exactly when you do not want to execute it,
    so the flag exists — but a check that skipped the code and said plain "OK"
    would overstate what it verified.
    """
    import argparse

    from corvin.plugin_cmd import cmd_check

    pkg = _scaffold(tmp_path)
    (pkg / "plugin.py").write_text("raise SystemExit('should not run')\n", encoding="utf-8")
    capsys.readouterr()

    rc = cmd_check(argparse.Namespace(path=str(pkg), no_import=True))
    out = capsys.readouterr().out
    assert rc == 0
    assert "no_import" in out and "the code was not" in out


def test_templates_are_shipped_in_the_wheel():
    """Templates must reach a pip install, or `corvin plugin new` is dead there.

    `core/plugins/templates/` sits BESIDE `corvin_plugins`, so the package mapping
    in [tool.hatch.build.targets.wheel.sources] does not include it. Without an
    explicit force-include the command works in the source tree and fails on every
    real install with "template not found in this install".

    This is the same failure mode pyproject.toml already documents for the AWPKG
    bundles and the ADR-0232 tripwires: nothing wrong in the repo, nothing red in
    CI, dead in the shipped product. A repo-only test cannot catch it, so this
    asserts the packaging declaration itself.
    """
    data = tomllib.loads((_REPO / "pyproject.toml").read_text(encoding="utf-8"))
    force = (
        data.get("tool", {})
        .get("hatch", {})
        .get("build", {})
        .get("targets", {})
        .get("wheel", {})
        .get("force-include", {})
    )
    assert "core/plugins/templates" in force, (
        "core/plugins/templates is not force-included into the wheel — "
        "`corvin plugin new` will fail on every pip install"
    )
    dest = force["core/plugins/templates"]
    assert dest == "corvin_plugins/templates", (
        f"templates map to {dest!r}; plugin_cmd._template_path looks for them "
        f"under corvin_plugins/templates"
    )
