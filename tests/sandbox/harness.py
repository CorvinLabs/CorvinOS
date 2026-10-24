"""Two-tenant sandbox harness for the release-readiness P0 proofs (W-8, skeleton).

* Real ``uvicorn`` subprocesses, never Starlette's TestClient: the console app
  (``corvin_console.app:create_app``) and the production host (``corvin_gateway.app:app``,
  which mounts the console under ``/v1/console`` and runs ``boot_platform`` / the ADR-0232
  tripwire). Each runs in its own process group; teardown kills the group.
* Code under test is THIS checkout: the server ``PYTHONPATH`` is the venv's editable-install
  path list (``_editable_impl_*.pth``) with the main checkout prefix rewritten to ``REPO_ROOT``,
  in the same order. A sandbox-only ``sitecustomize`` (on the server's ``PYTHONPATH``, never in
  product code) records where every loaded module came from and every tripwire call, into files
  under the sandbox root, so guards can check the RUNNING process.
* Server env is built from an ALLOWLIST: sandbox ``HOME``/``CORVIN_HOME``/``XDG_*``, absolute
  ``CORVIN_AUDIT_ANCHOR_KEY``, ``TMPDIR`` inside the sandbox, telemetry opt-outs, dead proxies.
  Nothing else from the caller's env (no ``PYTEST_*``, ``CLAUDE_*``, API keys, tokens).
* ``git`` resolves to a shim that logs (credentials redacted) and exits 97. Absolute
  ``/usr/bin/git`` is NOT caught; the proxy variables are a soft network block, not an egress
  filter.
* ``LiveStateTripwire``: targeted checks on the operator's real state (live chain tails, dev
  memory hashes, KG config, voice chain-key dirs). Read-only.
"""
from __future__ import annotations

import hashlib
import json
import os
import pwd
import secrets
import signal
import site
import socket
import subprocess
import sys
import sysconfig
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
LIVE_PORTS = frozenset({8000, 8765})
COOKIE = "corvin_console_sid"
TENANT_A = "sbxa"
TENANT_B = "sbxb"
SANDBOX_TENANT_PREFIX = "sbx"
GIT_EXIT = 97


# ── locations ────────────────────────────────────────────────────────────────

def real_home() -> Path:
    """The operator's home from the passwd database, independent of any ``HOME`` override."""
    return Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()


