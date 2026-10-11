"""Every file the scaffolder generates stays valid - and inert - for hostile names (review SC-07/08/09)."""
from __future__ import annotations

import ast
import tomllib

import pytest

from core.plugins.plugin_builder.scaffolding.enhanced_scaffolder import bootstrap_scaffold

HOSTILE = [
    'Nice" + __import__("pathlib").Path("/tmp/PWNED_SC07").write_text("x") + "',
    'triple """ quotes \\ backslash',
    "line\nbreak separator",
    "Rocket \U0001F680 emoji",
]


@pytest.mark.parametrize("name", HOSTILE)
def test_generated_python_and_toml_parse_and_carry_the_name_as_data(tmp_path, name):
    files = bootstrap_scaffold(output_dir=tmp_path, plugin_id="my.plugin", plugin_name=name,
                               description=name, author=name)
    plugin_dir = files["plugin"].parent
    for py in plugin_dir.rglob("*.py"):
        ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
    tomllib.loads((plugin_dir / "pyproject.toml").read_text(encoding="utf-8"))
    tree = ast.parse(files["plugin"].read_text(encoding="utf-8"))
    assigns = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)
               and any(getattr(t, "attr", None) == "display_name" for t in n.targets)]
    assert assigns and isinstance(assigns[0].value, ast.Constant), "display_name must be a literal"


def test_a_plugin_id_starting_with_a_digit_yields_a_valid_class(tmp_path):
    files = bootstrap_scaffold(output_dir=tmp_path, plugin_id="1password", plugin_name="One")
    ast.parse(files["plugin"].read_text(encoding="utf-8"))


def test_an_unencodable_name_fails_loudly_instead_of_writing_a_broken_package(tmp_path):
    with pytest.raises(RuntimeError):
        bootstrap_scaffold(output_dir=tmp_path, plugin_id="my.plugin", plugin_name="lone \ud800")
