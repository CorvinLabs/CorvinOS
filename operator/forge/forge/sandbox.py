"""Sandbox helpers.

Strategy (defense in depth):
  1. If ``bubblewrap`` (`bwrap`) is on PATH → run the impl in a minimal
     namespace: no network, read-only system, dedicated /tmp, only the
     impl file + caller-allowlisted paths visible.
  2. Always apply POSIX resource limits via ``preexec_fn`` (CPU, address
     space, file size, open files, core size). Belt-and-suspenders.
  3. Always run with a stripped env (``PATH``/``HOME``/``LANG`` only).

The two layers are independent. Even if bwrap is missing the rlimits keep
runaway impls from torching the host.
"""
from __future__ import annotations

import fcntl
import hashlib
import os
import resource
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Limits:
    cpu_seconds: int = 10              # SIGXCPU after this much CPU time
    address_space_mb: int = 512        # RLIMIT_AS
    file_size_mb: int = 64             # RLIMIT_FSIZE
    open_files: int = 256              # RLIMIT_NOFILE
    core_size: int = 0                 # RLIMIT_CORE  (no core dumps)


def have_bwrap() -> bool:
    return shutil.which("bwrap") is not None


# Layer-16 v2 D — directory shipped with the forge plugin that contains a
# sitecustomize.py shim refusing socket connects to 127.0.0.0/8 / ::1 /
# 169.254.169.254. The shim is reachable inside the bwrap when build_bwrap_cmd
# is called with deny_loopback=True; the runner sets that whenever
# Policy.deny_loopback_for_persona() says so.
SANDBOX_HELPERS_DIR = Path(__file__).resolve().parent / "sandbox_helpers"


_REQS_CACHE_SUBDIR = "req_cache"


def _reqs_cache_key(requirements: list[str]) -> str:
    """Stable 16-char hex key: sorted requirements + Python major.minor."""
    canon = ":".join(sorted(requirements))
    ver = f"py{sys.version_info.major}.{sys.version_info.minor}"
    return hashlib.sha256(f"{canon}|{ver}".encode()).hexdigest()[:16]