def main_checkout_root() -> Path:
    """Root of the main checkout this worktree belongs to (``git rev-parse --git-common-dir``).

    Runs the REAL git with the caller's env, read-only. Equals ``REPO_ROOT`` outside a worktree,
    and when there is no git or no checkout (e.g. a CI runner on an unpacked tree).
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True, text=True, timeout=20, check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return REPO_ROOT
    return Path(out).resolve().parent


MAIN_ROOT = main_checkout_root()


def live_corvin_roots() -> list[Path]:
    return [MAIN_ROOT / ".corvin", real_home() / ".corvin"]


def live_paths() -> list[Path]:
    return [*live_corvin_roots(), real_home() / "CorvinOS" / ".corvin", REPO_ROOT / ".corvin"]


def refuse_live_path(path: Path) -> None:
    """Raise if ``path`` reaches the live install or the real home, or is outside the temp dir."""
    resolved = Path(path).resolve()
    for live in live_paths():
        lv = live.resolve()
        if resolved == lv or lv in resolved.parents or resolved in lv.parents:
            raise RuntimeError(f"sandbox refuses live path: {resolved}")
    tmp = Path(tempfile.gettempdir()).resolve()
    if tmp not in resolved.parents and resolved != tmp:
        raise RuntimeError(f"sandbox root must live under {tmp}, got {resolved}")
    home = real_home()
    if home in resolved.parents or resolved == home:
        raise RuntimeError(f"sandbox root must not be under the real home: {resolved}")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    return free_port() if port in LIVE_PORTS else port


def _listen_inodes(port: int) -> set[str]:
    """Socket inodes in LISTEN state on ``port`` (Linux ``/proc/net/tcp{,6}``)."""
    inodes: set[str] = set()
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = Path(table).read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            f = line.split()
            if len(f) > 9 and f[3] == "0A" and int(f[1].rsplit(":", 1)[1], 16) == port:
                inodes.add(f[9])
    return inodes


def owns_listener(pid: int, port: int) -> bool:
    """True when ``pid`` itself holds the listening socket on ``port`` — so a foreign listener
    that grabbed the port is never mistaken for the server we started."""
    inodes = _listen_inodes(port)
    if not inodes:
        return False
    try:
        fds = os.listdir(f"/proc/{pid}/fd")
    except OSError:
        return False
    for fd in fds:
        try:
            target = os.readlink(f"/proc/{pid}/fd/{fd}")
        except OSError:
            continue
        if target.startswith("socket:[") and target[8:-1] in inodes:
            return True
    return False


def origin_problems(mods: dict[str, str]) -> list[str]:
    """Modules of a running server that did not come from this tree (see BLOCKER-1 in WP-02)."""
    repo, main = REPO_ROOT, MAIN_ROOT
    out: list[str] = []
    if "corvin_console" not in mods:
        return ["corvin_console not loaded (no module dump)"]
    for name, f in mods.items():
        p = Path(f).resolve()
        if p.is_relative_to(repo) or p.is_relative_to(main / ".venv"):
            continue
        if name.split(".")[0].lower().startswith("corvin") or (main != repo and p.is_relative_to(main)):
            out.append(f"{name} -> {f}")
    return out


def _site_packages() -> list[Path]:
    dirs = [Path(p) for p in site.getsitepackages()]
    dirs.append(Path(sysconfig.get_paths()["purelib"]))
    return [d for d in dict.fromkeys(dirs) if d.is_dir()]


def editable_pythonpath() -> list[str]:
    """The venv's editable-install path entries, main-checkout prefix → ``REPO_ROOT``, in order."""
    main = str(MAIN_ROOT)
    out: list[str] = []
    for sp in _site_packages():
        for pth in sorted(sp.glob("_editable_impl_*.pth")):
            for line in pth.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith(("#", "import ")):
                    continue
                if line == main or line.startswith(main + os.sep):
                    line = str(REPO_ROOT) + line[len(main):]
                out.append(line)
    if str(REPO_ROOT) not in out:
        out.insert(0, str(REPO_ROOT))
    return list(dict.fromkeys(out))


# ── sandbox-only probe (sitecustomize on the server PYTHONPATH) ──────────────

