#!/usr/bin/env python3
"""test_voice_config_ssot.py — cross-consumer guard for the corvin-voice config dir.

path-audit 2026-07-06: two independent reviewers found the voice-config directory
was resolved by *four* divergent rules. The console engines page + bridge_manager
wrote keys to one dir (Windows %APPDATA%\\Local, or $XDG_CONFIG_HOME) while the
installer key step and the voice STT/TTS scripts read another (hardcoded
~/.config), so the 0.10.18 Windows STT key fallback read a dir nothing wrote.

This guard imports every resolver that answers "where is the corvin-voice config
dir" and asserts they return the IDENTICAL path under the same environment on
every platform. If any consumer drifts again, this test fails.

The canonical rule (all consumers): VOICE_CONFIG_DIR → XDG_CONFIG_HOME → ~/.config,
uniform on Linux/macOS/Windows.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

_REPO = Path(__file__).resolve().parents[1]
for _p in (
    _REPO,
    _REPO / "operator" / "forge",
    _REPO / "operator" / "bridges",
    _REPO / "operator" / "bridges" / "shared",
    _REPO / "operator" / "voice" / "scripts",
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def _resolvers():
    """Return {name: callable} for every voice-config-dir resolver in the tree.

    Each callable takes no args and reads the live environment, so tests set env
    first and call after (the module-level snapshots in say.py/openai_whisper.py
    are recomputed via their _resolve_voice_config_dir() function, not the frozen
    constant)."""
    resolvers: dict[str, object] = {}

    from forge import paths as forge_paths  # type: ignore
    resolvers["forge.paths"] = forge_paths.voice_config_dir

    from corvinOS.shared import paths as shared_paths  # type: ignore
    resolvers["corvinOS.shared.paths"] = shared_paths.voice_config_dir

    import adapter  # type: ignore
    resolvers["adapter"] = adapter._resolve_voice_config_dir

    import say  # type: ignore
    resolvers["say"] = say._resolve_voice_config_dir

    from stt import openai_whisper  # type: ignore
    resolvers["stt.openai_whisper"] = openai_whisper._resolve_voice_config_dir

    # bridge_manager merges service.env into every spawned daemon env — it is a
    # first-class consumer and was the 6th divergent resolver (round-2 finding).
    import importlib
    bm = importlib.import_module("bridge_manager")  # operator/bridges on sys.path
    resolvers["bridge_manager"] = bm._voice_config_dir

    # profile/memory/vault stores (V-2 audit finding 2026-07-12): these three
    # resolved XDG-only and ignored the VOICE_CONFIG_DIR pin, so a pinned
    # launcher moved config/models but left profile/memory/BYOK-vault behind
    # (reader!=writer). They resolve a FILE/SUBDIR under the config dir, so
    # normalize back to the dir for the agreement check.
    import profile as voice_profile  # type: ignore  # bridges/shared on sys.path
    resolvers["profile"] = lambda: voice_profile._profile_path().parent

    import memory as voice_memory  # type: ignore
    resolvers["memory"] = lambda: voice_memory._memory_root().parent

    import vault as voice_vault  # type: ignore
    resolvers["vault"] = voice_vault._vault_root

    return resolvers


def _clean_env(**overrides: str) -> dict:
    env = {
        k: v for k, v in os.environ.items()
        if k not in ("APPDATA", "VOICE_CONFIG_DIR", "XDG_CONFIG_HOME")
    }
    env.update(overrides)
    return env


def _all_agree(**overrides: str) -> Path:
    resolvers = _resolvers()
    env = _clean_env(**overrides)
    results = {}
    with mock.patch.dict(os.environ, env, clear=True):
        for name, fn in resolvers.items():
            results[name] = fn()  # type: ignore[operator]
    distinct = set(str(p) for p in results.values())
    assert len(distinct) == 1, (
        "voice-config-dir resolvers disagree (SSOT drift):\n"
        + "\n".join(f"  {n}: {p}" for n, p in results.items())
    )
    return next(iter(results.values()))


def test_default_all_consumers_agree():
    result = _all_agree()
    assert result.name == "corvin-voice"
    assert result.parent.name == ".config"


def test_voice_config_dir_override_all_consumers_agree(tmp_path):
    pinned = tmp_path / "pinned-voice"
    result = _all_agree(VOICE_CONFIG_DIR=str(pinned))
    assert result == pinned


def test_xdg_config_home_all_consumers_agree(tmp_path):
    xdg = tmp_path / "xdg"
    result = _all_agree(XDG_CONFIG_HOME=str(xdg))
    assert result == xdg / "corvin-voice"


def test_appdata_does_not_split_windows(tmp_path):
    """A stray APPDATA must NOT change the resolution — proves the old
    %APPDATA%\\Local Windows split is gone from every consumer."""
    result = _all_agree(APPDATA=str(tmp_path / "AppData" / "Roaming"))
    assert "AppData" not in str(result)
    assert result.parent.name == ".config"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
