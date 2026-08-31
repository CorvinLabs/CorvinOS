"""E2E test: full_pipeline fixture — build → install → simulate → export → fresh-install → simulate → compare.

Demonstrates that an AWPKG containing per-agent tools, skills, and instructions is
fully self-contained: installing it on a fresh corvin_home and running the bundled
workflow produces byte-identical output to the first run.

Workflow under test: com.corvin.sales-report-generator
  data_analyst  → report_writer  → quality_checker

The WorkflowSimulator executes each Forge tool's Python code directly (via exec in an
isolated namespace) without bwrap, so the test is fast and dependency-free. The tool
impls are deterministic (same input → same JSON output every time), so the two-run
comparison is a valid self-containment proof.
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from awpkg.builder import build_from_dict
from awpkg.installer import (
    InstalledPackage,
    install,
    is_installed,
    list_installed,
    register_components,
    remove,
)
from awpkg.manifest import AgentSpec, Manifest, parse_bytes, parse_raw
from tests.helpers import FIXTURES, make_awpkg_from_fixture

try:
    import yaml as _yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

pytestmark = pytest.mark.skipif(not _HAS_YAML, reason="PyYAML required")

FIXTURE_NAME = "full_pipeline"
FIXTURE_DIR = FIXTURES / FIXTURE_NAME

# ---------------------------------------------------------------------------
# Test CSV input (deterministic)
# ---------------------------------------------------------------------------

SAMPLE_CSV = """\
product,units,revenue,region
Widget A,120,2400.00,North
Widget B,85,1275.00,South
Widget C,200,6000.00,North
Widget D,50,750.00,East
Widget E,310,4650.00,West
"""


# ---------------------------------------------------------------------------
# WorkflowSimulator — lightweight DAG executor
# ---------------------------------------------------------------------------

class WorkflowSimulator:
    """Execute a workflow DAG by running each node's Forge tool impl via exec().

    Only the tool's Python code string is executed — no bwrap, no subprocess.
    Each tool must conform to the forge output convention:
        print(json.dumps({"ok": True, "data": {...}}))

    The simulator resolves node dependency order topologically, passes outputs
    between nodes, and returns the final node's data payload.
    """

    def __init__(self, install_dir: Path, manifest: Manifest) -> None:
        self._install_dir = install_dir
        self._manifest = manifest
        wf_paths = manifest.components.get("workflows", [])
        if not wf_paths:
            raise ValueError("no workflows declared in manifest")
        wf_file = install_dir / wf_paths[0]
        self._workflow = _yaml.safe_load(wf_file.read_text(encoding="utf-8"))

    def run(self, workflow_input: dict[str, Any]) -> dict[str, Any]:
        """Run the workflow and return the last node's output data dict."""
        graph = self._workflow["orchestration"]["graph"]
        order = _topo_sort(graph)
        node_outputs: dict[str, dict[str, Any]] = {}

        for node_id in order:
            node = next(n for n in graph if n["id"] == node_id)
            agent_id = node.get("agent", "")
            agent_spec = self._manifest.agents.get(agent_id)

            # Resolve inputs: substitute {{source.output.field}} references
            raw_inputs = node.get("inputs", {})
            resolved_inputs = _resolve_inputs(raw_inputs, node_outputs, workflow_input)

            # Find the tool assigned to this agent
            tool_code = self._load_tool_for_agent(agent_spec)

            # Execute the tool
            output = _exec_tool(tool_code, resolved_inputs)
            node_outputs[node_id] = output

        # Return the last node's output
        last_node_id = order[-1]
        final = node_outputs[last_node_id]
        # Also attach the intermediate markdown for comparison
        final["_markdown"] = node_outputs.get(order[-2], {}).get("markdown", "")
        return final

    def _load_tool_for_agent(self, agent_spec: AgentSpec | None) -> str:
        """Load the Python code string from the first tool assigned to this agent."""
        if not agent_spec or not agent_spec.tools:
            raise ValueError(f"agent has no tools declared: {agent_spec}")
        tool_arc_path = agent_spec.tools[0]
        tool_file = self._install_dir / tool_arc_path
        raw = json.loads(tool_file.read_text(encoding="utf-8"))
        return raw["code"]

    def skills_for_agent(self, agent_id: str) -> list[str]:
        """Return the SKILL.md bodies for all skills assigned to this agent."""
        spec = self._manifest.agents.get(agent_id)
        if not spec:
            return []
        bodies = []
        for skill_path in spec.skills:
            f = self._install_dir / skill_path
            if f.exists():
                bodies.append(f.read_text(encoding="utf-8"))
        return bodies

    def instructions_for_agent(self, agent_id: str) -> str:
        """Return the instruction block declared in the manifest for this agent."""
        spec = self._manifest.agents.get(agent_id)
        return spec.instructions if spec else ""


