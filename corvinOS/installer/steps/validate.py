"""Final installation validation checklist."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


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
    has_faster_whisper = _importable("faster_whisper")

    if has_openai and has_anthropic and has_faster_whisper:
        print("✓ Python packages: openai + anthropic + faster-whisper")
    elif has_openai and has_anthropic:
        print("⚠ faster-whisper missing (STT will need OpenAI fallback)")
    elif has_openai:
        print("⚠ anthropic + faster-whisper missing")
    else:
        print("✗ openai package missing")
        failures += 1

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
    if not shutil.which("claude"):
        return ""
    try:
        r = subprocess.run(
            ["claude", "plugin", "list"],
            capture_output=True, text=True, check=False,
        )
        return r.stdout
    except Exception:
        return ""


def _run_stdout(cmd: list[str]) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, check=False)
        return r.stdout.strip()
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
