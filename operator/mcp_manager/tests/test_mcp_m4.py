"""Tests for MCP Plugin Manager M4 (ADR-0096).

Covers:
  - Session-scope activation (ephemeral JSON file)
  - Project-scope activation (.corvin/mcp-active.json)
  - clear_session_scope()
  - get_active_tool_ids() merge order (tenant < user < project < session)
  - get_active_mcp_servers() with session_key and project_dir
  - Docker installer: _install_docker success + missing-docker failure
  - Docker: update() re-pulls and re-pins digest
  - update() for npm, pip, github, local source types
  - corvin-mcp search command (builtin manifests)

Run with:
    cd operator/mcp_manager
    python -m pytest tests/test_mcp_m4.py -v
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))

import mcp_manager.activate as activate
import mcp_manager.catalog as catalog
import mcp_manager.installer as installer

TID = "_default"


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def tmp_corvin(monkeypatch, tmp_path):
    corvin = tmp_path / ".corvin"
    corvin.mkdir()
    monkeypatch.setenv("CORVIN_HOME", str(corvin))
    activate._active_cache.clear()
    return corvin


def _add_local_tool(tid: str, tool_id: str) -> None:
    """Add a minimal local-compliance tool to the catalog."""
    catalog.add_tool(tid, {
        "id": tool_id,
        "source": f"npm:{tool_id}@1.0.0",
        "installed_at": "2026-06-07T00:00:00+00:00",
        "runtime": {"command": "npx", "args": ["-y", f"{tool_id}@1.0.0"]},
        "secrets": [],
        "compliance": {"locality": "local", "network_egress": "none"},
    })


# ── Session-scope activation ───────────────────────────────────────────────────


class TestSessionScope:
    def test_activate_session_writes_file(self, tmp_corvin):
        _add_local_tool(TID, "tool-a")
        activate.activate(TID, "tool-a", "session", session_key="discord:123")
        path = activate._session_active_path(TID, "discord:123")
        assert path.is_file()
        items = json.loads(path.read_text())
        assert "tool-a" in items

    def test_activate_session_requires_session_key(self, tmp_corvin):
        _add_local_tool(TID, "tool-a")
        with pytest.raises(ValueError, match="session_key"):
            activate.activate(TID, "tool-a", "session")

    def test_activate_session_does_not_write_global_active(self, tmp_corvin):
        _add_local_tool(TID, "tool-a")
        activate.activate(TID, "tool-a", "session", session_key="discord:123")
        # Global active.json should NOT contain the session-scope entry
        data = activate.load_active(TID)
        assert "tool-a" not in data.get("session", [])

    def test_deactivate_session(self, tmp_corvin):
        _add_local_tool(TID, "tool-a")
        activate.activate(TID, "tool-a", "session", session_key="discord:123")
        removed = activate.deactivate(TID, "tool-a", "session", session_key="discord:123")
        assert removed is True
        path = activate._session_active_path(TID, "discord:123")
        items = json.loads(path.read_text())
        assert "tool-a" not in items

    def test_deactivate_session_missing_returns_false(self, tmp_corvin):
        _add_local_tool(TID, "tool-a")
        removed = activate.deactivate(TID, "tool-a", "session", session_key="discord:123")
        assert removed is False

    def test_deactivate_session_requires_session_key(self, tmp_corvin):
        with pytest.raises(ValueError, match="session_key"):
            activate.deactivate(TID, "tool-a", "session")

    def test_clear_session_scope_deletes_file(self, tmp_corvin):
        _add_local_tool(TID, "tool-a")
        activate.activate(TID, "tool-a", "session", session_key="discord:123")
        path = activate._session_active_path(TID, "discord:123")
        assert path.is_file()
        activate.clear_session_scope(TID, "discord:123")
        assert not path.exists()

    def test_clear_session_scope_noop_if_missing(self, tmp_corvin):
        # Should not raise even when the file doesn't exist
        activate.clear_session_scope(TID, "discord:nonexistent")

    def test_session_scope_isolated_per_session_key(self, tmp_corvin):
        _add_local_tool(TID, "tool-a")
        _add_local_tool(TID, "tool-b")
        activate.activate(TID, "tool-a", "session", session_key="discord:111")
        activate.activate(TID, "tool-b", "session", session_key="discord:222")
        ids_111 = activate.get_active_tool_ids(TID, session_key="discord:111")
        ids_222 = activate.get_active_tool_ids(TID, session_key="discord:222")
        assert "tool-a" in ids_111
        assert "tool-b" not in ids_111
        assert "tool-b" in ids_222
        assert "tool-a" not in ids_222


# ── Project-scope activation ───────────────────────────────────────────────────


class TestProjectScope:
    def test_activate_project_writes_dotcorvin(self, tmp_corvin, tmp_path):
        _add_local_tool(TID, "tool-a")
        project_dir = str(tmp_path / "myproject")
        Path(project_dir).mkdir()
        activate.activate(TID, "tool-a", "project", project_dir=project_dir)
        path = activate._project_active_path(project_dir)
        assert path.is_file()
        items = json.loads(path.read_text())
        assert "tool-a" in items

    def test_activate_project_requires_project_dir(self, tmp_corvin):
        _add_local_tool(TID, "tool-a")
        with pytest.raises(ValueError, match="project_dir"):
            activate.activate(TID, "tool-a", "project")

    def test_deactivate_project(self, tmp_corvin, tmp_path):
        _add_local_tool(TID, "tool-a")
        project_dir = str(tmp_path / "myproject")
        Path(project_dir).mkdir()
        activate.activate(TID, "tool-a", "project", project_dir=project_dir)
        removed = activate.deactivate(TID, "tool-a", "project", project_dir=project_dir)
        assert removed is True
        path = activate._project_active_path(project_dir)
        items = json.loads(path.read_text())
        assert "tool-a" not in items

    def test_project_scope_isolated_per_project_dir(self, tmp_corvin, tmp_path):
        _add_local_tool(TID, "tool-a")
        _add_local_tool(TID, "tool-b")
        proj_a = str(tmp_path / "proj_a")
        proj_b = str(tmp_path / "proj_b")
        Path(proj_a).mkdir()
        Path(proj_b).mkdir()
        activate.activate(TID, "tool-a", "project", project_dir=proj_a)
        activate.activate(TID, "tool-b", "project", project_dir=proj_b)
        ids_a = activate.get_active_tool_ids(TID, project_dir=proj_a)
        ids_b = activate.get_active_tool_ids(TID, project_dir=proj_b)
        assert "tool-a" in ids_a
        assert "tool-b" not in ids_a
        assert "tool-b" in ids_b
        assert "tool-a" not in ids_b


# ── Merge order ───────────────────────────────────────────────────────────────


class TestMergeOrder:
    """tenant < user < project < session — all four scopes active at once."""

    def test_all_four_scopes_merged(self, tmp_corvin, tmp_path):
        for name in ("t-tool", "u-tool", "p-tool", "s-tool"):
            _add_local_tool(TID, name)

        activate.activate(TID, "t-tool", "tenant")
        activate.activate(TID, "u-tool", "user")

        project_dir = str(tmp_path / "proj")
        Path(project_dir).mkdir()
        activate.activate(TID, "p-tool", "project", project_dir=project_dir)
        activate.activate(TID, "s-tool", "session", session_key="discord:999")

        ids = activate.get_active_tool_ids(
            TID,
            session_key="discord:999",
            project_dir=project_dir,
        )
        assert set(ids) == {"t-tool", "u-tool", "p-tool", "s-tool"}

    def test_deduplication_across_scopes(self, tmp_corvin, tmp_path):
        _add_local_tool(TID, "shared-tool")

        activate.activate(TID, "shared-tool", "user")

        project_dir = str(tmp_path / "proj")
        Path(project_dir).mkdir()
        activate.activate(TID, "shared-tool", "project", project_dir=project_dir)
        activate.activate(TID, "shared-tool", "session", session_key="discord:42")

        ids = activate.get_active_tool_ids(
            TID,
            session_key="discord:42",
            project_dir=project_dir,
        )
        # Should appear only once
        assert ids.count("shared-tool") == 1

    def test_no_session_no_project_returns_global(self, tmp_corvin):
        _add_local_tool(TID, "tool-a")
        activate.activate(TID, "tool-a", "user")
        ids = activate.get_active_tool_ids(TID)
        assert "tool-a" in ids


# ── get_active_mcp_servers with scopes ────────────────────────────────────────


class TestGetActiveMcpServersScoped:
    def test_session_tool_included(self, tmp_corvin):
        _add_local_tool(TID, "tool-a")
        activate.activate(TID, "tool-a", "session", session_key="discord:100")
        servers = activate.get_active_mcp_servers(TID, session_key="discord:100")
        assert "tool-a" in servers

    def test_session_tool_excluded_without_session_key(self, tmp_corvin):
        _add_local_tool(TID, "tool-a")
        activate.activate(TID, "tool-a", "session", session_key="discord:100")
        servers = activate.get_active_mcp_servers(TID)
        assert "tool-a" not in servers

    def test_project_tool_included(self, tmp_corvin, tmp_path):
        _add_local_tool(TID, "tool-a")
        project_dir = str(tmp_path / "proj")
        Path(project_dir).mkdir()
        activate.activate(TID, "tool-a", "project", project_dir=project_dir)
        servers = activate.get_active_mcp_servers(TID, project_dir=project_dir)
        assert "tool-a" in servers

    def test_project_tool_excluded_without_project_dir(self, tmp_corvin, tmp_path):
        _add_local_tool(TID, "tool-a")
        project_dir = str(tmp_path / "proj")
        Path(project_dir).mkdir()
        activate.activate(TID, "tool-a", "project", project_dir=project_dir)
        servers = activate.get_active_mcp_servers(TID)
        assert "tool-a" not in servers


# ── image_outdir (bug report 2026-07-12: generated images not showing inline) ──


class TestImageOutdirInjection:
    """The imagegen-zero-config MCP server writes to Path.cwd()/outputs by
    default -- an implicit cwd-inheritance-through-the-claude-CLI assumption
    the server's own code comments flag as unverified. get_active_mcp_servers
    now accepts image_outdir to set CORVIN_IMAGE_OUTDIR explicitly on that
    one catalog entry, mirroring the existing CORVIN_HOME/CORVIN_TENANT_ID
    plaintext-env pattern used for the same reliability reason."""

    def test_sets_corvin_image_outdir_on_imagegen_entry(self, tmp_corvin, tmp_path):
        catalog.add_tool(TID, {
            "id": "imagegen-zero-config",
            "source": "builtin:/fake/main.py",
            "installed_at": "2026-07-12T00:00:00+00:00",
            "runtime": {"command": "python", "args": ["/fake/main.py"],
                        "env": {"CORVIN_HOME": "/fake/home"}},
            "secrets": [],
            "compliance": {"locality": "local", "network_egress": "none"},
        })
        activate.activate(TID, "imagegen-zero-config", "tenant")
        outdir = str(tmp_path / "session-workdir" / "outputs")
        servers = activate.get_active_mcp_servers(TID, image_outdir=outdir)
        assert servers["imagegen-zero-config"]["env"]["CORVIN_IMAGE_OUTDIR"] == outdir
        # Pre-existing plaintext env entries must survive the injection.
        assert servers["imagegen-zero-config"]["env"]["CORVIN_HOME"] == "/fake/home"

    def test_no_image_outdir_leaves_env_unset(self, tmp_corvin):
        catalog.add_tool(TID, {
            "id": "imagegen-zero-config",
            "source": "builtin:/fake/main.py",
            "installed_at": "2026-07-12T00:00:00+00:00",
            "runtime": {"command": "python", "args": ["/fake/main.py"]},
            "secrets": [],
            "compliance": {"locality": "local", "network_egress": "none"},
        })
        activate.activate(TID, "imagegen-zero-config", "tenant")
        servers = activate.get_active_mcp_servers(TID)
        assert "CORVIN_IMAGE_OUTDIR" not in servers["imagegen-zero-config"].get("env", {})

    def test_other_tools_unaffected_by_image_outdir(self, tmp_corvin, tmp_path):
        _add_local_tool(TID, "tool-a")
        activate.activate(TID, "tool-a", "tenant")
        servers = activate.get_active_mcp_servers(
            TID, image_outdir=str(tmp_path / "outputs"),
        )
        assert "CORVIN_IMAGE_OUTDIR" not in servers["tool-a"].get("env", {})


# ── Docker installer ──────────────────────────────────────────────────────────


class TestDockerInstaller:
    def test_install_docker_no_docker_raises(self, tmp_corvin):
        """If docker is not on PATH, install raises ValueError."""
        with patch("shutil.which", return_value=None):
            with pytest.raises(ValueError, match="docker is not available"):
                installer.install("docker:ghcr.io/foo/bar:1.2.3", TID)

    def test_install_docker_success(self, tmp_corvin):
        """Happy path: docker pull + inspect both succeed."""
        fake_docker = "/usr/bin/docker"

        def fake_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            if "pull" in cmd:
                result.stdout = ""
                result.stderr = ""
            elif "inspect" in cmd:
                result.stdout = "ghcr.io/foo/bar@sha256:abc123def456\n"
                result.stderr = ""
            return result

        with patch("shutil.which", return_value=fake_docker), \
             patch("subprocess.run", side_effect=fake_run):
            entry = installer.install("docker:ghcr.io/foo/bar:1.2.3", TID)

        assert entry["source"] == "docker:ghcr.io/foo/bar:1.2.3"
        assert entry["docker_digest"] == "ghcr.io/foo/bar@sha256:abc123def456"
        assert entry["runtime"]["command"] == "docker"
        assert "ghcr.io/foo/bar:1.2.3" in entry["runtime"]["args"]
        assert entry["compliance"]["locality"] == "local"
        assert entry["compliance"]["network_egress"] == "required"

    def test_install_docker_pull_failure(self, tmp_corvin):
        fake_docker = "/usr/bin/docker"

        def fake_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 1
            result.stderr = "pull access denied"
            return result

        with patch("shutil.which", return_value=fake_docker), \
             patch("subprocess.run", side_effect=fake_run):
            with pytest.raises(ValueError, match="docker pull"):
                installer.install("docker:ghcr.io/foo/private:latest", TID)

    def test_install_docker_no_digest_raises(self, tmp_corvin):
        fake_docker = "/usr/bin/docker"

        def fake_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            if "pull" in cmd:
                result.stdout = ""
                result.stderr = ""
            elif "inspect" in cmd:
                # No digest available (locally built image)
                result.stdout = "<no value>\n"
                result.stderr = ""
            return result

        with patch("shutil.which", return_value=fake_docker), \
             patch("subprocess.run", side_effect=fake_run):
            with pytest.raises(ValueError, match="No repo digest"):
                installer.install("docker:local-only-image:latest", TID)

    def test_install_docker_invalid_spec(self, tmp_corvin):
        with pytest.raises(ValueError, match="Invalid docker image spec"):
            installer.install("docker:bad image; rm -rf", TID)

    def test_install_docker_saved_to_catalog(self, tmp_corvin):
        fake_docker = "/usr/bin/docker"

        def fake_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = "ghcr.io/foo/bar@sha256:deadbeef\n"
            result.stderr = ""
            return result

        with patch("shutil.which", return_value=fake_docker), \
             patch("subprocess.run", side_effect=fake_run):
            entry = installer.install("docker:ghcr.io/foo/bar:2.0", TID)

        fetched = catalog.get_tool(TID, entry["id"])
        assert fetched is not None
        assert fetched["docker_digest"] == "ghcr.io/foo/bar@sha256:deadbeef"


# ── verify_docker_digest ──────────────────────────────────────────────────────


class TestVerifyDockerDigest:
    def _make_entry(self, image_ref: str, digest: str) -> dict:
        return {
            "id": "test-docker",
            "source": f"docker:{image_ref}",
            "docker_digest": digest,
        }

    def test_verify_match(self):
        entry = self._make_entry("foo/bar:1.0", "foo/bar@sha256:abc")

        def fake_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = "foo/bar@sha256:abc\n"
            result.stderr = ""
            return result

        with patch("shutil.which", return_value="/usr/bin/docker"), \
             patch("subprocess.run", side_effect=fake_run):
            assert installer.verify_docker_digest(entry) is True

    def test_verify_mismatch(self):
        entry = self._make_entry("foo/bar:1.0", "foo/bar@sha256:abc")

        def fake_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = "foo/bar@sha256:DIFFERENT\n"
            result.stderr = ""
            return result

        with patch("shutil.which", return_value="/usr/bin/docker"), \
             patch("subprocess.run", side_effect=fake_run):
            assert installer.verify_docker_digest(entry) is False

    def test_verify_no_digest_pinned(self):
        entry = {"id": "x", "source": "docker:foo:1.0", "docker_digest": ""}
        assert installer.verify_docker_digest(entry) is False

    def test_verify_docker_unavailable(self):
        entry = self._make_entry("foo/bar:1.0", "foo/bar@sha256:abc")
        with patch("shutil.which", return_value=None):
            assert installer.verify_docker_digest(entry) is False


# ── update() ─────────────────────────────────────────────────────────────────


class TestUpdate:
    def test_update_nonexistent_tool(self, tmp_corvin):
        with pytest.raises(ValueError, match="not installed"):
            installer.update("nonexistent-tool", TID)

    def test_update_npm_calls_install_latest(self, tmp_corvin):
        catalog.add_tool(TID, {
            "id": "my-npm-tool",
            "source": "npm:my-package@1.0.0",
            "installed_at": "2026-01-01T00:00:00+00:00",
            "runtime": {"command": "npx", "args": ["-y", "my-package@1.0.0"]},
            "secrets": [],
            "compliance": {"locality": "local", "network_egress": "none"},
        })
        new_entry = installer.update("my-npm-tool", TID)
        # Should re-install with @latest
        assert new_entry["source"].startswith("npm:my-package@")

    def test_update_pip_calls_install_latest(self, tmp_corvin):
        catalog.add_tool(TID, {
            "id": "my-pip-tool",
            "source": "pip:my-package==1.0.0",
            "installed_at": "2026-01-01T00:00:00+00:00",
            "runtime": {"command": "uvx", "args": ["my-package"]},
            "secrets": [],
            "compliance": {"locality": "local", "network_egress": "none"},
        })
        with patch("shutil.which", return_value="/usr/bin/uvx"):
            new_entry = installer.update("my-pip-tool", TID)
        assert new_entry["source"].startswith("pip:my-package")

    def test_update_github_raises(self, tmp_corvin):
        catalog.add_tool(TID, {
            "id": "my-github-tool",
            "source": "github:owner/repo@v1.0.0",
            "installed_at": "2026-01-01T00:00:00+00:00",
            "runtime": {"command": "node", "args": ["/some/path/index.js"]},
            "secrets": [],
            "compliance": {"locality": "local", "network_egress": "none"},
        })
        with pytest.raises(ValueError, match="GitHub installs"):
            installer.update("my-github-tool", TID)

    def test_update_local_noop(self, tmp_corvin):
        original_entry = {
            "id": "my-local-tool",
            "source": "local:/some/path",
            "installed_at": "2026-01-01T00:00:00+00:00",
            "runtime": {"command": "node", "args": ["/some/path/index.js"]},
            "secrets": [],
            "compliance": {"locality": "local", "network_egress": "none"},
        }
        catalog.add_tool(TID, original_entry)
        result = installer.update("my-local-tool", TID)
        # Returns unchanged entry
        assert result["source"] == "local:/some/path"

    def test_update_docker_repulls(self, tmp_corvin):
        catalog.add_tool(TID, {
            "id": "my-docker-tool",
            "source": "docker:foo/bar:latest",
            "installed_at": "2026-01-01T00:00:00+00:00",
            "docker_digest": "foo/bar@sha256:old",
            "runtime": {"command": "docker", "args": ["run", "--rm", "-i", "--", "foo/bar:latest"]},
            "secrets": [],
            "compliance": {"locality": "local", "network_egress": "required"},
        })

        def fake_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = "foo/bar@sha256:newdigest\n"
            result.stderr = ""
            return result

        with patch("shutil.which", return_value="/usr/bin/docker"), \
             patch("subprocess.run", side_effect=fake_run):
            updated = installer.update("my-docker-tool", TID)

        assert updated["docker_digest"] == "foo/bar@sha256:newdigest"
        # Verify catalog was updated
        fetched = catalog.get_tool(TID, "my-docker-tool")
        assert fetched["docker_digest"] == "foo/bar@sha256:newdigest"


# ── Builtin manifests / search ────────────────────────────────────────────────


class TestBuiltinManifests:
    """Test that the bundled manifests directory exists and is searchable."""

    def _manifests_dir(self) -> Path:
        return (
            Path(__file__).resolve().parents[1]
            / "mcp_manager"
            / "builtin_manifests"
        )

    def test_manifests_dir_exists(self):
        assert self._manifests_dir().is_dir()

    def test_required_manifests_present(self):
        d = self._manifests_dir()
        required = {"brave-search.json", "filesystem.json", "github.json",
                    "sqlite.json", "fetch.json"}
        found = {f.name for f in d.glob("*.json")}
        assert required.issubset(found), f"Missing manifests: {required - found}"

    def test_manifest_schema(self):
        """Each manifest must have id, name, description, source, tags, compliance."""
        d = self._manifests_dir()
        for manifest_file in d.glob("*.json"):
            data = json.loads(manifest_file.read_text())
            assert "id" in data, f"{manifest_file.name} missing 'id'"
            assert "name" in data, f"{manifest_file.name} missing 'name'"
            assert "description" in data, f"{manifest_file.name} missing 'description'"
            assert "source" in data, f"{manifest_file.name} missing 'source'"
            assert "tags" in data, f"{manifest_file.name} missing 'tags'"
            assert isinstance(data["tags"], list), f"{manifest_file.name} tags must be a list"
            assert "compliance" in data, f"{manifest_file.name} missing 'compliance'"

    def test_search_by_tag(self, capsys):
        """cmd_search filters by query term in tags."""
        import argparse
        # Import corvin_mcp from the scripts directory
        scripts = Path(__file__).resolve().parents[2] / "voice" / "scripts"
        if str(scripts) not in sys.path:
            sys.path.insert(0, str(scripts))
        import corvin_mcp

        args = argparse.Namespace(query=["search"])
        corvin_mcp.cmd_search(args)
        captured = capsys.readouterr()
        # brave-search and fetch both have search-related tags
        assert "brave-search" in captured.out or "fetch" in captured.out

    def test_search_empty_query_lists_all(self, capsys):
        import argparse
        scripts = Path(__file__).resolve().parents[2] / "voice" / "scripts"
        if str(scripts) not in sys.path:
            sys.path.insert(0, str(scripts))
        import corvin_mcp

        args = argparse.Namespace(query=[])
        corvin_mcp.cmd_search(args)
        captured = capsys.readouterr()
        # All bundled tools should appear
        for name in ("brave-search", "filesystem", "github", "sqlite", "fetch"):
            assert name in captured.out

    def test_search_no_match(self, capsys):
        import argparse
        scripts = Path(__file__).resolve().parents[2] / "voice" / "scripts"
        if str(scripts) not in sys.path:
            sys.path.insert(0, str(scripts))
        import corvin_mcp

        args = argparse.Namespace(query=["zzznomatch"])
        corvin_mcp.cmd_search(args)
        captured = capsys.readouterr()
        assert "No bundled tools found" in captured.out


# ── session_key path sanitisation ─────────────────────────────────────────────


class TestSessionKeyPathSanitisation:
    """Verify that path-traversal attempts in session_key are neutralised."""

    def test_path_traversal_in_session_key(self, tmp_corvin):
        path = activate._session_active_path(TID, "../../evil")
        # The resolved path must stay inside the corvin home
        corvin_home = Path(os.environ["CORVIN_HOME"])
        assert str(path).startswith(str(corvin_home))

    def test_forward_slash_sanitised(self, tmp_corvin):
        path = activate._session_active_path(TID, "discord:channel/subchannel")
        # The forward-slash should be replaced; no extra path component
        assert "subchannel" not in path.parts