SITECUSTOMIZE = r'''
"""Sandbox-only probe for tests/sandbox. Never shipped; lives in the sandbox root."""
import json, os, sys, threading, time, importlib.abc

_DIR = os.environ.get("SBX_PROBE_DIR")
_PID = os.getpid()


def _dump_modules():
    while True:
        try:
            mods = {}
            for name, m in list(sys.modules.items()):
                f = getattr(m, "__file__", None)
                if f:
                    mods[name] = f
            tmp = os.path.join(_DIR, f"modules-{_PID}.json.tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(mods, fh)
            os.replace(tmp, os.path.join(_DIR, f"modules-{_PID}.json"))
        except Exception:
            pass
        time.sleep(0.5)


def _record(fn, ok, err=""):
    try:
        with open(os.path.join(_DIR, f"tripwire-{_PID}.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"fn": fn, "ok": ok, "err": err, "ts": time.time()}) + "\n")
    except Exception:
        pass


def _wrap(name, real):
    def wrapper(*a, **kw):
        try:
            res = real(*a, **kw)
        except BaseException as exc:
            _record(name, False, type(exc).__name__)
            raise
        _record(name, True)
        return res
    wrapper.__wrapped__ = real
    return wrapper


class _TripwireProbe(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if not name.endswith("corvin_compliance_reports.tripwire"):
            return None
        for finder in sys.meta_path:
            if finder is self or not hasattr(finder, "find_spec"):
                continue
            spec = finder.find_spec(name, path, target)
            if spec is not None:
                break
        else:
            return None
        loader = spec.loader
        orig = loader.exec_module

        def exec_module(module):
            orig(module)
            for fn in ("assert_all", "assert_post_boot"):
                real = getattr(module, fn, None)
                if callable(real):
                    setattr(module, fn, _wrap(fn, real))

        loader.exec_module = exec_module
        return spec


def _parent_death_guard():
    """Die with the parent: an aborted pytest run must not leave sandbox servers behind.

    Linux: prctl(PR_SET_PDEATHSIG, SIGKILL) — the kernel kills this process when the thread
    that forked it exits. Everywhere (and as a backstop on Linux): a watchdog thread that
    os._exit()s once the parent pid changes (macOS reparents orphans to launchd, so getppid()
    changes there too). Windows does not reparent and getppid() keeps the dead parent's pid,
    so the watchdog never fires there: orphans survive on Windows (the suite is Linux-only
    today because /proc is used elsewhere)."""
    parent = os.getppid()
    if sys.platform.startswith("linux"):
        try:
            import ctypes, signal as _sig
            libc = ctypes.CDLL(None, use_errno=True)
            libc.prctl(1, int(_sig.SIGKILL), 0, 0, 0)   # 1 = PR_SET_PDEATHSIG
        except Exception:
            pass
    if os.getppid() != parent:   # the parent died before the guard was armed
        os._exit(1)

    def _watch():
        while True:
            time.sleep(1.0)
            if os.getppid() != parent:
                os._exit(1)

    threading.Thread(target=_watch, daemon=True, name="sbx-parent-watch").start()


if _DIR:
    import faulthandler
    os.makedirs(_DIR, exist_ok=True)
    _parent_death_guard()
    # Thread stacks every 30 s: a server that never binds shows where its startup blocks.
    _STACKS = open(os.path.join(_DIR, f"stacks-{_PID}.txt"), "w")
    faulthandler.dump_traceback_later(30, repeat=True, file=_STACKS)
    sys.meta_path.insert(0, _TripwireProbe())
    threading.Thread(target=_dump_modules, daemon=True, name="sbx-probe").start()
'''

TOOL_SHIM = r'''#!/bin/sh
# Sandbox shim: record the call (credentials in URLs redacted), refuse it.
printf '{tool} %s\n' "$*" | sed -E 's#(://)[^/@ ]+@#\1***@#g' >> "{log}"
exit {code}
'''
# git: the S-9i/S-1b subject and update_manager's fetch; systemctl: an update restart must never
# reach the operator's corvin-* user units; npm/npx: mount_static's auto-build runs
# `npm install` inside the code checkout (app.py:760-776) when web-next/dist is missing.
SHIMMED_TOOLS = ("git", "systemctl", "npm", "npx")

ENV_ALLOWLIST = frozenset({
    "PATH", "LANG", "LC_ALL", "HOME", "CORVIN_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
    "XDG_CACHE_HOME", "XDG_STATE_HOME", "CORVIN_AUDIT_ANCHOR_KEY", "TMPDIR", "PYTHONPATH",
    "PYTHONDONTWRITEBYTECODE", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy",
    "https_proxy", "all_proxy", "NO_PROXY", "no_proxy", "CORVIN_TELEMETRY_OPTIN",
    "CORVIN_GEO_OPT_OUT", "CORVIN_TELEMETRY_URL", "SBX_GIT_LOG", "SBX_PROBE_DIR",
    "CORVIN_A2A_INGRESS", "CORVIN_CONSOLE_PORT", "CORVIN_CONSOLE_BASE_URL",
})
# Never in a sandbox server: these reach the operator's user session (systemd --user, D-Bus).
ENV_FORBIDDEN = frozenset({"DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR"})

TENANT_YAML = """spec:
  telemetry:
    ping_enabled: false
    healing_traces: false
"""


