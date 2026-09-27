"""ADR-2091: removed engine ids (Hermes / local Ollama) are mapped, never rejected.

``engine_registry.normalize_legacy_engine_id`` is the one shared helper the
bridge and the console both use to read a stored ``default_engine`` /
``worker_engine`` / per-chat pin / ``/engine`` argument.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import engine_registry as er  # type: ignore  # noqa: E402

LEGACY = [
    "hermes", "HERMES", "  hermes  ", "hermes-fast", "hermes-large",
    "Hermes-Capable", "local", "local-hermes", "ollama", "Ollama",
    "opencode_ollama", "claude_code_local", "hermes_engine",
]


@pytest.fixture(autouse=True)
def _reset_warned():
    er._legacy_warned.clear()
    yield
    er._legacy_warned.clear()


@pytest.mark.parametrize("value", LEGACY)
def test_legacy_ids_map_to_claude_code(value):
    assert er.normalize_legacy_engine_id(value) == "claude_code"
    assert er.is_legacy_engine_id(value)


@pytest.mark.parametrize("value", [
    "claude_code", "codex_cli", "opencode", "copilot", "ollama_cloud",
    "cloud", "gemini", "", "my-custom-engine",
])
def test_other_values_unchanged(value):
    assert er.normalize_legacy_engine_id(value) == value
    assert not er.is_legacy_engine_id(value)


def test_none_stays_none():
    assert er.normalize_legacy_engine_id(None) is None


def test_warns_once_per_value_per_process(caplog):
    caplog.set_level(logging.WARNING, logger=er.logger.name)
    for _ in range(3):
        er.normalize_legacy_engine_id("hermes")
    er.normalize_legacy_engine_id("ollama")
    msgs = [r.getMessage() for r in caplog.records
            if "engine.legacy_mapped" in r.getMessage()]
    assert len(msgs) == 2
    assert all(r.levelno == logging.WARNING for r in caplog.records)


def test_registry_no_longer_builds_hermes():
    assert "hermes" not in er.list_engine_ids(available_only=False)


def test_get_engine_does_not_map_legacy_ids():
    # An allow-list naming only `hermes` must not silently admit claude_code.
    assert er.get_engine("hermes") is None


def test_resolve_engine_id_maps_legacy_profile(monkeypatch):
    monkeypatch.delenv("CORVIN_DEFAULT_ENGINE", raising=False)
    assert er.resolve_engine_id({"default_engine": "hermes"}) == "claude_code"
    monkeypatch.setenv("CORVIN_DEFAULT_ENGINE", "ollama")
    assert er.resolve_engine_id(None) == "claude_code"


def test_make_factory_maps_legacy_default():
    f = er.make_factory("hermes")
    assert f.corvin_default_engine == "claude_code"
