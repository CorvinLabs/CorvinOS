"""Final installation validation checklist."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

_SHARED_DIR = str(Path(__file__).resolve().parents[3] / "operator" / "bridges" / "shared")


def _windows_shim(argv: list[str]):
    """Best-effort ``agents._win_shim.windows_shim_command`` — see plugins.py's
    ``_run_claude`` for the full writeup of why this needs its own sys.path
    insert (corvin_operator/ is deliberately not a real package). Falls back to argv
    unchanged if the shared module can't be found, so a validation-only step
    never hard-fails the install over this."""
    if _SHARED_DIR not in sys.path:
        sys.path.insert(0, _SHARED_DIR)
    try:
        from agents._win_shim import windows_shim_command  # noqa: PLC0415
        return windows_shim_command(argv)
    except ImportError:
        return argv


def run_validation(
    voice_config_dir: Path,
    has_systemd: bool = False,
    selected_bridges: list[str] | None = None,
) -> bool:
    """Run all validation checks. Returns True when all critical checks pass."""
    print("\n" + "=" * 60)
    print("[Validation] Checking installation...")
    print("=" * 60)

    failures = 0

    # ── Chat Engine ────────────────────────────────────────────────────────
    has_claude = bool(shutil.which("claude") or shutil.which("claude-code"))
    has_ollama = bool(shutil.which("ollama"))
    if has_claude:
        print("✓ claude CLI found")
        plugins = _list_plugins()
        if "voice@corvin-voice-local" in plugins:
            print("✓ voice plugin registered")
        else:
            print("⚠ voice plugin not found (optional: claude plugin install voice@corvin-voice-local)")
        if "cowork@corvin-voice-local" in plugins:
            print("✓ cowork plugin registered")
        else:
            print("  cowork plugin not found (optional)")
    elif has_ollama:
        print("✓ Hermes engine (Ollama) available — chat works without Claude CLI")
        print("  ℹ To use Claude: install claude CLI from https://claude.ai/code")
    else:
        print("⚠ No chat engine found")
        print("  Option A: Install claude CLI from https://claude.ai/code")
        print("  Option B: Install Ollama from https://ollama.com (free, local, no API key)")
        print("  CorvinOS will guide you through engine setup on first run.")
        # NOT a fatal failure — the web UI guides users through setup on first visit

    # ── Runtime tools ──────────────────────────────────────────────────────
    if shutil.which("node"):
        ver = _run_stdout(["node", "--version"])
        print(f"✓ node {ver}")
    else:
        print("✗ node not found")
        failures += 1

    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    print(f"✓ python {py_ver}")

    has_openai = _importable("openai")
    has_anthropic = _importable("anthropic")
    has_pywhispercpp = _importable("pywhispercpp")
    has_edge_tts = _importable("edge_tts")

    if has_openai and has_anthropic and has_pywhispercpp:
        print("✓ Python packages: openai + anthropic + pywhispercpp")
    elif has_openai and has_anthropic:
        print("⚠ pywhispercpp missing (STT will need OpenAI fallback)")
    elif has_openai:
        print("⚠ anthropic + pywhispercpp missing")
    else:
        print("✗ openai package missing")
        failures += 1

    # edge-tts is the keyless middle TTS tier (OpenAI → edge → Piper). Without it
    # the fallback chain skips straight to Piper, so surface its absence loudly.
    if has_edge_tts:
        print("✓ edge-tts installed (keyless TTS fallback)")
    else:
        print("⚠ edge-tts missing — keyless TTS fallback unavailable "
              "(install: pip install edge-tts)")

    # ── Systemd bridge services ────────────────────────────────────────────
    if has_systemd:
        print()
        print("  Checking bridge services...")
        running = 0
        services = [
            "corvin-voice-bridge-adapter.service",
            "corvin-voice-bridge-whatsapp.service",
            "corvin-voice-bridge-telegram.service",
            "corvin-voice-bridge-discord.service",
            "corvin-voice-bridge-slack.service",
            "corvin-voice-bridge-email.service",
        ]
        for svc in services:
            enabled = _systemctl("is-enabled", svc)
            if enabled:
                active = _systemctl("is-active", svc)
                if active:
                    print(f"  ✓ {svc} is active")
                    running += 1
                else:
                    print(f"  ⚠ {svc} is enabled but not active (check logs)")
        if running == 0:
            print("  ⚠ No bridge services running (expected if no bridges were selected)")

    # ── API keys ───────────────────────────────────────────────────────────
    print()
    print("  Checking configuration...")
    env_file = voice_config_dir / "service.env"
    if env_file.exists():
        content = env_file.read_text()
        if _key_present(content, "OPENAI_API_KEY", prefix="sk-"):
            print("  ✓ OPENAI_API_KEY configured")
        else:
            print("  ⚠ OPENAI_API_KEY not set or invalid (TTS/Whisper will be limited)")

        if _key_present(content, "ANTHROPIC_API_KEY"):
            print("  ✓ ANTHROPIC_API_KEY configured")
        else:
            print("  ℹ ANTHROPIC_API_KEY not set (optional)")
    else:
        print(f"  ℹ {env_file} not found yet (will be created on first run)")

    # ── Summary ────────────────────────────────────────────────────────────
    print()
    if failures == 0:
        print("✓ All critical checks passed")
    else:
        print(f"✗ {failures} critical issue(s) found — review above")

    return failures == 0


# ── Helpers ────────────────────────────────────────────────────────────────

def _list_plugins() -> str:
    claude_bin = shutil.which("claude")
    if not claude_bin:
        return ""
    try:
        # `text=True` without an explicit encoding decodes using the SYSTEM
        # locale (cp1252 on this Windows install) — claude's CLI output has
        # real UTF-8 bytes, so that decode raised UnicodeDecodeError inside
        # subprocess's internal reader thread, silently killing it and
        # leaving CompletedProcess.stdout as `None` instead of raising here;
        # every caller's `"x" in plugins` then crashed with "argument of type
        # 'NoneType' is not a container" (2026-09-14 live report). Also route
        # through the same cmd.exe-safe shim every other claude spawn site
        # uses — a bare "claude" list-form subprocess.run only happens to
        # work when PATH resolves an actual .exe; the far more common
        # npm-global-install case (claude.cmd) needs the shim or it raises
        # WinError 193/2 outright.
        r = subprocess.run(
            _windows_shim([claude_bin, "plugin", "list"]),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False,
        )
        return r.stdout or ""
    except Exception:
        return ""


def _run_stdout(cmd: list[str]) -> str:
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False,
        )
        return (r.stdout or "").strip()
    except Exception:
        return ""


def _importable(module: str) -> bool:
    r = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        capture_output=True, check=False,
    )
    return r.returncode == 0


def _systemctl(action: str, service: str) -> bool:
    try:
        r = subprocess.run(
            ["systemctl", "--user", action, "--quiet", service],
            capture_output=True, check=False,
        )
        return r.returncode == 0
    except Exception:
        return False


def _key_present(content: str, key: str, prefix: str = "") -> bool:
    for line in content.splitlines():
        if line.startswith(f"{key}="):
            val = line[len(key) + 1:].strip()
            if val and (not prefix or val.startswith(prefix)):
                return True
    return False
