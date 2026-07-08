"""Local STT (pywhispercpp): install speech-to-text engine + GGML model."""
from __future__ import annotations

from pathlib import Path

from .dependencies import pip_install as _pip_install


# Keep in sync with operator/voice/scripts/stt/local_whisper.py::_DEFAULT_MODEL —
# the provider must load the exact model this step downloaded, or a fresh
# install pays a silent first-use download delay instead of the visible one
# below (ADR-0185 Decision 3: models are fetched once during install, not on
# first use).
_DEFAULT_MODEL = "tiny-q5_1"


def ensure_stt(voice_config_dir: Path, interactive: bool = True) -> None:
    """Ensure local STT is available on every platform (ADR-0185 M1).

    ``pywhispercpp`` (a whisper.cpp binding) replaces ``faster-whisper`` as
    the canonical, cross-platform local STT engine — it ships genuine
    Windows wheels with no ``av``/``torch``/``ctranslate2`` dependency, so
    this step is now identical on Linux, macOS, and Windows: no more
    platform split.

    Two sub-steps:
      1. Verify ``pywhispercpp`` imports. It is a base dependency of
         ``corvinos``, so this is normally a no-op; the defensive
         pip-install below only matters for ``--no-deps`` installs or a
         partially-broken environment.
      2. Download the default quantized GGML model with visible progress
         (ADR-0185 Decision 3) so first real use never triggers a surprise
         download. Degrades gracefully on no network — never aborts the
         rest of ``corvin-install``.
    """
    try:
        import pywhispercpp  # noqa: F401
        print("  ✓ pywhispercpp available (local STT, no API key needed)")
    except ImportError:
        print("  Installing pywhispercpp for local voice input (no API key needed)...")
        installed = _pip_install("pywhispercpp")
        try:
            import pywhispercpp  # noqa: F401
            print("  ✓ pywhispercpp installed")
        except ImportError:
            if installed:
                print("  ⚠ pywhispercpp installed but not importable — re-open your shell")
            else:
                print("  ⚠ Could not install pywhispercpp. Voice input needs OpenAI API key.")
                print("    Manual fix: pip install pywhispercpp")
            return

    _download_whisper_model(voice_config_dir, _DEFAULT_MODEL)


# ── Model download ───────────────────────────────────────────────────────────


def _download_whisper_model(voice_config_dir: Path, model_name: str) -> bool:
    """Download the GGML model ``model_name`` into
    ``voice_config_dir/whisper-models`` using pywhispercpp's own
    downloader (``pywhispercpp.utils.download_model``) — it already
    streams with a progress bar and writes the exact filename
    ``local_whisper.py``'s ``Model(...)`` resolves by name, so hand-rolling
    a second fetch path (like Piper's 4-tier ``_fetch()``) would only add a
    duplicate, divergence-prone code path for no benefit.

    Returns True when the model file exists on disk afterwards (whether it
    was just downloaded or was already present). Never raises — any error
    (typically: no network) prints a clear "will retry next run" message
    and returns False so the caller (and the rest of corvin-install)
    continues unaffected.
    """
    model_dir = voice_config_dir / "whisper-models"
    model_dir.mkdir(parents=True, exist_ok=True)
    dest = model_dir / f"ggml-{model_name}.bin"

    if dest.exists() and dest.stat().st_size > 0:
        print(f"  ✓ Whisper STT model already present: {dest.name}")
        return True

    print(f"  Downloading Whisper STT model {model_name!r} (~31 MB, one-time)...")
    try:
        from pywhispercpp.utils import download_model
        download_model(model_name, download_dir=str(model_dir))
    except Exception as exc:  # noqa: BLE001
        print(f"  ⚠ Whisper model download failed ({exc})")
        print("    Will retry automatically on the next corvin-install run.")
        print("    Voice input falls back to OpenAI Whisper (needs an API key) until then.")
        return False

    if dest.exists() and dest.stat().st_size > 0:
        print(f"  ✓ Downloaded {dest.name}")
        return True

    print("  ⚠ Whisper model download did not produce a file — no network?")
    print("    Will retry automatically on the next corvin-install run.")
    return False
