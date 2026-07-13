"""Core installation orchestrator.

Delegates every concern to a focused steps/ module. No hardcoded paths.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from corvinOS.shared.paths import corvin_home, voice_config_dir
from corvinOS.installer.service_manager import get_service_manager
from corvinOS.installer.bridge_manager import BridgeManager
from corvinOS.installer.steps import platform as _platform


def _robust_rmtree(path) -> list[str]:
    """Remove a directory tree resiliently — the fix for Windows uninstall
    crashing with ``PermissionError [WinError 32] … used by another process`` (a
    still-running console/bridge holds a handle on e.g. ``audit.jsonl``) or
    ``[WinError 5]`` (read-only files). Retries with backoff, clears the
    read-only bit, and — rather than raising — returns the list of paths it could
    NOT delete so the caller can guide the user. Never raises."""
    import os
    import stat as _stat
    path = Path(path)
    if not path.exists():
        return []
    leftover: list[str] = []

    def _handle(func, p, exc):  # works for both onexc (3.12+) and onerror
        for attempt in range(5):
            try:
                try:
                    os.chmod(p, _stat.S_IWRITE)  # clear read-only (WinError 5)
                    parent = os.path.dirname(p)  # unlink needs a writable parent
                    # Only relax perms on dirs INSIDE the tree being removed —
                    # never chmod the tree's external parent (e.g. ~ when
                    # deleting ~/.corvin), which would force it to 0700.
                    if parent and (Path(parent) == path or path in Path(parent).parents):
                        os.chmod(parent, _stat.S_IRWXU)
                except OSError:
                    pass
                func(p)                          # retry the failed unlink/rmdir
                return
            except FileNotFoundError:
                return
            except PermissionError:
                time.sleep(0.4 * (attempt + 1))  # locked (WinError 32) — brief wait
            except OSError:
                break
        leftover.append(str(p))

    try:
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=_handle)
        else:  # pragma: no cover - older Pythons
            shutil.rmtree(path, onerror=lambda f, p, i: _handle(f, p, i))
    except FileNotFoundError:
        pass
    except Exception:  # noqa: BLE001 — uninstall must never crash on removal
        if path.exists():
            leftover.append(str(path))
    return leftover
from corvinOS.installer.steps import dependencies as _deps
from corvinOS.installer.steps import keys as _keys
from corvinOS.installer.steps import plugins as _plugins
from corvinOS.installer.steps import console as _console
from corvinOS.installer.steps import bridges as _bridges
from corvinOS.installer.steps import stt as _stt
from corvinOS.installer.steps import piper as _piper
from corvinOS.installer.steps import validate as _validate


def _find_repo_root() -> Path:
    """Return the repo / install root.

    In a source-tree install: corvinOS/installer/core.py → ../../.. = repo root.
    In a pip-wheel install: corvinOS/ lives inside site-packages; the "repo root"
    concept does not apply for build artefacts, but we still need a writable
    location for runtime data.  Fall back to the user's home directory so that
    paths like _REPO_ROOT / "operator" / ... are at least predictable
    (they won't exist, but the code gracefully handles missing paths).
    """
    candidate = Path(__file__).resolve().parent.parent.parent
    # A genuine repo root always has pyproject.toml at its root.
    if (candidate / "pyproject.toml").exists():
        return candidate
    # pip-wheel install: return site-packages root (best-effort).
    return candidate


_REPO_ROOT = _find_repo_root()
# True when running from a pip-wheel install (no source tree present).
# Used to skip npm-based frontend builds that are already bundled in the wheel.
_IS_WHEEL_INSTALL = not (_REPO_ROOT / "pyproject.toml").exists()

# Systemd units that are always registered (not bridge-specific).
# Bridge units are derived dynamically from the installer manifest.
# Includes both the Python-installer name (corvin-voice-bridge-adapter) and
# the bridge.sh legacy name (corvin-adapter) so both are caught.
_SYSTEM_UNITS = [
    "corvin-adapter.service",
    "corvin-voice-bridge-adapter.service",
    "corvin-voice-bridge-watchdog.service",
    "corvin-voice-bridge-watchdog.timer",
    "corvin-session-timeout.service",
    "corvin-session-timeout.timer",
    "corvin-audit-verify.service",
    "corvin-audit-verify.timer",
    "corvin-user-style.service",
    "corvin-user-style.timer",
    "corvin-engine-canary.service",
    "corvin-engine-canary.timer",
    "corvin-supply-chain-weekly.service",
    "corvin-supply-chain-weekly.timer",
    "corvin-supply-chain-critical.service",
    "corvin-supply-chain-critical.timer",
    "corvin-webui.service",
]


class CorvinInstaller:
    """Full installation orchestrator — driven by corvin-install / corvin-uninstall."""

    BRIDGES = ["discord", "whatsapp", "telegram", "slack", "email"]

    def __init__(self, interactive: bool = True, repo_root: "Path | None" = None):
        self.interactive = interactive
        # Injectable so tests can point destructive uninstall steps (in-repo
        # .corvin, web-next build artifacts) at a sandbox instead of the live
        # dev checkout — a real test run once wiped the production .corvin of
        # the running bridge through the module-global _REPO_ROOT.
        self.repo_root = Path(repo_root) if repo_root is not None else _REPO_ROOT
        self.corvin_home = corvin_home()
        self.voice_config = voice_config_dir()
        # Same injectability for the other roots uninstall() deletes from:
        # user systemd units and the Claude Code plugin cache/marketplace.
        self.systemd_user_dir = Path.home() / ".config" / "systemd" / "user"
        self.claude_plugins_dir = Path.home() / ".claude" / "plugins"
        # Windows autostart shortcut roots. ALSO injectable — uninstall()'s
        # WA-9 shortcut sweep read os.environ["APPDATA"/"USERPROFILE"] directly,
        # so a win32-mocked test with no env patch would unlink a Windows dev's
        # REAL Startup/Desktop CorvinOS.lnk (4th incarnation of the live-state
        # wipe class). Resolve from env at construction; tests point these at a
        # sandbox. None → the corresponding shortcut is skipped.
        _appdata = os.environ.get("APPDATA")
        _userprofile = os.environ.get("USERPROFILE")
        self.win_startup_dir = (
            Path(_appdata) / "Microsoft/Windows/Start Menu/Programs/Startup"
            if _appdata else None
        )
        self.win_desktop_dir = (
            Path(_userprofile) / "Desktop" if _userprofile else None
        )
        self.service_manager = get_service_manager()
        self.bridge_manager = BridgeManager()
        self.selected_bridges: list[str] = []
        self.platform: _platform.PlatformInfo | None = None

    # ── Step 1: Detect platform ────────────────────────────────────────────

    def step_1_detect_platform(self) -> None:
        print("\n" + "=" * 60)
        print("CorvinOS Installer")
        print("=" * 60)
        print(f"Python  : {sys.version.split()[0]}")
        print(f"Repo    : {self.repo_root}")

        self.platform = _platform.detect()
        print(f"OS      : {self.platform.os_kind.value}")
        print(f"Pkg mgr : {self.platform.pkg_mgr.value}")
        print(f"systemd : {self.platform.has_systemd}")

        for w in self.platform.warnings:
            print(f"⚠ {w}")

    # ── Step 2: Create directories ─────────────────────────────────────────

    def step_2_create_directories(self) -> None:
        print("\n[Step 2] Creating directories...")
        dirs = [
            self.corvin_home,
            self.voice_config,
            self.corvin_home / "logs",
            self.corvin_home / "sessions",
            self.corvin_home / "bridges",
            self.corvin_home / "tenants" / "_default" / "global",
        ]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)
            if sys.platform != "win32":
                d.chmod(0o700)
            print(f"  ✓ {d}")
        # Seed profile defaults on first run so learning + metaphors are on
        # immediately without requiring a /profile set command first.
        profile_file = self.voice_config / "profile.json"
        if not profile_file.exists():
            defaults = {"voice_audience_metaphors": "on", "voice_audience_learning": 3}
            profile_file.write_text(json.dumps(defaults, indent=2))
            if sys.platform != "win32":
                profile_file.chmod(0o600)
            print(f"  ✓ {profile_file} (defaults seeded)")

    # ── Step 3: System dependencies (Node.js, ffmpeg, espeak-ng …) ────────

    def step_3_system_dependencies(self) -> None:
        print("\n[Step 3] System dependencies...")
        assert self.platform is not None
        _deps.ensure_system_tools(self.platform, interactive=self.interactive)
        _deps.ensure_node(self.platform, interactive=self.interactive)

    # ── Step 4: Install Claude Code CLI ───────────────────────────────────

    def step_4_install_claude_code(self) -> None:
        print("\n[Step 4] Claude Code CLI...")
        _deps.ensure_claude_code(interactive=self.interactive)

    # ── Step 5: Claude Code login ──────────────────────────────────────────

    def step_5_claude_login(self) -> None:
        print("\n[Step 5] Claude Code login...")
        _deps.ensure_claude_login(interactive=self.interactive)

    # ── Step 6: Hermes (Ollama) bootstrap — optional ──────────────────────

    def step_6_bootstrap_hermes(self) -> None:
        """Install Ollama and pull the recommended model for this machine's RAM.

        Never hard-fails — Ollama is optional (other engines still work without it).
        """
        print("\n[Step 6] Hermes (Ollama) engine bootstrap...")
        try:
            try:
                from corvin_console.hermes_bootstrap import (  # noqa: PLC0415
                    bootstrap_hermes, get_available_ram_gb, select_model_for_ram,
                    is_ollama_installed,
                )
            except ImportError:
                from operator.bridges.shared.hermes_bootstrap import (  # noqa: PLC0415
                    bootstrap_hermes, get_available_ram_gb, select_model_for_ram,
                    is_ollama_installed,
                )

            ram = get_available_ram_gb()
            model = select_model_for_ram(ram)
            already_installed = is_ollama_installed()

            print(f"  RAM detected : {ram:.1f} GB")
            print(f"  Model        : {model}")
            print(f"  Ollama       : {'installed' if already_installed else 'not found'}")

            if not self.interactive:
                print("  Skipping — run corvin-install in a terminal to set up Hermes.")
                return

            if not already_installed:
                answer = input(
                    f"  Install Ollama + pull {model} (~2–9 GB)? [Y/n]: "
                ).strip().lower() or "y"
                if answer.startswith("n"):
                    print("  Skipping Hermes bootstrap.")
                    return

            # stream=True + a progress callback so the multi-GB model pull shows
            # LIVE progress (Ollama's native download bar) instead of looking
            # frozen at "[Step 6]" — the same on Linux, macOS and Windows.
            print(f"  Downloading {model} now — live progress below "
                  f"(this is a one-time ~2–9 GB download):", flush=True)
            result = bootstrap_hermes(
                force_model=model, stream=True,
                progress=lambda m: print(f"  · {m}", flush=True))

            if result.get("error"):
                print(f"  ⚠ Hermes bootstrap warning: {result['error']}")
                print(f"  Manual fix: ollama pull {model}")
            elif result.get("model_pulled"):
                print(f"  ✓ Hermes ready: {model}")
            else:
                print(f"  ⚠ Hermes: model not pulled — run: ollama pull {model}")

        except Exception as exc:
            print(f"  ⚠ Hermes bootstrap skipped: {exc}")
            print(f"  Manual: ollama pull <model>   (see https://ollama.ai)")

    # ── Step 7: Speech-to-Text (pywhispercpp) ─────────────────────────────

    def step_7_setup_stt(self) -> None:
        print("\n[Step 7] Speech-to-Text (pywhispercpp)...")
        _stt.ensure_stt(self.voice_config, interactive=self.interactive)

    # ── Step 8: Text-to-Speech (Piper) ────────────────────────────────────

    def step_8_setup_piper(self) -> None:
        print("\n[Step 8] Text-to-Speech (edge-tts + Piper)...")
        # edge-tts is the keyless middle tier (OpenAI → edge → Piper); ensure it
        # explicitly so the fallback order holds even on non-standard interpreters.
        _piper.ensure_edge_tts()
        _piper.ensure_piper(self.voice_config, interactive=self.interactive)

    # ── Step 9: API keys ───────────────────────────────────────────────────

    def step_9_api_keys(self) -> None:
        print("\n[Step 9] API keys...")
        existing = _keys.load_existing_keys()

        openai_key = _keys.prompt_openai_key(
            existing=existing.get("OPENAI_API_KEY", ""),
            interactive=self.interactive,
        )
        anthropic_key = _keys.prompt_anthropic_key(
            existing=existing.get("ANTHROPIC_API_KEY", ""),
            interactive=self.interactive,
        )

        extra: dict[str, str] = {}
        if claude_bin := shutil.which("claude"):
            extra["CLAUDE_BIN"] = claude_bin
        if piper_bin := shutil.which("piper"):
            extra["PIPER_BIN"] = piper_bin

        _keys.save_keys(openai_key, anthropic_key, extra, repo_root=self.repo_root)

    # ── Step 10: Select bridges ────────────────────────────────────────────

    def step_10_select_bridges(self) -> None:
        print("\n[Step 10] Select bridges...")
        print("  Bridges can be configured now or later via the web console.")
        print()

        # Bridge descriptions for user guidance
        bridge_descriptions = {
            "discord": "Chat via Discord",
            "whatsapp": "Chat via WhatsApp (QR-code pairing)",
            "telegram": "Chat via Telegram",
            "slack": "Chat via Slack",
            "email": "Receive tasks via email (IMAP/SMTP)",
        }

        if not self.interactive:
            # Default to NO bridges on a non-interactive install. Selecting all
            # of them registered + started five token-less messenger services
            # that crash-loop until StartLimitBurst on a fresh box. Bridges are
            # configured later from the console once tokens exist.
            self.selected_bridges = []
            print("  Non-interactive: no bridges selected (configure later in console)")
            return

        # Ask if user wants to set up a bridge now
        print("  Do you want to set up a bridge now?")
        answer = input("  [Y/n]: ").strip().lower() or "y"

        if answer.startswith("n"):
            print()
            print("  ✓ Skipping bridge setup")
            print("  💡 You can configure bridges anytime via the web console:")
            print("     Settings → Bridges")
            self.selected_bridges = []
            return

        # Show numbered list of bridges
        print()
        print("  Available bridges:")
        for i, bridge in enumerate(self.BRIDGES, 1):
            desc = bridge_descriptions.get(bridge, "")
            print(f"    {i}. {bridge:<12} — {desc}")

        print()
        while True:
            choice = input("  Select a bridge [1-5] or [a]ll or [n]one: ").strip().lower()

            if choice == "n":
                self.selected_bridges = []
                print("  ✓ No bridges selected")
                break
            elif choice == "a":
                self.selected_bridges = self.BRIDGES.copy()
                print(f"  ✓ Selected all: {', '.join(self.selected_bridges)}")
                break
            elif choice.isdigit() and 1 <= int(choice) <= len(self.BRIDGES):
                idx = int(choice) - 1
                self.selected_bridges = [self.BRIDGES[idx]]
                print(f"  ✓ Selected: {self.BRIDGES[idx]}")
                break
            else:
                print("  ✗ Invalid choice. Enter a number 1-5, 'a' for all, or 'n' for none.")

        if self.selected_bridges:
            print()
            print("  Additional bridges can be set up anytime via:")
            print("    Settings → Bridges (in the web console)")

    # ── Step 11: Install bridge dependencies ──────────────────────────────

    def step_11_install_bridges(self) -> None:
        print("\n[Step 11] Installing bridge dependencies...")
        for bridge in self.selected_bridges:
            self.bridge_manager.create_venv(bridge)
            try:
                self.bridge_manager.install_bridge_python_deps(bridge)
            except Exception as e:
                print(f"  ⚠ Python deps for {bridge}: {e}")
            try:
                self.bridge_manager.install_bridge_node_deps(bridge)
            except Exception as e:
                print(f"  ⚠ Node deps for {bridge}: {e}")

    # ── Step 12: Configure bridge tokens ──────────────────────────────────

    def step_12_configure_bridges(self) -> None:
        if not self.selected_bridges:
            return
        print("\n[Step 12] Configuring bridge tokens...")
        _bridges.configure_bridges(
            self.repo_root,
            self.selected_bridges,
            interactive=self.interactive,
        )

    # ── Step 13: Web Console ───────────────────────────────────────────────

    def step_13_web_console(self) -> None:
        print("\n[Step 13] Web Console...")
        if _IS_WHEEL_INSTALL:
            print("  ✓ Console SPA pre-built in wheel — no npm build needed")
            return
        _console.install_python_deps()
        _console.build_frontend(self.repo_root)

    # ── Step 14: Register services ─────────────────────────────────────────

    def _webui_env_vars(self) -> dict:
        """Build Environment= vars for the webui systemd unit / launchd plist."""
        sep = ";" if sys.platform == "win32" else ":"
        pythonpath_dirs = [
            "core/console", "core/gateway", "core/license", "core/compliance",
            "operator/forge", "operator/skill-forge",
        ]
        # Only include dirs that actually exist (source-tree install).
        paths = [str(self.repo_root / d) for d in pythonpath_dirs if (self.repo_root / d).exists()]
        env: dict = {"CORVIN_HOME": str(self.corvin_home)}
        if paths:
            env["PYTHONPATH"] = sep.join(paths)
        return env

    def step_14_register_services(self) -> None:
        print("\n[Step 14] Registering services...")
        # On Windows, service registration requires administrator privileges.
        # Skip gracefully — the console can still run in the foreground.
        if sys.platform == "win32":
            print("  ℹ Windows: service registration requires admin rights — skipped")
            print("    Start services manually from the web console, or run with admin PowerShell:")
            print(f"      Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser")
            print(f"      corvin-install")
            return

        adapter_cmd = self._get_adapter_command()
        if not adapter_cmd:
            # INST-7: wheel install with no runnable adapter source — skip
            # rather than register a `-m operator...` command that collides with
            # the stdlib `operator` module and can never start.
            print("  ℹ Adapter source not found (wheel install) — skipping "
                  "adapter service registration")
        else:
            try:
                self.service_manager.install_service(
                    # Canonical unit name must match bridge.sh / validate.py, which
                    # control `corvin-voice-bridge-adapter.service`. service_manager
                    # prefixes `corvin-`, so the name passed here is voice-bridge-*.
                    name="voice-bridge-adapter",
                    command=adapter_cmd,
                    description="CorvinOS central adapter",
                    auto_start=True,
                )
                print("  ✓ Adapter service registered")
            except Exception as e:
                print(f"  ⚠ Could not register adapter service: {e}")

        for bridge in self.selected_bridges:
            try:
                self.service_manager.install_service(
                    name=f"voice-bridge-{bridge}",
                    command=self._get_bridge_command(bridge),
                    description=f"CorvinOS {bridge} bridge",
                    auto_start=True,
                )
                print(f"  ✓ {bridge} bridge service registered")
            except Exception as e:
                print(f"  ⚠ Could not register {bridge}: {e}")

        # Register the WebUI as a persistent service so it survives reboots.
        # Uses sys.executable (the pip venv Python) so this works for both
        # pip installs and source-tree installs that ran corvin-install.
        try:
            # WA-5/M1: quote the interpreter path so a user profile containing a
            # space (e.g. C:\Users\John Doe\...\python.exe) survives as ONE token
            # when the service managers re-tokenize this command
            # (shlex.split(..., posix=False)); an unquoted path tears at the space.
            webui_cmd = (
                f'"{sys.executable}" -m uvicorn corvin_gateway.app:app'
                " --host 127.0.0.1 --port 8765 --log-level info"
            )
            # WA-19: a plain two-token command needs no shell quoting (unlike
            # inlining `python -c "..."` directly into a systemd unit / launchd
            # plist) — see _autoupdate_entrypoint.py's own docstring. Quoted
            # for the same reason as sys.executable above (a spaced profile
            # path must survive as one token).
            autoupdate_script = (
                Path(__file__).resolve().parents[2]
                / "ops" / "launcher" / "corvin" / "_autoupdate_entrypoint.py"
            )
            pre_exec = f'"{sys.executable}" "{autoupdate_script}"'
            self.service_manager.install_service(
                name="webui",
                command=webui_cmd,
                description="Corvin WebUI — gateway + console (uvicorn)",
                env_vars=self._webui_env_vars(),
                auto_start=True,
                pre_exec=pre_exec,
            )
            print("  ✓ WebUI service registered (corvin-webui.service)")
            # M3: record that the init system now owns the WebUI (launchd
            # RunAtLoad / systemd) so step 17 waits for it instead of starting a
            # SECOND foreground instance that would fight it for port 8765.
            self._webui_service_registered = True
        except Exception as e:
            print(f"  ⚠ Could not register WebUI service: {e}")

    # ── Step 15: Start services ────────────────────────────────────────────

    def step_15_start_services(self) -> None:
        print("\n[Step 15] Starting services...")
        # On Windows, services aren't registered, so skip this step
        if sys.platform == "win32":
            print("  ℹ Windows: services will be available from the web console")
            return

        try:
            self.service_manager.start_service("voice-bridge-adapter")
            print("  ✓ Adapter started")
        except Exception as e:
            print(f"  ⚠ Failed to start adapter: {e}")

        for bridge in self.selected_bridges:
            try:
                self.service_manager.start_service(f"voice-bridge-{bridge}")
                print(f"  ✓ {bridge} bridge started")
            except Exception as e:
                print(f"  ⚠ Failed to start {bridge}: {e}")

    # ── Step 16: Register Claude Code plugins ─────────────────────────────

    def step_16_register_plugins(self) -> None:
        _plugins.ensure_plugins(self.repo_root, interactive=self.interactive)

    # ── Step 17: Start Web Console server ─────────────────────────────────

    def step_17_start_console(self) -> None:
        print("\n[Step 17] Starting Web Console...")
        # Prefer the systemd service registered in step 14 so the server is
        # managed by the init system and survives reboots. Fall back to the
        # in-process Popen path on non-systemd platforms (macOS, Windows).
        if hasattr(self.service_manager, "_run_systemctl"):
            try:
                self.service_manager.start_service("webui")
                # Wait up to 15 s for the port to accept connections.
                for _ in range(30):
                    time.sleep(0.5)
                    try:
                        s = socket.socket()
                        s.settimeout(0.5)
                        s.connect(("127.0.0.1", 8765))
                        s.close()
                        print("  ✓ Web Console running on http://127.0.0.1:8765/console/")
                        return
                    except OSError:
                        pass
                print("  ⚠ Web Console did not respond within 15 s — check: journalctl --user -u corvin-webui")
                return
            except Exception as e:
                print(f"  ⚠ systemd start failed ({e}), falling back to foreground start")
        # macOS (M3): the launchd webui service (RunAtLoad) already started
        # uvicorn in step 14. Starting a SECOND foreground instance here would
        # _kill_port the launchd child, which KeepAlive respawns → both fight
        # for 8765 in a crash-loop. Wait for the launchd-managed server instead
        # of spawning our own.
        if sys.platform == "darwin" and getattr(self, "_webui_service_registered", False):
            for _ in range(30):
                time.sleep(0.5)
                try:
                    s = socket.socket()
                    s.settimeout(0.5)
                    s.connect(("127.0.0.1", 8765))
                    s.close()
                    print("  ✓ Web Console running on http://127.0.0.1:8765/console/ (launchd)")
                    return
                except OSError:
                    pass
            print("  ⚠ WebUI (launchd) did not respond within 15 s — check "
                  "~/.corvin/logs/launchd/webui.err")
            return
        _console.start_server(self.repo_root)

    # ── Step 18: Save config + open browser ───────────────────────────────

    def step_18_finalise(self) -> None:
        print("\n[Step 18] Saving configuration...")
        config = {
            "installed_bridges": self.selected_bridges,
            "corvin_home": str(self.corvin_home),
            "voice_config": str(self.voice_config),
            "version": "0.1.0",
        }
        cfg_path = self.voice_config / "installer.json"
        cfg_path.write_text(json.dumps(config, indent=2))
        if sys.platform != "win32":
            cfg_path.chmod(0o600)
        print(f"  ✓ Config saved to {cfg_path}")

        url = "http://localhost:8765/console/"
        print(f"\n  Web Console  →  {url}")
        print("  Configure bridges and tokens: Settings → Bridges")

    # ── Step 19: Validate installation ────────────────────────────────────

    def step_19_validate(self) -> None:
        assert self.platform is not None
        _validate.run_validation(
            voice_config_dir=self.voice_config,
            has_systemd=self.platform.has_systemd,
            selected_bridges=self.selected_bridges,
        )

    # ── Main entry points ──────────────────────────────────────────────────

    def install(self) -> None:
        """Run full installation (19 steps)."""
        try:
            self.step_1_detect_platform()
            self.step_2_create_directories()
            self.step_3_system_dependencies()   # Node.js must be installed before Claude Code
            self.step_4_install_claude_code()
            self.step_5_claude_login()
            self.step_6_bootstrap_hermes()      # optional — never hard-fails
            self.step_7_setup_stt()
            self.step_8_setup_piper()
            self.step_9_api_keys()
            self.step_10_select_bridges()
            self.step_11_install_bridges()
            self.step_12_configure_bridges()
            self.step_13_web_console()
            self.step_14_register_services()
            self.step_15_start_services()
            self.step_16_register_plugins()
            self.step_17_start_console()
            self.step_18_finalise()
            self.step_19_validate()

            print("\n" + "=" * 60)
            print("✓ CorvinOS installation complete!")
            print("=" * 60)
        except Exception as e:
            import traceback
            print(f"\n✗ Installation failed: {e}")
            traceback.print_exc()
            sys.exit(1)

    def restore(self) -> None:
        """Force-rebuild the web console and restart all services.

        Steps:
          1. Load installed bridges from the installer manifest.
          2. Stop all managed services (adapter, bridges).
          3. Stop the webui (systemd unit or port kill, whichever applies).
          4. Clean-build the React frontend (removes dist/, then npm install + build).
          5. Restart the webui.
          6. Restart all managed services.
        """
        print("\n" + "=" * 60)
        print("CorvinOS Restore — full webui rebuild + service restart")
        print("=" * 60)

        # ── Step 1: Load bridge manifest ───────────────────────────────────
        cfg_path = self.voice_config / "installer.json"
        if cfg_path.exists():
            try:
                config = json.loads(cfg_path.read_text())
                self.selected_bridges = config.get("installed_bridges", [])
                print(f"\n  Manifest : {cfg_path}")
                print(f"  Bridges  : {', '.join(self.selected_bridges) or 'none'}")
            except Exception:
                print("  ⚠ Could not read manifest — assuming all bridges installed")
                self.selected_bridges = self.BRIDGES.copy()
        else:
            print("  ⚠ No installer manifest found — assuming all bridges installed")
            self.selected_bridges = self.BRIDGES.copy()

        # Also pick up any bridge services registered via bridge.sh / manually
        # that aren't tracked in installer.json (e.g. installed before manifest existed).
        if sys.platform != "win32":
            # self.systemd_user_dir (not a re-hardcoded local) so tests can
            # sandbox this scan — a shadowing local here previously made this
            # path always hit the REAL host's systemd dir regardless of the
            # instance attribute set up for testing.
            if self.systemd_user_dir.exists():
                for unit_file in sorted(self.systemd_user_dir.glob("corvin-voice-bridge-*.service")):
                    bridge_id = unit_file.stem.removeprefix("corvin-voice-bridge-")
                    if bridge_id not in self.selected_bridges:
                        self.selected_bridges.append(bridge_id)
                        print(f"  + Detected installed unit: {bridge_id}")

        # ── Step 2: Stop managed services ──────────────────────────────────
        print("\n[1/5] Stopping services...")
        managed_services = ["voice-bridge-adapter"] + [f"voice-bridge-{b}" for b in self.selected_bridges]
        for svc in managed_services:
            try:
                self.service_manager.stop_service(svc)
                print(f"  ✓ Stopped: {svc}")
            except Exception as e:
                print(f"  ⚠ Could not stop {svc}: {e}")

        # ── Step 3: Stop webui ─────────────────────────────────────────────
        print("\n[2/5] Stopping webui...")
        _webui_stopped_via_systemd = False
        if sys.platform != "win32":
            result = subprocess.run(
                ["systemctl", "--user", "stop", "corvin-webui.service"],
                capture_output=True, check=False,
            )
            if result.returncode == 0:
                print("  ✓ corvin-webui.service stopped")
                _webui_stopped_via_systemd = True
            else:
                print("  ℹ corvin-webui.service not running via systemd — killing port 8765")
        from corvinOS.installer.steps.console import _kill_port
        _kill_port(8765)
        print("  ✓ Port 8765 cleared")

        # ── Step 4: Clean-rebuild React frontend ───────────────────────────
        print("\n[3/5] Rebuilding web console frontend (clean)...")
        if _IS_WHEEL_INSTALL:
            print("  ✓ Console SPA pre-built in wheel — no rebuild needed")
        else:
            webnext = self.repo_root / "core" / "console" / "corvin_console" / "web-next"
            dist_dir = webnext / "dist"
            if dist_dir.exists():
                try:
                    shutil.rmtree(dist_dir)
                    print(f"  ✓ Removed {dist_dir}")
                except Exception as e:
                    print(f"  ⚠ Could not remove dist/: {e}")

            ok = _console.build_frontend(self.repo_root)
            if not ok:
                print("\n  ✗ Frontend build FAILED.")
                print(f"    cd {webnext} && npm install && npm run build")
                print("  Services will still be restarted — webui may show a 503 page.")

        # ── Step 5: Restart webui ──────────────────────────────────────────
        print("\n[4/5] Starting webui...")
        if _webui_stopped_via_systemd and sys.platform != "win32":
            result = subprocess.run(
                ["systemctl", "--user", "start", "corvin-webui.service"],
                capture_output=True, check=False,
            )
            if result.returncode == 0:
                print("  ✓ corvin-webui.service started")
            else:
                print("  ⚠ systemd start failed — falling back to direct launch")
                _console.start_server(self.repo_root)
        else:
            _console.start_server(self.repo_root)

        # ── Step 6: Restart managed services ──────────────────────────────
        print("\n[5/5] Restarting services...")
        for svc in managed_services:
            try:
                self.service_manager.start_service(svc)
                print(f"  ✓ Started: {svc}")
            except Exception as e:
                print(f"  ⚠ Could not start {svc}: {e}")

        print("\n" + "=" * 60)
        print("✓ Restore complete.")
        print("  Web Console  →  http://localhost:8765/console/")
        print("=" * 60)

    def uninstall(self, purge: bool = False) -> None:
        """Remove all services, plugins, runtime data, and config (11 steps).

        Args:
            purge: When True, delete all data directories without prompting.
        """
        print("\n" + "=" * 60)
        print("CorvinOS Uninstaller")
        print("=" * 60)

        # Load installer manifest to learn which bridges were installed.
        # Fall back to all known bridges when the manifest is absent (orphaned install).
        cfg_path = self.voice_config / "installer.json"
        if cfg_path.exists():
            try:
                config = json.loads(cfg_path.read_text())
                self.selected_bridges = config.get("installed_bridges", [])
                print(f"  Manifest: {cfg_path}")
                print(f"  Bridges : {', '.join(self.selected_bridges) or 'none'}")
            except Exception:
                print("  ⚠ Could not read manifest — assuming all bridges installed")
                self.selected_bridges = self.BRIDGES.copy()
        else:
            print("  ⚠ No installer manifest found — assuming all bridges installed")
            self.selected_bridges = self.BRIDGES.copy()

        # ── Step 1: Stop + remove services via service manager ────────────
        print("\n[1/11] Stopping and removing services...")
        # "webui" included: step_14 registers it on Linux AND macOS — leaving
        # it out kept the console LaunchAgent/user-unit alive after
        # "uninstall" (macOS relaunched it at every login; WA-7 class).
        managed_services = (["voice-bridge-adapter", "webui"]
                            + [f"voice-bridge-{b}" for b in self.selected_bridges])
        for svc in managed_services:
            try:
                self.service_manager.stop_service(svc)
            except Exception:
                pass
            try:
                self.service_manager.uninstall_service(svc)
                print(f"  ✓ Removed service: {svc}")
            except Exception as e:
                print(f"  ⚠ Could not remove service {svc}: {e}")

        # ── Step 2: Remove remaining systemd unit files ────────────────────
        # Covers units installed by bridge.sh / corvin-install that may
        # not be tracked by the service manager (timers, watchdog, etc.).
        print("\n[2/11] Removing autostart entries (systemd units / Scheduled Task)...")
        if sys.platform != "win32":
            systemd_user = self.systemd_user_dir
            bridge_units = [
                f"corvin-voice-bridge-{b}.service" for b in self.selected_bridges
            ]
            all_units = _SYSTEM_UNITS + bridge_units

            found_files: list[Path] = []
            for unit in all_units:
                for candidate in (
                    systemd_user / unit,
                    systemd_user / "default.target.wants" / unit,
                ):
                    if candidate.exists():
                        found_files.append(candidate)

            if found_files:
                for unit_file in found_files:
                    try:
                        subprocess.run(
                            ["systemctl", "--user", "disable", "--now", unit_file.name],
                            capture_output=True,
                            check=False,
                        )
                        unit_file.unlink()
                        print(f"  ✓ Removed: {unit_file.name}")
                    except Exception as e:
                        print(f"  ⚠ Could not remove {unit_file.name}: {e}")
            else:
                print("  ℹ No named systemd service files found")

            # Glob sweep: catch any remaining corvin-*.service files not in the
            # known list (e.g. installed by bridge.sh, manually, or future units).
            swept = 0
            for leftover in sorted(systemd_user.glob("corvin-*.service")):
                try:
                    subprocess.run(
                        ["systemctl", "--user", "disable", "--now", leftover.name],
                        capture_output=True,
                        check=False,
                    )
                    leftover.unlink()
                    print(f"  ✓ Removed (sweep): {leftover.name}")
                    swept += 1
                except Exception as e:
                    print(f"  ⚠ Could not remove {leftover.name}: {e}")
            # Also clear any symlinks left in *.wants directories
            for wants_dir in systemd_user.glob("*.wants"):
                for link in sorted(wants_dir.glob("corvin-*.service")):
                    try:
                        link.unlink(missing_ok=True)
                        swept += 1
                    except Exception:
                        pass
            if swept:
                print(f"  ✓ Glob sweep removed {swept} additional file(s)")

            try:
                subprocess.run(
                    ["systemctl", "--user", "daemon-reload"],
                    capture_output=True,
                    check=False,
                )
            except Exception:
                pass

            # macOS falls into this non-win32 branch, where every systemctl
            # call is a silent no-op — sweep the LaunchAgents too, or the
            # console (KeepAlive=true) relaunches at every login after
            # "uninstall" (the same WA-7 class fixed for Windows below).
            if sys.platform == "darwin":
                la_dir = Path.home() / "Library" / "LaunchAgents"
                for plist in sorted(la_dir.glob("com.corvin.*.plist")):
                    try:
                        subprocess.run(
                            ["launchctl", "unload", str(plist)],
                            capture_output=True, check=False,
                        )
                        plist.unlink(missing_ok=True)
                        print(f"  ✓ Removed LaunchAgent: {plist.name}")
                    except Exception as e:
                        print(f"  ⚠ Could not remove {plist.name}: {e}")

            # WA-7 (POSIX): an opt-in Stufe-2 always-on service (ADR-0184) is a
            # ROOT-owned systemd system unit (/etc/systemd/system) or a macOS
            # LaunchDaemon (/Library/LaunchDaemons) — registered elevated and
            # NOT removable from this unprivileged uninstall. The sweeps above
            # only touch the user-level Stufe-1 units. Detect it and tell the
            # user to run `sudo corvin-service uninstall` first, else CorvinOS
            # keeps serving on 8765 after "uninstall" (parity with the Windows
            # branch below).
            try:
                from corvinOS.installer.system_service_manager import (  # noqa: PLC0415
                    get_system_service_manager,
                )
                if get_system_service_manager().status("webui") == "registered":
                    print("  ⚠ An always-on (Stufe 2) service is still registered. "
                          "It runs as root and cannot be removed here — first run:  "
                          "sudo corvin-service uninstall")
            except Exception:
                pass
        elif sys.platform == "win32":
            # install.ps1 (the standalone one-liner installer) registers a
            # persistent, infinite-restart, AtLogOn Scheduled Task named
            # "CorvinOS-Console" that self-upgrades on every boot. Nothing
            # here removed it (WindowsServiceManager's own task-name scheme,
            # "CorvinOS\\{name}", is a completely different naming convention
            # and its Windows service registration is skipped entirely — see
            # step 1 above), so "uninstalled" CorvinOS kept auto-restarting
            # and auto-updating forever (adversarial review finding).
            print("  Removing Windows Scheduled Task autostart...")
            for _task in ("CorvinOS-Console",):
                try:
                    query = subprocess.run(
                        ["schtasks", "/query", "/tn", _task],
                        capture_output=True, text=True, check=False,
                    )
                    if query.returncode != 0:
                        print(f"  ℹ Scheduled Task not found: {_task}")
                        continue
                    subprocess.run(
                        ["schtasks", "/end", "/tn", _task],
                        capture_output=True, check=False,
                    )
                    delete = subprocess.run(
                        ["schtasks", "/delete", "/tn", _task, "/f"],
                        capture_output=True, text=True, check=False,
                    )
                    if delete.returncode == 0:
                        print(f"  ✓ Removed Scheduled Task: {_task}")
                    else:
                        print(f"  ⚠ Could not remove Scheduled Task {_task}: "
                              f"{delete.stderr.strip()}")
                except Exception as e:
                    print(f"  ⚠ Could not remove Scheduled Task {_task}: {e}")

            # WA-9: install.ps1 falls back to a Startup-folder shortcut when
            # Register-ScheduledTask is denied (non-admin accounts on
            # managed/family/education Windows images), and always creates a
            # Desktop shortcut regardless. Neither is touched by the
            # schtasks cleanup above — remove them here so uninstall doesn't
            # leave a shortcut that still launches CorvinOS at every login.
            # Use the INJECTABLE shortcut roots resolved in __init__ (never
            # os.environ here) so a win32-mocked test can't unlink a real dev's
            # shortcuts — the roots default to APPDATA/USERPROFILE in production
            # but point at a sandbox under test (live-state-wipe class guard).
            for _root, _label in (
                (self.win_startup_dir, "Startup-folder shortcut"),
                (self.win_desktop_dir, "Desktop shortcut"),
            ):
                if _root is None:
                    continue
                _lnk = _root / "CorvinOS.lnk"
                try:
                    if _lnk.exists():
                        _lnk.unlink()
                        print(f"  ✓ Removed {_label}: {_lnk}")
                except Exception as e:
                    print(f"  ⚠ Could not remove {_label} {_lnk}: {e}")

            # WA-7: also sweep any per-bridge Scheduled Tasks left behind so a
            # bridge doesn't keep auto-launching after "uninstall".
            for _bridge in ("discord", "telegram", "slack", "whatsapp", "email"):
                _bt = f"CorvinOS-Bridge-{_bridge}"
                try:
                    q = subprocess.run(
                        ["schtasks", "/query", "/tn", _bt],
                        capture_output=True, text=True, check=False,
                    )
                    if q.returncode != 0:
                        continue
                    subprocess.run(
                        ["schtasks", "/end", "/tn", _bt],
                        capture_output=True, check=False,
                    )
                    d = subprocess.run(
                        ["schtasks", "/delete", "/tn", _bt, "/f"],
                        capture_output=True, text=True, check=False,
                    )
                    if d.returncode == 0:
                        print(f"  ✓ Removed Scheduled Task: {_bt}")
                except Exception as e:
                    print(f"  ⚠ Could not remove Scheduled Task {_bt}: {e}")

            # WA-7: an opt-in Stufe-2 always-on service (ADR-0184) is registered
            # under an ELEVATED session and is not removable from this
            # unelevated uninstall — detect it and tell the user to run
            # `corvin-service uninstall` from an admin PowerShell first, else
            # CorvinOS keeps running after "uninstall".
            try:
                from corvinOS.installer.system_service_manager import (  # noqa: PLC0415
                    get_system_service_manager,
                )
                if get_system_service_manager().status("webui") == "registered":
                    print("  ⚠ An always-on (Stufe 2) service is still registered. "
                          "It runs elevated and cannot be removed here — first run "
                          "from an ADMIN PowerShell:  corvin-service uninstall")
            except Exception:
                pass
        else:
            print("  ℹ Not on Linux or Windows — skipping autostart cleanup")

        # ── Step 3: Unregister Claude Code plugins ─────────────────────────
        print("\n[3/11] Unregistering Claude Code plugins...")
        _claude = shutil.which("claude")
        if _claude:
            for plugin_id in (
                "voice@corvin-voice-local",
                "cowork@corvin-voice-local",
            ):
                try:
                    result = subprocess.run(
                        ["claude", "plugin", "uninstall", plugin_id],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    if result.returncode == 0:
                        print(f"  ✓ Unregistered plugin: {plugin_id}")
                    else:
                        print(f"  ⚠ Plugin not found (already removed?): {plugin_id}")
                except Exception as e:
                    print(f"  ⚠ Could not unregister {plugin_id}: {e}")
        else:
            print("  ℹ claude CLI not found — plugin entries left as-is")

        # ── Step 4: Remove Claude Code plugin cache ────────────────────────
        print("\n[4/11] Removing Claude Code plugin cache...")
        plugin_cache = self.claude_plugins_dir / "cache" / "corvin-voice-local"
        if plugin_cache.exists():
            try:
                cache_size_mb = sum(
                    f.stat().st_size for f in plugin_cache.rglob("*") if f.is_file()
                ) / (1024 * 1024)
                print(f"  Path: {plugin_cache}  ({cache_size_mb:.1f} MB)")
                if purge or input("  Delete plugin cache? [Y/n]: ").strip().lower() != "n":
                    leftover = _robust_rmtree(plugin_cache)
                    if not leftover:
                        print("  ✓ Removed plugin cache")
                    else:
                        print(f"  ⚠ Could not fully remove plugin cache — "
                              f"{len(leftover)} item(s) locked; delete "
                              f"{plugin_cache} manually.")
                else:
                    print("  ⚠ Kept plugin cache")
            except Exception as e:
                print(f"  ⚠ Could not remove plugin cache: {e}")
        else:
            print("  ℹ Plugin cache not found — nothing to remove")

        # ── Step 5: Remove Claude Code marketplace registration ────────────
        print("\n[5/11] Removing Claude Code marketplace registration...")
        known_marketplaces = self.claude_plugins_dir / "known_marketplaces.json"
        if known_marketplaces.exists():
            try:
                marketplaces = json.loads(known_marketplaces.read_text())
                if "corvin-voice-local" in marketplaces:
                    marketplaces.pop("corvin-voice-local", None)
                    known_marketplaces.write_text(json.dumps(marketplaces, indent=2) + "\n")
                    print("  ✓ Removed marketplace registration: corvin-voice-local")
                else:
                    print("  ℹ Marketplace registration not found")
            except Exception as e:
                print(f"  ⚠ Could not update marketplace registration: {e}")
        else:
            print("  ℹ Marketplace file not found")

        # ── Step 6: Remove Web Console build artifacts ─────────────────────
        print("\n[6/11] Removing Web Console build artifacts...")
        if _IS_WHEEL_INSTALL:
            print("  ℹ pip-wheel install — SPA is inside the wheel, not a build artifact to remove")
            print("  ℹ To remove the package:  pip uninstall corvinos")
        else:
            webnext = self.repo_root / "core" / "console" / "corvin_console" / "web-next"
            for artifact_dir in (webnext / "dist", webnext / "node_modules"):
                if artifact_dir.exists():
                    try:
                        leftover = _robust_rmtree(artifact_dir)
                        if not leftover:
                            print(f"  ✓ Removed: {artifact_dir}")
                        else:
                            print(f"  ⚠ Could not fully remove {artifact_dir} — "
                                  f"{len(leftover)} item(s) locked")
                    except Exception as e:
                        print(f"  ⚠ Could not remove {artifact_dir}: {e}")
                else:
                    print(f"  ℹ Not found: {artifact_dir.name}")

        # ── Step 7: Remove bridge virtual environments ─────────────────────
        print("\n[7/11] Removing bridge virtual environments...")
        for bridge in self.selected_bridges:
            try:
                self.bridge_manager.cleanup_venv(bridge)
            except Exception as e:
                print(f"  ⚠ Could not clean venv for {bridge}: {e}")
        # Also catch orphaned venv dirs not tracked by the manifest
        bridges_root = self.corvin_home / "bridges"
        if bridges_root.exists():
            for venv_dir in bridges_root.glob("*/venv"):
                if venv_dir.is_dir():
                    try:
                        leftover = _robust_rmtree(venv_dir)
                        if not leftover:
                            print(f"  ✓ Removed orphaned venv: {venv_dir}")
                        else:
                            print(f"  ⚠ Could not fully remove {venv_dir} — "
                                  f"{len(leftover)} item(s) locked")
                    except Exception as e:
                        print(f"  ⚠ {e}")

        # ── Step 8: Reset onboarding + engine selection (always, not gated
        # behind purge/prompts below) ──────────────────────────────────────
        # Review finding (2026-07-12): a plain `corvin-uninstall` keeps the
        # onboarding-complete marker and the tenant's selected engine unless
        # the user opts into the Step 9/10 purge prompts below — so a
        # subsequent reinstall silently skips onboarding and reuses the old
        # engine, even though the whole point of uninstall+reinstall is a
        # clean slate. Unlike API keys/audit logs (genuinely worth protecting
        # behind a confirmation prompt), these are just UI/session state with
        # nothing to lose, so they are reset unconditionally.
        print("\n[8/11] Resetting onboarding + engine selection...")
        removed_any = False
        setup_complete_flag = self.voice_config / ".corvin_setup_complete"
        if setup_complete_flag.exists():
            try:
                setup_complete_flag.unlink()
                print(f"  ✓ Removed {setup_complete_flag}")
                removed_any = True
            except OSError as e:
                print(f"  ⚠ Could not remove {setup_complete_flag}: {e}")
        tenants_dir = self.corvin_home / "tenants"
        if tenants_dir.is_dir():
            for tenant_dir in sorted(tenants_dir.iterdir()):
                global_dir = tenant_dir / "global"
                onboarding_json = global_dir / "onboarding.json"
                if onboarding_json.exists():
                    try:
                        onboarding_json.unlink()
                        print(f"  ✓ Removed {onboarding_json}")
                        removed_any = True
                    except OSError as e:
                        print(f"  ⚠ Could not remove {onboarding_json}: {e}")
                tenant_yaml = global_dir / "tenant.corvin.yaml"
                if tenant_yaml.exists():
                    try:
                        import yaml  # type: ignore[import-not-found]
                        doc = yaml.safe_load(tenant_yaml.read_text("utf-8")) or {}
                        spec = doc.get("spec") if isinstance(doc, dict) else None
                        if isinstance(spec, dict) and spec.pop("default_engine", None) is not None:
                            # Atomic write (mirrors the console's own
                            # _save_tenant_yaml): this file is read on every
                            # adapter turn, so a crash/Ctrl-C/AV-lock mid-write
                            # must never leave a truncated file behind.
                            tmp = tenant_yaml.with_suffix(".yaml.tmp")
                            tmp.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
                            tmp.replace(tenant_yaml)
                            print(f"  ✓ Cleared default_engine in {tenant_yaml}")
                            removed_any = True
                    except Exception as e:
                        print(f"  ⚠ Could not reset engine selection in {tenant_yaml}: {e}")
        if not removed_any:
            print("  ℹ Nothing to reset — no prior onboarding/engine state found")

        # ── Step 9: Remove voice config (~/.config/corvin-voice/) ──────────
        print("\n[9/11] Removing voice config directory...")
        print(f"  Path: {self.voice_config}")
        if self.voice_config.exists():
            try:
                config_size_mb = sum(
                    f.stat().st_size for f in self.voice_config.rglob("*") if f.is_file()
                ) / (1024 * 1024)
                print(
                    f"  Contains: API keys, service.env, config.json, "
                    f"Piper models ({config_size_mb:.1f} MB)"
                )
            except Exception:
                pass

            confirmed = purge or (
                input("  Delete voice config (API keys, secrets)? [y/N]: ").strip().lower() == "y"
            )
            if confirmed:
                leftover = _robust_rmtree(self.voice_config)
                if not leftover:
                    print(f"  ✓ Removed {self.voice_config}")
                else:
                    # Security-relevant: never claim the secrets are gone when
                    # they are not. A running process holding a handle (common on
                    # Windows) leaves API keys / service.env on disk.
                    print(f"  ⚠ Could NOT fully remove {self.voice_config} — "
                          f"{len(leftover)} item(s), which may include API keys / "
                          f"service.env, are locked and REMAIN ON DISK.")
                    print("     Close any running CorvinOS process (web console, "
                          "voice bridge, `corvinos-serve`) and re-run "
                          "`corvin-uninstall`,")
                    print(f"     or delete {self.voice_config} manually to remove "
                          f"the secrets.")
            else:
                print(f"  ⚠ Kept {self.voice_config}")
                print(f"     Run `rm -rf {self.voice_config}` manually to finish.")
        else:
            print("  ℹ Not found — nothing to remove")

        # ── Step 10: Remove Corvin home (~/.corvin/) ───────────────────────
        print("\n[10/11] Removing Corvin home directory...")
        print(f"  Path: {self.corvin_home}")
        if self.corvin_home.exists():
            try:
                home_size_mb = sum(
                    f.stat().st_size for f in self.corvin_home.rglob("*") if f.is_file()
                ) / (1024 * 1024)
                print(
                    f"  Contains: sessions, audit logs, forge tools, "
                    f"skill-forge ({home_size_mb:.1f} MB)"
                )
            except Exception:
                pass

            confirmed = purge or (
                input("  Delete Corvin home (audit logs, sessions, models)? [y/N]: ")
                .strip().lower() == "y"
            )
            if confirmed:
                leftover = _robust_rmtree(self.corvin_home)
                if not leftover:
                    print(f"  ✓ Removed {self.corvin_home}")
                else:
                    print(f"  ⚠ Removed most of {self.corvin_home}, but "
                          f"{len(leftover)} item(s) are locked by a running process.")
                    print("     Close any running CorvinOS window (web console, voice "
                          "bridge, `corvinos-serve`) and re-run `corvin-uninstall`,")
                    print(f"     or delete {self.corvin_home} manually.")
            else:
                print(f"  ⚠ Kept {self.corvin_home}")
                print(f"     Run `rm -rf {self.corvin_home}` manually to finish.")
        else:
            print(f"  ℹ {self.corvin_home} not found")

        # ── Step 11: Remove in-repo .corvin/ ──────────────────────────────
        print("\n[11/11] Removing in-repo Corvin directory...")
        repo_corvin = self.repo_root / ".corvin"
        if repo_corvin.exists():
            print(f"  Path: {repo_corvin}")
            confirmed = purge or (
                input("  Delete in-repo Corvin directory? [y/N]: ").strip().lower() == "y"
            )
            if confirmed:
                leftover = _robust_rmtree(repo_corvin)
                if not leftover:
                    print(f"  ✓ Removed {repo_corvin}")
                else:
                    print(f"  ⚠ Could not fully remove {repo_corvin} — "
                          f"{len(leftover)} item(s) locked; delete manually.")
            else:
                print(f"  ⚠ Kept {repo_corvin}")
                print(f"     Run `rm -rf {repo_corvin}` manually to finish.")
        else:
            print("  ℹ Not found — nothing to remove")

        print("\n" + "=" * 60)
        print("✓ CorvinOS uninstall complete")
        print("=" * 60)
        print("\nFinal step — remove the Python package:")
        print("  uv tool uninstall corvinos     (one-liner installer / uv installs)")
        print("  pip uninstall corvinOS -y      (plain pip installs)")
        print()
        # Only suggest deleting the repo checkout on a SOURCE-TREE install. On a
        # wheel/pip install self.repo_root resolves to site-packages, so this
        # hint would tell the user to `rm -rf` their whole Python environment
        # (deleting every installed package). The `uv tool / pip uninstall`
        # above is the correct removal for wheel installs.
        if not _IS_WHEEL_INSTALL:
            print("Optional — delete the repo directory:")
            print(f"  rm -rf {self.repo_root}")
            print()

    # ── private helpers ────────────────────────────────────────────────────

    def _get_adapter_command(self) -> "str | None":
        # Source-tree install: adapter.py lives in the repo checkout.
        adapter = self.repo_root / "operator" / "bridges" / "shared" / "adapter.py"
        if adapter.exists():
            # M1: quote both path components so a spaced install/repo path
            # (e.g. C:\Users\John Doe\...) survives re-tokenization intact.
            return f'"{sys.executable}" "{adapter}"'
        # Wheel install: `operator` is NOT importable as a package (it shadows
        # the stdlib `operator` module), so the old fallback
        # `-m operator.bridges.shared.adapter` could never start (INST-7). The
        # build hook (hatch_build.py) vendors the subtree into
        # corvin_console/_vendor/operator/…; resolve that real file instead.
        try:
            import importlib.util as _ilu  # noqa: PLC0415
            spec = _ilu.find_spec("corvin_console")
            if spec and spec.origin:
                vendored = (
                    Path(spec.origin).parent / "_vendor" / "operator"
                    / "bridges" / "shared" / "adapter.py"
                )
                if vendored.exists():
                    return f'"{sys.executable}" "{vendored}"'
        except Exception:
            pass
        # No runnable adapter found — signal the caller to SKIP registration
        # rather than register a command that can never start.
        return None

    def _get_bridge_command(self, bridge: str) -> str:
        daemon = self.repo_root / "operator" / "bridges" / bridge / "daemon.js"
        if daemon.exists():
            node = shutil.which("node")
            if node:
                # M1: quote both path components (node.exe and the daemon path)
                # so a spaced install path survives re-tokenization intact.
                return f'"{node}" "{daemon}"'
        return f'npm --prefix "{self.corvin_home / "bridges" / bridge}" start'
