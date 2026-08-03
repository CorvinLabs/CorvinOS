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

# signal + teams were long console-configurable but absent here — the console
# saved their settings and then NOTHING could ever start the daemons (silent
# half-wiring, fresh-install audit 2026-07-22). Both have complete daemons.
# Since 2026-07-28 the list lives in ONE place (shared/channels.py) because the
# same omission had independently re-occurred in session_reset, settings_view
# and bridges_migrate; the fallback keeps this module importable if it is ever
# loaded without shared/ reachable.
# APPEND, never insert(0): shared/ holds ~200 flat modules with names like
# `audit`, `paths`, `profile`, `types` and `channels`, and prepending that
# directory makes every one of them shadow a stdlib or site-packages module for
# the whole launcher process. That exact mistake killed the webui service once
# (`operator/__init__.py` shadowing stdlib `operator`, 5187bd4) and was fixed
# once already in the plugin loader (94325c8).
_SHARED_DIR = str(_BRIDGE_DIR / "shared")
if _SHARED_DIR not in sys.path:
    sys.path.append(_SHARED_DIR)
try:
    from channels import BRIDGE_CHANNELS as _CHANNELS_TUPLE  # type: ignore
    _CHANNELS = list(_CHANNELS_TUPLE)
except Exception:  # noqa: BLE001 — never let a path quirk break the launcher
    _CHANNELS = ["discord", "telegram", "whatsapp", "slack", "email", "signal", "teams"]

try:
    from agents._win_shim import no_console_window_flags  # type: ignore
except Exception:  # noqa: BLE001 — see _run()'s own fallback below
    no_console_window_flags = None  # type: ignore[assignment]


def _run(cmd, **kwargs):
    """subprocess.run(), but never flashes a console window on Windows.

    2026-08-03, reported live: after a fresh Windows install, starting a
    bridge from the console still popped a visible console window — despite
    0.10.91/0.10.95/850e50f each having already fixed a DIFFERENT spawn site
    in this exact codebase for this exact symptom. Root cause: this module
    alone has 10+ one-shot subprocess.run() call sites (node --version,
    winget/npm install, tasklist, wmic, powershell CIM, taskkill...), each
    hand-rolled, each needing its own CREATE_NO_WINDOW — every prior fix
    round closed one call site and left the next one to be found the hard
    way. bridge_manager.py's web-console caller has no console of its own
    (started detached/hidden), so spawning ANY console-subsystem child
    without CREATE_NO_WINDOW makes Windows allocate a brand-new, visible one
    for it. Routing every plain subprocess.run() in this file through this
    one wrapper makes the flag structural instead of a per-call-site thing
    to remember — a new call site gets it for free.

    Deliberately NOT used for start_fg()'s own Popen (the explicit
    foreground CLI command — inheriting the user's own console there is
    correct, not a bug) or the long-lived daemon Popen calls in
    start_channel_detached()/ensure_adapter_detached() (already carry their
    own DETACHED_PROCESS combo, which implies CREATE_NO_WINDOW). no-op on
    non-Windows: subprocess.run() accepts creationflags=0 on every platform.
    """
    if no_console_window_flags is not None:
        kwargs.setdefault("creationflags", no_console_window_flags())
    return subprocess.run(cmd, **kwargs)


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


def _runtime_shared_dir() -> Path:
    """<corvin_home>/bridges/shared — the queue root (inbox/outbox) a daemon
    resolves when it runs from its runtime dir (SHARED = __dirname/../shared)."""
    return _runtime_bridges_dir() / "shared"


