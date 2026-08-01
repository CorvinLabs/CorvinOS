"""E2E coverage for ADR-0264's mechanism: scripts/adr_graph.py.

Per the adr_gate skill's Step 5 ("write E2E tests that would FAIL if this
mechanism broke or was accidentally reverted"), this exercises the real
traversal logic against real, hermetic fixture files on a real filesystem
(a tmp decisions/ directory, never mocked) plus one true end-to-end run of
the actual CLI as a real subprocess -- not a direct function call -- so a
regression in argument parsing or the entry point itself is caught too.

Run: python3 -m pytest tests/test_adr_0264_decision_graph.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from scripts.adr_graph import adrs_for_path, load_graph, subgraph  # noqa: E402

_SCRIPT = _REPO / "scripts" / "adr_graph.py"


def _write_adr(decisions_dir: Path, filename: str, *, id_: str, title: str,
                depends_on: list[str] | None = None,
                supersedes: list[str] | None = None,
                paths: list[str] | None = None,
                status: str = "accepted") -> Path:
    fm_lines = [
        "---",
        f"id: {id_}",
        f"status: {status}",
        f"supersedes: {supersedes or []}",
        f"depends_on: {depends_on or []}",
        "related: []",
        "commits: []",
        "paths:",
    ]
    for p in (paths or []):
        fm_lines.append(f'  - "{p}"')
    fm_lines.append("---")
    fm_lines.append("")
    fm_lines.append(f"# {id_} — {title}")
    fm_lines.append("")
    fm_lines.append("## Context")
    fm_lines.append("Fixture ADR for test_adr_0264_decision_graph.py.")
    text = "\n".join(fm_lines)
    path = decisions_dir / filename
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def decisions_dir(tmp_path: Path) -> Path:
    d = tmp_path / "decisions"
    d.mkdir()
    return d


class TestFrontmatterParsingAndGraphLoad:
    def test_adr_without_frontmatter_is_excluded_not_erroring(self, decisions_dir):
        (decisions_dir / "0001-legacy.md").write_text(
            "# ADR-0001 — Legacy pre-convention ADR\n\nNo frontmatter here.\n",
            encoding="utf-8",
        )
        nodes = load_graph(decisions_dir)
        assert nodes == {}

    def test_adr_with_frontmatter_is_loaded_with_title(self, decisions_dir):
        _write_adr(decisions_dir, "0100-a.md", id_="ADR-0100", title="Root Decision",
                   paths=["core/foo/**"])
        nodes = load_graph(decisions_dir)
        assert "ADR-0100" in nodes
        assert nodes["ADR-0100"].title == "Root Decision"
        assert nodes["ADR-0100"].paths == ["core/foo/**"]

    def test_malformed_yaml_frontmatter_is_skipped_not_raised(self, decisions_dir):
        (decisions_dir / "0002-broken.md").write_text(
            "---\nid: ADR-0002\n  bad indent: [unterminated\n---\n# ADR-0002 — Broken\n",
            encoding="utf-8",
        )
        nodes = load_graph(decisions_dir)  # must not raise
        assert "ADR-0002" not in nodes


class TestSupersededByDerivation:
    def test_superseded_by_is_computed_from_the_newer_adrs_supersedes_field(self, decisions_dir):
        """The exact mechanism ADR-0264 exists to fix: the OLD ADR never has
        to be hand-edited to point forward -- the graph derives it."""
        _write_adr(decisions_dir, "0100-old.md", id_="ADR-0100", title="Old Decision")
        _write_adr(decisions_dir, "0200-new.md", id_="ADR-0200", title="New Decision",
                   supersedes=["ADR-0100"])
        nodes = load_graph(decisions_dir)
        assert nodes["ADR-0100"].superseded_by == ["ADR-0200"]
        assert nodes["ADR-0200"].superseded_by == []

    def test_a_nodes_own_file_cannot_fake_its_superseded_by(self, decisions_dir):
        """superseded_by in the file itself is IGNORED -- only computed."""
        d = decisions_dir
        (d / "0100-old.md").write_text(textwrap.dedent("""\
            ---
            id: ADR-0100
            status: accepted
            supersedes: []
            superseded_by: [ADR-9999]
            depends_on: []
            related: []
            commits: []
            paths: []
            ---
            # ADR-0100 — Old
            """), encoding="utf-8")
        nodes = load_graph(d)
        assert nodes["ADR-0100"].superseded_by == []


class TestPathMatching:
    def test_exact_file_path_matches(self, decisions_dir):
        _write_adr(decisions_dir, "0100-a.md", id_="ADR-0100", title="A",
                   paths=["scripts/adr_graph.py"])
        nodes = load_graph(decisions_dir)
        matches = adrs_for_path("scripts/adr_graph.py", nodes)
        assert [n.id for n in matches] == ["ADR-0100"]

    def test_directory_glob_matches_everything_underneath(self, decisions_dir):
        _write_adr(decisions_dir, "0100-a.md", id_="ADR-0100", title="A",
                   paths=["core/plugins/plugin_builder/**"])
        nodes = load_graph(decisions_dir)
        matches = adrs_for_path(
            "core/plugins/plugin_builder/generators/adr.py", nodes)
        assert [n.id for n in matches] == ["ADR-0100"]

    def test_unrelated_path_matches_nothing(self, decisions_dir):
        _write_adr(decisions_dir, "0100-a.md", id_="ADR-0100", title="A",
                   paths=["core/plugins/plugin_builder/**"])
        nodes = load_graph(decisions_dir)
        assert adrs_for_path("README.md", nodes) == []


class TestSubgraphTraversal:
    def test_transitive_depends_on_closure_in_topological_order(self, decisions_dir):
        """C depends on B depends on A. Querying C must return [A, B, C] --
        dependencies-first, matching ADR-0264's prescribed reading order."""
        _write_adr(decisions_dir, "0100-a.md", id_="ADR-0100", title="A")
        _write_adr(decisions_dir, "0200-b.md", id_="ADR-0200", title="B",
                   depends_on=["ADR-0100"])
        _write_adr(decisions_dir, "0300-c.md", id_="ADR-0300", title="C",
                   depends_on=["ADR-0200"])
        nodes = load_graph(decisions_dir)
        sub = subgraph(["ADR-0300"], nodes)
        assert [n.id for n in sub] == ["ADR-0100", "ADR-0200", "ADR-0300"]

    def test_diamond_dependency_each_node_appears_once(self, decisions_dir):
        _write_adr(decisions_dir, "0100-a.md", id_="ADR-0100", title="A")
        _write_adr(decisions_dir, "0200-b.md", id_="ADR-0200", title="B",
                   depends_on=["ADR-0100"])
        _write_adr(decisions_dir, "0201-c.md", id_="ADR-0201", title="C",
                   depends_on=["ADR-0100"])
        _write_adr(decisions_dir, "0300-d.md", id_="ADR-0300", title="D",
                   depends_on=["ADR-0200", "ADR-0201"])
        nodes = load_graph(decisions_dir)
        sub = subgraph(["ADR-0300"], nodes)
        ids = [n.id for n in sub]
        assert ids.count("ADR-0100") == 1
        assert ids.index("ADR-0100") < ids.index("ADR-0200")
        assert ids.index("ADR-0100") < ids.index("ADR-0201")
        assert ids.index("ADR-0200") < ids.index("ADR-0300")
        assert ids.index("ADR-0201") < ids.index("ADR-0300")

    def test_cyclic_dependency_terminates_instead_of_looping_forever(self, decisions_dir):
        """A context tool must degrade gracefully even if the graph itself
        (human-authored frontmatter) has an accidental cycle."""
        _write_adr(decisions_dir, "0100-a.md", id_="ADR-0100", title="A",
                   depends_on=["ADR-0200"])
        _write_adr(decisions_dir, "0200-b.md", id_="ADR-0200", title="B",
                   depends_on=["ADR-0100"])
        nodes = load_graph(decisions_dir)
        sub = subgraph(["ADR-0100"], nodes)  # must terminate
        assert {n.id for n in sub} == {"ADR-0100", "ADR-0200"}

    def test_dangling_depends_on_is_skipped_not_raised(self, decisions_dir):
        _write_adr(decisions_dir, "0100-a.md", id_="ADR-0100", title="A",
                   depends_on=["ADR-9999"])
        nodes = load_graph(decisions_dir)
        sub = subgraph(["ADR-0100"], nodes)  # must not raise KeyError
        assert [n.id for n in sub] == ["ADR-0100"]


