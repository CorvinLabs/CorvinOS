"""Cross-platform bridge launcher — Python replacement for `bridge.sh fg`.

Works on Linux, macOS, and Windows (no bash required).

Two directory roles (ADR-0130):
  _source_channel_dir(ch)  = vendored JS source (read-only, in site-packages or repo)
  _runtime_channel_dir(ch) = ~/.corvin/bridges/<ch>/ (writable, user-specific)

node_modules/ is installed into the RUNTIME dir, never into site-packages.
settings.json (credentials) is read from the RUNTIME dir.
Source JS files are materialised into the runtime dir on first start so that
relative require() calls and node_modules resolution both work correctly.

Node.js resolution order:
  1. System PATH
  2. winget install OpenJS.NodeJS.LTS  (Windows 10 1709+)
  3. Binary download from nodejs.org → ~/.corvin/bin/node/

Usage:
  python bridge_manager.py fg            — start adapter + all configured bridges
  python bridge_manager.py ensure-node   — install Node.js if missing, then exit
  python bridge_manager.py doctor        — check prerequisites, no changes

Called by native_backend.py on Windows where bash is unavailable.
On Linux/macOS, bridge.sh fg is preferred; this file also works there.

MUST NOT import anthropic (CI AST lint enforces).
"""
from __future__ import annotations

import importlib
import json
import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional

# Source directory — vendored into site-packages or live in the repo.
# This is READ-ONLY from bridge_manager's perspective.
_BRIDGE_DIR = Path(__file__).parent

_CHANNELS = ["discord", "telegram", "whatsapp", "slack", "email"]


def _corvin_home() -> Path:
    """Resolve the runtime home the SAME way the daemons + adapter do, so the
    launcher (WRITER of settings.json/node_modules/shared-js) and the daemons it
    spawns (READERS via bridge_paths.js::corvinHome) never disagree.

    Order: CORVIN_HOME env → CORVIN_HOME pinned in service.env (the documented
    pin loaded into every spawned daemon) → repo marker (<repo>/.corvin) →
    ~/.corvin. Previously a bare import-time `Path.home()/.corvin` constant
    ignored CORVIN_HOME → reader≠writer (path-audit 2026-06-25 #CRITICAL1).
    """
    env = os.environ.get("CORVIN_HOME")
    if not env:
        _se: dict = {}
        try:
            _load_service_env(_se)  # honour the same pin the daemons receive
        except Exception:  # noqa: BLE001
            pass
        env = _se.get("CORVIN_HOME")
    if env:
        return Path(os.path.expanduser(os.path.expandvars(env)))
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / ".corvin_repo").exists() or (parent / "plugins").is_dir():
            return parent / ".corvin"
    return Path.home() / ".corvin"


def _runtime_bridges_dir() -> Path:
    """User-writable runtime root: <corvin_home>/bridges (holds settings.json +
    node_modules + shared/js). Lazy so a CORVIN_HOME set after import still wins."""
    return _corvin_home() / "bridges"


def _node_home() -> Path:
    """Downloaded-Node cache: <corvin_home>/bin/node (under the single home)."""
    return _corvin_home() / "bin" / "node"


def _voice_config_dir() -> Path:
    """Voice config dir — SSOT, byte-identical to forge.paths.voice_config_dir():
    VOICE_CONFIG_DIR → XDG_CONFIG_HOME → ~/.config/corvin-voice, uniform on every
    platform. bridge_manager merges this service.env into every spawned daemon's
    env, so it MUST resolve the same dir the installer + console write to. The
    former %APPDATA%/Local Windows branch (and the missing VOICE_CONFIG_DIR
    override) left the bridge reading a dir nothing wrote on Windows / under a
    custom XDG, re-opening the reader≠writer STT/TTS split for the bridge path
    (path-audit 2026-07-07 round-2). Guard: tests/test_voice_config_ssot.py."""
    override = os.environ.get("VOICE_CONFIG_DIR", "").strip()
    if override:
        return Path(os.path.expanduser(os.path.expandvars(override)))
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(os.path.expanduser(xdg)) if xdg else (Path.home() / ".config")
    return base / "corvin-voice"

# Captures the tail of the last failed `npm install` stderr so callers (e.g. the
# web console) can surface the real cause instead of an opaque "npm install failed".
_last_materialise_error: Optional[str] = None

