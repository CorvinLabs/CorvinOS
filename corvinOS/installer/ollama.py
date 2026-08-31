"""Ollama integration for CorvinOS installer.

Handles special paths and configuration when running via Ollama.
"""

import os
from pathlib import Path


def is_ollama_env() -> bool:
    """Detect if running inside Ollama environment."""
    return os.environ.get("CORVIN_INSTALLED_VIA_OLLAMA") == "1"


def ollama_corvin_home() -> Path:
    """Return Corvin home for Ollama context (if special handling needed)."""
    # For now, use standard ~/.corvin/
    # Ollama sandboxing might require different paths in future
    return Path.home() / ".corvin"


def ollama_voice_config_dir() -> Path:
    """Return voice config for Ollama context (SSOT — path-audit 2026-07-06)."""
    from corvinOS.shared.paths import voice_config_dir
    return voice_config_dir()


def warn_ollama_limits() -> None:
    """Print warnings about Ollama-specific limitations."""
    if not is_ollama_env():
        return

    print("\n⚠ Ollama Installation Notes:")
    print("  - Models will run inside Ollama's sandbox")
    print("  - Persistent data in ~/.corvin/ will survive Ollama restarts")
    print("  - API access requires Ollama bridge configuration")


def setup_ollama_env(installer) -> None:
    """Apply Ollama-specific configuration to installer."""
    if not is_ollama_env():
        return

    print("\n[Ollama] Applying Ollama-specific configuration...")

    # Override default bridge selection for Ollama
    # (may only want local bridges in Ollama context)
    installer.selected_bridges = ["discord", "whatsapp"]  # Popular ones
    print(f"  Selected bridges for Ollama: {', '.join(installer.selected_bridges)}")

    # Set environment variable for child processes
    os.environ["CORVIN_INSTALLED_VIA_OLLAMA"] = "1"
