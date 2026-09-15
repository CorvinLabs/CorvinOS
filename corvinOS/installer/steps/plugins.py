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
    Uses proper cmd.exe quoting to prevent injection vulnerabilities.
    """
    # corvin_operator/ has no __init__.py (it deliberately shadows the stdlib
    # `operator` module — see nerve_builtins.py / bridge_manager.py's own
    # comment on this exact class of bug), so the dotted
    # `from operator.bridges.shared.agents._win_shim import ...` form used
    # here previously can NEVER resolve; it broke every fresh Windows
    # install at step_16_register_plugins with "No module named
    # 'operator.bridges'; 'operator' is not a package" (2026-09-14 live
    # report). Put corvin_operator/bridges/shared on sys.path and import bare,
    # matching every other _win_shim call site in this codebase.
    _shared_dir = str(Path(__file__).resolve().parents[3] / "operator" / "bridges" / "shared")
    if _shared_dir not in sys.path:
        sys.path.insert(0, _shared_dir)
    from agents._win_shim import windows_shim_command  # noqa: PLC0415

    # `text=True` (used by every caller that reads .stdout) without an
    # explicit encoding decodes the child's output using the SYSTEM locale
    # encoding (cp1252 on this Windows install), not UTF-8. `claude`'s CLI
    # output contains real UTF-8 multi-byte characters (e.g. its own
    # checkmarks/icons), so the cp1252 decode inside subprocess's internal
    # reader thread raised UnicodeDecodeError there, silently killed that
    # thread (Python only logs it — the exception never reaches this
    # function), and CompletedProcess.stdout came back `None` instead of the
    # real text — crashing every caller's `plugin_id in list_result.stdout`
    # with "argument of type 'NoneType' is not a container" (2026-09-14 live
    # report, reproduced deterministically on `claude plugin list`). Force
    # UTF-8 unconditionally so this never depends on the ambient locale.
    kwargs.setdefault("encoding", "utf-8")
    kwargs.setdefault("errors", "replace")

    claude_bin = shutil.which("claude") or "claude"
    if sys.platform == "win32":
        cmd = windows_shim_command([claude_bin] + args)
        return subprocess.run(cmd, **kwargs)
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
    Path(log_path).write_text(result.stdout + result.stderr, encoding='utf-8')

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