# JS file extensions to materialise from source → runtime dir
_JS_COPY_SUFFIXES = frozenset({".js", ".mjs", ".cjs", ".json"})
# Names that must NOT be copied from source to runtime (user-managed files)
_JS_COPY_SKIP = frozenset({"settings.json", "node_modules"})

# Node.js LTS pinned version for binary download fallback
_NODE_VERSION = "v22.16.0"
_NODE_DIST_BASE = "https://nodejs.org/dist"

# Local Node.js binary cache lives under <corvin_home>/bin/node — see _node_home().


# ── Path helpers ───────────────────────────────────────────────────────────────

def _source_channel_dir(channel: str) -> Path:
    """Vendored/source dir containing daemon.js and package.json."""
    return _BRIDGE_DIR / channel


def _runtime_channel_dir(channel: str) -> Path:
    """User-writable runtime dir: ~/.corvin/bridges/<channel>/"""
    return _runtime_bridges_dir() / channel


# ── Node.js discovery ──────────────────────────────────────────────────────────

# WhatsApp's Baileys library requires Node 20+; an older system Node makes the
# bridge's `npm install` fail with a cryptic "requires Node.js 20+" error. Treat
# anything older as unusable so ensure_node() downloads the pinned v22 instead.
_MIN_NODE_MAJOR = 20


def _node_major(node_path: str) -> Optional[int]:
    """Return the major version of a node binary (e.g. 22), or None on error."""
    try:
        r = subprocess.run([node_path, "--version"], capture_output=True, text=True, timeout=5)
        v = (r.stdout or "").strip().lstrip("v")
        return int(v.split(".")[0]) if v else None
    except Exception:  # noqa: BLE001
        return None


def _node_usable(node_path: Optional[str]) -> bool:
    if not node_path:
        return False
    maj = _node_major(node_path)
    return maj is not None and maj >= _MIN_NODE_MAJOR


def find_node() -> Optional[str]:
    """Return a node binary that is NEW ENOUGH (>=20), or None.

    A too-old system Node (e.g. 18) is rejected so ensure_node() downloads the
    pinned LTS instead of letting the bridge's npm install fail.
    """
    node = shutil.which("node")
    if _node_usable(node):
        return node
    local = _local_node_exe()
    if local and local.exists() and _node_usable(str(local)):
        return str(local)
    return None


def _local_node_exe() -> Optional[Path]:
    if sys.platform == "win32":
        return _node_home() / "node.exe"
    return _node_home() / "bin" / "node"


def _find_npm() -> Optional[str]:
    node = find_node()
    if not node:
        return None
    node_dir = Path(node).parent
    for name in ("npm", "npm.cmd"):
        candidate = node_dir / name
        if candidate.exists():
            return str(candidate)
    return shutil.which("npm")


def ensure_node() -> Optional[str]:
    """Return a node binary path, installing Node.js if necessary."""
    node = find_node()
    if node:
        _info(f"Node.js: {node}")
        return node

    _info(f"Node.js >={_MIN_NODE_MAJOR} not found — attempting auto-install...")

    if sys.platform == "win32" and shutil.which("winget"):
        _info("  → winget install OpenJS.NodeJS.LTS")
        rc = subprocess.run(
            [
                "winget", "install", "--silent",
                "--accept-package-agreements",
                "--accept-source-agreements",
                "OpenJS.NodeJS.LTS",
            ],
            check=False,
        ).returncode
        if rc == 0:
            # importlib.invalidate_caches() does NOT update os.environ['PATH'].
            # winget writes to the registry; the current process still has the
            # old PATH snapshot. Check the typical winget install location directly.
            node_candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "nodejs" / "node.exe"
            if node_candidate.exists():
                _info(f"  ✓ Node.js installed via winget: {node_candidate}")
                return str(node_candidate)
            # Fallback: maybe a non-standard prefix was used
            node = shutil.which("node")
            if node:
                _info(f"  ✓ Node.js installed via winget: {node}")
                return node

    # Universal fallback: download binary from nodejs.org
    if _download_node():
        node = find_node()
        if node:
            _info(f"  ✓ Node.js ready: {node}")
            return node

    _info("  Could not install Node.js automatically.")
    _info("  Manual install: https://nodejs.org/en/download")
    return None


