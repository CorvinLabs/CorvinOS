"""End-to-end tests for AWPKG — build → inspect → install → verify → remove.

Covers all fixture workflow topologies:
  - dag_simple      : linear DAG + 1 skill
  - dag_complex     : parallel fan-out/fan-in DAG + 2 forge tools + 1 skill + 1 persona
  - delegation      : delegation_loop graph + 1 forge tool + 1 skill
  - mixed           : 2 workflows (DAG + delegation) + 3 forge tools + 2 skills + 1 persona

Security / rejection tests:
  - path_traversal          : ../etc/passwd in archive entry
  - undeclared_file         : file present but not in manifest.components
  - bad_tool_name           : forge tool doesn't start with code.
  - missing_manifest        : archive has no manifest.yaml
  - schema_violation        : manifest.yaml missing required fields
  - network_permission      : tool declares network:allow but manifest says network:false
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
sys.path.insert(0, str(Path(__file__).parents[1] / "core" / "awpkg"))

from awpkg.installer import (
    InstallError,
    NotInstalledError,
    install,
    is_installed,
    list_installed,
    remove,
)
from awpkg.inspector import InspectError, inspect
from awpkg.manifest import ManifestError, parse_bytes

from tests.helpers import (
    FIXTURES,
    MINIMAL_WORKFLOW,
    make_awpkg,
    make_awpkg_from_fixture,
    minimal_manifest,
)

try:
    import yaml as _yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

pytestmark = pytest.mark.skipif(not _HAS_YAML, reason="PyYAML required")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _corvin_home(tmp_path: Path) -> Path:
    home = tmp_path / ".corvin"
    home.mkdir()
    return home


# ---------------------------------------------------------------------------
# Manifest parsing unit tests
# ---------------------------------------------------------------------------

class TestManifestParsing:
    def test_minimal_valid(self):
        raw = minimal_manifest()
        from awpkg.manifest import parse_raw
        m = parse_raw(raw)
        assert m.id == "com.test.minimal"
        assert m.version == "1.0.0"

    def test_missing_required_field_raises(self):
        raw = minimal_manifest()
        del raw["description"]
        with pytest.raises(ManifestError):
            from awpkg.manifest import parse_raw
            parse_raw(raw)

    def test_bad_id_format_raises(self):
        raw = minimal_manifest(pkg_id="not-reverse-domain")
        with pytest.raises(ManifestError):
            from awpkg.manifest import parse_raw
            parse_raw(raw)

    def test_bad_semver_raises(self):
        raw = minimal_manifest()
        raw["version"] = "not-semver"
        with pytest.raises(ManifestError):
            from awpkg.manifest import parse_raw
            parse_raw(raw)

    def test_empty_components_raises(self):
        raw = minimal_manifest()
        raw["components"] = {"workflows": []}
        with pytest.raises(ManifestError):
            from awpkg.manifest import parse_raw
            parse_raw(raw)

    def test_full_manifest_roundtrip(self):
        raw = {
            "awpkg": "1.0",
            "id": "com.example.full-test",
            "name": "Full Test",
            "version": "2.3.4",
            "description": "Full manifest with all optional fields.",
            "author": "Tester",
            "license": "MIT",
            "homepage": "https://example.com",
            "min_corvin_version": "0.9.0",
            "max_corvin_version": None,
            "components": {
                "workflows": ["workflows/w.awp.yaml"],
                "forge_tools": ["tools/code_foo.json"],
                "skills": ["skills/foo/SKILL.md"],
                "personas": ["personas/foo.yaml"],
            },
            "permissions": {"network": False, "compute": True, "secrets": ["MY_KEY"]},
            "dependencies": [{"id": "com.other.dep", "version": ">=1.0.0"}],
        }
        from awpkg.manifest import parse_raw
        m = parse_raw(raw)
        assert m.compute_allowed is True
        assert m.required_secrets == ["MY_KEY"]
        assert len(m.dependencies) == 1


# ---------------------------------------------------------------------------
# Inspector unit tests
# ---------------------------------------------------------------------------

class TestInspector:
    def test_inspect_minimal(self, tmp_path):
        pkg = make_awpkg(
            minimal_manifest(),
            {"workflows/test.awp.yaml": MINIMAL_WORKFLOW},
            tmp_path,
        )
        result = inspect(pkg)
        assert result.manifest.id == "com.test.minimal"
        assert not result.warnings

    def test_inspect_no_manifest_raises(self, tmp_path):
        bad = tmp_path / "bad.awpkg"
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr("README.md", "no manifest")
        with pytest.raises(InspectError, match="manifest"):
            inspect(bad)

    def test_inspect_not_zip_raises(self, tmp_path):
        bad = tmp_path / "bad.awpkg"
        bad.write_bytes(b"not a zip")
        with pytest.raises(InspectError):
            inspect(bad)

    def test_inspect_summary_contains_id(self, tmp_path):
        pkg = make_awpkg(
            minimal_manifest("com.test.summary"),
            {"workflows/test.awp.yaml": MINIMAL_WORKFLOW},
            tmp_path,
        )
        result = inspect(pkg)
        assert "com.test.summary" in result.summary()

    def test_inspect_undeclared_warns(self, tmp_path):
        pkg = make_awpkg(
            minimal_manifest(),
            {
                "workflows/test.awp.yaml": MINIMAL_WORKFLOW,
                "data/secret.json": b'{"oops": true}',
            },
            tmp_path,
        )
        result = inspect(pkg)
        assert any("undeclared" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# Security — pre-extraction checks
# ---------------------------------------------------------------------------

class TestSecurity:
    def test_path_traversal_rejected(self, tmp_path):
        m = minimal_manifest()
        m["components"] = {"workflows": ["../../../etc/passwd"]}
        pkg = make_awpkg(m, {"../../../etc/passwd": b"root:x:0:0"}, tmp_path)
        with pytest.raises(InstallError, match="path-traversal"):
            install(pkg, corvin_home=_corvin_home(tmp_path))

    def test_absolute_path_rejected(self, tmp_path):
        m = minimal_manifest()
        m["components"] = {"workflows": ["/etc/passwd"]}
        # Build manually to bypass manifest check
        out = tmp_path / "abs.awpkg"
        manifest_bytes = _yaml.dump(m).encode()
        with zipfile.ZipFile(out, "w") as zf:
            zf.writestr("manifest.yaml", manifest_bytes)
            info = zipfile.ZipInfo("/etc/passwd")
            zf.writestr(info, b"root")
        with pytest.raises(InstallError):
            install(out, corvin_home=_corvin_home(tmp_path))

    def test_undeclared_file_rejected(self, tmp_path):
        pkg = make_awpkg(
            minimal_manifest(),
            {
                "workflows/test.awp.yaml": MINIMAL_WORKFLOW,
                "data/extra.json": b'{"bonus": true}',
            },
            tmp_path,
        )
        with pytest.raises(InstallError, match="undeclared"):
            install(pkg, corvin_home=_corvin_home(tmp_path))

    def test_bad_tool_name_rejected(self, tmp_path):
        m = minimal_manifest()
        m["components"] = {
            "workflows": ["workflows/test.awp.yaml"],
            "forge_tools": ["tools/bad_name_no_code_prefix.json"],
        }
        tool_json = json.dumps({"name": "bad_name_no_code_prefix"}).encode()
        pkg = make_awpkg(
            m,
            {
                "workflows/test.awp.yaml": MINIMAL_WORKFLOW,
                "tools/bad_name_no_code_prefix.json": tool_json,
            },
            tmp_path,
        )
        with pytest.raises(InstallError, match="code\\."):
            install(pkg, corvin_home=_corvin_home(tmp_path))

    def test_invalid_workflow_rejected(self, tmp_path):
        # Regression: the workflow validator used to be a silent no-op (it
        # called a non-existent WorkflowDoc.from_dict and swallowed every
        # exception), so ANY malformed/invalid workflow installed cleanly.
        # An unknown node type must now abort the install.
        bad_wf = b"""\
