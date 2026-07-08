"""ACO Engine Healer — proactive readiness check + auto-repair for engines and voice.

Runs at every Boot-Healer cycle BEFORE the session scan.  Checks:
  1. Chat Engine — is the configured engine (or a fallback) actually usable?
     Fallback chain: claude_code → hermes.  If hermes is selected but Ollama is
     not running, we start it (if the binary is present) without pulling models.
  2. TTS — is edge-tts importable?  If not, install it silently.  edge-tts
     requires no API key and no local model — it is the universal TTS fallback.
  3. STT — is pywhispercpp or the openai package available?  Log warning if
     neither is present (pywhispercpp is a base dep on every platform, ADR-0185).

Contract:
  * NEVER blocks for more than ~45 s (Ollama start timeout).
  * NEVER pulls Ollama models (would block for 30+ min).
  * NEVER crashes if a dependency is missing — degrades gracefully.
  * All outcomes written to audit chain as aco.engine_heal events.

Returns an EngineHealResult for the caller (boot_healer) to log/audit.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class EngineHealResult:
    engine_ok: bool = False
    engine_id: str = ""
    engine_action: str = ""         # "none" | "started_ollama" | "fell_back_to_hermes"
    tts_ok: bool = False
    tts_provider: str = ""          # "openai" | "edge" | "piper" | "none"
    tts_action: str = ""            # "none" | "installed_edge_tts"
    stt_ok: bool = False
    stt_provider: str = ""          # "pywhispercpp" | "openai_whisper" | "none"
    warnings: list[str] = field(default_factory=list)

    def to_audit_details(self) -> dict:
        return {
            "engine_ok": self.engine_ok,
            "engine_id": self.engine_id,
            "engine_action": self.engine_action,
            "tts_ok": self.tts_ok,
            "tts_provider": self.tts_provider,
            "tts_action": self.tts_action,
            "stt_ok": self.stt_ok,
            "stt_provider": self.stt_provider,
            "warnings": self.warnings,
        }


# ── Engine checks ─────────────────────────────────────────────────────────────

def _configured_engine(tenant_id: str) -> str:
    """Read the tenant's configured default_engine from tenant.corvin.yaml."""
    try:
        from forge import paths as _fp
        import yaml
        yaml_path = (
            _fp.tenant_home(tenant_id) / "global" / "tenant.corvin.yaml"
        )
        if not yaml_path.exists():
            return "claude_code"
        data = yaml.safe_load(yaml_path.read_text()) or {}
        spec = data.get("spec", {})
        engine = spec.get("default_engine", "")
        return engine if isinstance(engine, str) and engine.strip() else "claude_code"
    except Exception:
        return "claude_code"


def _claude_binary_ok() -> bool:
    """Return True if the claude binary is on PATH and responds to --version."""
    import os
    binary = (
        os.environ.get("CORVIN_CLAUDE_BIN")
        or shutil.which("claude")
        or shutil.which("claude-code")
    )
    if not binary:
        return False
    try:
        r = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            timeout=5,
        )
        return r.returncode == 0
    except Exception:
        return False


def _hermes_reachable() -> bool:
    """Return True if Ollama HTTP API answers on its configured URL."""
    try:
        from operator.bridges.shared.hermes_bootstrap import is_ollama_reachable
        return is_ollama_reachable()
    except Exception:
        pass
    # Fallback: direct HTTP check
    try:
        import urllib.request
        import os
        base = (
            os.environ.get("CORVIN_OLLAMA_BASE_URL")
            or os.environ.get("OLLAMA_HOST")
            or "http://localhost:11434"
        )
        with urllib.request.urlopen(f"{base}/api/tags", timeout=2):
            return True
    except Exception:
        return False


def _hermes_has_model() -> bool:
    """Return True if at least one model is pulled in Ollama."""
    try:
        import urllib.request
        import json
        import os
        base = (
            os.environ.get("CORVIN_OLLAMA_BASE_URL")
            or os.environ.get("OLLAMA_HOST")
            or "http://localhost:11434"
        )
        with urllib.request.urlopen(f"{base}/api/tags", timeout=3) as resp:
            data = json.loads(resp.read())
            return bool(data.get("models"))
    except Exception:
        return False


def _try_start_ollama() -> bool:
    """Start `ollama serve` if the binary is installed but server is offline.

    Returns True if Ollama is reachable after the attempt.
    """
    try:
        from operator.bridges.shared.hermes_bootstrap import ensure_ollama_running
        return ensure_ollama_running(timeout=40.0)
    except Exception:
        pass
    # Fallback: start directly
    binary = shutil.which("ollama")
    if not binary:
        return False
    try:
        kwargs: dict = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if sys.platform == "win32":
            kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED + NEW_GROUP
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen([binary, "serve"], **kwargs)
        # Wait up to 8 s for it to come up
        import time
        for _ in range(8):
            time.sleep(1)
            if _hermes_reachable():
                return True
        return _hermes_reachable()
    except Exception:
        return False