@dataclass
class Sandbox:
    root: Path
    home: Path
    corvin_home: Path
    xdg_config: Path
    anchor_key: Path
    bin_dir: Path
    git_log: Path
    probe_dir: Path
    env: dict[str, str] = field(default_factory=dict)

    @classmethod
    def create(cls) -> "Sandbox":
        inherited = os.environ.get("CORVIN_HOME", "")
        if inherited:
            refuse_live_path(Path(inherited))
        root = Path(tempfile.mkdtemp(prefix="corvin-sbx-")).resolve()
        refuse_live_path(root)
        home, corvin_home, xdg_config = root / "home", root / "corvin_home", root / "xdg_config"
        bin_dir, probe_dir, site_dir = root / "bin", root / "probe", root / "probe_site"
        for d in (home, corvin_home, xdg_config, bin_dir, probe_dir, site_dir, root / "tmp",
                  root / "xdg_data", root / "xdg_cache", root / "xdg_state"):
            d.mkdir(parents=True, exist_ok=True)

        anchor_key = root / "anchor.key"
        anchor_key.write_bytes(secrets.token_bytes(32))
        anchor_key.chmod(0o600)

        git_log = root / "tool_invocations.log"
        git_log.write_text("", encoding="utf-8")
        for tool in SHIMMED_TOOLS:
            shim = bin_dir / tool
            shim.write_text(TOOL_SHIM.replace("{tool}", tool).replace("{log}", str(git_log))
                            .replace("{code}", str(GIT_EXIT)), encoding="utf-8")
            shim.chmod(0o755)
        # Auto-update off before any server starts (routes/settings.py:53-55, :222-228 read
        # ~/.config/corvin-launcher/config.json; the default ON would run `git fetch`).
        for cfg_root in (home / ".config", xdg_config):
            launcher = cfg_root / "corvin-launcher" / "config.json"
            launcher.parent.mkdir(parents=True, exist_ok=True)
            launcher.write_text(json.dumps({"auto_update": False}), encoding="utf-8")
        tenant_yaml = corvin_home / "tenants" / "_default" / "global" / "tenant.corvin.yaml"
        tenant_yaml.parent.mkdir(parents=True, exist_ok=True)
        tenant_yaml.write_text(TENANT_YAML, encoding="utf-8")
        tenant_yaml.chmod(0o600)
        (site_dir / "sitecustomize.py").write_text(SITECUSTOMIZE, encoding="utf-8")

        dead = "http://127.0.0.1:9"
        env = {
            "PATH": f"{bin_dir}:/usr/local/bin:/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "HOME": str(home),
            "CORVIN_HOME": str(corvin_home),
            "XDG_CONFIG_HOME": str(xdg_config),
            "XDG_DATA_HOME": str(root / "xdg_data"),
            "XDG_CACHE_HOME": str(root / "xdg_cache"),
            "XDG_STATE_HOME": str(root / "xdg_state"),
            "CORVIN_AUDIT_ANCHOR_KEY": str(anchor_key),
            "TMPDIR": str(root / "tmp"),
            "PYTHONPATH": os.pathsep.join([str(site_dir), *editable_pythonpath()]),
            "PYTHONDONTWRITEBYTECODE": "1",
            "HTTP_PROXY": dead, "HTTPS_PROXY": dead, "ALL_PROXY": dead,
            "http_proxy": dead, "https_proxy": dead, "all_proxy": dead,
            "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
            "CORVIN_TELEMETRY_OPTIN": "false",
            "CORVIN_GEO_OPT_OUT": "1",
            "CORVIN_TELEMETRY_URL": dead,
            "CORVIN_A2A_INGRESS": "off",  # the live install owns :8775
            "SBX_GIT_LOG": str(git_log),
            "SBX_PROBE_DIR": str(probe_dir),
        }
        assert set(env) <= ENV_ALLOWLIST, sorted(set(env) - ENV_ALLOWLIST)
        sb = cls(root, home, corvin_home, xdg_config, anchor_key, bin_dir, git_log, probe_dir, env)
        sb.assert_isolated()
        return sb

    def assert_isolated(self) -> None:
        """Refuse a sandbox whose paths reach the live install or the real home."""
        for key in ("HOME", "CORVIN_HOME", "XDG_CONFIG_HOME", "TMPDIR"):
            refuse_live_path(Path(self.env[key]))
        if not Path(self.env["CORVIN_AUDIT_ANCHOR_KEY"]).is_absolute():
            raise RuntimeError("CORVIN_AUDIT_ANCHOR_KEY must be absolute")

    def tenant_home(self, tenant: str) -> Path:
        return self.corvin_home / "tenants" / tenant

    def tool_invocations(self, tool: str | None = None) -> list[str]:
        lines = [ln for ln in self.git_log.read_text(encoding="utf-8").splitlines() if ln.strip()]
        return [ln for ln in lines if tool is None or ln.startswith(tool + " ")]

    def git_invocations(self) -> list[str]:
        return self.tool_invocations("git")

    def git_calls_for(self, *needles: str) -> list[str]:
        return [ln for ln in self.git_invocations() if any(n in ln for n in needles)]

    def run_child(self, code: str, *args: str) -> str:
        proc = subprocess.run([sys.executable, "-c", code, *args], env=self.env,
                              cwd=str(self.root), capture_output=True, text=True, timeout=180)
        if proc.returncode != 0:
            raise RuntimeError(f"child failed rc={proc.returncode}\n{proc.stderr[-2000:]}")
        return proc.stdout

    def modules_of(self, pid: int) -> dict[str, str]:
        p = self.probe_dir / f"modules-{pid}.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def tripwire_calls(self, pid: int) -> list[dict[str, Any]]:
        p = self.probe_dir / f"tripwire-{pid}.jsonl"
        if not p.exists():
            return []
        return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]

    def chain_files(self) -> list[Path]:
        return sorted(p for p in self.root.rglob("*.jsonl") if "audit" in p.name)

    def instance_id(self) -> str | None:
        p = self.corvin_home / "global" / "instance_id.json"
        try:
            return json.loads(p.read_text(encoding="utf-8")).get("instance_id")
        except (OSError, ValueError):
            return None


