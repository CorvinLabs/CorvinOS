"""Engine auto-detection — ADR-0125 Zero-Config Engine Onboarding.

Probes installed engines and their credential sources using the
Subscription-First hierarchy:
  subscription > env_var > config_file > vault > none > null (not installed)

Each probe is isolated: one failing probe never blocks others.

MUST NOT import anthropic (CI AST lint enforces).
"""
from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

_log = logging.getLogger(__name__)


# Credential source priority (1 = highest, null = not installed).
# "discovered" marks auto-found tools not yet registered as full CorvinOS engines.
CREDENTIAL_SOURCES = ("subscription", "env_var", "config_file", "vault", "none", "discovered")
_PROBE_TIMEOUT = 5.0
_OLLAMA_TIMEOUT = 2.0

# Auto-discovery candidates — any binary found in PATH gets surfaced as "discovered".
# (binary_name, display_label) pairs; order = display order after registered engines.
# NOTE: "claude-code" is NOT a candidate — it is tried as a fallback in probe_claude_code()
#       as an alternative binary name on some systems. Listing it here would create a
#       duplicate if both "claude" and "claude-code" are present in PATH.
_DISCOVERY_CANDIDATES: list[tuple[str, str]] = [
    ("cursor", "Cursor"),
    ("aider", "Aider"),
    ("llm", "LLM CLI"),
    ("cody", "Cody"),
    ("continue", "Continue.dev"),
    ("gemini", "Gemini CLI"),
    ("gpt", "GPT CLI"),
    ("tabby", "Tabby"),
    ("warp-ai", "Warp AI"),
]


@dataclass
class EngineProbeResult:
    engine_id: str
    installed: bool
    authenticated: bool
    # One of CREDENTIAL_SOURCES, or None when binary is not installed.
    credential_source: Optional[str]
    version: Optional[str]
    # Non-empty only for hermes — list of pulled Ollama model names.
    models: List[str] = field(default_factory=list)
    # Human-readable single-line status for the console UI.
    detail: Optional[str] = None


def _find_binary(name: str) -> str | None:
    """Find a binary in PATH, with extended search on user systems.

    For now, use shutil.which() as the primary mechanism.
    Fallback paths (in a future improvement) could include:
    ~/.local/bin, ~/.cargo/bin, ~/.npm/bin, %LOCALAPPDATA% on Windows, etc.
    """
    return shutil.which(name)


def _windows_wrap(cmd: list[str]) -> list[str]:
    """Wrap a ``.cmd``/``.bat`` shim so Windows can actually launch it.

    Windows' ``CreateProcess`` cannot start a ``.cmd``/``.bat`` file directly
    when Python passes it as a bare argv list (no ``shell=True``) — it raises
    ``OSError: [WinError 193] %1 is not a valid Win32 application``. npm's
    global-install shims for Node CLIs (``claude``, ``codex``, ``copilot``,
    …) are exactly such ``.cmd`` files.
    """
    exe = cmd[0]
    if os.name == "nt" and exe.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", *cmd]
    return cmd