def check_engine_readiness(tenant_id: str) -> tuple[bool, str, str]:
    """Check if the configured engine is ready.  Returns (ok, engine_id, action)."""
    engine = _configured_engine(tenant_id)

    if engine == "hermes":
        if _hermes_reachable():
            return True, "hermes", "none"
        # Try to start Ollama
        logger.info("[ACO] Hermes configured but Ollama offline — attempting start")
        started = _try_start_ollama()
        if started:
            logger.info("[ACO] Ollama started successfully")
            return True, "hermes", "started_ollama"
        # Hermes installed but wouldn't start — fall back to claude_code
        if _claude_binary_ok():
            logger.warning("[ACO] Ollama not startable, falling back to claude_code")
            return True, "claude_code", "fell_back_to_claude"
        return False, "hermes", "ollama_start_failed"

    # claude_code (default) or any other engine
    if _claude_binary_ok():
        return True, engine, "none"

    # Claude binary missing — try Hermes as fallback
    logger.warning("[ACO] claude binary missing, trying Hermes as fallback")
    if _hermes_reachable():
        return True, "hermes", "fell_back_to_hermes"
    if shutil.which("ollama") and _try_start_ollama():
        return True, "hermes", "started_ollama_fallback"

    return False, engine, "no_engine_available"


# ── TTS checks ────────────────────────────────────────────────────────────────

def _edge_tts_importable() -> bool:
    try:
        import importlib
        return importlib.util.find_spec("edge_tts") is not None
    except Exception:
        return False


def _openai_importable() -> bool:
    try:
        import importlib
        return importlib.util.find_spec("openai") is not None
    except Exception:
        return False


def _piper_available() -> bool:
    return bool(shutil.which("piper") or shutil.which("piper-tts"))


def _try_install_edge_tts() -> bool:
    """Silently install edge-tts via pip. Fast — no model download."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "edge-tts", "-q"],
            capture_output=True,
            timeout=60,
        )
        return result.returncode == 0
    except Exception:
        return False


def check_tts_readiness() -> tuple[bool, str, str]:
    """Check TTS availability. Returns (ok, provider, action).

    Priority: edge-tts first (no API key needed, always works with internet),
    then piper (local, no internet), then openai (needs API key at runtime).
    """
    # Universal fallback — no API key, just internet. Always comes first.
    if _edge_tts_importable():
        return True, "edge", "none"

    # Local piper (no network needed once models are present)
    if _piper_available():
        return True, "piper", "none"

    # OpenAI TTS (quality, but requires API key at runtime)
    if _openai_importable():
        return True, "openai", "none"

    # Try installing edge-tts (fast, ~1 MB, no models)
    logger.info("[ACO] edge-tts not installed — attempting silent install")
    if _try_install_edge_tts() and _edge_tts_importable():
        logger.info("[ACO] edge-tts installed successfully")
        return True, "edge", "installed_edge_tts"

    return False, "none", "no_tts_available"


# ── STT checks ────────────────────────────────────────────────────────────────

def _pywhispercpp_importable() -> bool:
    try:
        import importlib
        return importlib.util.find_spec("pywhispercpp") is not None
    except Exception:
        return False


def check_stt_readiness() -> tuple[bool, str, str]:
    """Check STT availability. Returns (ok, provider, action).

    ADR-0185: pywhispercpp replaced faster-whisper as the local STT engine —
    it is a base dependency on every platform (no `av`/torch/ctranslate2, no
    Windows wheel gap), so we do NOT need faster-whisper's old "don't
    auto-install, it's huge and Windows-hostile" carve-out here anymore.
    """
    if _pywhispercpp_importable():
        return True, "pywhispercpp", "none"
    if _openai_importable():
        return True, "openai_whisper", "none"
    return False, "none", "no_stt_available"


# ── Combined check ────────────────────────────────────────────────────────────

def run_readiness_check(tenant_id: str = "_default") -> EngineHealResult:
    """Run engine + TTS + STT readiness checks for a given tenant.

    This is the entry point called by the Boot-Healer at every cycle.
    """
    result = EngineHealResult()

    # Engine
    try:
        result.engine_ok, result.engine_id, result.engine_action = (
            check_engine_readiness(tenant_id)
        )
        if not result.engine_ok:
            result.warnings.append(
                f"No usable engine for tenant={tenant_id} "
                f"(configured={_configured_engine(tenant_id)}, "
                f"action={result.engine_action})"
            )
    except Exception as exc:
        result.warnings.append(f"Engine check failed: {exc}")
        logger.debug("[ACO] Engine check error for tenant=%s", tenant_id, exc_info=True)

    # TTS
    try:
        result.tts_ok, result.tts_provider, result.tts_action = check_tts_readiness()
        if not result.tts_ok:
            result.warnings.append("No TTS provider available — voice responses will be silent")
    except Exception as exc:
        result.warnings.append(f"TTS check failed: {exc}")
        logger.debug("[ACO] TTS check error", exc_info=True)

    # STT
    try:
        result.stt_ok, result.stt_provider, _ = check_stt_readiness()
        if not result.stt_ok:
            result.warnings.append(
                "No STT provider available — voice input disabled "
                "(install pywhispercpp or set OPENAI_API_KEY)"
            )
    except Exception as exc:
        result.warnings.append(f"STT check failed: {exc}")
        logger.debug("[ACO] STT check error", exc_info=True)

    # Log summary
    if result.warnings:
        for w in result.warnings:
            logger.warning("[ACO] Engine-heal warning: %s", w)
    else:
        logger.info(
            "[ACO] Readiness OK — engine=%s tts=%s stt=%s",
            result.engine_id, result.tts_provider, result.stt_provider,
        )

    return result