awp: "1.0.0"
workflow:
  name: bad_workflow
  description: references an unknown node type.
orchestration:
  engine: dag
  graph:
    - id: step_one
      type: this_node_type_does_not_exist
      depends_on: []
"""
        m = minimal_manifest()
        m["components"] = {"workflows": ["workflows/test.awp.yaml"]}
        pkg = make_awpkg(m, {"workflows/test.awp.yaml": bad_wf}, tmp_path)
        with pytest.raises(InstallError):
            install(pkg, corvin_home=_corvin_home(tmp_path))

    def test_valid_workflow_still_installs(self, tmp_path):
        # Guard the other direction: a VALID workflow must not be rejected by
        # the newly-live validator.
        m = minimal_manifest()
        m["components"] = {"workflows": ["workflows/test.awp.yaml"]}
        pkg = make_awpkg(m, {"workflows/test.awp.yaml": MINIMAL_WORKFLOW}, tmp_path)
        install(pkg, corvin_home=_corvin_home(tmp_path))  # must not raise

    def test_missing_manifest_rejected(self, tmp_path):
        bad = tmp_path / "no_manifest.awpkg"
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr("workflows/test.awp.yaml", MINIMAL_WORKFLOW)
        with pytest.raises(InstallError, match="manifest"):
            install(bad, corvin_home=_corvin_home(tmp_path))

    def test_not_zip_rejected(self, tmp_path):
        bad = tmp_path / "bad.awpkg"
        bad.write_bytes(b"garbage data not a zip")
        with pytest.raises(InstallError):
            install(bad, corvin_home=_corvin_home(tmp_path))

    def test_network_permission_mismatch_rejected(self, tmp_path):
        m = minimal_manifest()
        m["components"] = {
            "workflows": ["workflows/test.awp.yaml"],
            "forge_tools": ["tools/code.net_tool.json"],
        }
        m["permissions"] = {"network": False}
        tool = json.dumps({"name": "code.net_tool", "meta": {"network": "allow"}}).encode()
        pkg = make_awpkg(
            m,
            {
                "workflows/test.awp.yaml": MINIMAL_WORKFLOW,
                "tools/code.net_tool.json": tool,
            },
            tmp_path,
        )
        with pytest.raises(InstallError, match="network"):
            install(pkg, corvin_home=_corvin_home(tmp_path))

    def test_schema_violation_rejected(self, tmp_path):
        m = {"awpkg": "1.0", "id": "bad", "components": {}}
        manifest_bytes = _yaml.dump(m).encode()
        out = tmp_path / "schema_bad.awpkg"
        with zipfile.ZipFile(out, "w") as zf:
            zf.writestr("manifest.yaml", manifest_bytes)
        with pytest.raises(InstallError):
            install(out, corvin_home=_corvin_home(tmp_path))

    def test_declared_missing_from_archive_rejected(self, tmp_path):
        m = minimal_manifest()
        # manifest declares workflow but archive is empty
        pkg = make_awpkg(m, {}, tmp_path)
        with pytest.raises(InstallError, match="missing from archive"):
            install(pkg, corvin_home=_corvin_home(tmp_path))


# ---------------------------------------------------------------------------
# Install / remove / list lifecycle
# ---------------------------------------------------------------------------

class TestLifecycle:
    def test_install_remove_roundtrip(self, tmp_path):
        home = _corvin_home(tmp_path)
        pkg = make_awpkg(
            minimal_manifest("com.test.roundtrip"),
            {"workflows/test.awp.yaml": MINIMAL_WORKFLOW},
            tmp_path,
        )
        result = install(pkg, scope="user", corvin_home=home)
        assert result.id == "com.test.roundtrip"
        assert is_installed("com.test.roundtrip", scope="user", corvin_home=home)

        listed = list_installed(scope="user", corvin_home=home)
        assert any(p.id == "com.test.roundtrip" for p in listed)

        remove("com.test.roundtrip", scope="user", corvin_home=home)
        assert not is_installed("com.test.roundtrip", scope="user", corvin_home=home)

    def test_remove_not_installed_raises(self, tmp_path):
        home = _corvin_home(tmp_path)
        with pytest.raises(NotInstalledError):
            remove("com.nonexistent.pkg", scope="user", corvin_home=home)

    def test_install_creates_meta_file(self, tmp_path):
        home = _corvin_home(tmp_path)
        pkg = make_awpkg(
            minimal_manifest("com.test.meta"),
            {"workflows/test.awp.yaml": MINIMAL_WORKFLOW},
            tmp_path,
        )
        result = install(pkg, scope="user", corvin_home=home)
        meta = result.install_dir / "_awpkg_meta.json"
        assert meta.exists()
        data = json.loads(meta.read_text())
        assert data["id"] == "com.test.meta"

    def test_install_extracts_all_components(self, tmp_path):
        home = _corvin_home(tmp_path)
        m = minimal_manifest("com.test.components")
        m["components"] = {
            "workflows": ["workflows/test.awp.yaml"],
            "skills": ["skills/my_skill/SKILL.md"],
        }
        pkg = make_awpkg(
            m,
            {
                "workflows/test.awp.yaml": MINIMAL_WORKFLOW,
                "skills/my_skill/SKILL.md": b"# my_skill\nDoes something.\n",
            },
            tmp_path,
        )
        result = install(pkg, scope="user", corvin_home=home)
        assert (result.install_dir / "workflows" / "test.awp.yaml").exists()
        assert (result.install_dir / "skills" / "my_skill" / "SKILL.md").exists()

    def test_list_empty_when_nothing_installed(self, tmp_path):
        home = _corvin_home(tmp_path)
        assert list_installed(scope="user", corvin_home=home) == []

    def test_project_scope_installs_in_project_dir(self, tmp_path):
        home = _corvin_home(tmp_path)
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()
        (project_dir / "plugins").mkdir()  # makes _resolve_project_root() stop here
        (project_dir / ".corvin").mkdir()

        old_cwd = Path.cwd()
        os.chdir(project_dir)
        try:
            pkg = make_awpkg(
                minimal_manifest("com.test.project-scope"),
                {"workflows/test.awp.yaml": MINIMAL_WORKFLOW},
                tmp_path,
            )
            result = install(pkg, scope="project", corvin_home=home)
            # project root = project_dir (has plugins/ subdir, found from CWD)
            expected = project_dir / ".corvin" / "packages" / "com.test.project-scope"
            assert result.install_dir == expected, (
                f"Expected {expected}, got {result.install_dir}"
            )
        finally:
            os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Fixture E2E: dag_simple — linear DAG
# ---------------------------------------------------------------------------

class TestDAGSimple:
    def test_inspect(self, tmp_path):
        pkg = make_awpkg_from_fixture("dag_simple", tmp_path)
        result = inspect(pkg)
        assert result.manifest.id == "com.corvin.daily-news-briefing"
        assert "workflows" in result.manifest.components
        assert "skills" in result.manifest.components
        assert not result.warnings

    def test_install_remove(self, tmp_path):
        home = _corvin_home(tmp_path)
        pkg = make_awpkg_from_fixture("dag_simple", tmp_path)
        installed = install(pkg, scope="user", corvin_home=home)
        assert installed.id == "com.corvin.daily-news-briefing"
        assert installed.version == "1.0.0"
        assert (installed.install_dir / "workflows" / "briefing.awp.yaml").exists()
        assert (installed.install_dir / "skills" / "news_analyst" / "SKILL.md").exists()
        remove(installed.id, scope="user", corvin_home=home)
        assert not installed.install_dir.exists()

    def test_workflow_is_valid_awp(self, tmp_path):
        """The packaged workflow must be parseable as AWP YAML."""
        pkg = make_awpkg_from_fixture("dag_simple", tmp_path)
        with zipfile.ZipFile(pkg) as zf:
            wf = _yaml.safe_load(zf.read("workflows/briefing.awp.yaml"))
        assert wf["awp"] == "1.0.0"
        assert wf["orchestration"]["engine"] == "dag"
        nodes = wf["orchestration"]["graph"]
        assert len(nodes) == 3
        ids = [n["id"] for n in nodes]
        assert "fetch_news" in ids
        assert "summarize" in ids
        assert "format_delivery" in ids

    def test_workflow_dependency_order(self, tmp_path):
        pkg = make_awpkg_from_fixture("dag_simple", tmp_path)
        with zipfile.ZipFile(pkg) as zf:
            wf = _yaml.safe_load(zf.read("workflows/briefing.awp.yaml"))
        graph = {n["id"]: n.get("depends_on", []) for n in wf["orchestration"]["graph"]}
        assert graph["fetch_news"] == []
        assert "fetch_news" in graph["summarize"]
        assert "summarize" in graph["format_delivery"]


# ---------------------------------------------------------------------------
# Fixture E2E: dag_complex — parallel fan-out/fan-in
# ---------------------------------------------------------------------------

class TestDAGComplex:
    def test_inspect(self, tmp_path):
        pkg = make_awpkg_from_fixture("dag_complex", tmp_path)
        result = inspect(pkg)
        assert result.manifest.id == "com.corvin.market-research-suite"
        assert len(result.manifest.components.get("forge_tools", [])) == 2
        assert len(result.manifest.components.get("personas", [])) == 1
        assert not result.warnings

    def test_install_remove(self, tmp_path):
        home = _corvin_home(tmp_path)
        pkg = make_awpkg_from_fixture("dag_complex", tmp_path)
        installed = install(pkg, scope="user", corvin_home=home)
        assert (installed.install_dir / "tools" / "code_fetch_news.json").exists()
        assert (installed.install_dir / "tools" / "code_fetch_social.json").exists()
        assert (installed.install_dir / "personas" / "quant_researcher.yaml").exists()
        remove(installed.id, scope="user", corvin_home=home)

    def test_parallel_nodes_have_no_common_dependency(self, tmp_path):
        pkg = make_awpkg_from_fixture("dag_complex", tmp_path)
        with zipfile.ZipFile(pkg) as zf:
            wf = _yaml.safe_load(zf.read("workflows/market_research.awp.yaml"))
        graph = {n["id"]: n.get("depends_on", []) for n in wf["orchestration"]["graph"]}
        parallel = ["fetch_news", "fetch_reddit", "fetch_filings"]
        for node_id in parallel:
            assert graph[node_id] == [], f"{node_id} should have no deps"
        assert set(graph["merge_context"]) == set(parallel)

    def test_forge_tool_names_valid(self, tmp_path):
        pkg = make_awpkg_from_fixture("dag_complex", tmp_path)
        with zipfile.ZipFile(pkg) as zf:
            for tool_path in ["tools/code_fetch_news.json", "tools/code_fetch_social.json"]:
                tool = json.loads(zf.read(tool_path))
                assert tool["name"].startswith("code."), f"{tool['name']} must start with code."

    def test_tools_network_deny(self, tmp_path):
        pkg = make_awpkg_from_fixture("dag_complex", tmp_path)
        with zipfile.ZipFile(pkg) as zf:
            for tool_path in ["tools/code_fetch_news.json", "tools/code_fetch_social.json"]:
                tool = json.loads(zf.read(tool_path))
                assert tool["meta"]["network"] == "deny"


# ---------------------------------------------------------------------------
# Fixture E2E: delegation — delegation_loop graph
# ---------------------------------------------------------------------------

class TestDelegation:
    def test_inspect(self, tmp_path):
        pkg = make_awpkg_from_fixture("delegation", tmp_path)
        result = inspect(pkg)
        assert result.manifest.id == "com.corvin.code-review-bot"
        assert not result.warnings

    def test_install_remove(self, tmp_path):
        home = _corvin_home(tmp_path)
        pkg = make_awpkg_from_fixture("delegation", tmp_path)
        installed = install(pkg, scope="user", corvin_home=home)
        assert (installed.install_dir / "tools" / "code_diff_parser.json").exists()
        assert (installed.install_dir / "skills" / "code_reviewer" / "SKILL.md").exists()
        remove(installed.id, scope="user", corvin_home=home)

    def test_delegation_loop_node_present(self, tmp_path):
        pkg = make_awpkg_from_fixture("delegation", tmp_path)
        with zipfile.ZipFile(pkg) as zf:
            wf = _yaml.safe_load(zf.read("workflows/code_review.awp.yaml"))
        types = [n["type"] for n in wf["orchestration"]["graph"]]
        assert "delegation_loop" in types

    def test_delegation_loop_has_workers(self, tmp_path):
        pkg = make_awpkg_from_fixture("delegation", tmp_path)
        with zipfile.ZipFile(pkg) as zf:
            wf = _yaml.safe_load(zf.read("workflows/code_review.awp.yaml"))
        loop_node = next(n for n in wf["orchestration"]["graph"] if n["type"] == "delegation_loop")
        workers = loop_node["config"]["workers"]
        worker_ids = [w["id"] for w in workers]
        assert "security_reviewer" in worker_ids
        assert "performance_reviewer" in worker_ids
        assert "style_reviewer" in worker_ids

    def test_synthesiser_depends_on_loop(self, tmp_path):
        pkg = make_awpkg_from_fixture("delegation", tmp_path)
        with zipfile.ZipFile(pkg) as zf:
            wf = _yaml.safe_load(zf.read("workflows/code_review.awp.yaml"))
        synth = next(n for n in wf["orchestration"]["graph"] if n["id"] == "synthesiser")
        assert "review_orchestrator" in synth["depends_on"]


# ---------------------------------------------------------------------------
# Fixture E2E: mixed — 2 workflows + delegation + forge + skills + persona
# ---------------------------------------------------------------------------

class TestMixed:
    def test_inspect(self, tmp_path):
        pkg = make_awpkg_from_fixture("mixed", tmp_path)
        result = inspect(pkg)
        m = result.manifest
        assert m.id == "com.corvin.trading-strategy-pack"
        assert len(m.components.get("workflows", [])) == 2
        assert len(m.components.get("forge_tools", [])) == 3
        assert len(m.components.get("skills", [])) == 2
        assert len(m.components.get("personas", [])) == 1
        assert m.compute_allowed is True
        assert not result.warnings

    def test_install_all_components(self, tmp_path):
        home = _corvin_home(tmp_path)
        pkg = make_awpkg_from_fixture("mixed", tmp_path)
        installed = install(pkg, scope="user", corvin_home=home)
        d = installed.install_dir
        assert (d / "workflows" / "signal_pipeline.awp.yaml").exists()
        assert (d / "workflows" / "backtest.awp.yaml").exists()
        assert (d / "tools" / "code_ohlcv_fetch.json").exists()
        assert (d / "tools" / "code_orderbook_snap.json").exists()
        assert (d / "tools" / "code_compute_indicators.json").exists()
        assert (d / "skills" / "quant_trading" / "SKILL.md").exists()
        assert (d / "skills" / "risk_management" / "SKILL.md").exists()
        assert (d / "personas" / "quant_trader.yaml").exists()
        remove(installed.id, scope="user", corvin_home=home)

    def test_signal_workflow_has_delegation_loop(self, tmp_path):
        pkg = make_awpkg_from_fixture("mixed", tmp_path)
        with zipfile.ZipFile(pkg) as zf:
            wf = _yaml.safe_load(zf.read("workflows/signal_pipeline.awp.yaml"))
        types = [n["type"] for n in wf["orchestration"]["graph"]]
        assert "delegation_loop" in types
        assert "agent" in types

    def test_signal_workflow_parallel_level0(self, tmp_path):
        pkg = make_awpkg_from_fixture("mixed", tmp_path)
        with zipfile.ZipFile(pkg) as zf:
            wf = _yaml.safe_load(zf.read("workflows/signal_pipeline.awp.yaml"))
        graph = {n["id"]: n.get("depends_on", []) for n in wf["orchestration"]["graph"]}
        for node_id in ["fetch_ohlcv", "fetch_orderbook", "fetch_funding"]:
            assert graph[node_id] == [], f"{node_id} should be parallel (no deps)"

    def test_backtest_workflow_is_linear_dag(self, tmp_path):
        pkg = make_awpkg_from_fixture("mixed", tmp_path)
        with zipfile.ZipFile(pkg) as zf:
            wf = _yaml.safe_load(zf.read("workflows/backtest.awp.yaml"))
        graph = {n["id"]: n.get("depends_on", []) for n in wf["orchestration"]["graph"]}
        assert graph["load_history"] == []
        assert graph["run_backtest"] == ["load_history"]
        assert graph["metrics"] == ["run_backtest"]
        assert graph["report"] == ["metrics"]

    def test_all_forge_tools_network_deny(self, tmp_path):
        pkg = make_awpkg_from_fixture("mixed", tmp_path)
        with zipfile.ZipFile(pkg) as zf:
            for tool_path in [
                "tools/code_ohlcv_fetch.json",
                "tools/code_orderbook_snap.json",
                "tools/code_compute_indicators.json",
            ]:
                tool = json.loads(zf.read(tool_path))
                assert tool["meta"]["network"] == "deny", f"{tool_path} must deny network"

    def test_dependency_declared(self, tmp_path):
        pkg = make_awpkg_from_fixture("mixed", tmp_path)
        result = inspect(pkg)
        deps = result.manifest.dependencies
        assert any(d["id"] == "com.corvin.market-research-suite" for d in deps)

    def test_list_shows_mixed_after_install(self, tmp_path):
        home = _corvin_home(tmp_path)
        pkg = make_awpkg_from_fixture("mixed", tmp_path)
        install(pkg, scope="user", corvin_home=home)
        listed = list_installed(scope="user", corvin_home=home)
        ids = [p.id for p in listed]
        assert "com.corvin.trading-strategy-pack" in ids
        remove("com.corvin.trading-strategy-pack", scope="user", corvin_home=home)


# ---------------------------------------------------------------------------
# Audit chain
# ---------------------------------------------------------------------------

class TestAuditChain:
    def test_install_emits_audit_event(self, tmp_path):
        home = _corvin_home(tmp_path)
        audit_path = home / "global" / "forge" / "audit.jsonl"
        os.environ["VOICE_AUDIT_PATH"] = str(audit_path)
        try:
            pkg = make_awpkg(
                minimal_manifest("com.test.audit"),
                {"workflows/test.awp.yaml": MINIMAL_WORKFLOW},
                tmp_path,
            )
            install(pkg, scope="user", corvin_home=home)
            assert audit_path.exists()
            events = [json.loads(l) for l in audit_path.read_text().splitlines() if l.strip()]
            event_types = [e["event_type"] for e in events]
            assert "package.installed" in event_types
        finally:
            os.environ.pop("VOICE_AUDIT_PATH", None)

    def test_remove_emits_audit_event(self, tmp_path):
        home = _corvin_home(tmp_path)
        audit_path = home / "global" / "forge" / "audit.jsonl"
        os.environ["VOICE_AUDIT_PATH"] = str(audit_path)
        try:
            pkg = make_awpkg(
                minimal_manifest("com.test.audit-remove"),
                {"workflows/test.awp.yaml": MINIMAL_WORKFLOW},
                tmp_path,
            )
            install(pkg, scope="user", corvin_home=home)
            remove("com.test.audit-remove", scope="user", corvin_home=home)
            events = [json.loads(l) for l in audit_path.read_text().splitlines() if l.strip()]
            event_types = [e["event_type"] for e in events]
            assert "package.removed" in event_types
        finally:
            os.environ.pop("VOICE_AUDIT_PATH", None)

    def test_audit_chain_integrity(self, tmp_path):
        """prev_hash chain must link correctly."""
        import hashlib
        home = _corvin_home(tmp_path)
        audit_path = home / "global" / "forge" / "audit.jsonl"
        os.environ["VOICE_AUDIT_PATH"] = str(audit_path)
        try:
            for i in range(3):
                pkg = make_awpkg(
                    minimal_manifest(f"com.test.chain-{i}"),
                    {"workflows/test.awp.yaml": MINIMAL_WORKFLOW},
                    tmp_path,
                    filename=f"chain-{i}.awpkg",
                )
                install(pkg, scope="user", corvin_home=home)

            events = [json.loads(l) for l in audit_path.read_text().splitlines() if l.strip()]
            prev = ""
            for evt in events:
                declared_prev = evt.get("prev_hash", "")
                assert declared_prev == prev, "chain broken"
                evt_copy = {k: v for k, v in evt.items() if k != "hash"}
                canonical = json.dumps(evt_copy, sort_keys=True, separators=(",", ":"))
                expected_hash = hashlib.sha256(
                    (prev + "\n" + canonical).encode()
                ).hexdigest()[:16]
                assert evt["hash"] == expected_hash
                prev = evt["hash"]
        finally:
            os.environ.pop("VOICE_AUDIT_PATH", None)


# ---------------------------------------------------------------------------
# Path-gate integration (unit test — does not run the actual hook process)
# ---------------------------------------------------------------------------

class TestPathGateIntegration:
    def test_packages_path_is_protected(self, tmp_path):
        """is_protected_path must return True for paths under packages/."""
        import sys as _sys
        # test_e2e.py parents: [0]=tests, [1]=awpkg, [2]=core, [3]=repo
        hook_path = Path(__file__).parents[3] / "operator" / "voice" / "hooks"
        if str(hook_path.parent) not in _sys.path:
            _sys.path.insert(0, str(hook_path.parent))
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "path_gate", hook_path / "path_gate.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        os.environ["CORVIN_HOME"] = str(tmp_path / ".corvin")
        try:
            protected = mod.is_protected_path(
                tmp_path / ".corvin" / "packages" / "com.example.pkg" / "manifest.yaml"
            )
            assert protected, "packages/** must be protected by path_gate"
        finally:
            os.environ.pop("CORVIN_HOME", None)
