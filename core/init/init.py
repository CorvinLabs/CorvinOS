"""corvin-init — Layer 19 service manager (Phase-1 MVP).

Replaces ``bridge.sh up/down`` with a proper supervisor that:

  * reads ``*.service.yaml`` definitions from each plugin
  * builds a dependency graph (`requires` / `wants`)
  * topologically sorts startup
  * supervises children (restart on failure with backoff)
  * exposes structured journal logs per service

The MVP is a standalone library + CLI; bridge.sh stays as the
operator-facing entry point and will be migrated to call into init
once adapter-side integration lands. Integration with the live bridge
infrastructure is intentionally deferred — this layer is structurally
independent and tested in isolation.

Service definition example (``forge.service.yaml``):

    name: forge
    type: oneshot           # or "daemon"
    exec_start: python3 -m forge.mcp_server
    restart: on-failure     # never / always / on-failure
    restart_sec: 5
    backoff: exponential    # exponential / linear / none
    max_restarts: 5
    requires:
      - audit
    wants:
      - skill-forge
    journal: "<corvin_home>/run/log/forge.log"
    hot_reload: SIGHUP

CLI:

    python3 -m corvin_init.init list
    python3 -m corvin_init.init start <name>
    python3 -m corvin_init.init stop <name>
    python3 -m corvin_init.init restart <name>
    python3 -m corvin_init.init status <name>
    python3 -m corvin_init.init journal <name> [--tail N]
    python3 -m corvin_init.init daemon            # Phase-4.2 long-lived
                                                    # supervisor with socket IPC

Daemon mode (Phase 4.2):

    The `daemon` subcommand starts a long-lived supervisor that listens
    on a platform-neutral transport:
      - Unix/Linux/macOS: Unix-domain socket at <corvin_home>/run/init.sock
      - Windows: TCP loopback at 127.0.0.1:<port>, port stored in <corvin_home>/run/daemon.port

    Each incoming connection sends one JSON line of the form
        {"command": "list" | "start" | "stop" | "restart" | "status"
                  | "journal" | "shutdown",
         "args": [...optional...]}
    and gets a JSON reply with `{ok: bool, ...}`.

    Lifecycle: the daemon discovers services on boot, builds the
    topological order, starts everything in order, then loops:
      - select(socket, timeout=tick_interval)
      - on accept: read one JSON line, dispatch, reply
      - on timeout: tick() to detect crashes + fire restarts
      - SIGTERM => shutdown_all in reverse-topological order, exit 0

    Phase-4.4 will migrate bridge.sh up/down to call into this
    daemon over the transport.

Status states:

    stopped   — never started or cleanly exited
    starting  — exec invoked, not yet confirmed
    running   — alive, last-seen recent
    failed    — exited non-zero, exhausted restart budget
    backoff   — waiting for next restart attempt
"""
from __future__ import annotations

import dataclasses
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# --------------------------------------------------------------------- yaml

def _load_yaml(text: str) -> Dict[str, Any]:
    """Tiny YAML subset parser — flat key:value, scalar lists with `- item`.

    Sufficient for service definitions; avoids the PyYAML dependency.
    Supports:
      key: value                                (scalar)
      key:                                      (list start)
        - item1                                 (list item)
        - item2
      # comment                                 (ignored)
      "..." quoted strings (basic, no escape sequences)
    """
    result: Dict[str, Any] = {}
    current_list_key: Optional[str] = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        # List item under previous key
        if line.lstrip().startswith("- ") and current_list_key is not None:
            value = line.lstrip()[2:].strip()
            value = _strip_quotes(value)
            result[current_list_key].append(value)
            continue
        if ":" not in line:
            raise ValueError(f"unparseable line: {raw!r}")
        key, _, rest = line.partition(":")
        key = key.strip()
        rest = rest.strip()
        if rest == "":
            # Start of a list block
            result[key] = []
            current_list_key = key
        else:
            current_list_key = None
            result[key] = _coerce_scalar(_strip_quotes(rest))
    return result


