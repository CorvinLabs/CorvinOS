#!/usr/bin/env python3
"""test_adapter_piper_parity.py — bridge TTS parity with say.py.

Review findings (2026-07-20):

V4 (LOW/MEDIUM): the bridge Piper twin (_try_piper_tts) still had two bugs
already fixed in say.py:
  (a) model resolution: a silent "lang_default → any configured model"
      fallback without the stem table, and CORVIN_PIPER_MODEL_DE used as a
      fallback for EVERY language (wrong-language speech);
  (b) binary resolution: only PIPER_BIN / which("piper") — no
      which("piper-tts") and no interpreter-neighbor tier.
The semantics are ported from say.py::_resolve_piper_binary and
say.py::_piper_model_for (see the reference comments in adapter.py).

V5 (LOW): the bridge OpenAI-TTS tier rejected every key without an 'sk-'
prefix; say.py and the console accept such keys (proxy/org keys).
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import adapter  # type: ignore


# ── V5: OpenAI key prefix parity ─────────────────────────────────────────────

def test_openai_tts_accepts_non_sk_prefixed_key(monkeypatch):
    """Proxy/org keys without the 'sk-' prefix must reach the OpenAI client —
    parity with say.py and the console (V5)."""
    seen_keys: list[str] = []

    class _FakeOpenAI:
        def __init__(self, api_key=None, **_kw):
            seen_keys.append(api_key)
            raise RuntimeError("stop after key acceptance — no network")

    fake_openai = types.ModuleType("openai")
    fake_openai.OpenAI = _FakeOpenAI
    monkeypatch.setitem(sys.modules, "openai", fake_openai)
    monkeypatch.delenv("CORVIN_TTS_LOCAL_ONLY", raising=False)
    monkeypatch.setattr(
        adapter._provider_keys, "resolve_key", lambda name: "proxy-org-key-123"
    )
    monkeypatch.setitem(adapter._voice_engine_state, "quota_until", 0.0)

    result = adapter._try_openai_tts("hello world", "en", None)

    assert result is None  # the fake client raises — fail path is fine
    assert seen_keys == ["proxy-org-key-123"], (
        "a non-'sk-' key must not be silently discarded before the client call"
    )


# ── V4b: Piper binary resolution parity ──────────────────────────────────────

def test_piper_binary_env_ignored_when_path_does_not_exist(monkeypatch, tmp_path):
    monkeypatch.setenv("PIPER_BIN", str(tmp_path / "does-not-exist"))
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr(adapter.sys, "executable", str(tmp_path / "python"))
    assert adapter._resolve_piper_binary() is None


def test_piper_binary_env_wins_when_it_exists(monkeypatch, tmp_path):
    exe = tmp_path / "custom-piper"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("PIPER_BIN", str(exe))
    monkeypatch.setattr("shutil.which", lambda name: "/should/not/be/used")
    assert adapter._resolve_piper_binary() == str(exe)


def test_piper_binary_falls_back_to_piper_tts_name(monkeypatch, tmp_path):
    """uv/pipx installs ship the binary as `piper-tts` — parity with say.py."""
    exe = tmp_path / "piper-tts"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.delenv("PIPER_BIN", raising=False)
    monkeypatch.setattr(
        "shutil.which", lambda name: str(exe) if name == "piper-tts" else None
    )
    assert adapter._resolve_piper_binary() == str(exe)


def test_piper_binary_interpreter_neighbor_tier(monkeypatch, tmp_path):
    """PATH-stripped service environments: the binary sitting next to the
    Python interpreter (venv/bin) must be found — parity with say.py."""
    venv_bin = tmp_path / "venv-bin"
    venv_bin.mkdir()
    (venv_bin / "piper").write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.delenv("PIPER_BIN", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr(adapter.sys, "executable", str(venv_bin / "python"))
    assert adapter._resolve_piper_binary() == str(venv_bin / "piper")


# ── V4a: Piper model resolution parity ───────────────────────────────────────

@pytest.fixture()
def _voice_cfg(monkeypatch, tmp_path):
    """Point the adapter's voice-config dir + model dir at tmp, env clean."""
    monkeypatch.setattr(adapter, "_VOICE_CONFIG_DIR", tmp_path)
    models = tmp_path / "piper-models"
    models.mkdir()
    monkeypatch.setenv("CORVIN_PIPER_MODEL_DIR", str(models))
    for lang in ("DE", "EN", "EN_US"):
        monkeypatch.delenv(f"CORVIN_PIPER_MODEL_{lang}", raising=False)
    return tmp_path, models


