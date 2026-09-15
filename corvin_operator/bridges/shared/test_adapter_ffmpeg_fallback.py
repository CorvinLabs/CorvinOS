#!/usr/bin/env python3
"""test_adapter_ffmpeg_fallback.py — TTS must not depend on a manually
installed system ffmpeg.

Reproduces a 2026-07-06 Windows-10 incident report: STT AND TTS both
failed on a fresh Windows install. Root causes found and fixed here:

  1. `_try_edge_tts` called `asyncio.wait_for` / `asyncio.run`, but
     `adapter.py` never imported `asyncio` at module level — every
     edge-tts call raised a NameError, silently caught and logged
     ("edge TTS: synthesis failed: name 'asyncio' is not defined"),
     on EVERY platform, not just Windows. edge-tts is the API-key-free
     fallback engine, so operators without an OpenAI key/quota got no
     voice notes at all.
  2. edge-tts and Piper both need ffmpeg to convert their MP3/WAV
     output to OGG-Opus, but installer/steps/dependencies.py explicitly
     skips installing system ffmpeg on Windows. `_resolve_ffmpeg_bin()`
     now falls back to the bundled `imageio-ffmpeg` static binary (a
     pure-Python dependency with prebuilt Windows/Linux/macOS wheels)
     when no system ffmpeg is on PATH.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import adapter  # type: ignore


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def test_asyncio_is_a_real_module_attribute() -> None:
    """Regression guard for the silent NameError in _try_edge_tts."""
    _section("adapter.asyncio is imported (not a NameError waiting to happen)")
    import asyncio as _asyncio_stdlib
    assert adapter.asyncio is _asyncio_stdlib
    print("  OK — adapter.py imports asyncio at module level")


def test_ffmpeg_bin_prefers_env_override() -> None:
    _section("FFMPEG_BIN env override wins over PATH/imageio-ffmpeg")
    saved = os.environ.get("FFMPEG_BIN")
    os.environ["FFMPEG_BIN"] = "/explicit/ffmpeg"
    try:
        assert adapter._resolve_ffmpeg_bin() == "/explicit/ffmpeg"
    finally:
        if saved is None:
            os.environ.pop("FFMPEG_BIN", None)
        else:
            os.environ["FFMPEG_BIN"] = saved
    print("  OK — explicit FFMPEG_BIN honoured")


def test_ffmpeg_bin_falls_back_to_imageio_ffmpeg_without_system_ffmpeg() -> None:
    _section("no system ffmpeg on PATH → bundled imageio-ffmpeg binary used")
    saved_ffmpeg_bin = os.environ.get("FFMPEG_BIN")
    saved_path = os.environ.get("PATH")
    os.environ.pop("FFMPEG_BIN", None)
    os.environ["PATH"] = "/nonexistent-dir-for-test"
    try:
        found = adapter._resolve_ffmpeg_bin()
        assert found is not None, (
            "expected a bundled imageio-ffmpeg fallback binary, got None — "
            "is imageio-ffmpeg installed? (pyproject.toml base dependency)"
        )
        assert os.path.isfile(found), f"resolved ffmpeg path doesn't exist: {found}"
    finally:
        if saved_ffmpeg_bin is None:
            os.environ.pop("FFMPEG_BIN", None)
        else:
            os.environ["FFMPEG_BIN"] = saved_ffmpeg_bin
        os.environ["PATH"] = saved_path
    print(f"  OK — fell back to {found}")


def test_edge_tts_produces_real_audio_without_system_ffmpeg() -> None:
    """End-to-end: edge-tts must synthesize real audio using ONLY the
    bundled ffmpeg fallback — no system ffmpeg, no API key.
    """
    _section("edge-tts E2E with no system ffmpeg on PATH")
    saved_ffmpeg_bin = os.environ.get("FFMPEG_BIN")
    saved_path = os.environ.get("PATH")
    os.environ.pop("FFMPEG_BIN", None)
    os.environ["PATH"] = "/nonexistent-dir-for-test"
    out_path = None
    try:
        out_path = adapter._try_edge_tts("Hallo, das ist ein Test.", "de")
        assert out_path is not None, "edge-tts returned None — see adapter log"
        assert out_path.exists() and out_path.stat().st_size > 0
    finally:
        if out_path is not None:
            out_path.unlink(missing_ok=True)
        if saved_ffmpeg_bin is None:
            os.environ.pop("FFMPEG_BIN", None)
        else:
            os.environ["FFMPEG_BIN"] = saved_ffmpeg_bin
        os.environ["PATH"] = saved_path
    print(f"  OK — synthesized {out_path}")


if __name__ == "__main__":
    test_asyncio_is_a_real_module_attribute()
    test_ffmpeg_bin_prefers_env_override()
    test_ffmpeg_bin_falls_back_to_imageio_ffmpeg_without_system_ffmpeg()
    test_edge_tts_produces_real_audio_without_system_ffmpeg()
    print("\nAll ffmpeg-fallback tests passed.")
