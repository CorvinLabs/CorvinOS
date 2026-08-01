"""Doc-generator + slugify tests (ADR-0253 Phase 4)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from plugin_builder.classifier import classify
from plugin_builder.generators import (
    generate_adr_doc,
    generate_architecture_doc,
    generate_build_plan_doc,
    generate_idea_doc,
)
from plugin_builder.generators.scaffold import slugify_plugin_id
from plugin_builder.models import Constraints, DependencySpec, PluginIdea, ProblemStatement

_REPO = Path(__file__).resolve().parents[4]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


@pytest.fixture
def idea() -> PluginIdea:
    return PluginIdea(
        plugin_name="Postgres Connector",
        problem=ProblemStatement(
            "Query Postgres from a turn.", "Data analysts", "none", "MVP first",
        ),
        dependencies=DependencySpec(
            external_libraries=("psycopg",), requires_auth=True,
            requires_network_egress=True, egress_hosts=("db.example.com",),
        ),
        constraints=Constraints(mvp_only=True, scope_notes="read-only for MVP"),
    )


@pytest.fixture
def classification(idea):
    return classify(idea)


def test_idea_doc_contains_problem_and_classification(idea, classification):
    doc = generate_idea_doc(idea, classification)
    assert idea.problem.problem in doc
    assert classification.kind.value in doc
    assert "data_connector" in doc
    assert "starting point" in doc.lower()


def test_idea_doc_surfaces_risk_flags(idea, classification):
    doc = generate_idea_doc(idea, classification)
    assert "Risks surfaced early" in doc
    for flag in classification.risk_flags:
        assert flag in doc


def test_architecture_doc_mentions_egress_and_tier(idea, classification):
    doc = generate_architecture_doc(idea, classification)
    assert "db.example.com" in doc
    assert "Tier B" in doc
    assert "Testing Strategy" in doc


def test_architecture_doc_no_egress_states_none_declared():
    idea_no_egress = PluginIdea(
        plugin_name="Quiet Skill",
        problem=ProblemStatement("no code needed, prompt only", "x", "none", "mvp"),
        dependencies=DependencySpec(),
        constraints=Constraints(),
    )
    c = classify(idea_no_egress)
    doc = generate_architecture_doc(idea_no_egress, c)
    assert "no declared network egress" in doc.lower()


def test_adr_doc_has_status_and_alternatives(idea, classification):
    doc = generate_adr_doc(idea, classification, "community.postgres-connector")
    assert "PROPOSED" in doc
    assert "## Alternatives" in doc
    assert "community.postgres-connector" in doc


def test_adr_doc_carries_adr_0264_frontmatter_parseable_by_the_graph_tool(idea, classification):
    """ADR-0264 ("achte darauf, dass dieses ADR auch im Plugin-Builder
    verwendet wird"): every Plugin-Builder-generated ADR must carry real,
    machine-parseable ADR-0264 frontmatter -- verified here with the SAME
    parser scripts/adr_graph.py uses, not a string/substring check, so a
    regression that produces frontmatter-shaped-but-invalid YAML is caught."""
    from scripts.adr_graph import _parse_frontmatter

    doc = generate_adr_doc(idea, classification, "community.postgres-connector")
    fm = _parse_frontmatter(doc)
    assert fm is not None, "generated doc frontmatter did not parse as YAML"
    assert fm["id"] == "community.postgres-connector-ADR-0001"
    assert fm["status"] == "proposed"
    assert "ADR-0253" in fm["related"]
    assert "ADR-0156" in fm["related"]
    assert fm["paths"] == ["**"]
    # Plugin-scoped id must never collide with the Corvin-ADR repo's own
    # sequential numbering scheme (ADR-NNNN) -- it carries the plugin_id.
    assert not fm["id"].startswith("ADR-")


def test_build_plan_has_five_phases(idea, classification):
    doc = generate_build_plan_doc(idea, classification)
    for phase in ("Phase 1", "Phase 2", "Phase 3", "Phase 4", "Phase 5"):
        assert phase in doc


def test_build_plan_flags_license_slot_for_tier_b():
    idea_b = PluginIdea(
        plugin_name="Some Connector",
        problem=ProblemStatement("connect to a database", "x", "none", "mvp"),
        dependencies=DependencySpec(),
        constraints=Constraints(),
    )
    c = classify(idea_b)
    doc = generate_build_plan_doc(idea_b, c)
    assert "executable-layer count" in doc


@pytest.mark.parametrize("name,expected_prefix", [
    ("Postgres Connector", "community.postgres-connector"),
    ("  Weird!!  Name??  ", "community.weird-name"),
    ("", "community.my-plugin"),
    ("....", "community.my-plugin"),
    ("Ünïcödé Plugin", "community"),
])
def test_slugify_plugin_id_is_always_charset_safe(name, expected_prefix):
    import re
    slug = slugify_plugin_id(name)
    assert re.match(r"^[a-z0-9]+([._-][a-z0-9]+)*$", slug), slug
    assert slug.startswith(expected_prefix)


def test_slugify_plugin_id_never_exceeds_64_chars():
    slug = slugify_plugin_id("x" * 200)
    assert len(slug) <= 64


def test_scaffold_display_name_cannot_break_out_of_python_string_literal(tmp_path):
    """Adversarial-review regression: a plugin_name containing a quote must
    not be able to terminate the `display_name = "..."` string literal (or
    the docstring's first line) in a written scaffold and inject code."""
    import ast

    from plugin_builder.generators import write_artifacts

    hostile_idea = PluginIdea(
        plugin_name='Evil"; import os; os.system("touch /tmp/pwned"); x = "',
        problem=ProblemStatement(
            "an extension-point hook for routing", "x", "none", "mvp",
        ),
        dependencies=DependencySpec(),
        constraints=Constraints(),
    )
    c = classify(hostile_idea)
    result = write_artifacts(hostile_idea, c, tmp_path)
    plugin_py = next(p for p in result.scaffold_files if p.suffix == ".py")
    src = plugin_py.read_text(encoding="utf-8")

    # The hostile text itself is inert here — the security property isn't
    # "the substring os.system never appears" (it's a valid display name
    # containing that text, harmlessly quoted), it's "it can never become a
    # SEPARATE executable statement", checked structurally below via ast.
    tree = ast.parse(src)  # raises SyntaxError if the injection broke parsing
    bare_imports = [n for n in tree.body if isinstance(n, ast.Import)]
    assert not bare_imports, "no bare `import ...` (e.g. `import os`) should have been injected"
    from_modules = {n.module for n in tree.body if isinstance(n, ast.ImportFrom)}
    assert from_modules <= {
        "__future__", "typing", "corvin_plugins.protocol", "corvin_plugins.extension_points",
    }, "no extra top-level import should have been injected"
    class_node = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef))
    display_assigns = [
        n for n in class_node.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "display_name" for t in n.targets)
    ]
    assert len(display_assigns) == 1, "exactly one display_name assignment, not extra injected statements"
    assert isinstance(display_assigns[0].value, ast.Constant)
    assert isinstance(display_assigns[0].value.value, str)