def test_de_env_model_is_not_a_fallback_for_english(_voice_cfg, monkeypatch, tmp_path):
    """The old code used CORVIN_PIPER_MODEL_DE as fallback for EVERY language —
    wrong-language speech. An English request must not resolve to it."""
    cfg_dir, _models = _voice_cfg
    de_model = tmp_path / "de.onnx"
    de_model.write_text("x", encoding="utf-8")
    monkeypatch.setenv("CORVIN_PIPER_MODEL_DE", str(de_model))
    assert adapter._piper_model_for("en") is None


def test_config_exact_lang_and_primary_tag(_voice_cfg):
    cfg_dir, _models = _voice_cfg
    en_model = cfg_dir / "en.onnx"
    en_model.write_text("x", encoding="utf-8")
    (cfg_dir / "config.json").write_text(
        json.dumps({"piper_model_en": str(en_model)}), encoding="utf-8"
    )
    assert adapter._piper_model_for("en") == str(en_model)
    # BCP-47 sub-tag falls back to its primary tag ("en-us" → "en").
    assert adapter._piper_model_for("en-us") == str(en_model)


def test_stem_table_model_beats_wrong_language_config(_voice_cfg):
    """The old code had NO stem-table tier: with only a German model in
    config.json, an English request silently spoke German even when the
    installer-named English model sat on disk."""
    cfg_dir, models = _voice_cfg
    en_model = models / "en_US-lessac-medium.onnx"
    en_model.write_text("x", encoding="utf-8")
    de_model = cfg_dir / "de.onnx"
    de_model.write_text("x", encoding="utf-8")
    (cfg_dir / "config.json").write_text(
        json.dumps({"piper_model_de": str(de_model)}), encoding="utf-8"
    )
    assert adapter._piper_model_for("en") == str(en_model)


def test_last_resort_cross_language_fallback_is_logged(_voice_cfg, monkeypatch):
    """The any-configured-model fallback survives as the LAST tier only, and
    it must be logged (visible degradation) — parity with say.py."""
    cfg_dir, _models = _voice_cfg
    de_model = cfg_dir / "de.onnx"
    de_model.write_text("x", encoding="utf-8")
    (cfg_dir / "config.json").write_text(
        json.dumps({"lang_default": "de", "piper_model_de": str(de_model)}),
        encoding="utf-8",
    )
    lines: list[str] = []
    monkeypatch.setattr(adapter, "log", lambda *a: lines.append(" ".join(map(str, a))))
    assert adapter._piper_model_for("en") == str(de_model)
    assert any("wrong-language" in ln for ln in lines), (
        f"cross-language fallback must be logged, got: {lines!r}"
    )


def test_stem_table_matches_say_py_ssot():
    """VOICE-6 SSOT: the bridge stem table must be byte-identical to say.py's
    (which in turn is guarded against installer/steps/piper.py::_MODELS)."""
    say_path = ROOT.parent.parent / "voice" / "scripts" / "say.py"
    text = say_path.read_text(encoding="utf-8")
    import ast

    say_models = None
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if getattr(tgt, "id", None) == "_PIPER_MODELS":
                    say_models = ast.literal_eval(node.value)
        elif isinstance(node, ast.AnnAssign):  # `_PIPER_MODELS: dict[...] = {...}`
            if getattr(node.target, "id", None) == "_PIPER_MODELS" and node.value:
                say_models = ast.literal_eval(node.value)
    assert say_models, "could not extract _PIPER_MODELS from say.py"
    assert adapter._PIPER_MODELS == say_models


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
