"""``corvin plugin install <marketplace-id>`` — driven through the REAL CLI boundary.

The previous version of this file mocked ``PluginMarketplace.get_default()`` /
``.get_index()`` / ``.install_plugin()`` — methods that never existed on the
class, so the tests were green while the command had never worked once (the
AttributeError was swallowed into ``exit 2``). These tests run the launcher as
a subprocess (``python -m corvin plugin install ...``) under a temp
``CORVIN_HOME`` and read back the tenant registry the command wrote.

Skips when no Corvin-Marketplace checkout is present (the install resolves a
marketplace id to LOCAL builtin source; remote download is out of scope and is
refused with a clear message — asserted below).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

_REPO = Path(__file__).resolve().parents[4]
_LAUNCHER = _REPO / "ops" / "launcher"
_MARKETPLACE = Path(
    os.environ.get("CORVIN_MARKETPLACE_ROOT")
    or _REPO.parent / "Corvin-Marketplace" / "plugins" / "buildin"
)
_RETRIEVER_DIR = _MARKETPLACE / "memory" / "semantic_context_retriever"


def _corvin(home: Path, *argv: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items()
           if k not in ("CORVIN_TENANT_ID", "VOICE_AUDIT_PATH", "FORGE_ROOT")}
    env["CORVIN_HOME"] = str(home)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(_LAUNCHER), str(_REPO / "core" / "plugins"), str(_REPO),
         str(_REPO / "core" / "console")]
    )
    return subprocess.run(
        [sys.executable, "-m", "corvin", "plugin", *argv],
        capture_output=True, text=True, timeout=120, env=env, cwd=str(home),
    )


def _installed_ids(home: Path, tenant: str = "_default") -> list[str]:
    reg = home / "tenants" / tenant / "plugins" / "registry.yaml"
    if not reg.is_file():
        return []
    data = yaml.safe_load(reg.read_text()) or {}
    plugins = data.get("plugins") or []
    if isinstance(plugins, dict):
        return sorted(plugins)
    return sorted(p.get("plugin_id") for p in plugins if isinstance(p, dict))


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "corvin_home"
    h.mkdir()
    return h


@pytest.mark.skipif(not _RETRIEVER_DIR.is_dir(), reason="no Corvin-Marketplace checkout")
def test_install_by_index_id_installs_the_local_builtin_source(home):
    proc = _corvin(home, "install", "plugin:buildin-memory-semantic_context_retriever")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Installed semantic-context-retriever@" in proc.stdout, proc.stdout
    assert "semantic-context-retriever" in _installed_ids(home)
    # The source was copied into the tenant, not referenced by absolute path.
    copied = home / "tenants" / "_default" / "plugins" / "installed" / "semantic-context-retriever"
    assert (copied / "plugin.yaml").is_file() and (copied / "provider.py").is_file()


@pytest.mark.skipif(not _RETRIEVER_DIR.is_dir(), reason="no Corvin-Marketplace checkout")
def test_install_by_bare_directory_name_resolves_across_categories(home):
    proc = _corvin(home, "install", "semantic_context_retriever")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "semantic-context-retriever" in _installed_ids(home)


@pytest.mark.skipif(not _RETRIEVER_DIR.is_dir(), reason="no Corvin-Marketplace checkout")
def test_install_twice_is_a_clear_error_not_a_crash(home):
    assert _corvin(home, "install", "semantic_context_retriever").returncode == 0
    proc = _corvin(home, "install", "semantic_context_retriever")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "already installed" in proc.stderr


def test_unknown_id_is_exit_1_with_a_not_found_message(home):
    proc = _corvin(home, "install", "no-such-plugin-anywhere")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "plugin not found in marketplace: no-such-plugin-anywhere" in proc.stderr
    assert "AttributeError" not in proc.stderr
    assert _installed_ids(home) == []


def test_community_tier_ids_are_refused_not_pretended(home):
    proc = _corvin(home, "install", "plugin:contributor-integration-slack_notifier")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "only builtin plugins install locally" in proc.stderr


def test_missing_local_directory_is_exit_2(home):
    proc = _corvin(home, "install", str(home / "does" / "not" / "exist"))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "plugin directory not found" in proc.stderr