def ensure_requirements(requirements: list[str], cache_root: Path) -> Path | None:
    """Install *requirements* into a shared ``--target`` dir keyed by content hash.

    Returns the target ``Path`` that must be bound read-only into the sandbox
    and prepended to ``PYTHONPATH``.  Returns ``None`` when *requirements* is
    empty so callers can skip the bind cleanly.

    Thread- and process-safe: a per-key lock file serialises concurrent pip
    runs for the same requirement set.  A ``.installed`` sentinel file marks
    a completed install; subsequent calls skip pip entirely.
    """
    if not requirements:
        return None

    key = _reqs_cache_key(requirements)
    target = cache_root / _REQS_CACHE_SUBDIR / key
    sentinel = target / ".installed"

    if sentinel.exists():
        return target

    target.mkdir(parents=True, exist_ok=True)
    lock_path = cache_root / _REQS_CACHE_SUBDIR / f"{key}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        # Re-check: another process may have completed while we waited.
        if sentinel.exists():
            return target

        result = subprocess.run(
            [
                sys.executable, "-m", "pip", "install",
                "--quiet",
                "--target", str(target),
                "--no-warn-script-location",
                *requirements,
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode != 0:
            print(
                f"[forge] pip install failed for requirements {requirements!r}:\n"
                f"{result.stderr.strip()}",
                file=sys.stderr,
            )
            return None

        sentinel.touch()
        return target
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def build_bwrap_cmd(
    inner_cmd: list[str],
    impl_path: Path,
    extra_ro_binds: list[Path] | None = None,
    extra_rw_binds: list[Path] | None = None,
    *,
    allow_network: bool = False,
    deny_loopback: bool = False,
    extra_pythonpath: list[Path] | None = None,
) -> list[str]:
    """Wrap ``inner_cmd`` in a minimal bubblewrap jail.

    Visible inside the sandbox:
      - / read-only mount of the host /usr, /lib*, /bin, /etc/ld.so.* (system libs)
      - the impl file (read-only)
      - any paths explicitly passed in extra_*_binds
      - a fresh tmpfs as /tmp
      - a dev/proc that's safe (no /sys, no host /proc)
    No real /home. PID and IPC namespaces are unshared.

    Network: deny by default. Set ``allow_network=True`` to share the host
    network namespace (loopback + outbound). The runner sets this based on
    ``Policy.network_for_persona(persona)`` so only personas the operator
    has explicitly permitted (browser / research by bundle default) ever
    get web access from inside a forged tool.
    """
    extra_ro_binds = extra_ro_binds or []
    extra_rw_binds = extra_rw_binds or []

    cmd = [
        "bwrap",
        "--unshare-all",          # all namespaces — net included
        "--die-with-parent",
        "--new-session",
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",
        "--ro-bind", "/usr", "/usr",
        "--ro-bind", "/etc/alternatives", "/etc/alternatives",
        "--ro-bind", "/etc/ld.so.cache", "/etc/ld.so.cache",
        "--ro-bind", "/etc/ld.so.conf", "/etc/ld.so.conf",
        "--ro-bind-try", "/etc/ld.so.conf.d", "/etc/ld.so.conf.d",
        "--ro-bind-try", "/etc/resolv.conf", "/etc/resolv.conf",
        "--ro-bind-try", "/etc/ssl", "/etc/ssl",
        "--ro-bind-try", "/etc/ca-certificates", "/etc/ca-certificates",
        "--symlink", "/usr/lib", "/lib",
        "--symlink", "/usr/lib64", "/lib64",
        "--symlink", "/usr/bin", "/bin",
        "--symlink", "/usr/sbin", "/sbin",
        "--ro-bind", str(impl_path), str(impl_path),
        "--setenv", "TMPDIR", "/tmp",
    ]

    # Network: re-share host net namespace when caller permits. resolv.conf
    # / SSL roots are bound above so DNS + TLS work when the namespace is
    # shared. Without --share-net the namespace stays isolated (no loopback
    # either, so even 127.0.0.1 connects fail — that is intentional).
    if allow_network:
        cmd.append("--share-net")

    # Layer-16 v2 D — Loopback-Deny. Only meaningful with --share-net (no
    # loopback to deny when the namespace is unshared). Bind the helpers
    # dir read-only and prepend it to PYTHONPATH so the python interpreter
    # auto-imports sitecustomize.py before the impl runs. Refuses
    # connect()/connect_ex() to 127.0.0.0/8, ::1, localhost and the
    # 169.254.169.254 cloud metadata service.
    # Build the PYTHONPATH for the sandboxed process, combining two sources:
    #   1. sandbox_helpers (loopback-deny shim) — only when deny_loopback
    #   2. per-tool requirements target dirs — from meta.requirements
    # Both are bound read-only; a single --setenv sets the combined path.
    pythonpath_parts: list[str] = []
    if allow_network and deny_loopback and SANDBOX_HELPERS_DIR.is_dir():
        cmd += ["--ro-bind", str(SANDBOX_HELPERS_DIR), str(SANDBOX_HELPERS_DIR)]
        pythonpath_parts.append(str(SANDBOX_HELPERS_DIR))
    for p in (extra_pythonpath or []):
        pythonpath_parts.append(str(p))
    if pythonpath_parts:
        cmd += ["--setenv", "PYTHONPATH", ":".join(pythonpath_parts)]

    for p in extra_ro_binds:
        cmd += ["--ro-bind-try", str(p), str(p)]
    for p in extra_rw_binds:
        cmd += ["--bind-try", str(p), str(p)]

    cmd += ["--"]
    cmd += inner_cmd
    return cmd


def interpreter_ro_binds() -> list[Path]:
    """Read-only binds that make ``sys.executable`` resolvable INSIDE the jail.

    Extracted from ``runner.py`` (ADR-0289) so the forged-tool runner and the
    P-G ContextStage sandbox share ONE implementation — a second copy of this
    would silently rot, and the failure mode is invisible ("bwrap: execvp
    .../python3: No such file", every sandboxed run dead on a uv install, which
    is the DEFAULT installer path).

    Two cases, both needed:

    * a venv interpreter (``<venv>/bin/python``) → bind the venv ROOT, not the
      resolved target: resolving first typically lands on /usr/bin/python3 and
      misses the site-packages tree the inner command needs;
    * a **uv**-managed venv, whose ``bin/python3`` symlinks THROUGH SEVERAL HOPS
      to an interpreter outside the venv (``bin/python3 → python →
      ~/.local/share/uv/python/cpython-3.11-…/bin/python3.11``, with a dir
      symlink in between) → bind the interpreter version STORE, the parent
      holding both the short-name symlink dir and the real ``cpython-X.Y.Z``
      dir, so every hop resolves.

    Guarded so a too-generic directory ('/', '/usr', '/home', '/opt', the user's
    home root, or any path shallower than four parts) is never bound; that case
    falls back to the resolved interpreter's own prefix.
    """
    out: list[Path] = []
    py_exec = Path(sys.executable)
    venv_root = py_exec.parent.parent
    if py_exec.parent.name == "bin" and not str(venv_root).startswith("/usr"):
        out.append(venv_root)
    real_exec = py_exec.resolve()
    if (not str(real_exec).startswith("/usr")
            and not real_exec.is_relative_to(venv_root)):
        store = real_exec.parent.parent.parent          # …/uv/python
        if (len(store.parts) >= 4
                and store != Path.home()
                and str(store) not in ("/", "/usr", "/home", "/opt")):
            out.append(store)
        else:
            out.append(real_exec.parent.parent)
    return out


def apply_rlimits(limits: Limits) -> None:
    """``preexec_fn`` for subprocess.run — runs in the child after fork."""
    cpu = limits.cpu_seconds
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))
    addr = limits.address_space_mb * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (addr, addr))
    fsize = limits.file_size_mb * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_FSIZE, (fsize, fsize))
    resource.setrlimit(resource.RLIMIT_NOFILE, (limits.open_files, limits.open_files))
    resource.setrlimit(resource.RLIMIT_CORE, (limits.core_size, limits.core_size))
    # New process group so we can kill the whole tree on timeout.
    try:
        os.setsid()
    except OSError:
        pass


def stripped_env() -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": "/tmp",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
    }