MINT_SESSION = r"""
import json, sys
from corvin_console import auth
rec = auth.create_session(tenant_id=sys.argv[1])
print(json.dumps({"sid": rec.sid, "csrf": auth.derive_csrf_token(rec.csrf_secret, rec.sid)}))
"""

ROUTES = r"""
import json, re
from fastapi import routing
from corvin_console.app import create_app
app = create_app()
allr = []


def _emit(path, methods):
    allr.append({"path": path, "methods": sorted(methods),
                 "params": re.findall(r"{([^}:]+)", path)})


# FastAPI 0.141 keeps included routers as _IncludedRouter objects; flatten them.
for r in app.routes:
    if isinstance(r, routing._IncludedRouter):
        for ctx in r.effective_route_contexts():
            _emit(ctx.path, ctx.methods or [])
    elif getattr(r, "path", None):
        _emit(r.path, getattr(r, "methods", None) or [])
print(json.dumps({"routes": allr}))
"""


@dataclass
class Session:
    tenant: str
    sid: str
    csrf: str

    def cookies(self) -> dict[str, str]:
        return {COOKIE: self.sid}


class Server:
    """A real ``uvicorn`` process in its own process group, serving from a sandbox."""

    CONSOLE = ("corvin_console.app:create_app", True, "")
    GATEWAY = ("corvin_gateway.app:app", False, "/v1/console")

    def __init__(self, sb: Sandbox, kind: tuple[str, bool, str] = CONSOLE, name: str = "console") -> None:
        self.sb = sb
        self.target, self.factory, self.prefix = kind
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.log_path = sb.root / f"server-{name}-{self.port}.log"
        self.proc: subprocess.Popen | None = None

    def start(self, timeout: float = 150.0) -> "Server":
        cmd = [sys.executable, "-m", "uvicorn", self.target, "--host", "127.0.0.1",
               "--port", str(self.port), "--log-level", "warning"]
        if self.factory:
            cmd.insert(4, "--factory")
        env = dict(self.sb.env)
        env["CORVIN_CONSOLE_PORT"] = str(self.port)  # chat_runtime defaults to 8765
        env["CORVIN_CONSOLE_BASE_URL"] = self.base
        assert set(env) <= ENV_ALLOWLIST and not (set(env) & ENV_FORBIDDEN)
        log = open(self.log_path, "w", encoding="utf-8")
        self.proc = subprocess.Popen(cmd, env=env, cwd=str(self.sb.root), stdout=log,
                                     stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"server exited rc={self.proc.returncode}: {self.log_tail()}")
            # Ready = OUR process holds the listening socket AND answers HTTP. A foreign
            # listener on the same port fails the first condition.
            if owns_listener(self.proc.pid, self.port):
                try:
                    with httpx.Client(timeout=2.0, trust_env=False) as c:
                        c.get(self.base + "/")
                    return self
                except httpx.TransportError:
                    pass
            time.sleep(0.3)
        self.stop()
        raise RuntimeError(f"server not up after {timeout}s: {self.log_tail()}")

    @property
    def pid(self) -> int:
        assert self.proc is not None
        return self.proc.pid

    def log_tail(self, n: int = 2000) -> str:
        try:
            return self.log_path.read_text(encoding="utf-8", errors="replace")[-n:]
        except OSError:
            return ""

    def final_origin_problems(self) -> list[str]:
        """Module-origin check on the LAST dump of the running process (call before stop)."""
        time.sleep(0.7)  # the probe rewrites its dump every 0.5 s
        return origin_problems(self.sb.modules_of(self.pid))

    def stop(self) -> None:
        if not self.proc or self.proc.poll() is not None:
            return
        try:
            os.killpg(self.proc.pid, signal.SIGTERM)
            self.proc.wait(timeout=15)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.proc.wait(timeout=10)

    def client(self) -> httpx.Client:
        return httpx.Client(base_url=self.base + self.prefix, timeout=20.0, trust_env=False)

    def environ(self) -> dict[str, str]:
        raw = Path(f"/proc/{self.pid}/environ").read_bytes()
        return dict(kv.split("=", 1) for kv in raw.decode(errors="replace").split("\0") if "=" in kv)


