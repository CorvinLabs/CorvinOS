"""ACO Engine Healer — proactive readiness check + auto-repair for engines and voice.

Runs at every Boot-Healer cycle BEFORE the session scan.  Checks:
  1. Chat Engine — is the configured engine actually usable?  There is no
     fallback engine: Hermes / local Ollama were removed (ADR-2091), and a
     stored legacy engine id reads as ``claude_code``.
  2. TTS — is edge-tts importable?  If not, install it silently.  edge-tts
     requires no API key and no local model — it is the universal TTS fallback.
  3. STT — is pywhispercpp or the openai package available?  Log warning if
     neither is present (pywhispercpp is a base dep on every platform, ADR-0185).

Contract:
  * NEVER blocks for long — the engine probe is a 5 s ``claude --version``.
  * NEVER starts or probes a local inference server.
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
    engine_action: str = ""         # "none" | "no_engine_available"
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
            # Count only: warning strings are free text (probe/exception
            # messages) and never enter the audit chain.
            "warning_count": len(self.warnings),
        }


# ── Engine checks ─────────────────────────────────────────────────────────────

def _configured_engine(tenant_id: str) -> str:
    """Read the tenant's configured default_engine from tenant.corvin.yaml.

    A removed legacy engine (ADR-2091: hermes / local Ollama) maps to
    ``claude_code`` through the shared ``engine_registry`` helper."""
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
        if not (isinstance(engine, str) and engine.strip()):
            return "claude_code"
        return _normalize_legacy(engine.strip())
    except Exception:
        return "claude_code"


def _normalize_legacy(engine_id: str) -> str:
    try:
        from pathlib import Path
        _shared = Path(__file__).resolve().parents[4] / "corvin_operator" / "bridges" / "shared"
        if _shared.is_dir() and str(_shared) not in sys.path:
            sys.path.insert(0, str(_shared))
        from engine_registry import normalize_legacy_engine_id
        return normalize_legacy_engine_id(engine_id) or "claude_code"
    except Exception:
        return engine_id


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


def check_engine_readiness(tenant_id: str) -> tuple[bool, str, str]:
    """Check if the configured engine is ready.  Returns (ok, engine_id, action).

    No automatic engine fallback (ADR-2091): a missing claude binary is
    reported, never papered over by switching engines."""
    engine = _configured_engine(tenant_id)
    if _claude_binary_ok():
        return True, engine, "none"
    logger.warning("[ACO] claude binary missing — no usable chat engine")
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

    Priority: openai first (Tier 1 quality), then piper (local, no internet),
    then edge-tts (universal fallback, no API key needed, works with internet).
    """
    # Tier 1: OpenAI TTS (best quality, requires API key at runtime)
    if _openai_importable():
        return True, "openai", "none"

    # Local piper (no network needed once models are present)
    if _piper_available():
        return True, "piper", "none"

    # Universal fallback — no API key, just internet
    if _edge_tts_importable():
        return True, "edge", "none"

    # Try installing edge-tts (fast, ~1 MB, no models) as last resort
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