class TestRealCliEndToEnd:
    """A genuine subprocess run of the actual entry point, not a direct
    function call -- catches regressions in argument parsing / __main__."""

    def test_cli_path_query_json_output_is_well_formed(self, decisions_dir):
        _write_adr(decisions_dir, "0100-a.md", id_="ADR-0100", title="Root",
                   paths=["core/plugins/plugin_builder/**"])
        result = subprocess.run(
            [sys.executable, str(_SCRIPT),
             "core/plugins/plugin_builder/generators/adr.py",
             "--decisions-dir", str(decisions_dir), "--format", "json"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["seeds"] == ["ADR-0100"]
        assert payload["nodes"][0]["id"] == "ADR-0100"

    def test_cli_adr_query_reports_superseded_status(self, decisions_dir):
        _write_adr(decisions_dir, "0100-old.md", id_="ADR-0100", title="Old")
        _write_adr(decisions_dir, "0200-new.md", id_="ADR-0200", title="New",
                   supersedes=["ADR-0100"])
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), "--adr", "0100",
             "--decisions-dir", str(decisions_dir), "--format", "json"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["nodes"][0]["superseded_by"] == ["ADR-0200"]

    def test_cli_no_match_exits_zero_not_error(self, decisions_dir):
        """Absence from the graph is the expected default (ADR-0264 'no
        retrofit'), never a failure exit code."""
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), "some/unrelated/file.py",
             "--decisions-dir", str(decisions_dir)],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, result.stderr

    def test_cli_against_the_real_repo_finds_adr_0264_itself(self):
        """Dogfooding check: ADR-0264's own frontmatter, committed in the
        real Corvin-ADR repo, must be discoverable by its own tool."""
        if not (_REPO.parent / "Corvin-ADR" / "decisions").is_dir():
            pytest.skip("sibling Corvin-ADR checkout not present")
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), "--adr", "0264"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, result.stderr
        assert "ADR-0264" in result.stdout