def _run(cmd: list[str], timeout: float = _PROBE_TIMEOUT) -> tuple[int, str, str]:
    """Run subprocess; return (returncode, stdout, stderr). Never raises."""
    try:
        r = subprocess.run(_windows_wrap(cmd), capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return -1, "", ""


def probe_claude_code() -> EngineProbeResult:
    engine_id = "claude_code"

    # Try multiple binary names to handle platform variations (Windows .cmd/.exe, symlinks, etc)
    binary = None
    binary_name = None
    version = None
    for candidate in ("claude", "claude-code"):
        binary = _find_binary(candidate)
        if binary:
            binary_name = candidate
            # Spawn the already-resolved full path, not the bare name: on
            # Windows a bare "claude" only gets an implicit ".exe" tried by
            # CreateProcess, so it never finds the npm "claude.cmd" shim that
            # _find_binary() (shutil.which, PATHEXT-aware) just located —
            # that mismatch made "installed" and "version probe" disagree.
            rc, stdout, _ = _run([binary, "--version"])
            version = stdout.split("\n")[0] if rc == 0 and stdout else None
            _log.debug(f"claude_code probe: found {binary_name} at {binary}, version={version}")
            break
    else:
        _log.debug(f"claude_code probe: binary not found in PATH")
        return EngineProbeResult(
            engine_id=engine_id, installed=False, authenticated=False,
            credential_source=None, version=None,
            detail="claude binary not found (tried: claude, claude-code)",
        )

    # Subscription-First: check OAuth session before API key.
    creds_path = Path.home() / ".claude" / ".credentials.json"
    if creds_path.exists():
        try:
            creds = json.loads(creds_path.read_text())
            # Claude Code OAuth stores session under claudeAiOauth or accessToken.
            if creds.get("claudeAiOauth") or creds.get("accessToken"):
                return EngineProbeResult(
                    engine_id=engine_id, installed=True, authenticated=True,
                    credential_source="subscription", version=version,
                    detail="Authenticated via Claude subscription (OAuth)",
                )
        except Exception:  # noqa: BLE001
            pass

    if os.environ.get("ANTHROPIC_API_KEY"):
        return EngineProbeResult(
            engine_id=engine_id, installed=True, authenticated=True,
            credential_source="env_var", version=version,
            detail="API key from ANTHROPIC_API_KEY",
        )

    return EngineProbeResult(
        engine_id=engine_id, installed=True, authenticated=False,
        credential_source="none", version=version,
        detail="Installed but not authenticated — run: claude login",
    )


def probe_copilot() -> EngineProbeResult:
    engine_id = "copilot"

    # Try to locate copilot binary with platform variations
    binary = _find_binary("copilot")
    if not binary:
        _log.debug(f"copilot probe: binary not found in PATH")
        return EngineProbeResult(
            engine_id=engine_id, installed=False, authenticated=False,
            credential_source=None, version=None,
            detail="copilot binary not found",
        )

    _log.debug(f"copilot probe: found at {binary}")
    rc, stdout, _ = _run([binary, "--version"])
    version = stdout.split("\n")[0] if rc == 0 and stdout else None

    # Copilot config file (subscription session).
    copilot_cfg = Path.home() / ".copilot" / "config.json"
    if copilot_cfg.exists():
        try:
            cfg = json.loads(copilot_cfg.read_text())
            if cfg.get("github_token") or cfg.get("access_token"):
                return EngineProbeResult(
                    engine_id=engine_id, installed=True, authenticated=True,
                    credential_source="subscription", version=version,
                    detail="Authenticated via GitHub Copilot subscription",
                )
        except Exception:  # noqa: BLE001
            pass

    # GitHub CLI auth — exit code 0 is the reliable signal on all versions.
    # Older gh versions write status to stderr; we trust the exit code only.
    rc2, _, _ = _run(["gh", "auth", "status"], timeout=_PROBE_TIMEOUT)
    if rc2 == 0:
        return EngineProbeResult(
            engine_id=engine_id, installed=True, authenticated=True,
            credential_source="subscription", version=version,
            detail="Authenticated via GitHub CLI (gh auth)",
        )

    if os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"):
        return EngineProbeResult(
            engine_id=engine_id, installed=True, authenticated=True,
            credential_source="env_var", version=version,
            detail="GitHub token from environment variable",
        )

    return EngineProbeResult(
        engine_id=engine_id, installed=True, authenticated=False,
        credential_source="none", version=version,
        detail="Installed but not authenticated — run: copilot auth login",
    )


def probe_hermes() -> EngineProbeResult:
    """Probe Ollama/Hermes — no auth needed; check binary, running state, and models."""
    engine_id = "hermes"

    # Try to locate ollama binary with platform variations
    binary = _find_binary("ollama")
    if not binary:
        _log.debug(f"hermes probe: ollama binary not found in PATH or common directories")

    # Probe Ollama API (may be running via Docker even without binary in PATH).
    base_url = (
        os.environ.get("CORVIN_OLLAMA_BASE_URL")
        or os.environ.get("OLLAMA_HOST")
        or "http://localhost:11434"
    ).rstrip("/")

    models: list[str] = []
    running = False
    try:
        with urllib.request.urlopen(f"{base_url}/api/tags", timeout=_OLLAMA_TIMEOUT) as resp:
            data = json.loads(resp.read())
        models = [m["name"] for m in (data.get("models") or [])]
        running = True
    except (urllib.error.URLError, OSError, Exception):  # noqa: BLE001
        pass

    if not binary and not running:
        return EngineProbeResult(
            engine_id=engine_id, installed=False, authenticated=False,
            credential_source=None, version=None, models=[],
            detail="Ollama not installed",
        )

    version: Optional[str] = None
    if binary:
        rc, stdout, _ = _run([binary, "--version"])
        version = stdout.split("\n")[0] if rc == 0 and stdout else None

    if running and models:
        return EngineProbeResult(
            engine_id=engine_id, installed=True, authenticated=True,
            credential_source="config_file", version=version, models=models,
            detail=f"Ollama running — {len(models)} model{'s' if len(models) != 1 else ''} available",
        )
    if running:
        return EngineProbeResult(
            engine_id=engine_id, installed=True, authenticated=False,
            credential_source="none", version=version, models=[],
            detail="Ollama running but no models — run: ollama pull qwen3:1.7b",
        )
    return EngineProbeResult(
        engine_id=engine_id, installed=True, authenticated=False,
        credential_source="none", version=version, models=[],
        detail="Ollama installed but not running — run: ollama serve",
    )


def probe_opencode() -> EngineProbeResult:
    engine_id = "opencode"

    # Try to locate opencode binary with platform variations
    binary = _find_binary("opencode")
    if not binary:
        _log.debug(f"opencode probe: binary not found in PATH")
        return EngineProbeResult(
            engine_id=engine_id, installed=False, authenticated=False,
            credential_source=None, version=None,
            detail="opencode binary not found",
        )

    _log.debug(f"opencode probe: found at {binary}")
    rc, stdout, _ = _run([binary, "--version"])
    version = stdout.split("\n")[0] if rc == 0 and stdout else None

    # OpenCode supports many providers — check common API keys in priority order.
    for env_var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY"):
        if os.environ.get(env_var):
            return EngineProbeResult(
                engine_id=engine_id, installed=True, authenticated=True,
                credential_source="env_var", version=version,
                detail=f"Provider key from {env_var}",
            )

    # OpenCode can also delegate to a local Ollama instance.
    # Respect the same CORVIN_OLLAMA_BASE_URL / OLLAMA_HOST env as probe_hermes.
    ollama_base = (
        os.environ.get("CORVIN_OLLAMA_BASE_URL")
        or os.environ.get("OLLAMA_HOST")
        or "http://localhost:11434"
    ).rstrip("/")
    try:
        with urllib.request.urlopen(f"{ollama_base}/api/tags", timeout=1.5):
            return EngineProbeResult(
                engine_id=engine_id, installed=True, authenticated=True,
                credential_source="config_file", version=version,
                detail="Using local Ollama as provider",
            )
    except Exception:  # noqa: BLE001
        pass

    return EngineProbeResult(
        engine_id=engine_id, installed=True, authenticated=False,
        credential_source="none", version=version,
        detail="Installed — set ANTHROPIC_API_KEY, OPENAI_API_KEY, or run Ollama",
    )


def probe_codex_cli() -> EngineProbeResult:
    engine_id = "codex_cli"

    # Try to locate codex binary with platform variations
    binary = _find_binary("codex")
    if not binary:
        return EngineProbeResult(
            engine_id=engine_id, installed=False, authenticated=False,
            credential_source=None, version=None,
            detail="codex binary not found",
        )

    rc, stdout, _ = _run([binary, "--version"])
    version = stdout.split("\n")[0] if rc == 0 and stdout else None

    if os.environ.get("OPENAI_API_KEY"):
        return EngineProbeResult(
            engine_id=engine_id, installed=True, authenticated=True,
            credential_source="env_var", version=version,
            detail="API key from OPENAI_API_KEY",
        )

    return EngineProbeResult(
        engine_id=engine_id, installed=True, authenticated=False,
        credential_source="none", version=version,
        detail="Installed — set OPENAI_API_KEY",
    )


# Ordered probe list — determines display order in the console UI.
_PROBES = [
    probe_claude_code,
    probe_hermes,
    probe_opencode,
    probe_codex_cli,
    probe_copilot,
]


_DETECT_TIMEOUT = 10.0  # Global wall-clock cap for all probes combined.

# IDs of the 5 registered engines — excluded from auto-discovery to avoid duplicates.
_REGISTERED_IDS = frozenset({"claude_code", "hermes", "opencode", "codex_cli", "copilot"})

# Canonical engine priority for recommended_engine() — earlier = preferred.
# Matches _PROBES submission order; kept separate so it's explicit and
# survives concurrent collect (which produces non-deterministic completion order).
_ENGINE_PRIORITY: list[str] = ["claude_code", "hermes", "opencode", "codex_cli", "copilot"]

# Lookup: engine_id → display position (used to sort detect_all() output).
_PROBE_RANK: dict[str, int] = {eid: i for i, eid in enumerate(_ENGINE_PRIORITY)}


def _discover_extra_engines() -> list[EngineProbeResult]:
    """Scan PATH for AI tools not in the registered 5 engines.

    Returns lightweight probes for any discovered binary. These are always
    installed=True (we only return them when found) and authenticated=False
    (we don't know their auth state). credential_source='discovered' signals
    the UI to render a distinct badge and informational hint.

    Entries whose engine_id (= binary name) is in _REGISTERED_IDS are skipped
    — they are already covered by the registered probes.
    """
    results: list[EngineProbeResult] = []
    for binary, label in _DISCOVERY_CANDIDATES:
        if binary in _REGISTERED_IDS:
            continue  # already covered by a registered probe
        resolved = _find_binary(binary)
        if not resolved:
            continue
        rc, stdout, _ = _run([resolved, "--version"], timeout=3.0)
        version = stdout.split("\n")[0].strip() if rc == 0 and stdout else None
        results.append(EngineProbeResult(
            engine_id=binary,
            installed=True,
            authenticated=False,
            credential_source="discovered",
            version=version,
            detail=f"{label} detected in PATH — not yet a registered CorvinOS engine",
        ))
    return results


def detect_all() -> list[EngineProbeResult]:
    """Run all engine probes concurrently + auto-discover unlisted tools.

    Registered probes (5 engines) run with a 10s global cap. Probes that exceed
    _DETECT_TIMEOUT are silently dropped (not retried). Auto-discovery runs separately
    with a 3s per-binary cap. If an engine times out, it will NOT appear in results
    (even if installed) — the calling code cannot distinguish timeout from not-installed.

    TIMEOUT ISSUE: If Hermes hangs (known issue: "hermes connect error: timed out"),
    the entire detect_all() call may appear to hang. Callers should handle timeouts
    at the API level with their own timeout wrapper (e.g., concurrent.futures.wait).

    Registered probes (5 engines) run with a 10 s global cap. Auto-discovery
    runs as a single future alongside them (sequential PATH scan, 3 s per binary
    cap, worst case 30 s). Installed=False probes from registered engines are kept
    so callers can distinguish "not installed" from "installed but not
    authenticated".

    The ThreadPoolExecutor is shut down with wait=False so that slow discovery
    futures (e.g. hung binaries) do not block the API response after the global
    timeout fires. Remaining threads complete in the background.
    """
    results: list[EngineProbeResult] = []
    all_probes = _PROBES + [_discover_extra_engines]  # type: ignore[list-item]
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=len(all_probes))
    try:
        futures = {pool.submit(probe): probe for probe in all_probes}
        done, not_done = concurrent.futures.wait(futures, timeout=_DETECT_TIMEOUT)
        if not_done:
            _log.warning("engine_detection: %d probe(s) timed out", len(not_done))
        for fut in done:
            try:
                r = fut.result()
                if isinstance(r, list):
                    results.extend(r)
                else:
                    results.append(r)
            except Exception:  # noqa: BLE001
                pass
    finally:
        # shutdown(wait=False) returns immediately; threads finish in background.
        # This prevents blocking the HTTP response for up to 30 s when
        # _discover_extra_engines is still scanning slow binaries after timeout.
        pool.shutdown(wait=False)

    # Sort: registered engines first (by _PROBE_RANK), then discoveries in scan order.
    # Concurrent futures complete in non-deterministic order; stable sort here ensures
    # the console UI always shows engines in a predictable sequence.
    registered = sorted(
        [r for r in results if r.engine_id in _REGISTERED_IDS],
        key=lambda r: _PROBE_RANK.get(r.engine_id, 999),
    )
    discovered = [r for r in results if r.engine_id not in _REGISTERED_IDS]
    return registered + discovered


def recommended_engine(results: list[EngineProbeResult]) -> Optional[str]:
    """Return the engine_id of the best ready engine, or None.

    Uses explicit _ENGINE_PRIORITY ordering so that claude_code is always
    preferred over other subscription-based engines regardless of which probe
    completed first in the concurrent detect_all() run.
    """
    by_id = {r.engine_id: r for r in results}
    for source in ("subscription", "env_var", "config_file"):
        for engine_id in _ENGINE_PRIORITY:
            r = by_id.get(engine_id)
            if r and r.authenticated and r.credential_source == source:
                return r.engine_id
    return None