def _adapter_queue_env(env: dict) -> None:
    """Pin the adapter's queue dirs to the RUNTIME shared dir.

    adapter.py defaults its queues to its own source/vendored shared/ dir,
    while daemons spawned from runtime dirs poll <corvin_home>/bridges/shared
    — two different directories on every wheel install. Result: inbound
    envelopes were never picked up and replies never reached a daemon (the
    fresh-install "bot never answers" class). Explicit env aligns the adapter
    with the daemons; bridge.sh source-tree mode (both sides source shared/)
    is untouched because it never calls this. setdefault → an operator
    override in service.env still wins.
    """
    shared = _runtime_shared_dir()
    for key, sub in (("ADAPTER_INBOX", "inbox"),
                     ("ADAPTER_OUTBOX", "outbox"),
                     ("ADAPTER_PROCESSED", "processed")):
        p = shared / sub
        p.mkdir(parents=True, exist_ok=True)
        env.setdefault(key, str(p))
    # adapter.py historically treated a set ADAPTER_INBOX as "I am a test
    # sandbox" and forked the audit chain + re-rooted CORVIN_HOME. We pin the
    # queues in PRODUCTION, so declare it explicitly — without this the
    # adapter would split its GDPR audit.jsonl and consent state into a
    # throwaway dir. Also pin CORVIN_HOME so every component (console, adapter,
    # daemons) resolves the SAME home.
    env["CORVIN_ADAPTER_SANDBOX"] = "0"
    env.setdefault("CORVIN_HOME", str(_corvin_home()))
    # Where the Python CLIs live. Same reader≠writer split as the queues above,
    # one layer up: shared/js is mirrored next to the daemon but shared/*.py and
    # voice/scripts/*.py are not, so a daemon started here resolved every
    # `*_CLI` constant in in_chat_commands.js into <corvin_home>/bridges/shared/,
    # which holds no Python at all. `/new`, `/role`, `/quota`, `/consent`,
    # `/goal`, `/engine`, `/audit` and the rest of the shell-out commands were
    # ENOENT on every wheel install and fine in every git checkout, because
    # there `__dirname/..` happens to be the source tree.
    # See bridge_paths.js::operatorRoot (the reader).
    env.setdefault("CORVIN_BRIDGE_OPERATOR_ROOT", str(_BRIDGE_DIR.parent))


# ── Node.js discovery ──────────────────────────────────────────────────────────

# WhatsApp's Baileys library requires Node 20+; an older system Node makes the
# bridge's `npm install` fail with a cryptic "requires Node.js 20+" error. Treat
# anything older as unusable so ensure_node() downloads the pinned v22 instead.
_MIN_NODE_MAJOR = 20