def _topo_sort(graph: list[dict]) -> list[str]:
    """Return node IDs in topological execution order."""
    deps = {n["id"]: list(n.get("depends_on", [])) for n in graph}
    order: list[str] = []
    visited: set[str] = set()

    def visit(nid: str) -> None:
        if nid in visited:
            return
        for dep in deps.get(nid, []):
            visit(dep)
        visited.add(nid)
        order.append(nid)

    for nid in deps:
        visit(nid)
    return order


def _resolve_inputs(
    raw: dict[str, Any],
    node_outputs: dict[str, dict[str, Any]],
    workflow_input: dict[str, Any],
) -> dict[str, Any]:
    """Substitute {{source.output.field}} and {{workflow.input.field}} references."""
    resolved: dict[str, Any] = {}
    for key, val in raw.items():
        if not isinstance(val, str) or "{{" not in val:
            resolved[key] = val
            continue
        # Simple single-reference substitution
        ref = val.strip("{{ }}").strip()
        parts = ref.split(".")
        if parts[0] == "workflow" and parts[1] == "input":
            resolved[key] = workflow_input.get(parts[2], "")
        elif len(parts) == 3 and parts[1] == "output":
            source_node, _, field = parts
            resolved[key] = node_outputs.get(source_node, {}).get(field, "")
        else:
            resolved[key] = val
    return resolved