def _download_node() -> bool:
    """Download and unpack Node.js LTS binary to _node_home()."""
    machine = platform.machine().lower()

    if sys.platform == "win32":
        arch = "arm64" if "arm" in machine else "x64"
        filename = f"node-{_NODE_VERSION}-win-{arch}.zip"
    elif sys.platform == "darwin":
        arch = "arm64" if "arm" in machine else "x64"
        filename = f"node-{_NODE_VERSION}-darwin-{arch}.tar.gz"
    else:
        arch = "arm64" if ("arm" in machine or "aarch" in machine) else "x64"
        filename = f"node-{_NODE_VERSION}-linux-{arch}.tar.xz"

    url = f"{_NODE_DIST_BASE}/{_NODE_VERSION}/{filename}"
    archive = _node_home().parent / filename
    _node_home().parent.mkdir(parents=True, exist_ok=True)

    _info(f"  Downloading {filename} (~25 MB)...")
    try:
        urllib.request.urlretrieve(url, archive)
    except Exception as exc:
        _info(f"  Download failed: {exc}")
        archive.unlink(missing_ok=True)  # don't leave a partial file on disk
        return False

    # Verify integrity against nodejs.org's published SHASUMS256.txt.
    shasums_url = f"{_NODE_DIST_BASE}/{_NODE_VERSION}/SHASUMS256.txt"
    try:
        import hashlib
        with urllib.request.urlopen(shasums_url, timeout=30) as resp:
            shasums_text = resp.read().decode("utf-8")
        expected_hash = None
        for line in shasums_text.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].strip() == filename:
                expected_hash = parts[0].strip()
                break
        if expected_hash is None:
            _info(f"  SHA256 entry for {filename} not found in SHASUMS256.txt.")
            archive.unlink(missing_ok=True)
            return False
        h = hashlib.sha256()
        with open(archive, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        if h.hexdigest() != expected_hash:
            _info(f"  SHA256 mismatch — download may be corrupt or tampered.")
            archive.unlink(missing_ok=True)
            return False
        _info("  ✓ SHA256 verified.")
    except Exception as exc:
        _info(f"  SHA256 verification failed: {exc} — proceeding without check.")

    _info("  Extracting...")
    try:
        if filename.endswith(".zip"):
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(_node_home().parent)
        else:
            import tarfile
            with tarfile.open(archive) as tf:
                # filter='data' (Python 3.12+) prevents path traversal from archives.
                try:
                    tf.extractall(_node_home().parent, filter="data")
                except TypeError:
                    tf.extractall(_node_home().parent)  # Python < 3.12 fallback

        stem = filename.replace(".zip", "").replace(".tar.gz", "").replace(".tar.xz", "")
        unpacked = _node_home().parent / stem
        if unpacked.exists() and not _node_home().exists():
            unpacked.rename(_node_home())

        archive.unlink(missing_ok=True)
    except Exception as exc:
        _info(f"  Extraction failed: {exc}")
        return False

    exe = _local_node_exe()
    if exe and exe.exists():
        if sys.platform != "win32":
            exe.chmod(0o755)
        return True

    _info("  Node.js binary not found after extraction.")
    return False


# ── Bridge runtime workspace ───────────────────────────────────────────────────

def _materialise_channel(channel: str, npm_bin: str) -> Optional[Path]:
    """Ensure ~/.corvin/bridges/<channel>/ contains JS source + node_modules.

    Copies JS files from the vendored/source dir into the runtime dir so that:
    - node_modules/ lands in the user-writable runtime dir (not site-packages)
    - relative require() calls in daemon.js resolve correctly
    - settings.json (user credentials) is NOT overwritten

    Returns the runtime dir, or None if materialisation or npm install failed.
    """
    global _last_materialise_error
    _last_materialise_error = None
    src = _source_channel_dir(channel)
    runtime = _runtime_channel_dir(channel)
    runtime.mkdir(parents=True, exist_ok=True)

    if not src.is_dir():
        _info(f"  ⚠ source dir not found for {channel}: {src}")
        return None

    # Copy JS source files (skip node_modules, user settings, dirs)
    for item in src.iterdir():
        if item.name in _JS_COPY_SKIP or item.is_dir():
            continue
        if item.suffix in _JS_COPY_SUFFIXES:
            dest = runtime / item.name
            if not dest.exists() or item.stat().st_mtime > dest.stat().st_mtime:
                shutil.copy2(item, dest)

    # npm install in runtime dir (never in source/vendored dir)
    pkg = runtime / "package.json"
    if not pkg.exists():
        # Channel has no npm deps — still valid (e.g. simple webhook bridges)
        return runtime

    nm = runtime / "node_modules"
    lock = runtime / "package-lock.json"
    needs_install = not nm.exists() or (
        lock.exists() and lock.stat().st_mtime > nm.stat().st_mtime
    )
    if needs_install:
        _info(f"  npm install for {channel} in ~/.corvin/bridges/{channel}/ ...")
        # Prepend the resolved node's bin dir to PATH so npm's internal `node`
        # (e.g. the package's `node ./engine-requirements.js` engine check, and
        # any postinstall scripts) resolves to OUR node — not an older system
        # Node still on PATH. Without this, a downloaded v22 npm still spawns the
        # system v18 and Baileys fails its "requires Node 20+" engine gate.
        env = os.environ.copy()
        env["PATH"] = str(Path(npm_bin).parent) + os.pathsep + env.get("PATH", "")
        r = subprocess.run(
            _npm_install_cmd(npm_bin),
            cwd=runtime,
            capture_output=True,
            text=True,
            env=env,
        )
        if r.returncode != 0:
            _last_materialise_error = (r.stderr or r.stdout or "").strip()[-600:]
            _info(f"  npm install failed for {channel}:\n{_last_materialise_error}")
            return None

    return runtime


def _npm_install_cmd(npm_bin: str) -> list[str]:
    """Build a cross-platform `npm install` argv.

    On Windows, npm is a `.cmd` shim that CreateProcess cannot launch directly
    (the same gotcha as `claude.cmd`), so a bare `subprocess.run([npm, ...])`
    fails with FileNotFoundError / "%1 is not a valid Win32 application" — which
    surfaced as "npm install failed" with no QR on fresh Windows boxes.

    Prefer invoking npm-cli.js through the resolved node binary directly: this
    needs no shell, sidesteps the .cmd shim entirely, and pins the exact node
    version. Falls back to `cmd /c npm.cmd` on Windows, or the bare npm path.
    """
    args = ["install", "--no-audit", "--no-fund"]
    node_dir = Path(npm_bin).parent
    node_exe = node_dir / ("node.exe" if sys.platform == "win32" else "node")
    for cli in (
        node_dir / "node_modules" / "npm" / "bin" / "npm-cli.js",                 # Windows bundle layout
        node_dir.parent / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js",  # POSIX bundle layout
    ):
        if cli.exists() and node_exe.exists():
            return [str(node_exe), str(cli), *args]
    if sys.platform == "win32" and npm_bin.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", npm_bin, *args]
    return [npm_bin, *args]


def _materialise_shared_js() -> Optional[Path]:
    """Copy operator/bridges/shared/js/ into ~/.corvin/bridges/shared/js/.

    The per-channel daemons `require('../shared/js/...')` relative to their own
    dir, so when a daemon runs from the RUNTIME dir (~/.corvin/bridges/<ch>/) the
    sibling shared/js/ tree must exist there too — otherwise it crashes with
    "Cannot find module '../shared/js/bridge_paths'". shared/js is pure Node
    (builtins + relative requires, no npm deps), so a file copy is enough.
    """
    src = _BRIDGE_DIR / "shared" / "js"
    if not src.is_dir():
        return None
    dst = _runtime_bridges_dir() / "shared" / "js"
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if item.is_dir() or item.suffix not in _JS_COPY_SUFFIXES:
            continue
        d = dst / item.name
        if not d.exists() or item.stat().st_mtime > d.stat().st_mtime:
            shutil.copy2(item, d)
    return dst


# ── Bridge lifecycle ───────────────────────────────────────────────────────────

def channel_configured(channel: str) -> bool:
    """Return True when the bridge has usable credentials in its runtime dir.

    Reads settings.json from ~/.corvin/bridges/<channel>/settings.json.
    Falls back to the source dir for source-tree installs where settings.json
    lives next to daemon.js.

    WhatsApp (Baileys) does not use settings.json — it is checked separately
    via auth/creds.json so that a missing or corrupt settings.json does not
    block an already-authenticated WhatsApp session.
    """
    # WhatsApp uses Baileys session files, not a settings.json token.
    # Check independently of settings.json so that a missing/corrupt
    # settings.json doesn't silently prevent the daemon from starting.
    if channel == "whatsapp":
        return (
            (_runtime_channel_dir(channel) / "auth" / "creds.json").exists()
            or (_source_channel_dir(channel) / "auth" / "creds.json").exists()
        )

    # All other channels: read settings.json, prefer runtime dir first.
    for settings_path in (
        _runtime_channel_dir(channel) / "settings.json",
        _source_channel_dir(channel) / "settings.json",
    ):
        if not settings_path.exists():
            continue
        try:
            cfg = json.loads(settings_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue

        if channel == "discord":
            return bool(cfg.get("discord_token") or cfg.get("bot_token"))
        if channel == "telegram":
            return bool(cfg.get("telegram_token"))
        if channel == "slack":
            return bool(cfg.get("slack_bot_token") and cfg.get("slack_app_token"))
        if channel == "email":
            return bool(cfg.get("imap_user") and cfg.get("imap_password"))

    return False


def _load_service_env(env: dict) -> None:
    """Merge ~/.config/corvin-voice/service.env into env (no-op if absent).

    service.env wins over the inherited shell environment, matching the
    semantics of bridge.sh's `set -a; . "$ENV_FILE"; set +a` where the
    file values overwrite existing shell variables.

    Surrounding quotes (single or double) are stripped from values so that
    OPENAI_API_KEY="sk-proj-xxx" produces sk-proj-xxx, not "sk-proj-xxx".
    """
    service_env = _voice_config_dir() / "service.env"
    if not service_env.exists():
        return
    for line in service_env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        # strip 'export ' prefix (common in shell-compatible .env files)
        if k.startswith("export "):
            k = k[len("export "):].strip()
        v = v.strip()
        # strip surrounding single or double quotes
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
            v = v[1:-1]
        if k:
            env[k] = v  # service.env wins (matches bridge.sh set-a semantics)


def start_fg(channels: Optional[list[str]] = None) -> int:
    """Start adapter + bridge daemons in the foreground. Returns exit code."""
    node = ensure_node()
    if node is None:
        return 1

    npm = _find_npm()
    if npm is None:
        _info("npm not found next to node binary — unexpected state.")
        return 1

    active = [ch for ch in (channels or _CHANNELS) if channel_configured(ch)]
    if not active:
        _info("No bridges configured. Add credentials to:")
        for ch in _CHANNELS:
            _info(f"  {_runtime_channel_dir(ch) / 'settings.json'}")
        _info("\nRun 'corvin start' again once at least one bridge is configured.")
        _info("(Adapter will still start for console-only use.)")

    # Materialise each active channel into its runtime dir
    runtime_dirs: dict[str, Path] = {}
    for ch in active:
        rt = _materialise_channel(ch, npm)
        if rt is not None:
            runtime_dirs[ch] = rt
        else:
            _info(f"  ⚠ {ch}: materialisation failed — skipping")

    # Daemons require('../shared/js/...') relative to their runtime dir, so the
    # sibling shared/js/ tree must be present there too.
    if runtime_dirs:
        _materialise_shared_js()

    processes: list[subprocess.Popen] = []

    def _spawn(label: str, cmd: list[str], cwd: Path) -> None:
        env = os.environ.copy()
        _load_service_env(env)
        proc = subprocess.Popen(cmd, cwd=cwd, env=env)
        processes.append(proc)
        _info(f"  started {label} (pid={proc.pid})")

    def _teardown(sig_name: str = "Ctrl-C") -> None:
        _info(f"\n  {sig_name} — stopping bridge...")
        for p in processes:
            try:
                p.terminate()
            except Exception:
                pass
        deadline = time.monotonic() + 3.0
        for p in processes:
            remaining = max(0.0, deadline - time.monotonic())
            try:
                p.wait(timeout=remaining)
            except Exception:
                pass
        for p in processes:
            try:
                p.kill()
            except Exception:
                pass
        _info("  All processes stopped.")

    # Adapter (Python) — runs from shared/ source dir (pure Python, no npm)
    adapter_py = _BRIDGE_DIR / "shared" / "adapter.py"
    if adapter_py.exists():
        _spawn("adapter", [sys.executable, str(adapter_py)], _BRIDGE_DIR / "shared")
    else:
        _info(f"  ⚠ adapter.py not found at {adapter_py}")

    # Bridge daemons (Node.js) — run from RUNTIME dirs (where node_modules lives)
    for ch, rt in runtime_dirs.items():
        daemon = rt / "daemon.js"
        if not daemon.exists():
            _info(f"  ⚠ {ch}/daemon.js not found in runtime dir {rt} — skipping")
            continue
        _spawn(ch, [node, str(daemon)], rt)

    if not processes:
        _info("No processes started.")
        return 1

    _info("\n  Bridge running. Ctrl-C to stop.\n")

    if sys.platform == "win32":
        try:
            while True:
                for p in processes:
                    rc = p.poll()
                    if rc is not None:
                        _info(f"  ⚠ process (pid={p.pid}) exited unexpectedly (rc={rc})")
                        _teardown("unexpected process exit")
                        return 1
                time.sleep(1.0)
        except KeyboardInterrupt:
            _teardown("Ctrl-C")
    else:
        def _handler(sig: int, _frame) -> None:
            _teardown(signal.Signals(sig).name)
            sys.exit(0)

        signal.signal(signal.SIGINT, _handler)
        signal.signal(signal.SIGTERM, _handler)
        try:
            os.wait()
        except ChildProcessError:
            pass

    return 0


# ── Single-channel detached start (web-console "Start bridge" button) ───────────

# Per-channel local HTTP port the daemon listens on (QR / pairing). WhatsApp's
# daemon serves its pairing QR here; the console proxies it to the browser.
_CHANNEL_HTTP_PORT = {"whatsapp": 7891}


def _port_open(port: int, host: str = "127.0.0.1") -> bool:
    """True if something is already listening on host:port (daemon up)."""
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def start_channel_detached(
    channel: str,
    progress: Optional["callable"] = None,  # type: ignore[name-defined]
    extra_args: Optional[list[str]] = None,
) -> dict:
    """Start ONE bridge daemon detached (non-blocking) so its QR/HTTP comes up.

    This is the engine behind the web console's "Start WhatsApp bridge" button.
    Unlike start_fg(), it does NOT gate on channel_configured(): WhatsApp needs
    the daemon RUNNING to show the pairing QR *before* any credentials exist
    (chicken-and-egg). Installs Node.js + npm deps on demand (one-time).

    `progress` (optional) receives short phase strings. Never raises.

    Returns a status dict:
      {ok: bool, pid?: int, already_running?: bool, node_missing?: bool, error?: str}
    """
    def _p(msg: str) -> None:
        if progress:
            try:
                progress(msg)
            except Exception:  # noqa: BLE001
                pass

    try:
        port = _CHANNEL_HTTP_PORT.get(channel)
        if port and _port_open(port):
            return {"ok": True, "already_running": True}

        # Node.js: a fresh box has none → ensure_node downloads ~25 MB. Tell the
        # user that's what the (otherwise silent, minute-long) wait is.
        if find_node() is None:
            _p("Installing Node.js runtime (~25 MB, one-time)…")
        else:
            _p("Checking Node.js…")
        node = ensure_node()
        if node is None:
            return {
                "ok": False, "node_missing": True,
                "error": "Node.js is required for the WhatsApp bridge and could not be "
                         "installed automatically — install it, then click Start again.",
            }
        npm = _find_npm()
        if npm is None:
            return {"ok": False, "error": "npm was not found next to the Node.js binary."}

        _p("Installing WhatsApp dependencies (one-time, up to a minute)…")
        rt = _materialise_channel(channel, npm)
        if rt is None:
            # Map the npm failure to a REASON CODE — never return the raw stderr
            # tail to the client (it carries absolute filesystem paths = the OS
            # username, GDPR-relevant PII / infra detail). The full tail stays in
            # the server log (_materialise_channel already logged it) + the npm
            # debug log. reason = node_too_old | network | disk_full | npm_failed.
            detail = (_last_materialise_error or "").lower()
            if "node.js 20" in detail or "engine" in detail and "node" in detail:
                reason = "node_too_old"
                msg = "WhatsApp needs Node.js 20+ — the bundled runtime did not apply. Retry, or install Node 20+."
            elif any(k in detail for k in ("etarget", "enotfound", "network", "getaddrinfo", "econnrefused", "registry")):
                reason = "network"
                msg = "Could not reach the npm registry to install WhatsApp dependencies — check the network and retry."
            elif "enospc" in detail or "no space" in detail:
                reason = "disk_full"
                msg = "Not enough disk space to install WhatsApp dependencies."
            else:
                reason = "npm_failed"
                msg = "Installing the WhatsApp dependencies failed — see the server log for details, then retry."
            return {"ok": False, "error": msg, "reason": reason}

        # Daemons require('../shared/js/...') relative to their dir, so the
        # sibling shared/js/ tree must exist in the runtime root too.
        _materialise_shared_js()

        daemon = rt / "daemon.js"
        if not daemon.exists():
            return {"ok": False, "error": f"{channel}/daemon.js not found in {rt}."}

        _p("Starting WhatsApp bridge…")
        env = os.environ.copy()
        _load_service_env(env)
        # Prepend our node's bin dir so the daemon (and anything it spawns)
        # resolves the same >=20 node we validated, not an older system Node.
        env["PATH"] = str(Path(node).parent) + os.pathsep + env.get("PATH", "")
        if port:
            env.setdefault("WA_HTTP_PORT", str(port))
        cmd = [node, str(daemon)] + list(extra_args or [])
        # Capture the daemon's early output to a logfile so a crash-on-boot (e.g.
        # a missing module, a bad port) surfaces as a real error instead of a
        # silent "nothing happened, no QR".
        log_path = rt / "daemon-start.log"
        log_fh = open(log_path, "wb")
        kwargs: dict = {"stdout": log_fh, "stderr": subprocess.STDOUT}
        if sys.platform == "win32":
            kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        proc = subprocess.Popen(cmd, cwd=str(rt), env=env, **kwargs)

        # Brief liveness probe: if the daemon dies within ~3s it crashed on boot.
        for _ in range(6):
            time.sleep(0.5)
            if proc.poll() is not None:
                # The daemon's own log (daemon-start.log) can contain sender JIDs
                # / phone numbers / message text — NEVER return its tail to the
                # client. Surface only the exit code + a pointer to the local log.
                try:
                    log_fh.close()
                except Exception:  # noqa: BLE001
                    pass
                _info(f"  {channel} daemon exited on boot (code {proc.returncode}); see {log_path}")
                return {
                    "ok": False,
                    "reason": f"daemon_exited_{proc.returncode}",
                    "error": (f"The WhatsApp bridge exited right after starting (exit code "
                              f"{proc.returncode}). See the bridge log on the server for details, then retry."),
                }
        _p("Bridge started — waiting for WhatsApp to generate the QR…")
        return {"ok": True, "pid": proc.pid}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"Unexpected error starting {channel}: {exc}"}


# ── Doctor ─────────────────────────────────────────────────────────────────────

def cmd_doctor() -> int:
    """Print prerequisite status. Returns 1 if anything is missing."""
    failures = 0
    node = find_node()
    if node:
        ver = subprocess.run(
            [node, "--version"], capture_output=True, text=True
        ).stdout.strip()
        _info(f"  ✓ Node.js {ver} ({node})")
    else:
        _info("  ✗ Node.js — not found (run: python bridge_manager.py ensure-node)")
        failures += 1

    npm = _find_npm()
    _info(f"  {'✓' if npm else '✗'} npm ({npm or 'not found'})")
    if not npm:
        failures += 1

    _info(f"  ✓ Python {sys.version.split()[0]} ({sys.executable})")

    _info("")
    _info("Bridge channels:")
    for ch in _CHANNELS:
        configured = channel_configured(ch)
        rt = _runtime_channel_dir(ch)
        nm_ok = (rt / "node_modules").exists()
        status = "✓" if configured else "○"
        label = "configured" if configured else "not configured"
        npm_status = " [node_modules ✓]" if nm_ok else " [npm install pending]"
        _info(f"  {status} {ch}: {label}{npm_status}")
        _info(f"    settings: {rt / 'settings.json'}")

    return 0 if failures == 0 else 1


# ── Entry point ────────────────────────────────────────────────────────────────

def _info(msg: str) -> None:
    print(msg, flush=True)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "fg"
    if cmd == "fg":
        sys.exit(start_fg())
    elif cmd == "ensure-node":
        sys.exit(0 if ensure_node() else 1)
    elif cmd == "doctor":
        sys.exit(cmd_doctor())
    else:
        _info("Usage: bridge_manager.py {fg|ensure-node|doctor}")
        sys.exit(2)