def _strip_quotes(s: str) -> str:
    if (s.startswith('"') and s.endswith('"')) or (
        s.startswith("'") and s.endswith("'")
    ):
        return s[1:-1]
    return s


def _coerce_scalar(s: str) -> Any:
    if s.lower() in ("true", "yes"):
        return True
    if s.lower() in ("false", "no"):
        return False
    if s.lower() in ("null", "none", "~"):
        return None
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


# --------------------------------------------------------------------- model

@dataclasses.dataclass
class ServiceDef:
    name: str
    exec_start: str
    type: str = "daemon"           # "oneshot" | "daemon"
    restart: str = "never"         # "never" | "always" | "on-failure"
    restart_sec: float = 1.0
    backoff: str = "exponential"   # "exponential" | "linear" | "none"
    max_restarts: int = 5
    requires: List[str] = dataclasses.field(default_factory=list)
    wants: List[str] = dataclasses.field(default_factory=list)
    journal: Optional[str] = None
    hot_reload: Optional[str] = None  # e.g. "SIGHUP"

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ServiceDef":
        if "name" not in d:
            raise ValueError("service definition missing 'name'")
        if "exec_start" not in d:
            raise ValueError(f"service {d['name']!r} missing 'exec_start'")
        return cls(
            name=d["name"],
            exec_start=d["exec_start"],
            type=d.get("type", "daemon"),
            restart=d.get("restart", "never"),
            restart_sec=float(d.get("restart_sec", 1.0)),
            backoff=d.get("backoff", "exponential"),
            max_restarts=int(d.get("max_restarts", 5)),
            requires=list(d.get("requires", []) or []),
            wants=list(d.get("wants", []) or []),
            journal=d.get("journal"),
            hot_reload=d.get("hot_reload"),
        )


@dataclasses.dataclass
class ServiceState:
    sd: ServiceDef
    status: str = "stopped"     # stopped/starting/running/failed/backoff
    pid: Optional[int] = None
    proc: Optional[subprocess.Popen] = None
    log_fh: Optional[Any] = None   # file handle bound to journal; closed on exit
    started_at: Optional[float] = None
    exited_at: Optional[float] = None
    exit_code: Optional[int] = None
    restart_count: int = 0
    next_restart_at: Optional[float] = None


# --------------------------------------------------------------------- loader

def discover_services(roots: List[Path]) -> Dict[str, ServiceDef]:
    """Scan the given directory roots for ``*.service.yaml`` files."""
    services: Dict[str, ServiceDef] = {}
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.service.yaml")):
            try:
                d = _load_yaml(path.read_text(encoding="utf-8"))
                sd = ServiceDef.from_dict(d)
            except Exception as exc:
                raise ValueError(f"{path}: {exc}") from exc
            if sd.name in services:
                raise ValueError(
                    f"duplicate service name {sd.name!r} "
                    f"(second occurrence in {path})"
                )
            services[sd.name] = sd
    return services


# --------------------------------------------------------------------- depgraph

def topological_order(services: Dict[str, ServiceDef]) -> List[str]:
    """Return service names in dependency-respecting startup order.

    Raises ``ValueError`` on missing dep references or cycles.
    """
    visited: Dict[str, str] = {}  # name -> "open" | "done"
    order: List[str] = []

    def visit(name: str, path: List[str]) -> None:
        state = visited.get(name)
        if state == "done":
            return
        if state == "open":
            cycle = " -> ".join(path + [name])
            raise ValueError(f"cycle in service deps: {cycle}")
        visited[name] = "open"
        sd = services.get(name)
        if sd is None:
            raise ValueError(
                f"service {name!r} referenced but not defined "
                f"(referenced from {path[-1] if path else '<root>'})"
            )
        for req in sd.requires:
            visit(req, path + [name])
        for want in sd.wants:
            if want in services:
                visit(want, path + [name])
            # missing wants are fine — they're optional
        visited[name] = "done"
        order.append(name)

    for name in services:
        visit(name, [])
    return order


