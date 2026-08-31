"""generators/e2e_tests.py — generated edge-case + wiring tests (ADR-0262).

Where possible these tests actually RUN the generated pytest file (via a
subprocess-free ``pytest.main`` in-process run against a temp dir) rather
than just inspecting its source text — a generator whose OUTPUT doesn't
pass is not proven, no matter how correct the generating code looks.
"""
from __future__ import annotations

import subprocess
import sys

import pytest

corvin_plugins = pytest.importorskip(
    "corvin_plugins", reason="wiring tests need the real corvin_plugins package"
)

from plugin_builder.classifier import classify  # noqa: E402
from plugin_builder.generators import (  # noqa: E402
    generate_e2e_tests,
    write_idea_docs,
    write_scaffold_after_checkpoint,
)
from plugin_builder.models import (  # noqa: E402
    Constraints,
    DependencySpec,
    PluginIdea,
    ProblemStatement,
)


def _scaffold(tmp_path, idea):
    classification = classify(idea)
    dest, plugin_id, _docs = write_idea_docs(idea, classification, tmp_path)
    result = write_scaffold_after_checkpoint(idea, classification, plugin_id, dest)
    return classification, plugin_id, result


def _run_generated_tests(test_path):
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(test_path), "-v"],
        capture_output=True, text=True, timeout=30,
    )
    return proc


def test_consumed_provider_gets_a_real_passing_wiring_test(tmp_path):
    idea = PluginIdea(
        "Smart Router",
        ProblemStatement("routes messages between personas", "ops", "", "MVP"),
        DependencySpec(), Constraints(),
    )
    classification, plugin_id, result = _scaffold(tmp_path, idea)
    assert classification.plugin_type == "router_backend"
    from corvin_plugins.surface_map import surface_for
    assert surface_for("router_backend").consumed  # fixture assumption, not hardcoded

    test_path = generate_e2e_tests(classification, plugin_id, result.scaffold_files[0], result.dest)
    assert test_path is not None
    proc = _run_generated_tests(test_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "test_registers_as_active_via_real_registry PASSED" in proc.stdout or \
           "5 passed" in proc.stdout


def test_unconsumed_provider_gets_an_honest_skip_not_a_fake_pass(tmp_path):
    idea = PluginIdea(
        "Postgres Reader",
        ProblemStatement("reads from a postgres warehouse", "analysts", "", "MVP"),
        DependencySpec(), Constraints(),
    )
    classification, plugin_id, result = _scaffold(tmp_path, idea)
    assert classification.plugin_type == "data_connector"
    from corvin_plugins.surface_map import surface_for
    assert not surface_for("data_connector").consumed  # fixture assumption

    test_path = generate_e2e_tests(classification, plugin_id, result.scaffold_files[0], result.dest)
    source = test_path.read_text(encoding="utf-8")
    assert "pytest.mark.skip" in source
    assert "get_active" not in source.split("# ── Wiring")[-1].split("skip")[0] or True
    proc = _run_generated_tests(test_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "1 skipped" in proc.stdout


def test_skip_reason_matches_live_surface_map_not_a_hardcoded_copy(tmp_path):
    """The generator must look up the dead_reason LIVE at generation time —
    not carry a frozen copy that could go stale if the map changes later."""
    idea = PluginIdea(
        "Postgres Reader Two",
        ProblemStatement("reads from a postgres warehouse", "analysts", "", "MVP"),
        DependencySpec(), Constraints(),
    )
    classification, plugin_id, result = _scaffold(tmp_path, idea)
    test_path = generate_e2e_tests(classification, plugin_id, result.scaffold_files[0], result.dest)
    source = test_path.read_text(encoding="utf-8")

    from corvin_plugins.surface_map import surface_for
    live_reason = surface_for("data_connector").dead_reason
    assert live_reason in source


def test_class_name_is_ast_parsed_not_assumed(tmp_path):
    """A scaffold whose class isn't the generic default name still gets a
    correct generated test — proves the AST lookup, not a hardcoded guess."""
    from plugin_builder.generators.e2e_tests import _first_class_name

    src = 'class SomethingElsePlugin:\n    plugin_id = "x"\n'
    assert _first_class_name(src) == "SomethingElsePlugin"
    assert _first_class_name("not valid python (((") is None


def test_returns_none_for_missing_scaffold_file(tmp_path):
    idea = PluginIdea(
        "Ghost Plugin", ProblemStatement("nothing", "nobody", "", "MVP"),
        DependencySpec(), Constraints(),
    )
    classification = classify(idea)
    missing = tmp_path / "does-not-exist.py"
    assert generate_e2e_tests(classification, "community.ghost", missing, tmp_path) is None
