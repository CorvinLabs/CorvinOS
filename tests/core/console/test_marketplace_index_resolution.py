"""Marketplace index path resolution (ADR-0512 amendment, 2026-09-01).

ADR-0512 moved `corvin_operator/marketplace/` out of CorvinOS into the sibling
Corvin-Marketplace repository, but the console kept resolving the index at the
in-repo path. Every branch created after that move served `count: 0` and the
panel rendered "No extensions found" — a silent empty marketplace, because a
missing index degrades to an empty one rather than an error.
"""

import json
import os
from pathlib import Path

import pytest

from corvin_console.routes.marketplace import _IndexManager, resolve_index_path


@pytest.fixture(autouse=True)
def _no_override(monkeypatch):
    monkeypatch.delenv("CORVIN_MARKETPLACE_INDEX", raising=False)


def _write_index(path: Path, plugin_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "version": "2.0",
        "schema": "ADR-0511",
        "plugin_count": 1,
        "plugins": [{"id": plugin_id, "name": plugin_id, "version": "1.0.0"}],
        "by_id": {}, "by_category": {}, "by_tier": {},
    }))


def test_env_override_wins(tmp_path, monkeypatch):
    custom = tmp_path / "custom" / "plugins.json"
    _write_index(custom, "from-env")
    monkeypatch.setenv("CORVIN_MARKETPLACE_INDEX", str(custom))
    assert resolve_index_path() == custom


def test_env_override_expands_user(monkeypatch):
    monkeypatch.setenv("CORVIN_MARKETPLACE_INDEX", "~/some/plugins.json")
    assert resolve_index_path() == Path.home() / "some" / "plugins.json"


def _relocate_module(monkeypatch, tmp_path: Path) -> Path:
    """Pretend the routes module lives in a checkout under tmp_path and
    return that checkout's root (the module anchors on Path(__file__))."""
    import corvin_console.routes.marketplace as mp
    fake = tmp_path / "repo" / "core" / "console" / "corvin_console" / "routes" / "marketplace.py"
    fake.parent.mkdir(parents=True)
    fake.write_text("")
    monkeypatch.setattr(mp, "__file__", str(fake))
    return tmp_path / "repo"


def test_legacy_in_repo_index_wins_over_sibling(tmp_path, monkeypatch):
    repo = _relocate_module(monkeypatch, tmp_path)
    legacy = repo / "corvin_operator" / "marketplace" / "index" / "plugins.json"
    _write_index(legacy, "from-legacy")
    _write_index(tmp_path / "Corvin-Marketplace" / "index" / "plugins.json", "from-sibling")
    assert resolve_index_path() == legacy


def test_resolution_does_not_depend_on_the_cwd(tmp_path, monkeypatch):
    """Fresh-system review 2026-10-21 (F7): the legacy candidate was built from
    Path.cwd(); a systemd unit's cwd is $HOME, so every boot logged "Index not
    found at /home/<user>/corvin_operator/..." — a path that never existed.
    An index in the cwd must be ignored, one in the checkout must be found."""
    repo = _relocate_module(monkeypatch, tmp_path)
    cwd = tmp_path / "somewhere-else"
    _write_index(cwd / "corvin_operator" / "marketplace" / "index" / "plugins.json", "from-cwd")
    monkeypatch.chdir(cwd)
    resolved = resolve_index_path()
    assert "somewhere-else" not in str(resolved)
    assert resolved == repo / "corvin_operator" / "marketplace" / "index" / "plugins.json"


def test_falls_back_to_sibling_checkout(tmp_path, monkeypatch):
    """The normal developer layout after ADR-0512: no in-repo index at all."""
    repo = _relocate_module(monkeypatch, tmp_path)
    sibling = tmp_path / "Corvin-Marketplace" / "index" / "plugins.json"
    _write_index(sibling, "from-sibling")
    assert resolve_index_path() == sibling
    sibling.unlink()
    # No sibling checkout either — resolution must still return the (absent)
    # legacy path so the caller's FileNotFoundError branch reaches GitHub.
    assert resolve_index_path() == repo / "corvin_operator" / "marketplace" / "index" / "plugins.json"


def test_missing_everything_degrades_to_empty_index(tmp_path, monkeypatch):
    """A resolvable-but-absent index must not raise; the API returns count 0.

    Offline by construction: with network the manager's GitHub fallback
    serves the real index (37 plugins on 2026-10-21) and this test asserted
    an empty list against it."""
    import urllib.request

    def _offline(*a, **k):
        raise OSError("offline")
    monkeypatch.setattr(urllib.request, "urlopen", _offline)
    manager = _IndexManager()
    manager.set_index_path(tmp_path / "nope" / "plugins.json")
    index = manager.get_index()
    assert index["plugins"] == []
    assert index["plugin_count"] == 0


def test_manager_loads_the_resolved_index(tmp_path, monkeypatch):
    custom = tmp_path / "plugins.json"
    _write_index(custom, "plugin:test-one")
    monkeypatch.setenv("CORVIN_MARKETPLACE_INDEX", str(custom))
    manager = _IndexManager()
    assert manager.get_index()["plugins"][0]["id"] == "plugin:test-one"