# --------------------------------------------------------------------- sup

class Supervisor:
    """In-process service supervisor.

    A single instance keeps a state map and drives lifecycle transitions
    via ``tick()``. Tests call ``tick()`` after simulating a crash and
    advancing the clock; production would loop ``tick()`` from a daemon.
    """

    def __init__(
        self,
        services: Dict[str, ServiceDef],
        *,
        journal_dir: Optional[Path] = None,
        clock=time.time,
    ) -> None:
        self.services = services
        self.journal_dir = journal_dir
        self.clock = clock
        self.states: Dict[str, ServiceState] = {
            name: ServiceState(sd=sd) for name, sd in services.items()
        }
        if journal_dir:
            journal_dir.mkdir(parents=True, exist_ok=True)

    # ---- queries

    def list_status(self) -> List[Dict[str, Any]]:
        out = []
        for name in topological_order(self.services):
            st = self.states[name]
            out.append(
                {
                    "name": name,
                    "status": st.status,
                    "pid": st.pid,
                    "exit_code": st.exit_code,
                    "restart_count": st.restart_count,
                    "uptime": (
                        self.clock() - st.started_at
                        if st.status == "running" and st.started_at
                        else None
                    ),
                }
            )
        return out

    def status(self, name: str) -> Dict[str, Any]:
        if name not in self.states:
            raise KeyError(name)
        st = self.states[name]
        return {
            "name": name,
            "status": st.status,
            "pid": st.pid,
            "exit_code": st.exit_code,
            "restart_count": st.restart_count,
            "next_restart_at": st.next_restart_at,
        }

    def deps_of(self, name: str) -> Dict[str, List[str]]:
        sd = self.services[name]
        return {"requires": list(sd.requires), "wants": list(sd.wants)}

    # ---- mutations

    def start(self, name: str, *, recursive: bool = True) -> None:
        if name not in self.states:
            raise KeyError(name)
        if recursive:
            # Topological start of this service plus all its requires
            visited: Set[str] = set()

            def go(n: str) -> None:
                if n in visited:
                    return
                visited.add(n)
                for r in self.services[n].requires:
                    go(r)
                self._spawn(n)

            go(name)
        else:
            self._spawn(name)

    def stop(self, name: str) -> None:
        st = self.states[name]
        if st.proc is None or st.proc.poll() is not None:
            st.status = "stopped"
            st.proc = None
            st.pid = None
            return
        st.proc.terminate()
        try:
            st.proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            st.proc.kill()
            st.proc.wait()
        st.status = "stopped"
        st.exited_at = self.clock()
        st.exit_code = st.proc.returncode
        st.proc = None
        st.pid = None
        self._close_log_fh(st)
        self._journal(name, f"stopped (exit_code={st.exit_code})")

    def restart(self, name: str) -> None:
        self.stop(name)
        # restart_count tracks SUPERVISED restarts (after a failure),
        # not operator-issued restarts. Don't bump on manual restart.
        self._spawn(name)

    def reload(self, name: str) -> bool:
        """Send hot_reload signal if defined. Returns True if sent."""
        st = self.states[name]
        if st.proc is None or st.sd.hot_reload is None:
            return False
        sig_name = st.sd.hot_reload
        sig_num = getattr(signal, sig_name, None)
        if sig_num is None:
            return False
        try:
            os.kill(st.proc.pid, sig_num)
        except ProcessLookupError:
            return False
        self._journal(name, f"reload signal sent ({sig_name})")
        return True

    # ---- lifecycle tick

    def tick(self) -> List[str]:
        """Drive lifecycle: detect exits, schedule restarts, fire restarts.

        Returns the list of state changes that happened in this tick
        (purely for test introspection).
        """
        changes: List[str] = []
        now = self.clock()
        for name, st in self.states.items():
            # Detect exit of running processes
            if st.proc is not None:
                rc = st.proc.poll()
                if rc is not None:
                    st.exit_code = rc
                    st.exited_at = now
                    st.proc = None
                    st.pid = None
                    self._close_log_fh(st)
                    if rc == 0 or st.sd.restart == "never":
                        st.status = "stopped" if rc == 0 else "failed"
                        changes.append(f"{name}->{st.status}")
                        self._journal(name, f"exited (rc={rc})")
                    else:
                        # Schedule restart
                        if st.restart_count >= st.sd.max_restarts:
                            st.status = "failed"
                            changes.append(f"{name}->failed(max_restarts)")
                            self._journal(
                                name,
                                f"failed permanently after "
                                f"{st.restart_count} restart attempts",
                            )
                        else:
                            backoff = self._compute_backoff(st)
                            st.next_restart_at = now + backoff
                            st.status = "backoff"
                            changes.append(
                                f"{name}->backoff({backoff:.1f}s)"
                            )
                            self._journal(
                                name,
                                f"crashed (rc={rc}), backoff {backoff:.1f}s",
                            )

            # Fire scheduled restart
            if (
                st.status == "backoff"
                and st.next_restart_at is not None
                and now >= st.next_restart_at
            ):
                st.restart_count += 1
                self._journal(
                    name, f"restart attempt #{st.restart_count}"
                )
                self._spawn(name, supervised=True)
                changes.append(f"{name}->restart#{st.restart_count}")

        return changes

    # ---- helpers

    def _spawn(self, name: str, *, supervised: bool = False) -> None:
        st = self.states[name]
        if st.proc is not None and st.proc.poll() is None:
            return  # already running
        # Critical: if a previous run's log_fh is still bound to this state
        # (e.g. on rapid restart after stop()), close it BEFORE opening the
        # new one. Otherwise two file objects with buffering=1 race on the
        # same journal file and produce interleaved/corrupt log lines.
        self._close_log_fh(st)
        if not supervised:
            # Operator-issued start resets the restart budget
            st.restart_count = 0
        st.status = "starting"
        cmd = st.sd.exec_start
        # Open journal file in append mode (so restarts accumulate)
        log_path = self._journal_path(name)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_fh = open(log_path, "a", buffering=1)
        try:
            proc = subprocess.Popen(
                cmd,
                shell=True,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            log_fh.close()
            st.status = "failed"
            st.exit_code = -1
            self._journal(name, f"spawn failed: {exc}")
            return
        st.proc = proc
        st.pid = proc.pid
        st.log_fh = log_fh
        st.started_at = self.clock()
        st.status = "running"
        st.next_restart_at = None
        self._journal(name, f"started pid={proc.pid} cmd={cmd!r}")

    def _close_log_fh(self, st: ServiceState) -> None:
        """Close the bound journal handle if any. Idempotent + best-effort."""
        if st.log_fh is None:
            return
        try:
            st.log_fh.close()
        except OSError:
            pass
        st.log_fh = None

    def _compute_backoff(self, st: ServiceState) -> float:
        base = st.sd.restart_sec
        n = st.restart_count
        if st.sd.backoff == "linear":
            return base * (n + 1)
        if st.sd.backoff == "none":
            return base
        # exponential (default)
        return base * (2 ** n)

    def _journal_path(self, name: str) -> Path:
        sd = self.services[name]
        if sd.journal:
            return Path(os.path.expanduser(os.path.expandvars(sd.journal)))
        if self.journal_dir:
            return self.journal_dir / f"{name}.log"
        return Path("/tmp") / f"corvin-{name}.log"

    def _journal(self, name: str, msg: str) -> None:
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        line = f"{ts} {msg}\n"
        try:
            self._journal_path(name).parent.mkdir(parents=True, exist_ok=True)
            with self._journal_path(name).open("a") as fh:
                fh.write(line)
        except OSError:
            # Best-effort journal — never break supervision because of disk
            pass

    def journal_tail(self, name: str, n: int = 50) -> List[str]:
        path = self._journal_path(name)
        if not path.exists():
            return []
        # Memory-bounded tail: stream the file and keep only the last n
        # lines in a deque, instead of loading the entire (potentially
        # multi-MiB) journal into memory and slicing.
        from collections import deque
        tail: deque[str] = deque(maxlen=n)
        try:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    tail.append(line.rstrip("\n"))
        except OSError:
            return []
        return list(tail)

    def shutdown_all(self) -> None:
        """Stop every running service in reverse-topological order."""
        order = topological_order(self.services)
        for name in reversed(order):
            self.stop(name)


# --------------------------------------------------------------------- cli

# --------------------------------------------------------------------- daemon

# Phase 4.2: long-lived daemon with Unix-socket IPC. The daemon is
# the production supervisor for /svc start/stop/restart commands; the
# CLI subcommands without `daemon` work standalone for one-shot
# operator use.

DEFAULT_SOCKET_NAME = "init.sock"
DEFAULT_TICK_INTERVAL = 1.0  # seconds between supervisor tick() calls


def _socket_path() -> Path:
    """Resolve the daemon's Unix-socket path. Lives under <corvin_home>/run/."""
    home = os.environ.get("CORVIN_HOME") or os.environ.get("CORVIN_HOME")
    if home:
        run_dir = Path(home) / "run"
    else:
        # Fall back to walk-up like paths.py does
        here = Path(__file__).resolve()
        for parent in [here, *here.parents]:
            if (parent / ".corvin_repo").exists() or (parent / "plugins").is_dir():
                run_dir = parent / ".corvin" / "run"
                break
        else:
            run_dir = Path.home() / ".corvin" / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir / DEFAULT_SOCKET_NAME


def _handle_daemon_request(sup: "Supervisor", payload: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatch one JSON request payload against the supervisor.

    Always returns a JSON-serialisable dict shaped {ok: bool, ...}. Never
    raises — domain errors come back as ok=false with a message field.
    """
    cmd = (payload.get("command") or "").strip().lower()
    args = payload.get("args") or []
    try:
        if cmd == "list":
            return {"ok": True, "services": sup.list_status()}
        if cmd == "status":
            if not args:
                return {"ok": False, "error": "status requires service name"}
            return {"ok": True, "status": sup.status(args[0])}
        if cmd == "start":
            if not args:
                return {"ok": False, "error": "start requires service name"}
            sup.start(args[0])
            return {"ok": True, "started": args[0]}
        if cmd == "stop":
            if not args:
                return {"ok": False, "error": "stop requires service name"}
            sup.stop(args[0])
            return {"ok": True, "stopped": args[0]}
        if cmd == "restart":
            if not args:
                return {"ok": False, "error": "restart requires service name"}
            sup.restart(args[0])
            return {"ok": True, "restarted": args[0]}
        if cmd == "deps":
            if not args:
                return {"ok": False, "error": "deps requires service name"}
            return {"ok": True, "deps": sup.deps_of(args[0])}
        if cmd == "journal":
            if not args:
                return {"ok": False, "error": "journal requires service name"}
            n = 50
            if len(args) >= 2:
                try:
                    n = int(args[1])
                except (TypeError, ValueError):
                    return {"ok": False, "error": "journal N must be int"}
            return {"ok": True, "lines": sup.journal_tail(args[0], n)}
        if cmd == "reload":
            if not args:
                return {"ok": False, "error": "reload requires service name"}
            sent = sup.reload(args[0])
            return {"ok": True, "reloaded": args[0], "signal_sent": sent}
        if cmd == "shutdown":
            return {"ok": True, "shutting_down": True}
        if cmd == "ping":
            return {"ok": True, "pong": True}
        return {"ok": False, "error": f"unknown command: {cmd!r}"}
    except KeyError as exc:
        return {"ok": False, "error": f"unknown service: {exc}"}
    except Exception as exc:  # pragma: no cover (defense in depth)
        return {"ok": False, "error": f"internal: {exc}"}


def daemon(plugin_roots: Optional[List[Path]] = None,
           tick_interval: Optional[float] = None,
           socket_path: Optional[Path] = None,
           autostart: Optional[bool] = None) -> int:
    # Env-var overrides for testability — let the test suite drive
    # daemon() without modifying the CLI signature.
    if plugin_roots is None:
        env_roots = os.environ.get("CORVIN_INIT_PLUGIN_ROOTS")
        if env_roots:
            plugin_roots = [Path(p) for p in env_roots.split(":") if p]
    if autostart is None:
        autostart = (os.environ.get("CORVIN_INIT_NO_AUTOSTART") or "").lower() not in ("1", "true", "yes")
    if tick_interval is None:
        try:
            tick_interval = float(
                os.environ.get("CORVIN_INIT_TICK_INTERVAL") or DEFAULT_TICK_INTERVAL
            )
        except ValueError:
            tick_interval = DEFAULT_TICK_INTERVAL
    """Run the supervisor as a long-lived daemon.

    autostart: if True (default), every discovered service is started in
    topological order on boot. Tests set autostart=False to take fine-
    grained control of which services start.
    """
    import select as _select
    import importlib.util

    # Import daemon_transport using absolute path to support both
    # module-mode and direct execution
    transport_path = Path(__file__).parent / "daemon_transport.py"
    spec = importlib.util.spec_from_file_location("daemon_transport", transport_path)
    transport_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(transport_module)
    create_transport = transport_module.create_transport

    if plugin_roots is None:
        plugin_roots = [Path(__file__).resolve().parent.parent]
    services = discover_services(plugin_roots)

    if socket_path is None:
        socket_path = _socket_path()

    journal_dir = socket_path.parent / "log"
    sup = Supervisor(services, journal_dir=journal_dir)

    if autostart:
        for name in topological_order(services):
            try:
                sup.start(name)
            except Exception as exc:
                print(f"[init-daemon] start {name} failed: {exc}",
                      file=sys.stderr, flush=True)

    # Set up the listener socket using platform-neutral transport.
    # Windows: TCP loopback (127.0.0.1:random_port)
    # Unix: AF_UNIX domain socket (existing behavior)
    transport = create_transport(socket_path)
    try:
        sock, endpoint = transport.bind()
    except RuntimeError as exc:
        print(f"[init-daemon] bind failed: {exc}",
              file=sys.stderr, flush=True)
        return 2

    sock.listen(64)  # deep enough to absorb burst connects without ECONNREFUSED
    sock.settimeout(0.0)  # non-blocking, we drive it via select

    shutdown_requested = [False]

    def _on_sigterm(_signo, _frame):
        shutdown_requested[0] = True
    signal.signal(signal.SIGTERM, _on_sigterm)
    signal.signal(signal.SIGINT, _on_sigterm)

    print(f"[init-daemon] listening on {socket_path} "
          f"({len(services)} services, tick={tick_interval}s)",
          flush=True)

    try:
        while not shutdown_requested[0]:
            r, _, _ = _select.select([sock], [], [], tick_interval)
            if r:
                try:
                    conn, _ = sock.accept()
                except (OSError, BlockingIOError):
                    continue
                try:
                    data = b""
                    conn.settimeout(2.0)
                    while b"\n" not in data and len(data) < 65536:
                        chunk = conn.recv(4096)
                        if not chunk:
                            break
                        data += chunk
                    line = data.split(b"\n", 1)[0].decode("utf-8",
                                                          errors="replace")
                    try:
                        payload = json.loads(line) if line else {}
                    except json.JSONDecodeError:
                        reply = {"ok": False, "error": "parse error"}
                    else:
                        reply = _handle_daemon_request(sup, payload)
                        if reply.get("shutting_down"):
                            shutdown_requested[0] = True
                    conn.sendall(
                        (json.dumps(reply) + "\n").encode("utf-8")
                    )
                except (OSError, ConnectionError) as exc:
                    print(f"[init-daemon] conn error: {exc}",
                          file=sys.stderr, flush=True)
                finally:
                    try:
                        conn.close()
                    except OSError:
                        pass
            # Drive supervisor lifecycle (detect exits, fire restarts)
            try:
                sup.tick()
            except Exception as exc:
                print(f"[init-daemon] tick error: {exc}",
                      file=sys.stderr, flush=True)
    finally:
        try:
            sock.close()
        except OSError:
            pass
        transport.cleanup()
        sup.shutdown_all()
        print("[init-daemon] shutdown complete", flush=True)
    return 0


def daemon_call(command: str, *args: str,
                socket_path: Optional[Path] = None,
                timeout: float = 5.0) -> Dict[str, Any]:
    """Send one command to a running daemon and return the parsed reply.

    Used by the CLI client (`phase3_cli.py svc <subcommand>`) and the
    E2E test driver. Never raises on protocol errors — returns a
    {ok: false, error: ...} dict.
    """
    import importlib.util

    # Import daemon_transport using absolute path
    transport_path = Path(__file__).parent / "daemon_transport.py"
    spec = importlib.util.spec_from_file_location("daemon_transport", transport_path)
    transport_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(transport_module)
    create_transport = transport_module.create_transport

    if socket_path is None:
        socket_path = _socket_path()

    transport = create_transport(socket_path)
    payload = {"command": command, "args": list(args)}
    try:
        sock = transport.connect(timeout=timeout)
    except (OSError, RuntimeError) as exc:
        return {"ok": False, "error": f"daemon call failed: {exc}"}

    try:
        sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        data = b""
        while b"\n" not in data and len(data) < 65536:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        line = data.split(b"\n", 1)[0].decode("utf-8", errors="replace")
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return {"ok": False, "error": "daemon returned invalid JSON"}
    except (OSError, ConnectionError) as exc:
        return {"ok": False, "error": f"daemon call failed: {exc}"}
    finally:
        try:
            sock.close()
        except OSError:
            pass


def _cli(argv: List[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd = argv[0]
    # Phase 4.2: daemon mode is its own entry point — no per-call
    # Supervisor instance, the daemon owns its own.
    if cmd == "daemon":
        return daemon()
    # Default service roots: every plugin dir
    roots = [Path(__file__).resolve().parent.parent]
    services = discover_services(roots)
    sup = Supervisor(services)

    # Initialize secure file permissions on ~/.corvin (cross-platform)
    try:
        from core.platform import setup_corvin_home_permissions
        corvin_home = Path.home() / ".corvin"
        if corvin_home.exists():
            setup_corvin_home_permissions(corvin_home)
    except Exception as exc:
        # Logging permission errors is a nice-to-have, not critical
        print(f"Note: permission setup warning: {exc}", file=sys.stderr)

    try:
        if cmd == "list":
            for row in sup.list_status():
                print(
                    f"{row['name']:20s} {row['status']:10s} "
                    f"pid={row['pid'] or '-':>6} "
                    f"restarts={row['restart_count']}"
                )
            return 0
        if cmd == "start" and len(argv) >= 2:
            sup.start(argv[1])
            return 0
        if cmd == "stop" and len(argv) >= 2:
            sup.stop(argv[1])
            return 0
        if cmd == "restart" and len(argv) >= 2:
            sup.restart(argv[1])
            return 0
        if cmd == "status" and len(argv) >= 2:
            import json
            print(json.dumps(sup.status(argv[1]), indent=2))
            return 0
        if cmd == "journal" and len(argv) >= 2:
            n = 50
            if "--tail" in argv:
                idx = argv.index("--tail")
                if idx + 1 < len(argv):
                    n = int(argv[idx + 1])
            for line in sup.journal_tail(argv[1], n):
                print(line)
            return 0
    except KeyError as exc:
        print(f"unknown service: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
