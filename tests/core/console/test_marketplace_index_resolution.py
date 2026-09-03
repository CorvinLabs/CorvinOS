"""Marketplace index path resolution (ADR-0512 amendment, 2026-09-01).

ADR-0512 moved `operator/marketplace/` out of CorvinOS into the sibling
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


def test_legacy_in_repo_index_wins_over_sibling(tmp_path, monkeypatch):
    legacy = tmp_path / "operator" / "marketplace" / "index" / "plugins.json"
    _write_index(legacy, "from-legacy")
    monkeypatch.chdir(tmp_path)
    assert resolve_index_path() == legacy


def test_falls_back_to_sibling_checkout(tmp_path, monkeypatch):
    """The normal developer layout after ADR-0512: no in-repo index at all."""
    monkeypatch.chdir(tmp_path)  # no operator/marketplace here
    resolved = resolve_index_path()
    sibling = Path(
        __import__("corvin_console.routes.marketplace", fromlist=["x"]).__file__
    ).resolve().parents[4].parent / "Corvin-Marketplace" / "index" / "plugins.json"
    if sibling.is_file():
        assert resolved == sibling
    else:
        # No sibling checkout on this machine — resolution must still return the
        # legacy path so the caller's FileNotFoundError branch reaches GitHub.
        assert resolved == tmp_path / "operator" / "marketplace" / "index" / "plugins.json"


def test_missing_everything_degrades_to_empty_index(tmp_path, monkeypatch):
    """A resolvable-but-absent index must not raise; the API returns count 0."""
    monkeypatch.chdir(tmp_path)
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