def mint_session(sb: Sandbox, tenant: str) -> Session:
    data = json.loads(sb.run_child(MINT_SESSION, tenant).strip().splitlines()[-1])
    return Session(tenant, data["sid"], data["csrf"])


def list_app_routes(sb: Sandbox) -> dict[str, Any]:
    """Enumerate ``app.routes`` (every method) inside a child process of the sandbox."""
    return json.loads(sb.run_child(ROUTES).strip().splitlines()[-1])


def get_routes(routes: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in routes["routes"] if "GET" in r["methods"]]


def find_route(routes: dict[str, Any], suffix: str, method: str = "GET") -> str:
    for r in routes["routes"]:
        if r["path"].endswith(suffix) and method in r["methods"]:
            return r["path"]
    raise AssertionError(f"no {method} route ending in {suffix!r} in app.routes")


def fetch(server: Server, sess: Session, path: str, *, method: str = "GET", json_body=None) -> httpx.Response:
    headers = {"x-csrf-token": sess.csrf} if method != "GET" else {}
    with server.client() as c:
        return c.request(method, path, cookies=sess.cookies(), headers=headers, json=json_body)


def memory_slug(root: Path) -> str:
    """Same rule as the developer-memory slug in ``routes/memory.py`` (not imported on purpose)."""
    import re

    return re.sub(r"[^a-zA-Z0-9]", "-", str(root))


