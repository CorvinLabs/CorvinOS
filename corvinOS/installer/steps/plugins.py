"""Claude Code plugin registration: voice + cowork."""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _run_claude(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run 'claude <args>', handling the .cmd wrapper on Windows.

    On Windows, shutil.which("claude") finds claude.cmd, but subprocess.run
    with a list raises WinError 2 for .cmd files without shell=True.
    """
    claude_bin = shutil.which("claude") or "claude"
    if sys.platform == "win32":
        parts = [f'"{claude_bin}"'] + [
            f'"{a}"' if (" " in str(a) or str(a) == "") else str(a) for a in args
        ]
        return subprocess.run(" ".join(parts), shell=True, **kwargs)
    return subprocess.run([claude_bin] + args, **kwargs)


def ensure_plugins(repo_root: Path, interactive: bool = True) -> None:
    """Register the voice and cowork plugins from the local marketplace.

    Non-critical — skips gracefully if Claude Code isn't available.
    On Windows, may require Git Bash or PowerShell 7.
    """
    if not shutil.which("claude"):
        print("\n[Plugins] Registering Claude Code plugins...")
        print("⚠ claude CLI not found — skipping plugin registration.")
        print("  Plugins are optional. To use them later:")
        print("    1. Install Claude Code from https://claude.ai/code")
        print("    2. Run: corvin-install")
        return

    print("\n[Plugins] Registering Claude Code plugins...")

    # Sync local marketplace
    _sync_marketplace(repo_root)

    # Install voice plugin
    _ensure_plugin(
        plugin_id="voice@corvin-voice-local",
        label="Voice plugin",
    )

    # Install cowork plugin (multi-persona)
    _ensure_plugin(
        plugin_id="cowork@corvin-voice-local",
        label="Cowork plugin (multi-persona)",
    )

    print("  ℹ Personas: /cowork-list   Switch: /persona browser")
    print("  ℹ Commands: /help   Voice test: /voice-test")


# ── internals ──────────────────────────────────────────────────────────────

def _sync_marketplace(repo_root: Path) -> None:
    """Register and update the local plugin marketplace (idempotent)."""
    _run_claude(
        ["plugin", "marketplace", "add", str(repo_root)],
        capture_output=True,
        check=False,
    )
    _run_claude(
        ["plugin", "marketplace", "update", "corvin-voice-local"],
        capture_output=True,
        check=False,
    )


def _ensure_plugin(plugin_id: str, label: str) -> bool:
    """Install a plugin if not already present. Returns True on success."""
    # Check if already installed
    list_result = _run_claude(
        ["plugin", "list"],
        capture_output=True,
        text=True,
        check=False,
    )
    if plugin_id in list_result.stdout:
        print(f"✓ {label} already registered")
        return True

    # Install
    with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as tmp:
        log_path = tmp.name

    result = _run_claude(
        ["plugin", "install", plugin_id],
        capture_output=True,
        text=True,
        check=False,
    )
    Path(log_path).write_text(result.stdout + result.stderr)

    # Verify it actually appears in plugin list
    list_result = _run_claude(
        ["plugin", "list"],
        capture_output=True,
        text=True,
        check=False,
    )
    if plugin_id in list_result.stdout:
        print(f"✓ {label} installed")
        return True

    print(f"⚠ {label}: install returned, but plugin not in list.")
    print(f"  Output: {(result.stdout + result.stderr).strip()[:300]}")
    print(f"  Most common cause: not logged in — run 'claude auth login'")
    print(f"  Manual fix: claude plugin install {plugin_id}")
    return False
