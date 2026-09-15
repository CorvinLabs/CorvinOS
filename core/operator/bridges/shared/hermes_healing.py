"""Hermes health detection and automated repair.

Part of the ACO (Autonomous Code Optimizer) L5 self-healing system (ADR-0178).
Detects when Ollama (the local engine fallback) is unavailable and performs
bounded, reversible, offline repairs:
  * Start the stopped Ollama server
  * Re-pull the configured model if missing
  * Verify reachability after each step

Never raises — errors are caught and logged. All operations are POSIX + Windows compatible.
"""
from __future__ import annotations

import json
import logging
import urllib.request
from typing import Optional

logger = logging.getLogger(__name__)

_OLLAMA_BASE_URL = "http://localhost:11434"


def is_hermes_reachable(base_url: str = _OLLAMA_BASE_URL, timeout: float = 2.0) -> bool:
    """True if Ollama is running and answering API requests."""
    try:
        with urllib.request.urlopen(f"{base_url}/api/tags", timeout=timeout) as resp:
            return getattr(resp, "status", 200) == 200
    except Exception:  # noqa: BLE001
        return False


def is_hermes_inference_ready(
    base_url: str = _OLLAMA_BASE_URL,
    timeout: float = 5.0,
    models: list[str] | None = None,
) -> bool:
    """True if Ollama can serve inference — not just respond to /api/tags.

    Sends a minimal 1-token generate request to detect the hung-but-alive
    case where /api/tags returns 200 but the inference engine is blocked.
    Uses the first available qwen3 model; falls back to False if none installed.
    """
    ms = models or get_available_models(base_url=base_url)
    model = next((m for m in ms if "qwen3" in m), None)
    if not model:
        return False
    try:
        data = json.dumps({
            "model": model,
            "prompt": "hi",
            "stream": False,
            "options": {"num_predict": 1},
        }).encode()
        req = urllib.request.Request(
            f"{base_url}/api/generate",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return getattr(resp, "status", 200) == 200
    except Exception:  # noqa: BLE001
        return False


def get_available_models(base_url: str = _OLLAMA_BASE_URL, timeout: float = 2.0) -> list[str]:
    """List installed Ollama model names. Empty list if Ollama is not reachable."""
    try:
        with urllib.request.urlopen(f"{base_url}/api/tags", timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return [m.get("name", "") for m in data.get("models", [])]
    except Exception:  # noqa: BLE001
        return []


def has_hermes_model(models: Optional[list[str]] = None,
                     base_url: str = _OLLAMA_BASE_URL) -> bool:
    """True if any qwen3 model (Hermes default) is installed. Lazy-loads models list."""
    ms = models or get_available_models(base_url=base_url)
    return any("qwen3" in m for m in ms)


def get_health_status() -> dict[str, bool]:
    """Return a health dict: {reachable, inference_ok, has_model, models}."""
    reachable = is_hermes_reachable()
    models = get_available_models() if reachable else []
    inference_ok = is_hermes_inference_ready(models=models) if reachable else False
    return {
        "reachable": reachable,
        "inference_ok": inference_ok,
        "has_model": has_hermes_model(models),
        "model_count": len(models),
        "models": models,
    }


def repair_hermes(timeout_server: float = 30.0,
                  timeout_pull: float = 600.0) -> dict[str, bool | str]:
    """Attempt to repair Hermes: start server, pull model if needed.

    Returns a dict with keys:
      server_started (bool), model_pulled (bool), error (str or None), reachable (bool)

    All operations are best-effort; errors are silently caught. Never raises.
    """
    result = {
        "server_started": False,
        "model_pulled": False,
        "error": None,
        "reachable": False,
    }
    try:
        from hermes_bootstrap import (  # type: ignore  # noqa: PLC0415
            ensure_ollama_running,
            get_available_ram_gb,
            is_ollama_reachable,
            pull_model,
            select_model_for_ram,
        )
        # Ensure server is running.
        if not is_ollama_reachable():
            if not ensure_ollama_running(timeout=timeout_server):
                result["error"] = "Ollama server failed to start or become reachable"
                return result
            result["server_started"] = True
        result["reachable"] = True
        # Ensure model is present.
        if not has_hermes_model():
            ram_gb = get_available_ram_gb()
            model = select_model_for_ram(ram_gb)
            if pull_model(model, timeout=timeout_pull):
                result["model_pulled"] = True
            else:
                result["error"] = f"Failed to pull model {model}"
        return result
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"Repair failed: {exc}"
        return result


def diagnose_hermes() -> str:
    """Return a human-readable diagnostic string (one line, < 200 chars)."""
    status = get_health_status()
    if status["reachable"]:
        if status["has_model"]:
            return f"✓ Hermes healthy ({status['model_count']} models)"
        else:
            return "⚠ Ollama running but no Hermes model installed"
    else:
        return "✗ Ollama unreachable — use /repair to start"
