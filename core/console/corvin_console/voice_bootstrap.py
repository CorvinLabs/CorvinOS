"""Zero-config voice initialization — download Piper models on first startup.

Runs at console boot (not request time) to ensure Piper TTS is available
without requiring manual setup or complex package dependencies.
"""
import logging
import os
import sys
import tempfile
from pathlib import Path

_log = logging.getLogger(__name__)

# Piper models directory (same as voice.py uses)
_PIPER_MODELS_DIR = Path.home() / ".config" / "corvin-voice" / "piper-models"

# Supported languages + model metadata
PIPER_MODELS = {
    "de": {
        "model": "de_DE-kerstin-low.onnx",
        "config": "de_DE-kerstin-low.onnx.json",
        "url_base": "https://github.com/rhasspy/piper/releases/download/2023.11.14-2/",
        "size_mb": 63,
    },
    "en": {
        "model": "en_US-lessac-medium.onnx",
        "config": "en_US-lessac-medium.onnx.json",
        "url_base": "https://github.com/rhasspy/piper/releases/download/2023.11.14-2/",
        "size_mb": 100,
    },
}


def _download_piper_model(lang: str, model_info: dict) -> bool:
    """Download a single Piper model if not already present. Returns True on success."""
    model_path = _PIPER_MODELS_DIR / model_info["model"]
    config_path = _PIPER_MODELS_DIR / model_info["config"]

    if model_path.exists() and config_path.exists():
        return True  # Already have it

    _PIPER_MODELS_DIR.mkdir(parents=True, exist_ok=True)

    try:
        import urllib.request
        import ssl

        # Disable SSL verification for GitHub (workaround for test environments)
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        # Download model + config
        for fname in [model_info["model"], model_info["config"]]:
            url = model_info["url_base"] + fname
            dest = _PIPER_MODELS_DIR / fname

            _log.info(f"Downloading {fname} ({model_info['size_mb']}MB)...")
            try:
                urllib.request.urlopen(url, context=ctx)
                urllib.request.urlretrieve(url, dest, context=ctx)
                _log.info(f"✓ {fname} downloaded")
            except Exception as e:
                _log.warning(f"Failed to download {fname}: {e}")
                return False

        return True
    except Exception as e:
        _log.warning(f"Piper model download failed for {lang}: {e}")
        return False


def bootstrap_voice_models() -> dict[str, bool]:
    """Ensure Piper models are available. Returns {lang: success} dict.

    Called at console startup. Best-effort; a failure here is non-fatal
    (say.py has edge/OpenAI fallbacks). Logs all failures for debugging.
    """
    if not os.environ.get("CORVIN_VOICE_BOOTSTRAP_ENABLED", "1") == "1":
        _log.debug("Voice bootstrap disabled (CORVIN_VOICE_BOOTSTRAP_ENABLED=0)")
        return {}

    results = {}
    for lang, model_info in PIPER_MODELS.items():
        try:
            results[lang] = _download_piper_model(lang, model_info)
        except Exception as e:
            _log.error(f"Unexpected error bootstrapping {lang}: {e}")
            results[lang] = False

    available = [lang for lang, ok in results.items() if ok]
    _log.info(f"Voice bootstrap complete — Piper models available: {available or 'none'}")
    return results


def ensure_piper_available() -> bool:
    """Quick check: are German + English Piper models present?

    Returns True if at least German is available (minimum for zero-config).
    Called at console startup; non-blocking, best-effort.
    """
    piper_de = _PIPER_MODELS_DIR / PIPER_MODELS["de"]["model"]
    return piper_de.exists()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    bootstrap_voice_models()
