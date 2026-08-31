"""Layer 29.5 — per-delegation bwrap sandboxing.

Closes the "worker subprocess shares the bridge's PID/IPC/UTS
namespace + can read /etc/passwd / /proc" gap noted in the EU-AI-Act
multi-engine analysis. With sandbox enabled, each engine subprocess
runs inside a fresh bwrap-jail with:

* PID / IPC / UTS / cgroup namespaces unshared
* /usr, /lib, /lib64, /etc/ssl, /etc/resolv.conf, /etc/hosts RO bind
* Engine-specific config dirs (~/.claude, ~/.codex, ~/.opencode,
  ~/.config/opencode) RO bind so auth still works
* Hermetic working_dir (Layer 29.2a) RW bind, --chdir into it
* /tmp + /var/tmp as fresh tmpfs
* /proc + /dev populated by bwrap
* `--die-with-parent`: cleanup if the bridge adapter dies

Network: ``--share-net`` by default (cloud engines need outbound
HTTPS). A future `local_only=True` mode could `--unshare-net` for
local-Ollama runs but that needs slirp4netns or loopback bind to
work — out of scope for v1.

Three modes (mirror of Layer 29.3a output-judge structure):

* ``off``         — no bwrap wrapping; subprocess runs natively
* ``advisory``    — try bwrap; on bwrap missing log audit + proceed
                    natively (observability)
* ``enforcing``   — try bwrap; on bwrap missing **deny the delegation**
                    with a curated error + WARNING audit

Asymmetric resolution: env floor `CORVIN_DELEGATE_SANDBOX_FLOOR`
(operator-set) wins over the LLM-controllable tool arg via
``max_strictness`` — same security-gate property as 29.3a.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Iterable, Literal


# ---------------------------------------------------------------------------
# Mode resolution (mirror of output_judge.MODES + max_strictness)
# ---------------------------------------------------------------------------


MODES: tuple[str, ...] = ("off", "advisory", "enforcing")
_MODE_ORDINAL: dict[str, int] = {m: i for i, m in enumerate(MODES)}


def normalize_mode(value: str | None) -> str:
    """Map fuzzy input to canonical mode. Unknown → ``off`` (fail-safe)."""
    if value is None:
        return "off"
    v = str(value).strip().lower()
    if v in _MODE_ORDINAL:
        return v
    if v in ("true", "yes", "on", "1"):
        return "advisory"
    if v in ("false", "no", "0", ""):
        return "off"
    return "off"


def max_strictness(*modes: str | None) -> str:
    """Pick the strictest mode across all inputs.

    LLM-controllable tool-arg can ONLY widen strictness above the
    operator-set env floor — never weaken it. Mirror of
    ``output_judge.max_strictness``.
    """
    best = "off"
    best_ord = _MODE_ORDINAL["off"]
    for m in modes:
        canonical = normalize_mode(m)
        ord_ = _MODE_ORDINAL[canonical]
        if ord_ > best_ord:
            best = canonical
            best_ord = ord_
    return best


def env_floor_mode() -> str:
    """Read the operator-set floor from ``CORVIN_DELEGATE_SANDBOX_FLOOR``.

    Default is ``off``. Rationale: bwrap wrapping requires the
    engine binary + auth dirs to be reachable inside the namespace
    (RO-bind-mounted via ``_engine_ro_mounts``). On most Linux
    hosts the universal ``_BASE_RO_MOUNTS`` covers the binary,
    but engine-specific paths (nvm-installed codex, pyenv-managed
    claude wrapper, custom OPENCODE_BIN) can fail silently. An
    operator who confirmed bwrap works on their host opts in via
    ``CORVIN_DELEGATE_SANDBOX_FLOOR=advisory`` (best-effort) or
    ``=enforcing`` (deny when bwrap missing).
    """
    raw = os.environ.get("CORVIN_DELEGATE_SANDBOX_FLOOR")
    if raw is None or not raw.strip():
        return "off"
    return normalize_mode(raw)


# ---------------------------------------------------------------------------
# bwrap availability + binary resolution
# ---------------------------------------------------------------------------


def is_bwrap_available() -> bool:
    """True if the ``bwrap`` binary is on PATH and executable."""
    return shutil.which("bwrap") is not None


def _resolve_bwrap_binary() -> str:
    """Locate bwrap. Operator override via $BWRAP_BIN."""
    return os.environ.get("BWRAP_BIN") or shutil.which("bwrap") or "bwrap"


# ---------------------------------------------------------------------------
# Per-engine RO mounts (engine config + auth)
# ---------------------------------------------------------------------------


def _engine_ro_mounts(engine_id: str) -> list[Path]:
    """Engine-specific RO bind-mounts (auth + config dirs).

    Each mount is best-effort: ``--ro-bind-try`` skips silently when
    the source path doesn't exist (e.g. operator never ran ``codex``
    so ~/.codex is absent). The mounts let the engine binary read
    its auth tokens / API keys / provider config from the host
    without giving it write access.
    """
    home = Path.home()
    if engine_id == "claude_code":
        return [home / ".claude", home / ".config" / "claude"]
    if engine_id == "codex_cli":
        return [home / ".codex", home / ".config" / "codex"]
    if engine_id == "opencode":
        return [
            home / ".opencode",
            home / ".config" / "opencode",
        ]
    return []


# ---------------------------------------------------------------------------
# bwrap argv composition
# ---------------------------------------------------------------------------


# Universal RO mounts every engine needs (binaries + libc + TLS + DNS).
_BASE_RO_MOUNTS: tuple[str, ...] = (
    "/usr",
    "/lib",
    "/lib64",
    "/etc/ssl",
    "/etc/ca-certificates",
    "/etc/resolv.conf",
    "/etc/hosts",
    "/etc/nsswitch.conf",
    "/etc/passwd",     # required for whoami / user lookups
    "/etc/group",
    "/etc/alternatives",
    "/bin",            # symlink to /usr/bin on most systems
    "/sbin",
)


def build_bwrap_args(
    *,
    engine_id: str,
    hermetic_dir: Path,
    allow_net: bool = True,
    extra_ro_mounts: Iterable[Path] | None = None,
    extra_rw_mounts: Iterable[Path] | None = None,
) -> list[str]:
    """Construct the bwrap argv that wraps the engine subprocess.

    Returns the argv list excluding the engine binary itself and
    its arguments — caller appends those: ``[*bwrap_args, engine_bin,
    *engine_args]``.

    The hermetic_dir is RW-mounted and set as cwd. Extra mounts let
    callers expose specific operator-pinned dirs (e.g. a project
    folder to summarise) without widening to the whole home dir.
    """
    bwrap = _resolve_bwrap_binary()
    args: list[str] = [
        bwrap,
        "--unshare-pid",       # PID namespace
        "--unshare-ipc",       # IPC namespace (sysv shared memory etc.)
        "--unshare-uts",       # UTS namespace (hostname)
        "--unshare-cgroup-try",  # cgroup namespace (best-effort, kernel-dep)
        "--die-with-parent",   # cleanup on adapter death
        "--new-session",       # detach controlling terminal
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",
        "--tmpfs", "/var/tmp",
    ]

    # Universal RO mounts (--ro-bind-try silently skips missing dirs).
    for src in _BASE_RO_MOUNTS:
        args += ["--ro-bind-try", src, src]

    # Engine-specific config dirs.
    for src in _engine_ro_mounts(engine_id):
        args += ["--ro-bind-try", str(src), str(src)]

    # Extra operator-pinned RO mounts (e.g. project folder to summarise).
    for src in (extra_ro_mounts or ()):
        p = Path(src)
        args += ["--ro-bind-try", str(p), str(p)]

    # Hermetic working_dir is RW.
    args += ["--bind", str(hermetic_dir), str(hermetic_dir)]
    args += ["--chdir", str(hermetic_dir)]

    # Extra RW mounts (rare — caller really wants the worker to write
    # somewhere persistent).
    for src in (extra_rw_mounts or ()):
        p = Path(src)
        args += ["--bind", str(p), str(p)]

    # Network policy. Default: share host namespace so cloud engines
    # work. unshare-net would need slirp4netns or loopback-bind to
    # function for any network-using engine — out of scope for v1.
    if allow_net:
        args += ["--share-net"]
    else:
        args += ["--unshare-net"]

    # NOTE: deliberately do NOT pass --clearenv. The L29.2b env-allowlist
    # (in delegation.py) already scrubs os.environ for the duration of
    # the spawn, so the env inherited into bwrap is already the curated
    # allowlist. --clearenv would force us to re-set every env var via
    # --setenv (incl. PATH / HOME / engine API keys), creating a brittle
    # double-track. The L29.2b path is the one source of truth for env.

    return args


# ---------------------------------------------------------------------------
# Bwrap-availability + mode-aware decision
# ---------------------------------------------------------------------------


SandboxDecision = Literal["bwrap", "skipped-off", "fallback-no-bwrap", "denied-no-bwrap"]


def decide_sandbox(mode: str) -> SandboxDecision:
    """Map (mode, bwrap-availability) to a final decision.

    Returns one of:
      * ``"bwrap"``                — wrap with bwrap argv
      * ``"skipped-off"``          — mode is off, no wrapping
      * ``"fallback-no-bwrap"``    — advisory mode + bwrap missing → run native + audit
      * ``"denied-no-bwrap"``      — enforcing mode + bwrap missing → deny

    The caller (run_delegate) translates ``"denied-no-bwrap"`` into a
    DelegateResult with ok=False.
    """
    canonical = normalize_mode(mode)
    if canonical == "off":
        return "skipped-off"
    if is_bwrap_available():
        return "bwrap"
    if canonical == "enforcing":
        return "denied-no-bwrap"
    return "fallback-no-bwrap"


__all__ = [
    "MODES",
    "build_bwrap_args",
    "decide_sandbox",
    "env_floor_mode",
    "is_bwrap_available",
    "max_strictness",
    "normalize_mode",
]