def tree_hashes(base: Path, pattern: str) -> dict[str, str]:
    out = {}
    for p in sorted(base.glob(pattern)):
        if p.is_file() and not p.name.endswith(".log") and p.name != ".in_use":
            out[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _sha32(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:32]


# ── live-state tripwire ──────────────────────────────────────────────────────

class LiveStateTripwire:
    """Targeted, read-only checks on the operator's real state.

    1. Live chain tails (``<main>/.corvin`` and ``~/.corvin``, tenant + instance chains): no
       record appended since the session started carries a sandbox tenant id, a sandbox marker,
       the sandbox root path or the sandbox instance id.
    2. ``~/.claude/projects/*/memory/*.md`` content hashes unchanged.
    3. ``~/.claude/plugins/corvin-knowledge.json`` unchanged.
    4. Voice chain-key dirs (``~/.config/corvin-voice/{chain_ids,chain_tails,chain_witness,
       mac_active_chains}``): no entry keyed on a sandbox chain (sha256(realpath|abspath)[:32],
       ``g-`` + sha256(genesis)[:32]). Pre-existing entries are live churn and not compared.
    """

    VOICE_DIRS = ("chain_ids", "chain_tails", "chain_witness", "mac_active_chains")

    def __init__(self, home: Path | None = None, corvin_roots: list[Path] | None = None) -> None:
        self.home = Path(home) if home is not None else real_home()
        self.corvin_roots = corvin_roots if corvin_roots is not None else live_corvin_roots()
        self.sandboxes: list[Sandbox] = []
        self.markers: set[str] = set()
        self.captured_needles: set[str] = set()
        self.captured_voice: set[str] = set()
        self.chain_offsets = {str(p): p.stat().st_size for p in self._live_chains()}
        self.memory = self._memory()
        self.kg = self._kg()
        # The code checkout itself: mount_static's auto-build (app.py:760-776) writes here.
        web_next = REPO_ROOT / "core" / "console" / "corvin_console" / "web-next"
        self.checkout = {str(p): p.exists() for p in (web_next / "node_modules", web_next / "dist")}

    def _live_chains(self) -> list[Path]:
        out: list[Path] = []
        for root in self.corvin_roots:
            for pat in ("global/forge/audit.jsonl", "global/audit.jsonl",
                        "tenants/*/global/forge/audit.jsonl", "tenants/*/global/audit.jsonl"):
                out.extend(p for p in root.glob(pat) if p.is_file())
        return out

    def _memory(self) -> dict[str, str]:
        return tree_hashes(self.home / ".claude" / "projects", "*/memory/*.md")

    def _kg(self) -> str | None:
        p = self.home / ".claude" / "plugins" / "corvin-knowledge.json"
        return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None

    def _appended(self, chain: Path) -> list[dict[str, Any]]:
        start = self.chain_offsets.get(str(chain), 0)
        size = chain.stat().st_size
        if size < start:
            start = 0  # rotated/shrunk: scan it whole
        with chain.open("rb") as fh:
            fh.seek(start)
            data = fh.read()
        recs = []
        for line in data.splitlines():
            try:
                recs.append(json.loads(line))
            except ValueError:
                continue
        return recs

    def _voice_candidates(self) -> set[str]:
        names: set[str] = set()
        for sb in self.sandboxes:
            for chain in sb.chain_files():
                names.add(_sha32(str(chain.resolve())))
                names.add(_sha32(os.path.abspath(str(chain))))
                try:
                    with chain.open(encoding="utf-8") as fh:
                        for line in fh:
                            h = json.loads(line).get("hash")
                            if h:
                                names.add("g-" + _sha32(h))
                                break
                except (OSError, ValueError):
                    pass
        return names

    def capture(self) -> None:
        """Accumulate sandbox markers (root, instance id, chain-key names) while the sandbox
        still exists. The final check runs after the sandbox was removed and uses these."""
        for sb in self.sandboxes:
            if not sb.root.exists():
                continue
            self.captured_needles.add(str(sb.root))
            iid = sb.instance_id()
            if iid:
                self.captured_needles.add(iid)
        self.captured_voice |= self._voice_candidates()

    def check(self) -> list[str]:
        problems: list[str] = []
        self.capture()
        needles = set(self.markers) | self.captured_needles
        for chain in self._live_chains():
            for rec in self._appended(chain):
                tid = str(rec.get("tenant_id") or (rec.get("details") or {}).get("tenant_id") or "")
                text = json.dumps(rec)
                if tid.startswith(SANDBOX_TENANT_PREFIX) or any(n in text for n in needles):
                    problems.append(f"live chain {chain} got a sandbox record: {rec.get('event_type')}")
        if self._memory() != self.memory:
            problems.append("~/.claude/projects/*/memory/*.md changed")
        if self._kg() != self.kg:
            problems.append("~/.claude/plugins/corvin-knowledge.json changed")
        for path, existed in self.checkout.items():
            if Path(path).exists() != existed:
                problems.append(f"code checkout changed: {path} {'removed' if existed else 'created'}")
        cands = self.captured_voice
        for d in self.VOICE_DIRS:
            base = self.home / ".config" / "corvin-voice" / d
            if base.is_dir():
                hit = cands & {p.name for p in base.iterdir()}
                if hit:
                    problems.append(f"{base} holds sandbox-keyed entries: {sorted(hit)[:3]}")
        return problems
