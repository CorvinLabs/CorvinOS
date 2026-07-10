#!/usr/bin/env python3
"""test_secrets_ssot.py — cross-consumer guard for provider-key resolution.

WA-22 audit (2026-07-10): the same logical value (e.g. "the OpenAI key used
for STT") was independently resolved by say.py, stt/openai_whisper.py, and
the console's byok.py/setup.py routes — divergent precedence orders and
candidate-file lists (some scanned a second, independently-drifting `.env`
file that nothing programmatic wrote to). This guard imports every resolver
that answers "what is provider key X" and asserts they return the IDENTICAL
value under the same environment/file fixtures. If any consumer drifts
again, this test fails.

The canonical rule (operator/bridges/shared/provider_keys.py): process env
(dedicated name, then general, then legacy alias) → service.env file in the
same order. The second `.env` file is retired — nothing reads or writes it
post-consolidation.
"""
from __future__ import annotations

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

import provider_keys as canonical  # type: ignore  # operator/bridges/shared/provider_keys.py


def _clean_env(**overrides: str) -> dict:
    import os
    keys_to_strip = (
        "VOICE_CONFIG_DIR", "XDG_CONFIG_HOME",
        "CORVIN_TTS_OPENAI_KEY", "CORVIN_STT_OPENAI_KEY",
        "OPENAI_API_KEY", "OPENAI_APIKEY",
    )
    env = {k: v for k, v in os.environ.items() if k not in keys_to_strip}
    env.update(overrides)
    return env


def _resolve_all(key_name: str, tmp_path: Path, **env_overrides: str) -> dict[str, str | None]:
    """Resolve *key_name* through every independent implementation under the
    same fixture: tmp_path as VOICE_CONFIG_DIR + given env overrides."""
    env = _clean_env(VOICE_CONFIG_DIR=str(tmp_path), **env_overrides)

    with mock.patch.dict("os.environ", env, clear=True):
        results: dict[str, str | None] = {
            "secrets.resolve_key": canonical.resolve_key(key_name),
        }

        import say  # type: ignore
        say.VOICE_CONFIG_DIR = tmp_path
        if key_name == "tts_openai_api_key":
            results["say._resolve_key"] = say._resolve_key()

        from stt import openai_whisper  # type: ignore
        openai_whisper._VOICE_CONFIG_DIR = tmp_path
        if key_name == "stt_openai_api_key":
            results["stt.openai_whisper._resolve_api_key"] = openai_whisper._resolve_api_key()

    return results


def _assert_all_agree(results: dict[str, str | None]) -> None:
    distinct = set(results.values())
    assert len(distinct) == 1, (
        "provider-key resolvers disagree (SSOT drift):\n"
        + "\n".join(f"  {n}: {v!r}" for n, v in results.items())
    )


def test_tts_key_env_dedicated_wins():
    results = _resolve_all(
        "tts_openai_api_key", Path("/tmp/unused"),
        CORVIN_TTS_OPENAI_KEY="sk-tts-dedicated",
        OPENAI_API_KEY="sk-general",
    )
    _assert_all_agree(results)
    assert results["secrets.resolve_key"] == "sk-tts-dedicated"


def test_tts_key_falls_back_to_general_env():
    results = _resolve_all(
        "tts_openai_api_key", Path("/tmp/unused"),
        OPENAI_API_KEY="sk-general-only",
    )
    _assert_all_agree(results)
    assert results["secrets.resolve_key"] == "sk-general-only"


def test_tts_key_file_fallback(tmp_path):
    (tmp_path / "service.env").write_text("CORVIN_TTS_OPENAI_KEY=sk-from-file\n")
    results = _resolve_all("tts_openai_api_key", tmp_path)
    _assert_all_agree(results)
    assert results["secrets.resolve_key"] == "sk-from-file"


def test_tts_key_none_configured_returns_none(tmp_path):
    results = _resolve_all("tts_openai_api_key", tmp_path)
    _assert_all_agree(results)
    assert results["secrets.resolve_key"] is None


def test_stt_key_env_dedicated_wins():
    results = _resolve_all(
        "stt_openai_api_key", Path("/tmp/unused"),
        CORVIN_STT_OPENAI_KEY="sk-stt-dedicated",
        OPENAI_API_KEY="sk-general",
    )
    _assert_all_agree(results)
    assert results["secrets.resolve_key"] == "sk-stt-dedicated"


def test_stt_key_falls_back_to_general_env():
    results = _resolve_all(
        "stt_openai_api_key", Path("/tmp/unused"),
        OPENAI_API_KEY="sk-general-only",
    )
    _assert_all_agree(results)
    assert results["secrets.resolve_key"] == "sk-general-only"


def test_stt_key_file_fallback(tmp_path):
    (tmp_path / "service.env").write_text("CORVIN_STT_OPENAI_KEY=sk-from-file\n")
    results = _resolve_all("stt_openai_api_key", tmp_path)
    _assert_all_agree(results)
    assert results["secrets.resolve_key"] == "sk-from-file"


def test_retired_dotenv_file_is_no_longer_consulted(tmp_path):
    """The second `.env` file is retired — a value living ONLY there must
    no longer surface, for any implementation. (Pre-consolidation, say.py /
    stt/openai_whisper.py both scanned it; a real install had it diverge
    from service.env with a stale/wrong value.)"""
    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-stale-in-retired-file\n")
    results = _resolve_all("tts_openai_api_key", tmp_path)
    _assert_all_agree(results)
    assert results["secrets.resolve_key"] is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