def _node_major(node_path: str) -> Optional[int]:
    """Return the major version of a node binary (e.g. 22), or None on error."""
    try:
        r = _run([node_path, "--version"], capture_output=True, text=True, timeout=5)
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
        rc = _run(
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
        r = _run(
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
        if channel == "signal":
            return bool(cfg.get("signal_number"))
        if channel == "teams":
            return bool(cfg.get("microsoft_app_id") and cfg.get("microsoft_app_password"))

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


def _graceful_signal(p: subprocess.Popen) -> None:
    """Ask *p* to shut itself down cleanly, platform-appropriately.

    POSIX: .terminate() == SIGTERM, delivered to and handled by the child
    (adapter.py registers a SIGTERM handler for exactly this — a graceful
    drain of in-flight claude turns before exiting).

    Windows: .terminate() calls Win32 TerminateProcess() — a hard kill with
    NO signal delivery and NO handler invocation at all, unlike POSIX. The
    graceful-drain path never fired there before this fix (every stop/
    restart abruptly killed in-flight turns). CTRL_BREAK_EVENT is the real
    Windows equivalent — Python maps it to a deliverable SIGBREAK, but ONLY
    in a process spawned with CREATE_NEW_PROCESS_GROUP (see start_fg's
    _spawn_kwargs; adapter.py registers a SIGBREAK handler for exactly this).
    """
    if sys.platform.startswith("win") and hasattr(signal, "CTRL_BREAK_EVENT"):
        try:
            p.send_signal(signal.CTRL_BREAK_EVENT)
            return
        except Exception:
            pass
    try:
        p.terminate()
    except Exception:
        pass


def _hard_kill(p: subprocess.Popen) -> None:
    """Unconditionally end *p*, reaching its whole process tree on Windows.

    p.kill() alone only reaches the tracked PID on Windows — when that PID
    is a cmd.exe wrapper (npm's claude.cmd shim, see agents/_win_shim.py),
    the real node.exe process it spawned is a grandchild TerminateProcess
    never touches, leaking it as an orphan. taskkill /T /F reaches the
    whole tree; ships with every Windows install, no extra dependency.
    """
    if sys.platform.startswith("win"):
        try:
            _run(
                ["taskkill", "/T", "/F", "/PID", str(p.pid)],
                capture_output=True, timeout=10,
            )
            return
        except Exception:
            pass
    try:
        p.kill()
    except Exception:
        pass


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

    # Windows-only: passed to Popen so a later CTRL_BREAK_EVENT reaches this
    # child (and its own process group) without also hitting bridge_manager
    # itself — by default a spawned child shares the parent's console/
    # process group on Windows.
    _spawn_kwargs: dict = {}
    if sys.platform.startswith("win"):
        _spawn_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    def _spawn(label: str, cmd: list[str], cwd: Path,
               mutate_env: Optional["callable"] = None) -> None:  # type: ignore[name-defined]
        env = os.environ.copy()
        _load_service_env(env)
        # Same reason as in start_channel_detached: these daemons also run from
        # the runtime dir, where only shared/js was mirrored.
        env.setdefault("CORVIN_BRIDGE_OPERATOR_ROOT", str(_BRIDGE_DIR.parent))
        if mutate_env is not None:
            mutate_env(env)
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, **_spawn_kwargs)
        processes.append(proc)
        _info(f"  started {label} (pid={proc.pid})")

    def _teardown(sig_name: str = "Ctrl-C") -> None:
        _info(f"\n  {sig_name} — stopping bridge...")
        for p in processes:
            _graceful_signal(p)
        deadline = time.monotonic() + 3.0
        for p in processes:
            remaining = max(0.0, deadline - time.monotonic())
            try:
                p.wait(timeout=remaining)
            except Exception:
                pass
        for p in processes:
            if p.poll() is None:
                _hard_kill(p)
        _info("  All processes stopped.")

    # Adapter (Python) — runs from shared/ source dir (pure Python, no npm),
    # but its QUEUES are pinned to the runtime shared dir the daemons poll.
    adapter_py = _BRIDGE_DIR / "shared" / "adapter.py"
    if adapter_py.exists():
        _spawn("adapter", [sys.executable, str(adapter_py)], _BRIDGE_DIR / "shared",
               mutate_env=_adapter_queue_env)
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


def _systemd_unit_active(channel: str) -> bool:
    """True if ``corvin-voice-bridge-<channel>.service`` is active/activating.

    Found during the ADR-0215 adversarial review (2026-07-24): the
    onboarding wizard's WhatsApp start path (`_run_wa_start_job` in
    routes/setup.py) calls `start_channel_detached()`, whose ONLY
    duplicate-start guard was `_port_open()` — a TCP probe. In the race
    window between a systemd unit start (`corvin-voice-bridge-whatsapp.
    service`) and that daemon actually binding its pairing-QR port, a
    concurrent wizard click could spawn a SECOND daemon via a raw
    `subprocess.Popen(..., start_new_session=True)`, entirely outside the
    systemd cgroup — `systemctl stop` would never see or kill it (orphan
    process). This check closes that window: if the unit is already
    active OR in the middle of starting, treat the channel as already
    running instead of racing a second raw spawn. Mirrors
    `routes/bridges.py::_unit_active()` (kept independent, not imported,
    since this module must not depend on the FastAPI console package).
    Best-effort: `systemctl` absent/erroring means "no unit control here"
    (e.g. non-systemd platform), so the existing port-probe guard still
    applies — this is additive, not a replacement.
    """
    try:
        proc = _run(
            ["systemctl", "--user", "is-active", f"corvin-voice-bridge-{channel}.service"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    state = proc.stdout.strip()
    return state in ("active", "activating", "reloading")


def _adapter_pidfile() -> Path:
    return _corvin_home() / "run" / "adapter.pid"


def _pid_alive(pid: int) -> bool:
    """Cross-platform liveness probe. NEVER use os.kill(pid, 0) on Windows —
    any non-CTRL signal value there unconditionally TerminateProcess()es."""
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            out = _run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, check=False, timeout=10,
            ).stdout
        except (OSError, subprocess.TimeoutExpired):
            return False
        return f'"{pid}"' in out
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _pid_cmdline(pid: int) -> str:
    """Best-effort process command line for cmdline-verified liveness.
    Empty string when unknown (treated as 'cannot confirm' by callers)."""
    if pid <= 0:
        return ""
    if os.name == "nt":
        procs, confident = _win_process_snapshot()
        if not confident:
            return ""
        return procs.get(pid, "")
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            return fh.read().replace(b"\x00", b" ").decode("utf-8", "replace")
    except OSError:
        # macOS / no procfs — fall back to ps.
        try:
            return _run(
                ["ps", "-p", str(pid), "-o", "command="],
                capture_output=True, text=True, check=False, timeout=10,
            ).stdout
        except (OSError, subprocess.TimeoutExpired):
            return ""


def _adapter_running_pid(adapter_py: Path) -> int:
    """Return the PID of an adapter already polling OUR shared inbox, or 0.

    Cross-launcher aware: a bridge.sh- or systemd-started adapter never wrote
    our pidfile, so trusting the pidfile alone would spawn a SECOND adapter
    double-processing the same inbox. Two-stage check:
      1. Our pidfile PID, but only if its cmdline still names this adapter.py
         (defeats PID reuse — a recycled PID belonging to an unrelated process
         must not read as 'adapter alive').
      2. A system-wide scan for any live process whose cmdline runs this exact
         adapter.py path (catches adapters started by another launcher)."""
    marker = str(adapter_py)

    pidfile = _adapter_pidfile()
    try:
        pid = int(pidfile.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        pid = 0
    if pid and _pid_alive(pid) and "adapter.py" in _pid_cmdline(pid):
        return pid

    # System-wide scan (POSIX: /proc; else pgrep -f).
    proc_dir = Path("/proc")
    if proc_dir.is_dir():
        for entry in proc_dir.iterdir():
            if not entry.name.isdigit():
                continue
            other = int(entry.name)
            if other == os.getpid():
                continue
            if marker in _pid_cmdline(other):
                return other
    else:
        try:
            out = _run(
                ["pgrep", "-f", marker], capture_output=True, text=True,
                check=False, timeout=10,
            ).stdout
            for line in out.split():
                if line.strip().isdigit() and int(line) != os.getpid():
                    return int(line)
        except (OSError, subprocess.TimeoutExpired):
            pass
    return 0


def adapter_running_pid() -> int:
    """Public wrapper: PID of an adapter polling our shared inbox, or 0.

    Additive (ADR-0238). The plugin supervisors need to tell "bridge daemon up"
    apart from "bridge daemon up but nothing polls the queue" — the half-bridge
    state in which a bot receives every message and answers none. The logic is
    _adapter_running_pid()'s; this only spares external callers from reaching
    into a private name.
    """
    return _adapter_running_pid(_BRIDGE_DIR / "shared" / "adapter.py")


# ── Windows process enumeration (2026-07-30 — wmic deprecation fix) ────────
#
# wmic.exe has been deprecated since Windows 10 21H1 and is REMOVED BY
# DEFAULT on newer Windows 11 builds (Microsoft's own guidance: use CIM
# cmdlets instead). Every Windows process-enumeration call in this module
# used to shell out to wmic exclusively — on a machine where it is absent,
# every call fails with FileNotFoundError, is caught, and returns
# confident=False. _scan_channel_daemon_pid()'s caller (bridge supervisor,
# core/plugins/corvin_plugins/bridges/supervisor.py) treats
# confident=False as "cannot verify, refuse to start" (by design — starting
# a possible duplicate is worse) — so a channel (observed live: WhatsApp)
# could NEVER auto-start on an affected Windows install, no config change
# possible, no error the operator could act on beyond "cannot verify
# whether a daemon is already running". _pid_cmdline() has the same
# dependency and gates adapter-duplicate detection (adapter_running_pid()).
#
# Fix: prefer PowerShell's Get-CimInstance (the modern WMI-via-CIM
# provider — NOT the deprecated wmic.exe CLI, so unaffected by its
# removal) for a full process snapshot, falling back to wmic only if
# PowerShell itself is unavailable (exotic/locked-down installs).
def _win_process_snapshot() -> tuple[dict[int, str], bool]:
    """(pid -> commandline map, confident) for every process on Windows.

    confident=False only when NEITHER PowerShell's CIM cmdlets NOR the
    legacy wmic.exe could be run at all — "found nothing" and "could not
    look" must stay distinguishable (see _scan_channel_daemon_pid).
    """
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if powershell:
        try:
            out = _run(
                [powershell, "-NoProfile", "-NonInteractive", "-Command",
                 "Get-CimInstance Win32_Process | "
                 "ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }"],
                capture_output=True, text=True, check=False, timeout=15,
            ).stdout
        except (OSError, subprocess.TimeoutExpired):
            out = ""
        if out.strip():
            procs: dict[int, str] = {}
            for line in out.splitlines():
                pid_s, sep, cmdline = line.partition("\t")
                if sep and pid_s.strip().isdigit():
                    procs[int(pid_s.strip())] = cmdline
            if procs:
                return procs, True

    # Fallback: legacy wmic.exe (older Windows, or PowerShell itself blocked).
    try:
        out = _run(
            ["wmic", "process", "get", "ProcessId,CommandLine", "/FORMAT:LIST"],
            capture_output=True, text=True, check=False, timeout=15,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return {}, False
    if not out.strip():
        return {}, False
    procs = {}
    cur_cmdline: str | None = None
    for line in out.splitlines():
        line = line.strip()
        if line.lower().startswith("commandline="):
            cur_cmdline = line.split("=", 1)[1]
        elif line.lower().startswith("processid=") and cur_cmdline is not None:
            value = line.split("=", 1)[1].strip()
            if value.isdigit():
                procs[int(value)] = cur_cmdline
            cur_cmdline = None
    return procs, True


def _cmdline_names_daemon(cmdline: str, channel: str) -> bool:
    """True when a process command line runs THIS channel's daemon.js.

    Path-separator- and case-normalised so the same marker matches a POSIX
    runtime dir, a Windows runtime dir and a source-tree checkout. Matching on
    the '<channel>/daemon.js' tail (not on the bare channel name) keeps
    'telegram' from matching a process that merely mentions telegram somewhere.
    """
    if not cmdline:
        return False
    norm = cmdline.replace("\\", "/").lower()
    return f"/{channel.lower()}/daemon.js" in norm


def _scan_channel_daemon_pid(channel: str) -> tuple[int, bool]:
    """(pid, confident) for a live process running <channel>/daemon.js.

    ``confident`` is False when NO enumeration method was available on this
    platform — "I found nothing" and "I could not look" are different answers,
    and a caller that conflates them starts a second daemon.
    """
    proc_dir = Path("/proc")
    if proc_dir.is_dir():
        try:
            entries = list(proc_dir.iterdir())
        except OSError:
            return 0, False
        for entry in entries:
            if not entry.name.isdigit():
                continue
            pid = int(entry.name)
            if pid == os.getpid():
                continue
            if _cmdline_names_daemon(_pid_cmdline(pid), channel):
                return pid, True
        return 0, True

    if os.name == "nt":
        procs, confident = _win_process_snapshot()
        if not confident:
            return 0, False
        for pid, cmdline in procs.items():
            if pid == os.getpid():
                continue
            if _cmdline_names_daemon(cmdline, channel):
                return pid, True
        return 0, True

    # macOS / BSD — no procfs, no wmic.
    try:
        out = _run(
            ["pgrep", "-f", f"{channel}/daemon.js"],
            capture_output=True, text=True, check=False, timeout=10,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return 0, False
    for token in out.split():
        if token.strip().isdigit() and int(token) != os.getpid():
            return int(token), True
    return 0, True


def channel_daemon_running(channel: str) -> dict:
    """Is a daemon for `channel` already running? Additive probe (ADR-0238).

    Returns ``{"running": bool, "via": str, "pid": int, "confident": bool}``.

    Existing start paths are untouched: start_channel_detached() keeps its own
    port + systemd guards. This is the GENERIC probe the plugin supervisors need,
    because a channel started by bridge.sh, by systemd or by hand writes no
    pidfile we own — and two daemons on one outbox answer every message twice
    while `systemctl stop` only ever sees one of them (the ADR-0215 orphan
    class).

    Layered cheapest-first, and each layer is authoritative on its own:
      1. systemd unit active OR activating (closes the started-but-not-bound race)
      2. the channel's well-known local port is bound (WhatsApp's pairing port)
      3. a live process whose cmdline runs <channel>/daemon.js

    ``confident=False`` means the process scan could not run at all on this
    platform. Callers MUST treat that as "do not start" rather than as "nothing
    is running".
    """
    if _systemd_unit_active(channel):
        return {"running": True, "via": "systemd", "pid": 0, "confident": True}

    port = _CHANNEL_HTTP_PORT.get(channel)
    if port and _port_open(port):
        return {"running": True, "via": "port", "pid": 0, "confident": True}

    pid, confident = _scan_channel_daemon_pid(channel)
    if pid:
        return {"running": True, "via": "process", "pid": pid, "confident": True}
    return {"running": False, "via": "", "pid": 0, "confident": confident}


def ensure_adapter_detached() -> dict:
    """Start adapter.py detached iff it is not already running.

    start_channel_detached() — the engine behind the web console's bridge
    Start button — used to launch ONLY the Node daemon. On installs without
    bridge.sh/systemd nothing ever polled the shared inbox: the bridge paired
    fine, inbound envelopes piled up, and the bot never answered. The adapter
    is the other half of every bridge; starting one without the other is a
    broken state, so the button path now ensures both.

    Idempotency is cmdline-verified and cross-launcher aware (see
    _adapter_running_pid) so a second console click, or a click after
    bridge.sh/systemd already started an adapter, never spawns a duplicate
    that double-processes the inbox.
    """
    adapter_py = _BRIDGE_DIR / "shared" / "adapter.py"
    if not adapter_py.exists():
        return {"ok": False, "error": f"adapter.py not found at {adapter_py}"}

    running = _adapter_running_pid(adapter_py)
    if running:
        return {"ok": True, "already_running": True, "pid": running}

    pidfile = _adapter_pidfile()
    env = os.environ.copy()
    _load_service_env(env)
    _adapter_queue_env(env)

    log_dir = _corvin_home() / "run" / "log"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_fh = open(log_dir / "adapter-start.log", "ab")
    except OSError:
        log_fh = subprocess.DEVNULL  # type: ignore[assignment]
    kwargs: dict = {"stdout": log_fh, "stderr": subprocess.STDOUT}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(
            [sys.executable, str(adapter_py)],
            cwd=str(adapter_py.parent), env=env, **kwargs,
        )
    except OSError as exc:
        return {"ok": False, "error": f"adapter spawn failed: {exc}"}
    try:
        pidfile.parent.mkdir(parents=True, exist_ok=True)
        pidfile.write_text(str(proc.pid), encoding="utf-8")
    except OSError:
        pass  # liveness falls back to "spawn again next click, pid probe fails"

    # Brief liveness probe — an import error dies within a second.
    time.sleep(1.0)
    if proc.poll() is not None:
        return {
            "ok": False,
            "error": (f"adapter exited on boot (code {proc.returncode}); "
                      f"see {log_dir / 'adapter-start.log'}"),
        }
    return {"ok": True, "pid": proc.pid}


_WINDOWS_AUTOSTART_ATTEMPTED: set[str] = set()


def ensure_windows_autostart(channel: str) -> dict:
    """Register `channel` for Scheduled-Task restart-forever supervision on
    Windows (systemd's `Restart=always` has no Windows equivalent).

    Reported live: a fresh Windows install or a pip upgrade left the bridge
    NOT running after a reboot / relogin, or dead-and-never-restarted after a
    crash. Root cause: start_channel_detached() (this function's caller, the
    engine behind the Console's "Start bridge" button) genuinely detaches the
    daemon+adapter processes from the caller's terminal (DETACHED_PROCESS,
    see the module docstring above) — but a detached process is still just a
    ONE-SHOT spawn. Nothing supervises it: no restart on crash, and nothing
    re-launches it after a reboot or user relogin. Console autostart IS
    registered by default (install.ps1's Install-CorvinAutostart), but
    bridge autostart was previously opt-in-only (`bridge.ps1
    install-autostart`, a separate command a user has to know exists and run
    by hand) -- so on a stock install, only the console ever came back after
    a reboot; the bridge silently stayed dead until someone noticed and
    clicked Start again.

    Fix: call bridge.ps1's own `install-autostart <channel>` (Register-
    ScheduledTask, AtLogOn trigger, -RestartCount 999, -Hidden -- the exact
    same Scheduled-Task shape already used for the console) automatically,
    every time a channel is started this way. Reuses bridge.ps1 --
    `_BRIDGE_DIR` always resolves to bridge_manager.py's OWN directory, so
    `_BRIDGE_DIR / "bridge.ps1"` finds the right sibling copy whether this
    is a dev checkout (operator/bridges/) or a vendored wheel install
    (corvin_console/_vendor/operator/bridges/) -- no separate resolution
    needed, no duplicated PowerShell logic to drift out of sync with
    bridge.ps1's own Install-AutostartTask.

    bridge.ps1 install-autostart's own Register-ScheduledTask call is
    already idempotent (Unregister ... -ErrorAction SilentlyContinue then
    re-register), so calling this twice for the same channel is safe --
    _WINDOWS_AUTOSTART_ATTEMPTED just skips the (~1-2s) PowerShell spawn on
    a repeat call within the same running process, it is not a correctness
    requirement. Best-effort and non-fatal: a failure here must never block
    the actual bridge start that already succeeded (the reason this always
    runs AFTER the daemon+adapter are confirmed up, never before)."""
    if not sys.platform.startswith("win"):
        return {"ok": True, "skipped": "not windows"}
    if channel in _WINDOWS_AUTOSTART_ATTEMPTED:
        return {"ok": True, "already_attempted": True}
    bridge_ps1 = _BRIDGE_DIR / "bridge.ps1"
    if not bridge_ps1.exists():
        return {"ok": False, "error": f"bridge.ps1 not found at {bridge_ps1}"}
    try:
        proc = _run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(bridge_ps1), "install-autostart", channel],
            cwd=str(_BRIDGE_DIR), capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _info(f"  ⚠ windows autostart registration for {channel} failed: {exc}")
        return {"ok": False, "error": str(exc)}
    if proc.returncode != 0:
        _info(
            f"  ⚠ windows autostart registration for {channel} exited "
            f"{proc.returncode}: {(proc.stderr or proc.stdout or '').strip()[:300]}"
        )
        return {"ok": False, "error": f"install-autostart exited {proc.returncode}"}
    _WINDOWS_AUTOSTART_ATTEMPTED.add(channel)
    return {"ok": True}


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
            # 2026-08-03, reported live: a package upgrade that fixes THIS
            # function's own Windows Scheduled-Task registration (e.g. the
            # wscript.exe-wrapper fix in bridge.ps1) never took effect on an
            # install where the bridge daemon was already running from
            # BEFORE the upgrade -- this early return used to skip
            # ensure_windows_autostart() entirely whenever the daemon was
            # already up, so the OLD Task (still pointing at the pre-fix
            # Action) kept firing at every logon/crash-restart forever,
            # with no code path that ever refreshed it short of manually
            # stopping the bridge first. Register-ScheduledTask's own
            # Unregister-then-Register is idempotent and cheap (~1-2s), so
            # refreshing it here on every call -- not just on a fresh
            # start -- makes a package upgrade self-heal an already-running
            # bridge's autostart registration the next time this is
            # invoked, without requiring the operator to manually stop and
            # restart the bridge first.
            autostart_status = ensure_windows_autostart(channel)
            return {"ok": True, "already_running": True, "windows_autostart": autostart_status}
        # ADR-0215: close the systemd-unit-starting-but-port-not-bound-yet
        # race window (see _systemd_unit_active() docstring) before falling
        # through to a raw, non-systemd-tracked spawn below.
        if _systemd_unit_active(channel):
            return {"ok": True, "already_running": True, "via": "systemd"}

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
        # The daemon runs from the RUNTIME dir (cwd=rt below) where only
        # shared/js is mirrored, so it cannot find the Python CLIs its
        # in-chat commands shell out to by walking up from __dirname.
        # See _adapter_queue_env's note and bridge_paths.js::operatorRoot.
        env.setdefault("CORVIN_BRIDGE_OPERATOR_ROOT", str(_BRIDGE_DIR.parent))
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
        # The daemon alone is half a bridge — without the adapter nothing
        # polls the inbox and the bot never answers. Non-fatal on failure
        # (the QR flow must still come up), but surfaced to the caller.
        _p("Starting the message adapter…")
        adapter_status = ensure_adapter_detached()
        if not adapter_status.get("ok"):
            _info(f"  ⚠ adapter start failed: {adapter_status.get('error')}")

        # A detached process is still just a one-shot spawn — nothing
        # restarts it after a crash or a reboot/relogin without this.
        # Windows-only (no-ops elsewhere); best-effort, never blocks the
        # bridge start that already succeeded above.
        autostart_status = ensure_windows_autostart(channel)
        if not autostart_status.get("ok"):
            _info(f"  ⚠ windows autostart registration failed: {autostart_status.get('error')}")

        _p("Bridge started — waiting for WhatsApp to generate the QR…")
        return {
            "ok": True, "pid": proc.pid, "adapter": adapter_status,
            "windows_autostart": autostart_status,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"Unexpected error starting {channel}: {exc}"}


# ── Doctor ─────────────────────────────────────────────────────────────────────

def cmd_doctor() -> int:
    """Print prerequisite status. Returns 1 if anything is missing."""
    failures = 0
    node = find_node()
    if node:
        ver = _run(
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