def _exec_tool(code: str, inputs: dict[str, Any]) -> dict[str, Any]:
    """Execute a Forge tool's Python code string with inputs via stdin simulation.

    Returns the parsed data payload from the tool's JSON output.
    """
    stdin_data = json.dumps(inputs).encode("utf-8")
    stdout_capture = io.StringIO()

    import builtins
    original_stdin = sys.stdin
    original_stdout = sys.stdout

    sys.stdin = io.TextIOWrapper(io.BytesIO(stdin_data))  # type: ignore[assignment]
    sys.stdout = stdout_capture
    try:
        namespace: dict[str, Any] = {"__builtins__": builtins}
        exec(compile(code, "<forge_tool>", "exec"), namespace)  # noqa: S102
    finally:
        sys.stdin = original_stdin
        sys.stdout = original_stdout

    raw_output = stdout_capture.getvalue().strip()
    envelope = json.loads(raw_output)
    if not envelope.get("ok"):
        raise RuntimeError(f"tool returned error: {envelope}")
    return envelope["data"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _corvin_home(tmp_path: Path, suffix: str = "") -> Path:
    home = tmp_path / f".corvin{suffix}"
    home.mkdir(exist_ok=True)
    return home


def _build_fixture_pkg(tmp_path: Path) -> Path:
    return make_awpkg_from_fixture(FIXTURE_NAME, tmp_path)


def _install_and_register(pkg_path: Path, home: Path) -> InstalledPackage:
    installed = install(pkg_path, scope="user", corvin_home=home)
    register_components(installed, corvin_home=home)
    return installed


def _load_manifest(installed: InstalledPackage) -> Manifest:
    manifest_file = installed.install_dir / "manifest.yaml"
    return parse_bytes(manifest_file.read_bytes())


def _simulate(installed: InstalledPackage, manifest: Manifest) -> dict[str, Any]:
    sim = WorkflowSimulator(installed.install_dir, manifest)
    return sim.run({"csv_data": SAMPLE_CSV})


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------

class TestFullPipelineManifest:
    """Validate that the full_pipeline manifest parses correctly with new fields."""

    def test_manifest_parses(self):
        manifest_file = FIXTURE_DIR / "manifest.yaml"
        m = parse_bytes(manifest_file.read_bytes())
        assert m.id == "com.corvin.sales-report-generator"
        assert m.version == "1.0.0"

    def test_workflow_description_present(self):
        m = parse_bytes((FIXTURE_DIR / "manifest.yaml").read_bytes())
        assert len(m.workflow_description) > 100
        assert "Stage 1" in m.workflow_description
        assert "Stage 2" in m.workflow_description
        assert "Stage 3" in m.workflow_description

    def test_ascii_chart_present(self):
        m = parse_bytes((FIXTURE_DIR / "manifest.yaml").read_bytes())
        assert len(m.ascii_chart) > 50
        assert "data_analyst" in m.ascii_chart
        assert "report_writer" in m.ascii_chart
        assert "quality_checker" in m.ascii_chart

    def test_three_agents_declared(self):
        m = parse_bytes((FIXTURE_DIR / "manifest.yaml").read_bytes())
        assert set(m.agents.keys()) == {"data_analyst", "report_writer", "quality_checker"}

    def test_each_agent_has_tool_skill_instructions(self):
        m = parse_bytes((FIXTURE_DIR / "manifest.yaml").read_bytes())
        for agent_id, spec in m.agents.items():
            assert spec.tools, f"{agent_id} has no tools"
            assert spec.skills, f"{agent_id} has no skills"
            assert len(spec.instructions) > 50, f"{agent_id} instructions too short"

    def test_agent_tool_paths_are_subset_of_components(self):
        m = parse_bytes((FIXTURE_DIR / "manifest.yaml").read_bytes())
        declared_tools = set(m.components.get("forge_tools", []))
        for agent_id, spec in m.agents.items():
            for t in spec.tools:
                assert t in declared_tools, (
                    f"{agent_id}.tools[{t!r}] not in components.forge_tools"
                )

    def test_agent_skill_paths_are_subset_of_components(self):
        m = parse_bytes((FIXTURE_DIR / "manifest.yaml").read_bytes())
        declared_skills = set(m.components.get("skills", []))
        for agent_id, spec in m.agents.items():
            for s in spec.skills:
                assert s in declared_skills, (
                    f"{agent_id}.skills[{s!r}] not in components.skills"
                )

    def test_three_forge_tools_declared(self):
        m = parse_bytes((FIXTURE_DIR / "manifest.yaml").read_bytes())
        assert len(m.components.get("forge_tools", [])) == 3

    def test_three_skills_declared(self):
        m = parse_bytes((FIXTURE_DIR / "manifest.yaml").read_bytes())
        assert len(m.components.get("skills", [])) == 3

    def test_three_personas_declared(self):
        m = parse_bytes((FIXTURE_DIR / "manifest.yaml").read_bytes())
        assert len(m.components.get("personas", [])) == 3


class TestFullPipelineInstall:
    """Verify that install + register_components writes all expected files."""

    def test_install_extracts_all_components(self, tmp_path):
        home = _corvin_home(tmp_path)
        pkg = _build_fixture_pkg(tmp_path)
        installed = install(pkg, scope="user", corvin_home=home)
        d = installed.install_dir
        assert (d / "workflows" / "sales_report.awp.yaml").exists()
        assert (d / "tools" / "code_csv_stats.json").exists()
        assert (d / "tools" / "code_md_report.json").exists()
        assert (d / "tools" / "code_score_report.json").exists()
        assert (d / "skills" / "data_analysis" / "SKILL.md").exists()
        assert (d / "skills" / "report_writing" / "SKILL.md").exists()
        assert (d / "skills" / "quality_assessment" / "SKILL.md").exists()
        assert (d / "personas" / "data_analyst.yaml").exists()
        assert (d / "personas" / "report_writer.yaml").exists()
        assert (d / "personas" / "quality_checker.yaml").exists()

    def test_register_components_writes_forge_tools(self, tmp_path):
        home = _corvin_home(tmp_path)
        pkg = _build_fixture_pkg(tmp_path)
        installed = _install_and_register(pkg, home)
        forge_root = home / "forge" / "tools" / "user"
        assert (forge_root / "code.csv_stats" / "tool.json").exists()
        assert (forge_root / "code.md_report" / "tool.json").exists()
        assert (forge_root / "code.score_report" / "tool.json").exists()

    def test_register_components_writes_skills(self, tmp_path):
        home = _corvin_home(tmp_path)
        pkg = _build_fixture_pkg(tmp_path)
        installed = _install_and_register(pkg, home)
        sf_root = home / "skill-forge" / "skills" / "user"
        assert (sf_root / "data_analysis" / "SKILL.md").exists()
        assert (sf_root / "report_writing" / "SKILL.md").exists()
        assert (sf_root / "quality_assessment" / "SKILL.md").exists()

    def test_register_components_writes_skill_meta(self, tmp_path):
        home = _corvin_home(tmp_path)
        pkg = _build_fixture_pkg(tmp_path)
        installed = _install_and_register(pkg, home)
        sf_root = home / "skill-forge" / "skills" / "user"
        for skill_name in ("data_analysis", "report_writing", "quality_assessment"):
            meta_file = sf_root / skill_name / "meta.json"
            assert meta_file.exists(), f"meta.json missing for {skill_name}"
            meta = json.loads(meta_file.read_text())
            assert meta["scope"] == "user"
            assert "com.corvin.sales-report-generator" in meta["source"]

    def test_register_returns_correct_summary(self, tmp_path):
        home = _corvin_home(tmp_path)
        pkg = _build_fixture_pkg(tmp_path)
        installed = install(pkg, scope="user", corvin_home=home)
        summary = register_components(installed, corvin_home=home)
        assert set(summary["forge_tools"]) == {"code.csv_stats", "code.md_report", "code.score_report"}
        assert set(summary["skills"]) == {"data_analysis", "report_writing", "quality_assessment"}


class TestWorkflowSimulator:
    """Unit-test the WorkflowSimulator against known inputs/outputs."""

    def test_csv_stats_tool_execution(self, tmp_path):
        home = _corvin_home(tmp_path)
        pkg = _build_fixture_pkg(tmp_path)
        installed = _install_and_register(pkg, home)
        tool_file = installed.install_dir / "tools" / "code_csv_stats.json"
        code = json.loads(tool_file.read_text())["code"]
        result = _exec_tool(code, {"csv_data": SAMPLE_CSV})
        assert result["row_count"] == 5
        assert "units" in result["stats"]
        assert "revenue" in result["stats"]
        assert result["stats"]["units"]["count"] == 5
        assert result["stats"]["revenue"]["total"] == pytest.approx(15075.0)

    def test_md_report_tool_execution(self, tmp_path):
        home = _corvin_home(tmp_path)
        pkg = _build_fixture_pkg(tmp_path)
        installed = _install_and_register(pkg, home)
        stats = {
            "units": {"count": 5, "mean": 153.0, "min": 50.0, "max": 310.0, "total": 765.0},
            "revenue": {"count": 5, "mean": 3015.0, "min": 750.0, "max": 6000.0, "total": 15075.0},
        }
        tool_file = installed.install_dir / "tools" / "code_md_report.json"
        code = json.loads(tool_file.read_text())["code"]
        result = _exec_tool(code, {"stats": stats, "row_count": 5})
        md = result["markdown"]
        assert "# Sales Report" in md
        assert "## Column Statistics" in md
        assert "## Summary" in md
        assert "|" in md

    def test_score_report_tool_execution(self, tmp_path):
        home = _corvin_home(tmp_path)
        pkg = _build_fixture_pkg(tmp_path)
        installed = _install_and_register(pkg, home)
        md = "# Sales Report\n\n## Column Statistics\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n## Summary\n\nFive records."
        tool_file = installed.install_dir / "tools" / "code_score_report.json"
        code = json.loads(tool_file.read_text())["code"]
        result = _exec_tool(code, {"markdown": md})
        assert result["score"] >= 70
        assert result["passed"] is True

    def test_workflow_topo_order(self, tmp_path):
        home = _corvin_home(tmp_path)
        pkg = _build_fixture_pkg(tmp_path)
        installed = _install_and_register(pkg, home)
        manifest = _load_manifest(installed)
        sim = WorkflowSimulator(installed.install_dir, manifest)
        graph = sim._workflow["orchestration"]["graph"]
        order = _topo_sort(graph)
        assert order.index("analyze_data") < order.index("write_report")
        assert order.index("write_report") < order.index("check_quality")

    def test_simulator_full_run(self, tmp_path):
        home = _corvin_home(tmp_path)
        pkg = _build_fixture_pkg(tmp_path)
        installed = _install_and_register(pkg, home)
        manifest = _load_manifest(installed)
        result = _simulate(installed, manifest)
        assert "score" in result
        assert "feedback" in result
        assert "passed" in result
        assert result["score"] >= 70, f"Expected passing score, got {result['score']}"
        assert result["passed"] is True

    def test_simulator_reads_skills_from_package(self, tmp_path):
        home = _corvin_home(tmp_path)
        pkg = _build_fixture_pkg(tmp_path)
        installed = _install_and_register(pkg, home)
        manifest = _load_manifest(installed)
        sim = WorkflowSimulator(installed.install_dir, manifest)
        skills = sim.skills_for_agent("data_analyst")
        assert len(skills) == 1
        assert "Validate the shape" in skills[0]

    def test_simulator_reads_instructions_from_manifest(self, tmp_path):
        home = _corvin_home(tmp_path)
        pkg = _build_fixture_pkg(tmp_path)
        installed = _install_and_register(pkg, home)
        manifest = _load_manifest(installed)
        sim = WorkflowSimulator(installed.install_dir, manifest)
        instructions = sim.instructions_for_agent("report_writer")
        assert "Markdown" in instructions
        assert "code.md_report" in instructions


class TestSelfContainedRoundTrip:
    """THE KEY TEST: prove that the package is fully self-contained.

    Steps:
      1. Build package from fixture.
      2. Install in home_a, register, run simulation → result_a.
      3. Export re-packed .awpkg from home_a.
      4. Install exported package in home_b (fresh), register, run → result_b.
      5. Assert result_a == result_b (same output, different host state).
    """

    def test_output_identical_after_fresh_reinstall(self, tmp_path):
        # --- Run 1: install in home_a ---
        home_a = _corvin_home(tmp_path, "_a")
        pkg_a = _build_fixture_pkg(tmp_path)
        installed_a = _install_and_register(pkg_a, home_a)
        manifest_a = _load_manifest(installed_a)
        result_a = _simulate(installed_a, manifest_a)

        # --- Export from home_a ---
        from awpkg.builder import export as pkg_export
        export_dir = tmp_path / "exported"
        export_dir.mkdir()
        exported_pkg = pkg_export(
            installed_a.id,
            output_dir=export_dir,
            scope="user",
            corvin_home=home_a,
        )
        assert exported_pkg.exists(), "export() must produce a file"

        # --- Run 2: fresh install in home_b ---
        home_b = _corvin_home(tmp_path, "_b")
        installed_b = _install_and_register(exported_pkg, home_b)
        manifest_b = _load_manifest(installed_b)
        result_b = _simulate(installed_b, manifest_b)

        # --- Compare ---
        assert result_a["score"] == result_b["score"], (
            f"score mismatch: {result_a['score']} vs {result_b['score']}"
        )
        assert result_a["passed"] == result_b["passed"]
        assert result_a["feedback"] == result_b["feedback"]
        assert result_a.get("_markdown") == result_b.get("_markdown"), (
            "Intermediate markdown differs between runs — package is not self-contained"
        )

    def test_manifest_fields_preserved_after_roundtrip(self, tmp_path):
        home_a = _corvin_home(tmp_path, "_ma")
        pkg_a = _build_fixture_pkg(tmp_path)
        installed_a = install(pkg_a, scope="user", corvin_home=home_a)
        manifest_a = _load_manifest(installed_a)

        from awpkg.builder import export as pkg_export
        export_dir = tmp_path / "exp_manifest"
        export_dir.mkdir()
        exported = pkg_export(installed_a.id, output_dir=export_dir, scope="user", corvin_home=home_a)

        home_b = _corvin_home(tmp_path, "_mb")
        installed_b = install(exported, scope="user", corvin_home=home_b)
        manifest_b = _load_manifest(installed_b)

        assert manifest_a.workflow_description == manifest_b.workflow_description
        assert manifest_a.ascii_chart == manifest_b.ascii_chart
        assert set(manifest_a.agents.keys()) == set(manifest_b.agents.keys())
        for agent_id in manifest_a.agents:
            spec_a = manifest_a.agents[agent_id]
            spec_b = manifest_b.agents[agent_id]
            assert spec_a.instructions == spec_b.instructions
            assert spec_a.tools == spec_b.tools
            assert spec_a.skills == spec_b.skills

    def test_registered_tools_identical_after_roundtrip(self, tmp_path):
        home_a = _corvin_home(tmp_path, "_ta")
        pkg_a = _build_fixture_pkg(tmp_path)
        installed_a = _install_and_register(pkg_a, home_a)

        from awpkg.builder import export as pkg_export
        export_dir = tmp_path / "exp_tools"
        export_dir.mkdir()
        exported = pkg_export(installed_a.id, output_dir=export_dir, scope="user", corvin_home=home_a)

        home_b = _corvin_home(tmp_path, "_tb")
        installed_b = _install_and_register(exported, home_b)

        for tool_name in ("code.csv_stats", "code.md_report", "code.score_report"):
            file_a = home_a / "forge" / "tools" / "user" / tool_name / "tool.json"
            file_b = home_b / "forge" / "tools" / "user" / tool_name / "tool.json"
            assert file_a.exists() and file_b.exists()
            data_a = json.loads(file_a.read_text())
            data_b = json.loads(file_b.read_text())
            assert data_a["code"] == data_b["code"], (
                f"{tool_name}: tool code differs between home_a and home_b"
            )

    def test_registered_skills_identical_after_roundtrip(self, tmp_path):
        home_a = _corvin_home(tmp_path, "_sa")
        pkg_a = _build_fixture_pkg(tmp_path)
        installed_a = _install_and_register(pkg_a, home_a)

        from awpkg.builder import export as pkg_export
        export_dir = tmp_path / "exp_skills"
        export_dir.mkdir()
        exported = pkg_export(installed_a.id, output_dir=export_dir, scope="user", corvin_home=home_a)

        home_b = _corvin_home(tmp_path, "_sb")
        installed_b = _install_and_register(exported, home_b)

        for skill_name in ("data_analysis", "report_writing", "quality_assessment"):
            file_a = home_a / "skill-forge" / "skills" / "user" / skill_name / "SKILL.md"
            file_b = home_b / "skill-forge" / "skills" / "user" / skill_name / "SKILL.md"
            assert file_a.exists() and file_b.exists()
            assert file_a.read_text() == file_b.read_text(), (
                f"{skill_name}: SKILL.md content differs between home_a and home_b"
            )

    def test_remove_cleans_up_completely(self, tmp_path):
        home = _corvin_home(tmp_path)
        pkg = _build_fixture_pkg(tmp_path)
        installed = _install_and_register(pkg, home)
        assert is_installed(installed.id, scope="user", corvin_home=home)
        remove(installed.id, scope="user", corvin_home=home)
        assert not is_installed(installed.id, scope="user", corvin_home=home)
        assert not installed.install_dir.exists()

    def test_list_installed_shows_package(self, tmp_path):
        home = _corvin_home(tmp_path)
        pkg = _build_fixture_pkg(tmp_path)
        installed = _install_and_register(pkg, home)
        listed = list_installed(scope="user", corvin_home=home)
        ids = [p.id for p in listed]
        assert "com.corvin.sales-report-generator" in ids
        remove(installed.id, scope="user", corvin_home=home)
        listed_after = list_installed(scope="user", corvin_home=home)
        assert "com.corvin.sales-report-generator" not in [p.id for p in listed_after]
