"""Hermes/Ollama bootstrap helper — ADR-0125 Zero-Config Engine Onboarding.

Selects the appropriate Ollama model based on available system RAM and optionally
installs Ollama + pulls the model.

RAM thresholds (models MUST match HermesEngine.HERMES_MODEL_ALIASES — Hermes
defaults to qwen3:8b via the "hermes-balanced" alias, so bootstrap pulls the
SAME qwen3 family; pulling qwen2.5 left Hermes without its configured model and
"sofort einsatzfähig" failed):
  < 6 GB  → qwen3:1.7b  ("hermes-fast")
  ≥ 6 GB  → qwen3:8b    ("hermes-balanced", the default)

Bootstrap is intentionally opt-in (not called automatically at adapter start).
The console POST /settings/engine/bootstrap endpoint is the only call site. It
also STARTS `ollama serve` when Ollama is installed but not yet reachable — the
common "Ollama not reachable" state — so a fresh box reaches a working Hermes.

MUST NOT import anthropic (CI AST lint enforces).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Callable, Optional

_OLLAMA_BASE_URL = "http://localhost:11434"


def get_available_ram_gb() -> float:
    """Return total system RAM in GB. Cross-platform. Falls back to 4.0 on read error."""
    try:
        if sys.platform == "linux":
            mem_info = Path("/proc/meminfo").read_text()
            for line in mem_info.splitlines():
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return kb / 1024 / 1024
        elif sys.platform == "darwin":
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True, timeout=5.0,
            )
            if result.returncode == 0:
                return int(result.stdout.strip()) / (1024 ** 3)
        elif sys.platform == "win32":
            import ctypes  # noqa: PLC0415
            stat = ctypes.c_ulonglong(0)
            ctypes.windll.kernel32.GetPhysicallyInstalledSystemMemory(ctypes.byref(stat))  # type: ignore[attr-defined]
            return stat.value / (1024 ** 2)  # KB → GB
    except Exception:  # noqa: BLE001
        pass
    return 4.0


def select_model_for_ram(ram_gb: float) -> str:
    """Return the recommended Ollama model tag for the given RAM amount.

    Aligned with HermesEngine.HERMES_MODEL_ALIASES: the default Hermes model is
    qwen3:8b ("hermes-balanced"), with qwen3:1.7b ("hermes-fast") for small RAM.
    """
    if ram_gb < 6.0:
        return "qwen3:1.7b"
    return "qwen3:8b"


def is_ollama_reachable(base_url: str = _OLLAMA_BASE_URL, timeout: float = 2.0) -> bool:
    """True if the Ollama HTTP API answers on /api/tags."""
    try:
        with urllib.request.urlopen(f"{base_url}/api/tags", timeout=timeout) as resp:
            return getattr(resp, "status", 200) == 200
    except Exception:  # noqa: BLE001
        return False


def _ollama_app_win() -> Optional[str]:
    """Path to the Ollama Windows desktop app ("ollama app.exe"), if installed.

    On Windows the desktop app — NOT `ollama serve` — is the canonical thing that
    starts and supervises the local server (it also survives reboots via the
    tray). Launching it is far more reliable than a detached `ollama serve`.
    """
    if sys.platform != "win32":
        return None
    import os
    for base in (os.environ.get("LOCALAPPDATA", ""), os.environ.get("ProgramFiles", ""),
                 os.environ.get("ProgramW6432", "")):
        if not base:
            continue
        for rel in ("Programs/Ollama/ollama app.exe", "Ollama/ollama app.exe",
                    "Programs/Ollama/ollama.exe"):
            p = Path(base) / rel
            if p.is_file():
                return str(p)
    return None


def ensure_ollama_running(timeout: float = 40.0) -> bool:
    """Make the Ollama server reachable.

    If it already answers, return True. Otherwise, when Ollama is installed but
    the server is not running, start it and wait for the HTTP API. On Windows we
    launch the desktop app ("ollama app.exe") — the canonical server starter —
    and also fall back to `ollama serve`; on POSIX we start `ollama serve`
    detached. Returns False if Ollama is not installed or did not come up in
    time. Never raises.
    """
    if is_ollama_reachable():
        return True
    ollama = _ollama_bin()
    if ollama is None:
        return False
    started = False
    # Windows: prefer the desktop app (it owns the server lifecycle).
    app = _ollama_app_win()
    if app:
        try:
            subprocess.Popen([app], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=0x00000008 | 0x00000200)  # DETACHED | NEW_GROUP
            started = True
        except Exception:  # noqa: BLE001
            pass
    try:
        kwargs: dict = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = 0x00000008 | 0x00000200
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen(
            [ollama, "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **kwargs,
        )
        started = True
    except Exception:  # noqa: BLE001
        if not started:
            return False
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_ollama_reachable():
            return True
        time.sleep(0.5)
    return False


def _ollama_bin() -> Optional[str]:
    """Resolve the ollama executable: PATH first, then known install locations.

    After a fresh winget/brew/curl install, the ollama binary is NOT on the
    PATH of the ALREADY-running console process (PATH is captured at start), so
    a bare ``ollama pull`` raises FileNotFoundError and the model never lands.
    Fall back to the per-platform default install paths so serve+pull work in the
    same session as the install.
    """
    found = shutil.which("ollama")
    if found:
        return found
    import os
    candidates: list[str] = []
    if sys.platform == "win32":
        for base in (os.environ.get("LOCALAPPDATA", ""), os.environ.get("ProgramFiles", ""),
                     os.environ.get("ProgramW6432", "")):
            if base:
                candidates.append(str(Path(base) / "Programs" / "Ollama" / "ollama.exe"))
                candidates.append(str(Path(base) / "Ollama" / "ollama.exe"))
    elif sys.platform == "darwin":
        candidates += ["/usr/local/bin/ollama", "/opt/homebrew/bin/ollama",
                       "/Applications/Ollama.app/Contents/Resources/ollama"]
    else:
        candidates += ["/usr/local/bin/ollama", "/usr/bin/ollama"]
    for c in candidates:
        if c and Path(c).is_file():
            return c
    return None


def is_ollama_installed() -> bool:
    return _ollama_bin() is not None


def _run(cmd: list[str], timeout: float) -> tuple[int, str, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return -1, "", ""


def install_ollama() -> bool:
    """Install Ollama via the official script/package manager. Returns True on success.

    Linux:   curl the official install.sh via bash.
    macOS:   brew install ollama (if brew available), otherwise returns False.
    Windows: winget install Ollama.Ollama (requires Windows 10 1709+ with winget).
    """
    if sys.platform == "linux":
        rc, _, _ = _run(
            ["bash", "-c", "curl -fsSL https://ollama.ai/install.sh | sh"],
            timeout=300.0,
        )
        return rc == 0
    if sys.platform == "darwin":
        if shutil.which("brew"):
            rc, _, _ = _run(["brew", "install", "ollama"], timeout=300.0)
            return rc == 0
        return False
    if sys.platform == "win32":
        # Ollama ships a native Windows binary since v0.1.32 (Jan 2024).
        # winget is built into Windows 10 1709+ and Windows 11.
        if shutil.which("winget"):
            rc, _, _ = _run(
                [
                    "winget", "install", "--silent",
                    "--accept-package-agreements",
                    "--accept-source-agreements",
                    "Ollama.Ollama",
                ],
                timeout=300.0,
            )
            return rc == 0
        return False
    return False


def _diagnose_ollama_serve() -> str:
    """Best-effort: capture WHY `ollama serve` fails to bind, so the opaque
    "not reachable" surfaces an actionable cause (address-in-use, permission
    denied, missing $HOME, etc.). Runs serve briefly and returns its stderr tail.
    """
    ollama = _ollama_bin()
    if not ollama:
        return "ollama executable not found on PATH or known install locations"
    try:
        r = subprocess.run([ollama, "serve"], capture_output=True, text=True, timeout=4.0)
        return ((r.stderr or r.stdout or "").strip().replace("\n", " "))[:400]
    except subprocess.TimeoutExpired as e:  # serve was still running at timeout
        raw = e.stderr or e.stdout or b""
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        return (raw.strip().replace("\n", " ")[:400]
                or "serve started but the API did not answer in time")
    except Exception as e:  # noqa: BLE001
        return f"{type(e).__name__}: {e}"[:400]


def pull_model(model: str, timeout: float = 1800.0, stream: bool = False,
               on_progress: Optional[Callable[[str], None]] = None) -> bool:
    """Pull an Ollama model. Returns True on success.

    A model pull downloads several GB and takes minutes. With ``stream=False``
    (default) the output is captured — fine for background/non-interactive use.
    With ``stream=True`` the download progress is surfaced LIVE so a terminal
    never looks frozen (the "Step 6 hangs" symptom):
      * ``on_progress`` given  → each output line is forwarded to it.
      * ``on_progress`` None    → Ollama's native progress bar is inherited by
        the current terminal (works identically on Linux, macOS, Windows).
    """
    ollama = _ollama_bin() or "ollama"
    cmd = [ollama, "pull", model]
    if not stream:
        rc, _, _ = _run(cmd, timeout=timeout)
        return rc == 0
    if on_progress is None:
        # Inherit stdio → Ollama draws its real-time download bar in the terminal.
        try:
            return subprocess.run(cmd, timeout=timeout).returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return False
    # Programmatic streaming: forward each progress line to the callback.
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1)
    except (FileNotFoundError, OSError):
        return False
    deadline = time.monotonic() + timeout
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\r\n")
            if line:
                try:
                    on_progress(line)
                except Exception:  # noqa: BLE001
                    pass
            if time.monotonic() > deadline:
                proc.kill()
                return False
        return proc.wait(timeout=30) == 0
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
        return False


def bootstrap_hermes(
    force_model: Optional[str] = None,
    progress: Optional[Callable[[str], None]] = None,
    stream: bool = False,
) -> dict:
    """Auto-bootstrap Hermes:
    1. Select model based on available RAM (or use force_model).
    2. Install Ollama if missing (Linux only via official script).
    3. Pull the selected model.

    `progress` (optional) is called with a short human-readable phase string at
    each step so a caller can surface live progress (the model pull alone takes
    several minutes). Never raises inside the callback path.

    Returns a status dict with keys:
      model_selected, ram_gb, ollama_installed, ollama_running, model_pulled, error

    Never raises — unexpected errors are captured in result['error'].
    """
    def _p(msg: str) -> None:
        if progress:
            try:
                progress(msg)
            except Exception:  # noqa: BLE001
                pass

    try:
        ram_gb = get_available_ram_gb()
        model = force_model or select_model_for_ram(ram_gb)
        _p(f"Selected model {model} ({ram_gb:.0f} GB RAM)")

        status: dict = {
            "model_selected": model,
            "ram_gb": round(ram_gb, 1),
            "ollama_installed": is_ollama_installed(),
            "ollama_running": False,
            "model_pulled": False,
            "error": None,
        }

        if not status["ollama_installed"]:
            if sys.platform == "win32" and not shutil.which("winget"):
                status["error"] = (
                    "winget not found — install Ollama manually from "
                    "https://ollama.ai/download/windows and re-run bootstrap."
                )
                return status
            if sys.platform == "darwin" and not shutil.which("brew"):
                status["error"] = (
                    "Ollama auto-install on macOS requires Homebrew. "
                    "Install Homebrew (https://brew.sh) and re-run, "
                    "or download Ollama from https://ollama.ai/download/mac"
                )
                return status
            _p("Installing Ollama…")
            if not install_ollama():
                status["error"] = (
                    "Ollama installation failed — install manually from https://ollama.ai"
                )
                return status
            status["ollama_installed"] = True

        # Make sure the server is actually reachable — installing (or having
        # installed) Ollama does not guarantee a running server. This is the
        # "Ollama not reachable" state the Setup wizard reports; start it now.
        _p("Starting Ollama server…")
        status["ollama_running"] = ensure_ollama_running()
        if not status["ollama_running"]:
            diag = _diagnose_ollama_serve()
            status["serve_error"] = diag
            status["error"] = (
                "Ollama is installed but the server is not reachable — start it "
                "with `ollama serve` (default http://localhost:11434), then re-run."
                + (f"  [ollama serve says: {diag}]" if diag else "")
            )
            return status

        _p(f"Downloading model {model} — this can take several minutes…")
        # stream=True surfaces the live download progress so the caller's terminal
        # never looks frozen during the multi-GB pull ("Step 6 hangs"). We inherit
        # stdio (on_progress=None) so Ollama's native \r-updated progress bar
        # renders live — line-forwarding would stall inside a single large layer
        # (Ollama uses \r, not \n, within a layer). Phase text still goes through
        # the `progress` callback above.
        status["model_pulled"] = pull_model(model, stream=stream)
        if not status["model_pulled"]:
            status["error"] = f"Failed to pull {model} — run: ollama pull {model}"
        else:
            _p("Model ready")

        return status
    except Exception as exc:  # noqa: BLE001
        return {
            "model_selected": force_model or "unknown",
            "ram_gb": 0.0,
            "ollama_installed": False,
            "ollama_running": False,
            "model_pulled": False,
            "error": f"Unexpected bootstrap error: {exc}",
        }
