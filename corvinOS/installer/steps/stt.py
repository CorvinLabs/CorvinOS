"""Local STT (pywhispercpp): install speech-to-text engine + GGML model."""
from __future__ import annotations

from pathlib import Path

from .dependencies import pip_install as _pip_install


# Keep in sync with operator/voice/scripts/stt/local_whisper.py — the provider
# must load the exact model this step downloaded, or a fresh install pays a
# silent first-use download delay instead of the visible one below (ADR-0185
# Decision 3: models are fetched once during install, not on first use).
# Three RAM tiers (mirror the provider): `base-q5_1` < 3 GB, `small-q5_1`
# 3–16 GB (the quality default — `base` mis-transcribes German/accented audio),
# `medium-q5_0` ≥ 16 GB. Prefetching whatever `_default_model()` resolves keeps
# install-time download and runtime load on the SAME file.
# Mirror of operator/voice/scripts/stt/local_whisper.py's tier constants. The
# runtime provider is the Single Source of Truth (`_default_model()` below
# delegates to it); these locals are only the offline fallback used when the
# provider module can't be imported in this install layout. The test
# tests/test_stt_model_tiers.py fails if they ever drift apart.
_STT_MODEL_HIGH = "medium-q5_0"
_STT_MODEL_QUALITY = "small-q5_1"
_STT_MODEL_LOWRAM = "base-q5_1"
_STT_LOWRAM_THRESHOLD_MB = 3000
_STT_HIGHRAM_THRESHOLD_MB = 16000
_STT_HIGH_MIN_CPUS = 8


def _provider_default_model() -> "str | None":
    """Ask the runtime STT provider which model this host should prefetch —
    the Single Source of Truth, so install-time download and first-use load
    can never target different files. Returns None if the provider module
    isn't importable in this install layout (then the caller falls back to the
    self-contained RAM check below)."""
    import os
    import sys
    try:
        from corvin_console._operator_bootstrap import ensure_operator_on_path
        ensure_operator_on_path()
    except Exception:  # noqa: BLE001
        pass
    # Repo layout: <root>/corvinOS/installer/steps/ → <root>/operator/voice/scripts
    scripts = os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", "..", "operator", "voice", "scripts",
    ))
    if os.path.isdir(scripts) and scripts not in sys.path:
        sys.path.insert(0, scripts)
    try:
        from stt.local_whisper import _default_local_model  # type: ignore
    except Exception:  # noqa: BLE001
        return None
    try:
        model = _default_local_model()
    except Exception:  # noqa: BLE001
        return None
    return model or None


def _default_model() -> str:
    """RAM-adaptive default model. Delegates to the provider SSOT; the
    self-contained 3-tier RAM check below is only the offline fallback."""
    model = _provider_default_model()
    if model:
        return model
    # Fallback: self-contained RAM detection (no cross-package import). Kept
    # in lock-step with the provider's ladder via the mirror test above.
    ram_mb = None
    try:
        import os
        if hasattr(os, "sysconf") and "SC_PHYS_PAGES" in os.sysconf_names:
            ram_mb = int(os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024))
    except (ValueError, OSError, AttributeError):
        ram_mb = None
    if ram_mb is None:
        try:
            import ctypes

            class _MEMSTAT(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            stat = _MEMSTAT()
            stat.dwLength = ctypes.sizeof(_MEMSTAT)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):  # type: ignore[attr-defined]
                ram_mb = int(stat.ullTotalPhys / (1024 * 1024))
        except (AttributeError, OSError):
            ram_mb = None
    if ram_mb is None:
        return _STT_MODEL_QUALITY
    if ram_mb < _STT_LOWRAM_THRESHOLD_MB:
        return _STT_MODEL_LOWRAM
    # Mirror the provider's RAM+CPU gate for the heavy tier (this fallback runs
    # only when the provider module can't be imported; it omits cgroup-awareness
    # but keeps the CPU gate so a many-RAM/few-core box still prefetches small).
    try:
        import os as _os2
        cpus = _os2.cpu_count() or 1
    except Exception:  # noqa: BLE001
        cpus = 1
    if ram_mb >= _STT_HIGHRAM_THRESHOLD_MB and cpus >= _STT_HIGH_MIN_CPUS:
        return _STT_MODEL_HIGH
    return _STT_MODEL_QUALITY


# Back-compat alias for callers/tests referencing the module constant.
_DEFAULT_MODEL = _STT_MODEL_QUALITY


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

    _download_whisper_model(voice_config_dir, _default_model())


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

    print(f"  Downloading Whisper STT model {model_name!r} (one-time)...")
    # The transfer itself is quick, but on a slow/high-latency link the
    # connection+redirect handshake can sit silently for a minute or two before
    # the progress bar appears — which reads as a hang to a non-technical user.
    # Set expectations up front (ADR-0185 Decision 3: visible progress).
    print("    (this can take a minute on a slow connection — it only happens once)")
    import sys as _sys
    _sys.stdout.flush()
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
