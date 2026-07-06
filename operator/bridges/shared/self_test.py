"""Boot-time self-test for Corvin.

Verifies core subsystems are wired correctly before the adapter accepts
real traffic. Reachable via three entry points, one logic:

  - adapter boot           run_self_test() right after path_gate_self_test()
  - bridge.sh doctor       operator on-demand
  - ops/healthcheck.sh     Docker HEALTHCHECK, --quick mode

Classification (load-bearing):

  CRITICAL  failure → adapter logs prominently, audit emits
            ``boot.self_test_failed``, ``bridge.sh doctor`` exits non-zero,
            Docker container reports unhealthy.
  WARNING   failure → logged, surfaced in ``doctor``, never blocks boot
            or healthcheck.
  INFO      diagnostic only — never affects exit code.

Privacy: detail fields carry only paths, exit codes, and check names —
never persona content, transcript text, license-key bytes, or any user
identifier beyond the tenant id.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# ── ADR-0141 Tier 3 — self-register this security capability at import time ──
try:  # pragma: no cover - exercised at adapter boot / self-test
    from security_capabilities import (  # noqa: E402
        register_capability as _reg_cap,
        module_self_hash as _self_hash,
    )

    _reg_cap("self_test", version="1.0", file_hash=_self_hash(__file__))
except Exception:  # pragma: no cover - fail-closed: absent capability blocks spawn
    pass

# ── Severity vocabulary ────────────────────────────────────────────────────

CRITICAL = "CRITICAL"
WARNING = "WARNING"
INFO = "INFO"

_VALID_SEVERITY = {CRITICAL, WARNING, INFO}


@dataclass(frozen=True)
class CheckResult:
    name: str
    severity: str
    ok: bool
    detail: str = ""

    def __post_init__(self) -> None:
        if self.severity not in _VALID_SEVERITY:
            raise ValueError(f"invalid severity: {self.severity!r}")


@dataclass(frozen=True)
class SelfTestResult:
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def critical_failures(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.ok and c.severity == CRITICAL]

    @property
    def warnings(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.ok and c.severity == WARNING]

    @property
    def ok(self) -> bool:
        return not self.critical_failures

    @property
    def all_green(self) -> bool:
        return all(c.ok for c in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "all_green": self.all_green,
            "checks": [asdict(c) for c in self.checks],
            "critical_failures": [c.name for c in self.critical_failures],
            "warnings": [c.name for c in self.warnings],
        }


# ── Path / tenant resolution (lazy, defensive) ─────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _ensure_sys_path() -> None:
    """Make `forge.*`, `skill_forge.*` and bridge shared modules importable."""
    candidates = (
        _REPO_ROOT / "operator" / "forge",
        _REPO_ROOT / "operator" / "skill-forge",
        _REPO_ROOT / "operator" / "bridges" / "shared",
    )
    for p in candidates:
        sp = str(p)
        if p.is_dir() and sp not in sys.path:
            sys.path.insert(0, sp)


def _corvin_home() -> Path:
    env = os.environ.get("CORVIN_HOME")
    if env:
        return Path(os.path.expanduser(os.path.expandvars(env)))
    return Path.home() / ".corvin"


def _resolved_tenant_id() -> tuple[str, str]:
    """Return ``(tenant_id, source)`` where source describes how it was resolved.

    Falls back to ``_default`` if the forge package is missing — that's the
    Apache-baseline default per ADR-0007.
    """
    _ensure_sys_path()
    try:
        from forge.tenants import current_tenant  # type: ignore

        return current_tenant(), "forge.tenants.current_tenant()"
    except Exception:  # noqa: BLE001
        env = os.environ.get("CORVIN_TENANT_ID")
        if env:
            return env, "CORVIN_TENANT_ID env"
        return "_default", "fallback default"


def _tenant_home_path(tenant_id: str) -> Path:
    return _corvin_home() / "tenants" / tenant_id


# ── Individual checks ──────────────────────────────────────────────────────

_TENANT_SUBDIRS = ("global", "sessions", "forge", "skill-forge", "voice", "cowork")


def _check_tenant_tree() -> list[CheckResult]:
    out: list[CheckResult] = []
    try:
        tid, source = _resolved_tenant_id()
    except Exception as e:  # noqa: BLE001
        return [CheckResult("tenant.resolved", CRITICAL, False,
                            f"resolver raised {type(e).__name__}: {e}")]
    out.append(CheckResult("tenant.resolved", CRITICAL, True,
                           f"tenant_id={tid!r} via {source}"))

    home = _tenant_home_path(tid)
    if not home.exists():
        out.append(CheckResult("tenant.home_exists", CRITICAL, False,
                               f"missing {home} — run corvin_migrate.py"))
        return out
    out.append(CheckResult("tenant.home_exists", CRITICAL, True, str(home)))

    writable = os.access(home, os.W_OK)
    out.append(CheckResult("tenant.home_writable", CRITICAL, writable,
                           str(home) if writable else f"not writable: {home}"))

    missing = [d for d in _TENANT_SUBDIRS if not (home / d).exists()]
    out.append(CheckResult(
        "tenant.subdirs_present", WARNING, not missing,
        "all present" if not missing else f"missing: {','.join(missing)}"))
    return out


def _check_memory() -> list[CheckResult]:
    out: list[CheckResult] = []
    tid, _ = _resolved_tenant_id()
    mem_dir = _tenant_home_path(tid) / "global" / "memory"
    out.append(CheckResult(
        "memory.dir_present", WARNING, mem_dir.exists(),
        str(mem_dir) if mem_dir.exists() else "absent (created on first turn)"))

    db = mem_dir / "recall.db"
    if not db.exists():
        out.append(CheckResult("memory.recall_db", INFO, True,
                               "absent (created on first turn)"))
    else:
        # Mode check is CRITICAL — leaked DB exposes redacted-but-recoverable text.
        mode = stat.S_IMODE(db.stat().st_mode)
        mode_ok = mode == 0o600
        out.append(CheckResult(
            "memory.recall_db_mode", CRITICAL, mode_ok,
            f"mode={oct(mode)}" if mode_ok else
            f"INSECURE mode={oct(mode)} (expected 0o600): {db}"))
        # FTS5 query check: verify the live DB is readable, then probe FTS5
        # compile-time support on an in-memory DB so we never touch live state.
        try:
            import sqlite3
            # Read-check on the live DB (SELECT only — no DDL writes).
            con = sqlite3.connect(str(db), timeout=2.0)
            try:
                con.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchall()
            finally:
                con.close()
            # Probe FTS5 on a throwaway in-memory DB — avoids any DDL write to
            # the live recall.db (CLAUDE.md: checks must be side-effect-free).
            mem = sqlite3.connect(":memory:")
            try:
                mem.execute("CREATE VIRTUAL TABLE _st_probe USING fts5(x)")
                mem.execute("DROP TABLE _st_probe")
            finally:
                mem.close()
            out.append(CheckResult("memory.recall_db_openable", WARNING, True,
                                   "FTS5 available"))
        except Exception as e:  # noqa: BLE001
            out.append(CheckResult("memory.recall_db_openable", WARNING, False,
                                   f"{type(e).__name__}: {e}"))

    um_dir = mem_dir / "user_model"
    if um_dir.exists():
        um_writable = os.access(um_dir, os.W_OK)
        out.append(CheckResult("memory.user_model_writable", WARNING,
                               um_writable, str(um_dir)))
    else:
        out.append(CheckResult("memory.user_model_dir", INFO, True,
                               "absent (created on first distill)"))
    return out


def _audit_path() -> Path:
    env = os.environ.get("VOICE_AUDIT_PATH")
    if env:
        return Path(env)
    tid, _ = _resolved_tenant_id()
    return _tenant_home_path(tid) / "global" / "forge" / "audit.jsonl"


def _check_audit_chain(*, quick: bool) -> list[CheckResult]:
    out: list[CheckResult] = []
    path = _audit_path()
    out.append(CheckResult("audit.path", INFO, True, str(path)))

    if quick:
        # Quick mode: file exists or empty, no full chain walk.
        if path.exists() and path.stat().st_size > 0:
            out.append(CheckResult("audit.file_readable", CRITICAL,
                                   os.access(path, os.R_OK), str(path)))
        return out

    # Full verify via voice-audit subprocess — single source of truth for the
    # verification contract; never reimplement here.
    script = _REPO_ROOT / "operator" / "voice" / "scripts" / "voice_audit.py"
    if not script.exists():
        return out + [CheckResult("audit.chain_verified", WARNING, False,
                                  f"voice_audit.py missing at {script}")]
    try:
        # `verify --all` walks EVERY audit.jsonl chain under the corvin_home
        # tree (global, tenant, forge, skill-forge, per-session) and lets
        # voice_audit resolve the canonical root itself — closing both the
        # "only one chain verified" gap and the self-test/manual-verify
        # path-divergence. rc=1 if ANY chain is broken.
        r = subprocess.run(
            [sys.executable, str(script), "verify", "--all"],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode == 0:
            out.append(CheckResult("audit.chain_verified", CRITICAL, True,
                                   (r.stdout or "").strip().splitlines()[-1]
                                   if r.stdout.strip() else "all chains OK"))
        else:
            # rc=2 (IO error) is CRITICAL — chain unreadable means we can't
            # trust ANY following audit write. rc=1 (integrity violation) is
            # the canonical compliance breach.
            out.append(CheckResult(
                "audit.chain_verified", CRITICAL, False,
                f"voice-audit verify rc={r.returncode}: "
                f"{(r.stderr or r.stdout).strip()[:200]}"))
    except subprocess.TimeoutExpired:
        # The walk is bounded to a safe .corvin tree (~tens of chains, sub-second);
        # a >60s timeout means a real structural problem (runaway walk / stuck
        # verify), not a transient hiccup — fail CRITICAL so it can't mask a
        # broken chain. (Review FINDING 4; CLAUDE.md: don't downgrade CRITICAL.)
        out.append(CheckResult("audit.chain_verified", CRITICAL, False,
                               "voice-audit verify --all timeout (>60s)"))
    except Exception as e:  # noqa: BLE001
        out.append(CheckResult("audit.chain_verified", WARNING, False,
                               f"{type(e).__name__}: {e}"))
    return out


def _check_nbac_genesis() -> list[CheckResult]:
    """ADR-0117 M1: verify that the audit chain has a valid genesis block.

    - Missing genesis block → WARNING (chain.genesis_missing — grace period).
    - Present but invalid signature → CRITICAL (chain.genesis_invalid — structural breach).
    - Valid genesis → INFO.

    Must NOT import anthropic (CI AST lint).
    """
    out: list[CheckResult] = []
    audit_p = _audit_path()
    try:
        from nbac import (  # noqa: PLC0415
            get_genesis_block as _get_genesis_block,
            verify_genesis_block as _verify_genesis_block,
        )
    except ImportError:
        out.append(CheckResult("nbac.module_available", WARNING, False,
                               "nbac module not importable — skip genesis check"))
        return out

    try:
        block = _get_genesis_block(audit_p)
    except Exception as exc:  # noqa: BLE001
        out.append(CheckResult("nbac.genesis_readable", WARNING, False,
                               f"genesis read error: {type(exc).__name__}"))
        return out

    if block is None:
        out.append(CheckResult("nbac.genesis_present", WARNING, False,
                               f"no chain.genesis event in {audit_p} "
                               "(grace period: pre-ADR-0117 chain)"))
        return out

    out.append(CheckResult("nbac.genesis_present", INFO, True,
                           f"genesis found; network_id={block.get('details', block).get('network_id', '?')}"))

    try:
        sig_ok = _verify_genesis_block(block)
    except Exception as exc:  # noqa: BLE001
        # Treat verification errors the same as a failed verification — CRITICAL.
        # A crash here (e.g. corrupted base64, wrong key size) is a structural breach.
        out.append(CheckResult("nbac.genesis_signature", CRITICAL, False,
                               f"genesis signature verify raised {type(exc).__name__} "
                               "— chain may be corrupted or pubkey mismatch"))
        return out

    if sig_ok:
        out.append(CheckResult("nbac.genesis_signature", INFO, True,
                               "genesis block signature valid (Network Root Key)"))
    else:
        out.append(CheckResult("nbac.genesis_signature", CRITICAL, False,
                               "genesis block signature INVALID — chain may be forked "
                               "or pubkey mismatch (see chain.genesis_invalid)"))
    return out


def _check_vault() -> list[CheckResult]:
    out: list[CheckResult] = []
    vault = Path.home() / ".config" / "corvin-voice" / "secrets.json"
    if not vault.exists():
        out.append(CheckResult("vault.present", INFO, True,
                               f"absent (no secrets configured): {vault}"))
        return out
    out.append(CheckResult("vault.present", INFO, True, str(vault)))
    mode = stat.S_IMODE(vault.stat().st_mode)
    mode_ok = mode == 0o600
    out.append(CheckResult(
        "vault.mode_0600", CRITICAL, mode_ok,
        f"mode={oct(mode)}" if mode_ok else
        f"INSECURE mode={oct(mode)} (expected 0o600): {vault}"))
    return out


def _probe_executable(name: str) -> tuple[bool, str]:
    """Return ``(ok, detail)``. Uses ``--version`` with an 8 s timeout.

    Wraps ``.cmd``/``.bat`` shims (npm global installs on Windows) via
    ``cmd /c`` — see ``engine_detector.windows_wrap`` for why a bare
    ``subprocess.run`` raises ``WinError 193`` on those without it.
    """
    exe = shutil.which(name)
    if exe is None:
        return False, f"{name} not on PATH"
    try:
        from engine_detector import windows_wrap  # noqa: PLC0415 - lazy, sibling module
        cmd = windows_wrap([exe, "--version"])
    except ImportError:
        cmd = [exe, "--version"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
        if r.returncode != 0:
            return False, f"{name} --version rc={r.returncode}"
        version = (r.stdout or r.stderr).strip().splitlines()[:1]
        return True, f"{exe} {version[0] if version else ''}".strip()
    except subprocess.TimeoutExpired:
        return False, (
            f"{name} --version timed out after 8s (often antivirus scanning a "
            f"freshly spawned shell — try running `{name} --version` in a terminal)"
        )
    except Exception as e:  # noqa: BLE001
        return False, f"{name}: {type(e).__name__}: {e}"


def _check_engines(*, quick: bool) -> list[CheckResult]:
    out: list[CheckResult] = []
    ok, detail = _probe_executable("claude")
    out.append(CheckResult("engine.claude_cli", CRITICAL, ok, detail))
    if quick:
        return out
    # Optional engines: never CRITICAL — absence is normal for most deployments.
    for name in ("codex", "opencode"):
        ok, detail = _probe_executable(name)
        out.append(CheckResult(f"engine.{name}_cli", INFO, True,
                               detail if ok else f"optional, {detail}"))
    return out


def _check_hermes_ollama() -> list[CheckResult]:
    """Layer 22 / ADR-0066 — HermesEngine: probe local Ollama availability.

    WARNING when Ollama is not reachable (it is an optional engine —
    adapter starts normally without it; HermesEngine delegations fail
    gracefully with a ``hermes.ollama_unavailable`` audit event).
    INFO when reachable; reports the count of pulled models.

    Privacy: only model count lands in detail — never model names or
    any user-visible content. Base URL probe is a loopback GET with
    a 2 s socket timeout.
    """
    import urllib.error
    import urllib.request

    base_url = (
        os.environ.get("CORVIN_OLLAMA_BASE_URL")
        or os.environ.get("OLLAMA_HOST")
        or "http://localhost:11434"
    ).rstrip("/")

    try:
        import json
        with urllib.request.urlopen(f"{base_url}/api/tags", timeout=2) as resp:
            data = json.loads(resp.read())
        model_count = len(data.get("models") or [])
        return [CheckResult(
            "engine.hermes_ollama", INFO, True,
            f"ollama reachable, {model_count} model(s) pulled",
        )]
    except urllib.error.URLError:
        return [CheckResult(
            "engine.hermes_ollama", WARNING, False,
            f"ollama not reachable at {base_url} (HermesEngine optional — "
            "adapter starts normally; delegate_hermes calls will fail gracefully)",
        )]
    except Exception as e:  # noqa: BLE001
        return [CheckResult(
            "engine.hermes_ollama", WARNING, False,
            f"ollama probe error: {type(e).__name__}",
        )]


def _check_copilot_cli() -> list[CheckResult]:
    """Layer 22 / ADR-0071 — CopilotCliEngine: probe `copilot` binary availability.

    INFO when found; INFO (not WARNING) when absent — the binary is optional
    and requires a GitHub Copilot subscription. Adapter starts normally
    without it; delegate_copilot calls fail gracefully.

    Privacy: only the exit-code and short version string land in detail.
    """
    ok, detail = _probe_executable("copilot")
    return [CheckResult(
        "engine.copilot_cli", INFO, True,
        detail if ok else f"optional, {detail}",
    )]


def _check_mcp_servers(*, quick: bool) -> list[CheckResult]:
    out: list[CheckResult] = []
    _ensure_sys_path()
    for mod, label in (("forge.mcp_server", "forge"),
                       ("skill_forge.mcp_server", "skill_forge")):
        try:
            __import__(mod)
            out.append(CheckResult(f"mcp.{label}_importable", CRITICAL, True,
                                   mod))
        except Exception as e:  # noqa: BLE001
            out.append(CheckResult(
                f"mcp.{label}_importable", CRITICAL, False,
                f"{type(e).__name__}: {e}"))

    if quick:
        return out

    # In production the MCP servers are stdio-spawned children of the engine
    # for the duration of a turn — never long-running daemons. The strongest
    # boot-time check we can do without an engine present is: instantiate the
    # server class against a throwaway root and confirm the `serve` entry
    # point is callable. This catches partial-import damage that a bare
    # `import` would not.
    out.extend(_check_mcp_construct())

    # Third-party MCPs declared in cowork personas: check the executables.
    out.extend(_check_third_party_mcps())
    return out


def _check_mcp_construct() -> list[CheckResult]:
    out: list[CheckResult] = []
    import tempfile
    with tempfile.TemporaryDirectory(prefix="corvin-st-mcp-") as td:
        root = Path(td)
        try:
            from forge.mcp_server import MCPServer as _ForgeMCP  # type: ignore
            srv = _ForgeMCP(root)
            ok = callable(getattr(srv, "serve", None))
            out.append(CheckResult(
                "mcp.forge_constructible", WARNING, ok,
                "MCPServer(tmp_root) OK" if ok else "no serve() method"))
        except Exception as e:  # noqa: BLE001
            out.append(CheckResult("mcp.forge_constructible", WARNING, False,
                                   f"{type(e).__name__}: {e}"))
        try:
            # SkillForgeMCPServer takes no positional args — its workspace is
            # resolved internally via the tenant resolver.
            from skill_forge.mcp_server import SkillForgeMCPServer  # type: ignore
            srv = SkillForgeMCPServer()
            ok = callable(getattr(srv, "serve", None))
            out.append(CheckResult(
                "mcp.skill_forge_constructible", WARNING, ok,
                "SkillForgeMCPServer() OK" if ok else "no serve() method"))
        except Exception as e:  # noqa: BLE001
            out.append(CheckResult(
                "mcp.skill_forge_constructible", WARNING, False,
                f"{type(e).__name__}: {e}"))
    return out


def _check_third_party_mcps() -> list[CheckResult]:
    """Scan cowork personas for `mcp_servers[*].command`; check `which`."""
    out: list[CheckResult] = []
    personas_dir = _REPO_ROOT / "operator" / "cowork" / "personas"
    if not personas_dir.is_dir():
        return out

    seen: dict[str, list[str]] = {}  # executable → personas that reference it
    for pfile in sorted(personas_dir.glob("*.json")):
        try:
            data = json.loads(pfile.read_text())
        except Exception:  # noqa: BLE001
            continue
        servers = data.get("mcp_servers") or {}
        if not isinstance(servers, dict):
            continue
        for srv_name, srv in servers.items():
            if not isinstance(srv, dict):
                continue
            cmd = srv.get("command")
            if not cmd or not isinstance(cmd, str):
                continue
            # Skip python/node intermediates — they're checked by engine probe.
            if cmd in ("python", "python3", "node", "npx", sys.executable):
                continue
            seen.setdefault(cmd, []).append(f"{pfile.stem}/{srv_name}")

    for cmd, refs in seen.items():
        ok = shutil.which(cmd) is not None
        out.append(CheckResult(
            f"mcp.third_party.{cmd}", WARNING, ok,
            f"used by {','.join(refs[:3])}" + (f" (+{len(refs) - 3} more)" if len(refs) > 3 else "")
            if ok else f"{cmd} not on PATH, used by {','.join(refs[:3])}"))
    return out


def _check_artifacts(*, quick: bool) -> list[CheckResult]:
    """Layer 33 — verify the artifact memory subsystem is wired.

    These checks are mostly INFO/WARNING because Layer 33 is opt-in
    per turn (the LLM may never call an artifact_* tool). The only
    CRITICAL check is `artifacts.mcp_handlers_registered` — without
    that the documented MCP surface is broken.
    """
    out: list[CheckResult] = []
    _ensure_sys_path()

    # Library import — CRITICAL because the auto-register hook depends on it.
    try:
        from forge import artifacts as _art  # type: ignore  # noqa: F401
        out.append(CheckResult("artifacts.library_importable", CRITICAL, True,
                               "forge.artifacts"))
    except Exception as e:  # noqa: BLE001
        out.append(CheckResult("artifacts.library_importable", CRITICAL, False,
                               f"{type(e).__name__}: {e}"))
        return out  # nothing else makes sense without the library

    # Config file: INFO when absent (defaults apply), readable when present.
    try:
        cfg = _art.load_config()
        out.append(CheckResult("artifacts.config_readable", INFO, True,
                               f"backend={cfg.get('storage_backend')} "
                               f"ttl={cfg.get('session_artifact_ttl_days')}d"))
    except Exception as e:  # noqa: BLE001
        out.append(CheckResult("artifacts.config_readable", WARNING, False,
                               f"{type(e).__name__}: {e}"))

    # Global artifact root: WARNING when missing (created on first pin)
    # — not CRITICAL because a fresh tenant tree won't have it yet.
    try:
        groot = _art.global_artifacts_dir()
        if groot.exists():
            out.append(CheckResult("artifacts.global_root_writable",
                                   WARNING, os.access(groot, os.W_OK),
                                   str(groot)))
        else:
            out.append(CheckResult("artifacts.global_root", INFO, True,
                                   "absent (created on first pin)"))
    except Exception as e:  # noqa: BLE001
        out.append(CheckResult("artifacts.global_root", WARNING, False,
                               f"{type(e).__name__}: {e}"))

    # MCP handlers: CRITICAL — without these the documented LLM surface is broken.
    try:
        from forge.mcp_server import MCPServer  # type: ignore
        import tempfile
        with tempfile.TemporaryDirectory(prefix="corvin-st-art-") as td:
            srv = MCPServer(Path(td))
            tools = {t["name"] for t in srv._all_tools()}
        expected = {"artifact_list", "artifact_search", "artifact_get",
                    "artifact_extract", "artifact_register", "artifact_pin"}
        missing = expected - tools
        out.append(CheckResult(
            "artifacts.mcp_handlers_registered", CRITICAL, not missing,
            f"all six tools advertised" if not missing
            else f"missing: {','.join(sorted(missing))}"))
    except Exception as e:  # noqa: BLE001
        out.append(CheckResult("artifacts.mcp_handlers_registered",
                               CRITICAL, False,
                               f"{type(e).__name__}: {e}"))

    # Auto-register hook: INFO if the hook script is present and listed
    # in hooks.json — proves the wiring is complete after a tag bump.
    try:
        hook = (_REPO_ROOT / "operator" / "voice" / "hooks"
                / "artifact_register.py")
        hooks_json = _REPO_ROOT / "operator" / "voice" / "hooks" / "hooks.json"
        wired = (hook.is_file()
                 and hooks_json.is_file()
                 and "artifact_register.py" in hooks_json.read_text())
        out.append(CheckResult("artifacts.auto_register_hook", INFO, wired,
                               "wired in hooks.json" if wired
                               else "hook file or hooks.json entry missing"))
    except Exception:  # noqa: BLE001
        out.append(CheckResult("artifacts.auto_register_hook", INFO, False,
                               "could not introspect hooks.json"))
    return out


def _check_license(*, quick: bool) -> list[CheckResult]:
    """ADR-0092 — License key system (M1+M2).

    Absence of a licence key = Free tier (Apache-only deployment, normal).
    Invalid token = WARNING; Apache-core features keep working regardless.
    Severity is never CRITICAL — the system is fully functional without a key.
    """
    out: list[CheckResult] = []
    _ensure_sys_path()

    # Check that the 'cryptography' package is installed.
    # Without it, Ed25519 token verification is disabled and all tokens
    # silently degrade to Free tier — users cannot activate their license.
    # This is fail-closed (not a security bypass), but a UX issue that must
    # be surfaced prominently.
    try:
        import cryptography  # type: ignore  # noqa: F401
        out.append(CheckResult(
            "license.cryptography_available", INFO, True,
            "cryptography package present — Ed25519 verification enabled",
        ))
    except ImportError:
        # P0-A (security review 2026-06-18): escalate to CRITICAL when a token
        # file is present. A user who has a paid licence cannot activate it —
        # silent Free-tier downgrade is a security gap, not just a UX issue.
        import os as _os_cry
        # Mirror validator._find_token() sources (review LOW: do NOT treat
        # config-dir license.key as a token — the validator never loads it; the
        # canonical key file is <corvin_home>/global/license.key). config dir
        # honours XDG_CONFIG_HOME like validator._config_dir.
        _cfg_base = _os_cry.environ.get("XDG_CONFIG_HOME") or "~/.config"
        _cfg_dir = Path(_os_cry.path.expanduser(_cfg_base)) / "corvin-voice"
        _corvin_home_env = _os_cry.environ.get("CORVIN_HOME")
        _global_lic = (
            Path(_os_cry.path.expanduser(_corvin_home_env)) if _corvin_home_env
            else Path.home() / ".corvin"
        ) / "global" / "license.key"
        _token_paths = [
            _cfg_dir / "session.key",
            _global_lic,
        ]
        _token_env = bool(
            _os_cry.environ.get("CORVIN_LICENSE_KEY")
            or _os_cry.environ.get("CORVIN_SESSION_KEY")
        )
        _token_present = _token_env or any(p.is_file() for p in _token_paths)
        _cry_sev = CRITICAL if _token_present else WARNING
        out.append(CheckResult(
            "license.cryptography_available", _cry_sev, False,
            "cryptography package not installed — Ed25519 token verification "
            "is DISABLED."
            + (
                " A token file is present but CANNOT be verified — system is "
                "operating in Free tier against operator intent. "
                if _token_present else " "
            )
            + "Install: pip install cryptography",
        ))

    # ADR-0092: new operator/license/ module
    try:
        import pathlib as _pl
        _lic_root = str(_pl.Path(__file__).resolve().parents[2])
        import sys as _sys2
        if _lic_root not in _sys2.path:
            _sys2.path.insert(0, _lic_root)
        from license.validator import is_loaded, active_tier  # type: ignore
        from license.validator import _ACTIVE_LICENSE  # type: ignore  # noqa: PLC2701
        # STL-02 (ADR-0146): provenance guard. The adapter's boot B1 check verifies
        # license.validator/limits load from operator/license/; self_test (doctor /
        # Docker HEALTHCHECK) did not. Without it a PYTHONPATH-shadowed validator
        # could make doctor CONFIRM a forged 'enterprise' tier as healthy. Treat a
        # shadow as CRITICAL and refuse to vouch for the reported tier.
        _b1_expected = _pl.Path(__file__).resolve().parents[2] / "license"
        _shadowed = None
        for _b1_name in ("license.validator", "license.limits"):
            _b1_mod = _sys2.modules.get(_b1_name)
            if _b1_mod is None:
                continue
            _b1_file = getattr(_b1_mod, "__file__", None)
            if not _b1_file or not _pl.Path(_b1_file).resolve().is_relative_to(_b1_expected):
                _shadowed = _b1_name
                break
        if _shadowed is not None:
            out.append(CheckResult(
                "license.provenance", CRITICAL, False,
                f"{_shadowed} loaded from an unexpected path — PYTHONPATH shadow "
                "suspected; refusing to vouch for the reported tier.",
            ))
        else:
            _tier = active_tier()
            _loaded = is_loaded()
            out.append(CheckResult(
                "license.adr0092",
                INFO,
                True,
                f"tier={_tier!r} loaded={_loaded}",
            ))
    except Exception as e:  # noqa: BLE001
        out.append(CheckResult(
            "license.adr0092", WARNING, False,
            f"operator/license/ unavailable: {type(e).__name__}: {e}",
        ))

    # B3 (ADR-0138 M4 / ADR-0144 F-04): production installs must use the compiled
    # Rust binary. The Python stub lacks compile-time key hardening.
    # CORVIN_PRODUCTION_MODE=1 escalates missing binary from WARNING to CRITICAL
    # — this is the load-bearing prerequisite for GA (ADR-0144 §F-04).
    try:
        from license.seal_loader import is_compiled as _seal_is_compiled  # type: ignore
        _seal_ok = _seal_is_compiled()
        _prod_mode = os.environ.get("CORVIN_PRODUCTION_MODE", "0").strip() == "1"
        _seal_severity = CRITICAL if (_prod_mode and not _seal_ok) else WARNING
        out.append(CheckResult(
            "license.seal_compiled", _seal_severity, _seal_ok,
            "compiled Rust seal binary in use"
            if _seal_ok
            else (
                "Python stub in use — CRITICAL: production mode requires compiled binary "
                "(corvinlabs/corvin-seal-private). Set CORVIN_PRODUCTION_MODE=0 for dev/CI."
                if _prod_mode
                else "Python stub in use — dev/CI mode (stub lacks compile-time key hardening)"
            ),
        ))
    except Exception as _e_seal:  # noqa: BLE001
        out.append(CheckResult("license.seal_compiled", WARNING, False,
                               f"seal_loader unavailable: {type(_e_seal).__name__}"))

    # A1 (ADR-0138 M6): warn when the shipped placeholder operator key is active.
    # A build that never replaced CORVIN_PUBLIC_KEY_B64 cannot verify operator
    # tokens — any token with the matching private half would be accepted.
    try:
        import pathlib as _plA1
        _lic_root_a1 = str(_plA1.Path(__file__).resolve().parents[2])
        import sys as _sys_a1
        if _lic_root_a1 not in _sys_a1.path:
            _sys_a1.path.insert(0, _lic_root_a1)
        from license.validator import (  # type: ignore
            CORVIN_PUBLIC_KEY_B64 as _cpk,
            _PLACEHOLDER_OPERATOR_PUBKEYS as _phk,
        )
        _is_ph = _cpk in _phk
        # CORVIN_PRODUCTION_MODE=1 escalates placeholder key to CRITICAL (same pattern
        # as seal_compiled above). In dev/CI the placeholder is expected; in production
        # the operator key MUST be replaced before shipping (ADR-0144 F-04).
        _ph_prod_mode = os.environ.get("CORVIN_PRODUCTION_MODE", "0").strip() == "1"
        _ph_severity = CRITICAL if (_ph_prod_mode and _is_ph) else WARNING
        out.append(CheckResult(
            "license.operator_key", _ph_severity, not _is_ph,
            "operator public key is the shipped placeholder — operator tokens cannot "
            "be verified (Free tier enforced). "
            + ("CRITICAL: replace before production use (ADR-0144 F-04)." if _ph_prod_mode and _is_ph
               else "Dev/CI mode — expected during development.")
            if _is_ph else "operator public key set",
        ))
    except Exception as _e_a1:  # noqa: BLE001
        out.append(CheckResult("license.operator_key", WARNING, False,
                               f"validator unavailable: {type(_e_a1).__name__}"))

    if quick:
        return out

    # Legacy corvin_license plugin (ADR-0017) — kept for backwards compat
    try:
        from corvin_license import verifier as _lic_verifier  # type: ignore  # noqa: F401
        out.append(CheckResult("license.plugin_legacy", INFO, True,
                               "corvin_license (legacy) installed"))
        tid, _ = _resolved_tenant_id()
        lic_path = _tenant_home_path(tid) / "global" / "license" / "license.jwt"
        if lic_path.exists():
            try:
                from corvin_license.verifier import load_license_from_disk  # type: ignore
                info = load_license_from_disk()  # reads canonical path (keyword-only, no positional)
                out.append(CheckResult("license.verify_legacy", INFO, True,
                                       f"tier={getattr(info, 'tier', '?')}"))
            except Exception as e:  # noqa: BLE001
                out.append(CheckResult("license.verify_legacy", WARNING, False,
                                       f"{type(e).__name__}: {e}"))
    except Exception:
        pass  # legacy plugin not installed — normal for ADR-0092 deployments

    return out


# ── ADR-0133 CLAG — Chain-Locked Adaptive Gating self-test ──────────────────


def _check_clag(*, quick: bool) -> list[CheckResult]:
    """ADR-0133 — verify that the CLAG module is importable and functional.

    WARNING (not CRITICAL): CLAG is a hardening layer; its absence degrades
    security but the adapter can still start.  Operators must fix before
    production.

    Checks:
      1. ``clag`` module importable from the forge package.
      2. ``verify_last_k()`` returns no failures on the current audit chain
         (empty chain = OK; non-zero failures = WARNING).
    """
    out: list[CheckResult] = []
    _ensure_sys_path()

    # 1. Importability
    try:
        from forge import clag as _clag  # type: ignore
        out.append(CheckResult("clag.importable", WARNING, True,
                               "forge.clag"))
    except Exception as exc:  # noqa: BLE001
        out.append(CheckResult("clag.importable", WARNING, False,
                               f"{type(exc).__name__}: {exc}"))
        return out  # nothing else makes sense without the module

    if quick:
        return out

    # 2. Verify last-K events on live audit chain (non-invasive — read-only)
    try:
        tid, _ = _resolved_tenant_id()
        audit_p = _tenant_home_path(tid) / "global" / "forge" / "audit.jsonl"
        env_p = os.environ.get("VOICE_AUDIT_PATH")
        if env_p:
            audit_p = Path(env_p)

        if not audit_p.exists():
            out.append(CheckResult("clag.chain_verify", INFO, True,
                                   "audit chain absent — skipped (empty deployment)"))
        else:
            failures = _clag.verify_last_k(audit_p, k=_clag.VERIFY_K_DEFAULT)
            if failures:
                out.append(CheckResult(
                    "clag.chain_verify", WARNING, False,
                    f"{len(failures)} hash-link error(s) in last "
                    f"{_clag.VERIFY_K_DEFAULT} events — run voice-audit verify",
                ))
            else:
                out.append(CheckResult(
                    "clag.chain_verify", INFO, True,
                    f"last {_clag.VERIFY_K_DEFAULT} events hash-link OK",
                ))
    except Exception as exc:  # noqa: BLE001
        out.append(CheckResult("clag.chain_verify", WARNING, False,
                               f"{type(exc).__name__}: {exc}"))

    return out


# ── ADR-0135 — Chain Continuity Anchor self-test ───────────────────────────


def _check_chain_anchor(*, quick: bool) -> list[CheckResult]:
    """ADR-0135 M1 — verify chain_anchor.json at boot.

    CRITICAL on HMAC/tail/count mismatch (possible chain truncation or replay).
    WARNING on absent anchor (first boot or legitimate clean reset).
    INFO on successful verification.

    quick mode: only checks if the CLAG module can call verify_chain_anchor.
    """
    out: list[CheckResult] = []
    _ensure_sys_path()

    try:
        from forge import clag as _clag  # type: ignore
    except Exception as exc:  # noqa: BLE001
        out.append(CheckResult("chain_anchor.importable", WARNING, False,
                               f"forge.clag unavailable: {type(exc).__name__}: {exc}"))
        return out

    if not hasattr(_clag, "verify_chain_anchor"):
        out.append(CheckResult("chain_anchor.importable", WARNING, False,
                               "forge.clag.verify_chain_anchor not found — update required"))
        return out

    if quick:
        out.append(CheckResult("chain_anchor.importable", INFO, True,
                               "verify_chain_anchor available"))
        return out

    try:
        tid, _ = _resolved_tenant_id()
        tenant_home = _tenant_home_path(tid)
        env_p = os.environ.get("VOICE_AUDIT_PATH")
        audit_p = Path(env_p) if env_p else tenant_home / "global" / "forge" / "audit.jsonl"
        anchor_p = audit_p.parent / "chain_anchor.json"

        status, detail = _clag.verify_chain_anchor(audit_p, anchor_p, emit=False)

        if status == "ok":
            out.append(CheckResult("chain_anchor.verify", INFO, True, detail))
        elif status == "absent":
            out.append(CheckResult("chain_anchor.verify", WARNING, True, detail))
        else:  # "failed"
            out.append(CheckResult("chain_anchor.verify", CRITICAL, False, detail))
    except Exception as exc:  # noqa: BLE001
        # An unexpected exception (e.g. PermissionError on anchor_path.exists())
        # is itself a security signal — fail CRITICAL, not WARNING, so the boot
        # check is never silently bypassed.
        out.append(CheckResult("chain_anchor.verify", CRITICAL, False,
                               f"unexpected error during anchor check: {type(exc).__name__}: {exc}"))

    return out


# ── ADR-0136 — Instance seed self-test ────────────────────────────────────────


def _check_instance_seed() -> list[CheckResult]:
    """ADR-0136 M1 — verify instance_seed.key exists and is mode 0600.

    CRITICAL when the file exists but has world-readable or group-readable
    permissions (key material exposure).
    WARNING when the file is absent (will be auto-created on next chain write
    — no operational impact, but worth flagging for first-boot awareness).
    INFO on correct permissions.
    """
    out: list[CheckResult] = []
    try:
        tid, _ = _resolved_tenant_id()
        seed_path = _tenant_home_path(tid) / "global" / "instance_seed.key"

        if not seed_path.exists():
            out.append(CheckResult("instance_seed.key", WARNING, True,
                                   "instance_seed.key absent — will auto-generate on first chain write"))
            return out

        mode = seed_path.stat().st_mode & 0o777
        if mode != 0o600:
            out.append(CheckResult(
                "instance_seed.key", CRITICAL, False,
                f"instance_seed.key has mode {oct(mode)} — must be 0600 (key material leak risk)",
            ))
        else:
            out.append(CheckResult("instance_seed.key", INFO, True,
                                   "instance_seed.key present and mode 0600"))
    except Exception as exc:  # noqa: BLE001
        out.append(CheckResult("instance_seed.key", WARNING, False,
                               f"{type(exc).__name__}: {exc}"))

    return out


def _check_instance_key() -> list[CheckResult]:
    """ADR-0145/0153 — verify the IBC Ed25519 private key instance_key.pem is
    mode 0600. CRITICAL on group/other-readable (a co-located UID could forge
    instance_attestation signatures); WARNING when absent; INFO otherwise.
    Mirrors _check_instance_seed (R2 finding: this key had no boot mode gate)."""
    out: list[CheckResult] = []
    try:
        _ensure_sys_path()
        try:
            from instance_identity import instance_key_path  # type: ignore
        except ImportError:
            from operator.bridges.shared.instance_identity import instance_key_path  # type: ignore
        kp = instance_key_path()
        if not kp.exists():
            out.append(CheckResult("instance_key.pem", WARNING, True,
                                   "instance_key.pem absent — auto-generated on first IBC use"))
            return out
        mode = kp.stat().st_mode & 0o777
        if mode & 0o077:
            out.append(CheckResult(
                "instance_key.pem", CRITICAL, False,
                f"instance_key.pem has mode {oct(mode)} — must be 0600 "
                "(IBC private-key leak → attestation forgery)"))
        else:
            out.append(CheckResult("instance_key.pem", INFO, True,
                                   "instance_key.pem present and mode 0600"))
    except Exception as exc:  # noqa: BLE001
        out.append(CheckResult("instance_key.pem", WARNING, False,
                               f"{type(exc).__name__}: {exc}"))
    return out


# ── Layer 35 / 37 EU compliance checks ─────────────────────────────────────


def _load_tenant_config_for_self_test() -> dict | None:
    """Best-effort load of the active tenant.corvin.yaml.

    Returns ``None`` when PyYAML / the file is unavailable so the
    subsequent checks degrade gracefully to INFO "not configured".
    """
    try:
        import yaml  # type: ignore
    except ImportError:
        return None
    tid, _ = _resolved_tenant_id()
    cfg_path = _tenant_home_path(tid) / "global" / "tenant.corvin.yaml"
    if not cfg_path.is_file():
        return None
    try:
        return yaml.safe_load(cfg_path.read_text("utf-8"))
    except (OSError, yaml.YAMLError):
        return None


def _check_egress_preset() -> list[CheckResult]:
    """ADR-0043 / Layer 35: validate the tenant's egress preset.

    INFO when no preset is active (back-compat default).
    WARNING when the preset shape is inconsistent (forbidden_hosts +
    enabled=False, deny-all + empty allowed_hosts, external-egress
    engine + L35 cannot enforce runtime confinement).
    CRITICAL when the loader raises on the policy block (operator
    must fix configuration before boot proceeds).
    """
    out: list[CheckResult] = []
    cfg = _load_tenant_config_for_self_test()
    if cfg is None:
        out.append(CheckResult("egress.preset_loaded", INFO, True,
                               "no tenant.corvin.yaml or PyYAML missing"))
        return out

    _ensure_sys_path()
    try:
        from egress_gate import EgressGate  # type: ignore
    except ImportError as e:
        out.append(CheckResult("egress.preset_loaded", WARNING, False,
                               f"egress_gate import failed: {e}"))
        return out

    try:
        gate = EgressGate.from_tenant_config(cfg)
    except ValueError as e:
        out.append(CheckResult("egress.preset_loaded", CRITICAL, False,
                               f"egress policy load: {e}"))
        return out

    if not gate.policy.enabled:
        out.append(CheckResult("egress.preset_loaded", INFO, True,
                               "egress disabled (back-compat default)"))
        return out

    # Engine compliance overlay — best-effort cross-check
    expected_engines: list[str] = []
    engine_compliance: dict = {}
    try:
        from data_classification import DataFlowGuard  # type: ignore
        guard = DataFlowGuard.from_tenant_config(cfg)
        engine_compliance = guard.engine_compliance
    except Exception:  # noqa: BLE001
        pass
    spec = cfg.get("spec") if isinstance(cfg, dict) else None
    if isinstance(spec, dict):
        residency = spec.get("data_residency") or {}
        allowed = residency.get("allowed_engines") or []
        if isinstance(allowed, list):
            expected_engines = [e for e in allowed if isinstance(e, str)]

    warnings = gate.validate_preset_consistency(
        expected_engines=expected_engines or None,
        engine_compliance=engine_compliance or None,
    )
    if warnings:
        for w in warnings:
            out.append(CheckResult("egress.preset_consistency", WARNING,
                                   False, w[:200]))
    else:
        out.append(CheckResult("egress.preset_loaded", INFO, True,
                               f"enabled, default_action={gate.policy.default_action}, "
                               f"allowed={len(gate.policy.allowed_hosts)}, "
                               f"forbidden={len(gate.policy.forbidden_hosts)}"))
    return out


def _check_agent_heartbeat(*, quick: bool) -> list[CheckResult]:
    """ADR-0047 — Instance Agent reachability check.

    Only runs when CORVIN_HOSTED_MODE=true (i.e. hosted deployments where
    the agent is expected to be running).  In self-hosted deployments the
    agent is optional; its absence is INFO, not a failure.

    CRITICAL when hosted mode is active but the agent has not responded
    for more than 5 minutes (CORVIN_AGENT_HEARTBEAT_MAX_GAP_S, default 300).
    """
    out: list[CheckResult] = []
    hosted = os.environ.get("CORVIN_HOSTED_MODE", "").strip().lower() in ("1", "true", "yes")
    if not hosted:
        out.append(CheckResult("agent.heartbeat", INFO, True,
                               "hosted mode off — agent check skipped"))
        return out

    agent_url = (
        os.environ.get("CORVIN_AGENT_URL", "http://127.0.0.1:8766").rstrip("/")
    )
    max_gap = int(os.environ.get("CORVIN_AGENT_HEARTBEAT_MAX_GAP_S", "300"))

    if quick:
        # Quick mode: TCP connect only (no HTTP round-trip).
        import socket
        from urllib.parse import urlparse
        parsed = urlparse(agent_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 8766
        try:
            with socket.create_connection((host, port), timeout=3):
                pass
            out.append(CheckResult("agent.heartbeat", INFO, True,
                                   f"TCP connect {host}:{port} OK"))
        except OSError as exc:
            out.append(CheckResult("agent.heartbeat", CRITICAL, False,
                                   f"agent not reachable at {host}:{port}: {exc}"))
        return out

    # Full health check via HTTP.
    import json
    import time
    from urllib.request import urlopen, Request
    from urllib.error import URLError
    health_url = f"{agent_url}/health"
    try:
        req = Request(health_url, method="GET")
        with urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read())
        if not body.get("ok"):
            out.append(CheckResult("agent.heartbeat", CRITICAL, False,
                                   f"agent /health returned ok=false: {body}"))
        else:
            out.append(CheckResult("agent.heartbeat", CRITICAL, True,
                                   f"uptime={body.get('uptime_s', '?')}s "
                                   f"keypair={body.get('keypair_ready')}"))
    except URLError as exc:
        out.append(CheckResult("agent.heartbeat", CRITICAL, False,
                               f"agent unreachable at {health_url}: {exc}"))
    except Exception as exc:  # noqa: BLE001
        out.append(CheckResult("agent.heartbeat", WARNING, False,
                               f"{type(exc).__name__}: {exc}"))
    return out


def _check_social_keypair() -> list[CheckResult]:
    """ADR-0053 / Layer 39: verify actor_keypair.json is mode 0600 when present.

    CRITICAL if the keypair file is world-readable — the Ed25519 private key
    must never be exposed beyond the owning user. INFO when the file is absent
    (social federation not yet enabled — normal default).
    """
    out: list[CheckResult] = []
    _ensure_sys_path()
    try:
        from social_actor import check_keypair_mode, keypair_path  # type: ignore
    except ImportError:
        out.append(CheckResult("social.keypair_mode", INFO, True,
                               "social_actor not available (L39 not installed)"))
        return out

    path = keypair_path()
    if not path.exists():
        out.append(CheckResult("social.keypair_mode", INFO, True,
                               "actor_keypair.json absent — social federation not enabled"))
        return out

    ok = check_keypair_mode()
    if not ok:
        out.append(CheckResult("social.keypair_mode", CRITICAL, False,
                               "actor_keypair.json is world-readable (must be 0600)"))
    else:
        out.append(CheckResult("social.keypair_mode", INFO, True,
                               "actor_keypair.json mode 0600 OK"))
    return out


def _check_a2a_key_files() -> list[CheckResult]:
    """Layer 38: verify A2A origin/endpoint key files are mode 0600.

    CRITICAL if any file under ``operator/cowork/remote_origins/`` or
    ``operator/cowork/remote_endpoints/`` is world-readable — these JSON
    files contain HMAC keys and bearer tokens.
    INFO when neither directory exists (A2A not yet provisioned — normal).
    """
    out: list[CheckResult] = []
    dirs = {
        "a2a.origin_key_mode":   _REPO_ROOT / "operator" / "cowork" / "remote_origins",
        "a2a.endpoint_key_mode": _REPO_ROOT / "operator" / "cowork" / "remote_endpoints",
    }
    any_exists = False
    for check_id, d in dirs.items():
        if not d.exists():
            continue
        any_exists = True
        bad: list[str] = []
        for p in d.glob("*.json"):
            try:
                mode = p.stat().st_mode & 0o777
                if mode & 0o044:  # world- or group-readable
                    bad.append(f"{p.name} ({oct(mode)})")
            except OSError:
                pass
        if bad:
            out.append(CheckResult(
                check_id, CRITICAL, False,
                f"world/group-readable A2A key files in {d.name}/: {', '.join(bad[:5])}",
            ))
        else:
            out.append(CheckResult(check_id, INFO, True,
                                   f"all {d.name}/*.json files mode ≤0600"))
    if not any_exists:
        out.append(CheckResult("a2a.origin_key_mode", INFO, True,
                               "no A2A key directories found — A2A not provisioned"))
    return out


def _check_audit_sealer() -> list[CheckResult]:
    """ADR-0044 / Layer 37: verify the configured sealer binary is
    installed when encryption-at-rest is enabled.

    CRITICAL when enabled but binary missing — silently fail-back to
    plaintext would weaken the compliance claim.
    INFO when disabled (back-compat default).
    """
    out: list[CheckResult] = []
    cfg = _load_tenant_config_for_self_test()
    if cfg is None:
        out.append(CheckResult("audit.sealer", INFO, True,
                               "no tenant.corvin.yaml or PyYAML missing"))
        return out

    _ensure_sys_path()
    try:
        from audit_sealer import policy_from_tenant_config, sealer_binary_available  # type: ignore
    except ImportError as e:
        out.append(CheckResult("audit.sealer", WARNING, False,
                               f"audit_sealer import failed: {e}"))
        return out

    try:
        policy = policy_from_tenant_config(cfg)
    except ValueError as e:
        out.append(CheckResult("audit.sealer", CRITICAL, False,
                               f"audit policy load: {e}"))
        return out

    if not policy.encryption.enabled:
        out.append(CheckResult("audit.sealer", INFO, True,
                               "encryption_at_rest disabled (back-compat default)"))
        return out

    binary = policy.encryption.sealer_cmd
    available = sealer_binary_available(binary)
    if not available:
        out.append(CheckResult("audit.sealer", CRITICAL, False,
                               f"encryption_at_rest enabled but {binary!r} binary not on $PATH"))
        return out
    out.append(CheckResult("audit.sealer", INFO, True,
                           f"{binary!r} available, retention={policy.retention.retention_years}y"))
    return out


def _check_a2a_network_membership() -> list[CheckResult]:
    """ADR-0103 M4 — A2A network membership self-test.

    CRITICAL checks (block boot in strict mode):
      - ``a2a_network_pubkey.pem`` is present and parseable.
      - Local SesT fingerprint is not on the manifest revocation list.

    WARNING check:
      - Cached manifest is older than 3 days (``a2a.manifest_stale``).

    Severity rationale: failing CRITICAL here blocks A2A reception, not the
    whole adapter.  We degrade to WARNING so the adapter starts normally but
    A2A worker spawn is refused for authenticated-but-revoked senders.
    The CRITICAL label is preserved for the pubkey file (without it the
    crypto verification chain is structurally broken).
    """
    out: list[CheckResult] = []
    _ensure_sys_path()

    # 1. Embedded pubkey present and parseable
    pubkey_path = _REPO_ROOT / "operator" / "license" / "a2a_network_pubkey.pem"
    if not pubkey_path.exists():
        out.append(CheckResult(
            "a2a.network_pubkey", CRITICAL, False,
            f"a2a_network_pubkey.pem missing at {pubkey_path} "
            "(ADR-0103: RS256 verification structurally broken)"))
        return out

    try:
        from cryptography.hazmat.primitives.serialization import (  # type: ignore
            load_pem_public_key,
        )
        load_pem_public_key(pubkey_path.read_bytes())
        out.append(CheckResult("a2a.network_pubkey", CRITICAL, True,
                               "a2a_network_pubkey.pem present and parseable"))
    except ImportError:
        out.append(CheckResult(
            "a2a.network_pubkey", WARNING, False,
            "'cryptography' package not installed — RS256 verification unavailable. "
            "Install it: pip install cryptography"))
        return out
    except Exception as e:  # noqa: BLE001
        out.append(CheckResult(
            "a2a.network_pubkey", CRITICAL, False,
            f"a2a_network_pubkey.pem unreadable or malformed: {type(e).__name__}"))
        return out

    # 2. Manifest check — load cached manifest, check staleness + own revocation
    try:
        import sys as _sys
        _shared = _REPO_ROOT / "operator" / "bridges" / "shared"
        if str(_shared) not in _sys.path:
            _sys.path.insert(0, str(_shared))
        from a2a_manifest import load_manifest as _lm  # type: ignore
        manifest = _lm()

        # Staleness warning
        if manifest.is_stale:
            out.append(CheckResult(
                "a2a.manifest_age", WARNING, False,
                f"A2A network manifest is {manifest.age_days:.1f} days old "
                "(expected daily refresh at adapter restart)"))
        else:
            age_str = f"{manifest.age_days:.1f}d" if not manifest.is_empty else "empty"
            out.append(CheckResult(
                "a2a.manifest_age", INFO, True,
                f"manifest age={age_str} sig_verified={manifest.sig_verified}"))

        # Local SesT revocation check — mirrors validator._find_token() order
        import hashlib as _hl
        import os as _os
        token = _os.environ.get("CORVIN_LICENSE_KEY", "").strip()
        if not token:
            # Session key written by the refresh daemon
            try:
                session_key = Path.home() / ".config" / "corvin-voice" / "session.key"
                if session_key.exists():
                    t = session_key.read_text("utf-8").strip()
                    if t:
                        token = t
            except Exception:
                pass
        if not token:
            try:
                corvin_home = Path(
                    _os.environ.get("CORVIN_HOME", "")
                    or (Path.home() / ".corvin")
                )
                key_file = corvin_home / "global" / "license.key"
                if key_file.exists():
                    token = key_file.read_text("utf-8").strip()
            except Exception:
                pass

        if token:
            parts = token.split(".")
            if len(parts) == 3:
                fp = _hl.sha256(
                    (parts[0] + "." + parts[1]).encode("ascii")
                ).hexdigest()
                if fp in manifest.revoked_sest_fps:
                    out.append(CheckResult(
                        "a2a.sest_not_revoked", CRITICAL, False,
                        "Local SesT fingerprint is on the A2A network revocation "
                        "list. Renew your license key at corvin-labs.com/pricing."))
                else:
                    out.append(CheckResult(
                        "a2a.sest_not_revoked", INFO, True,
                        f"sest_fp={fp[:16]}… not revoked"))
            else:
                out.append(CheckResult("a2a.sest_not_revoked", INFO, True,
                                       "no SesT — free tier, no revocation check needed"))
        else:
            out.append(CheckResult("a2a.sest_not_revoked", INFO, True,
                                   "no SesT — free tier, no revocation check needed"))

    except Exception as e:  # noqa: BLE001
        out.append(CheckResult("a2a.manifest_age", WARNING, False,
                               f"A2A manifest check failed unexpectedly ({type(e).__name__}); "
                               "revocation status unknown"))

    return out


def _check_acs_runtime() -> list[CheckResult]:
    """ADR-0104 — ACS Runtime (Autonomous Compute Shell, second compute engine).

    INFO when importable; INFO (not WARNING) when absent — ACS is optional,
    just like copilot. Adapter starts normally without it; acs-workflow runs
    fail gracefully with a clear error message.

    Privacy: only import success/failure and a module count land in detail —
    never workflow IDs, task text, or engine output.
    """
    out: list[CheckResult] = []
    _ensure_sys_path()
    for mod, label in (("acs_runtime", "acs_runtime"),
                       ("acs_validator", "acs_validator"),
                       ("acs_gate_chain", "acs_gate_chain")):
        try:
            __import__(mod)
            out.append(CheckResult(f"acs.{label}_importable", INFO, True, mod))
        except Exception as e:  # noqa: BLE001
            out.append(CheckResult(
                f"acs.{label}_importable", INFO, True,
                f"optional, not installed: {type(e).__name__}"))
    return out


def _check_compliance_manifest() -> list[CheckResult]:
    """ADR-0056: verify the compliance manifest bundle.

    Loads compliance/*.yaml, checks GPG signature (when present), and
    validates that every rule has a registered test reference.

    Severity mapping:
      CRITICAL — manifest.sig present but GPG-invalid, or min_version not met
      WARNING  — manifest.sig missing, or any rule has no test reference found
      INFO     — all rules pass or manifest directory absent (opt-in feature)

    Emits ``compliance.manifest_check`` to the L16 hash chain independently
    of the boot.self_test_* event (ADR-0056 §Audit integration).
    """
    out: list[CheckResult] = []

    _ensure_sys_path()
    try:
        from compliance_manifest import (  # type: ignore
            ManifestCheckResult,
            run_compliance_check,
            resolve_manifest_dir,
        )
    except ImportError as exc:
        out.append(CheckResult("compliance.manifest", WARNING, False,
                               f"compliance_manifest module import failed: {exc}"))
        return out

    manifest_dir = resolve_manifest_dir()
    if not manifest_dir.exists():
        out.append(CheckResult("compliance.manifest", INFO, True,
                               f"manifest dir not found at {manifest_dir.name}/ — skipped"))
        return out

    # Load tenant config for min_version + signer_fingerprint
    tenant_cfg_raw = _load_tenant_config_for_self_test()
    tenant_compliance_cfg: dict = {}
    _deployment_profile = ""
    if tenant_cfg_raw and isinstance(tenant_cfg_raw.get("spec"), dict):
        _spec = tenant_cfg_raw["spec"]
        tenant_compliance_cfg = _spec.get("compliance_manifest") or {}
        _deployment_profile = str(_spec.get("deployment_profile") or "")

    result: ManifestCheckResult = run_compliance_check(
        manifest_dir,
        tenant_config=tenant_compliance_cfg,
    )

    # M4 (ADR-0057): eu_production deployments must pin spec.compliance_manifest.min_version
    _EU_PROFILES = frozenset({"eu_production", "eu_production_ollama"})
    if _deployment_profile in _EU_PROFILES and not tenant_compliance_cfg.get("min_version"):
        out.append(CheckResult(
            "compliance.manifest.version_pin", WARNING, False,
            f"eu_production profile without spec.compliance_manifest.min_version "
            f"— pin to v{result.manifest_version} in tenant.corvin.yaml "
            f"(ADR-0057 M4)",
        ))

    # Emit dedicated compliance.manifest_check audit event (ADR-0056 allow-list)
    _emit_compliance_audit(result)

    if result.load_error:
        sev = CRITICAL if result.sig_status == "invalid" else WARNING
        out.append(CheckResult("compliance.manifest", sev, False,
                               result.load_error))
        return out

    if result.sig_status == "invalid":
        out.append(CheckResult("compliance.manifest.sig", CRITICAL, False,
                               "GPG signature verification failed"))
        return out

    if result.sig_status == "missing":
        out.append(CheckResult("compliance.manifest.sig", WARNING, False,
                               "manifest.sig missing — run compliance/sign.sh sign"))

    if result.rules_failed > 0:
        out.append(CheckResult("compliance.manifest.rules", CRITICAL, False,
                               f"{result.rules_failed} rule(s) failed validation"))
        return out

    if result.rules_warned > 0:
        out.append(CheckResult("compliance.manifest.rules", WARNING, False,
                               f"{result.rules_warned} rule(s) have no test reference"))

    if result.rules_passed == result.rules_checked and result.sig_status in ("valid", "skipped"):
        out.append(CheckResult("compliance.manifest", INFO, True,
                               f"v{result.manifest_version}: "
                               f"{result.rules_passed}/{result.rules_checked} rules OK "
                               f"(sig={result.sig_status})"))

    return out


def _check_operator_declaration() -> list[CheckResult]:
    """ADR-0057 / Component 3: verify operator Art. 28-30 declaration.

    CRITICAL when deployment_profile is eu_production / eu_production_ollama
    AND spec.operator_declaration is missing or dpia_completed is false.
    INFO / skipped for all other profiles.
    """
    out: list[CheckResult] = []
    _ensure_sys_path()
    try:
        from operator_declaration import (  # type: ignore
            check_operator_declaration,
            emit_declaration_audit,
        )
    except ImportError as exc:
        out.append(CheckResult("operator.declaration", WARNING, False,
                               f"operator_declaration module missing: {exc}"))
        return out

    tenant_id, _ = _resolved_tenant_id()
    result = check_operator_declaration(tenant_id)

    if result.ok:
        if result.declaration_version:
            # eu_production profile with valid declaration
            emit_declaration_audit(result)
            out.append(CheckResult(
                "operator.declaration", INFO, True,
                f"v{result.declaration_version} dpia={result.dpia_date} "
                f"profile={result.profile}",
            ))
        else:
            out.append(CheckResult(
                "operator.declaration", INFO, True,
                f"profile={result.profile!r} — declaration not required",
            ))
    else:
        out.append(CheckResult(
            "operator.declaration", CRITICAL, False,
            result.error[:300],
        ))
    return out


def _emit_compliance_audit(result: "ManifestCheckResult") -> None:  # type: ignore[name-defined]
    """Emit compliance.manifest_check to the L16 hash chain.

    Best-effort — never raises. Detail follows the ADR-0056 allow-list:
    manifest_version, sig_valid, rules_* counts only.
    """
    try:
        from forge.security_events import write_event  # type: ignore

        audit_target = _audit_path()
        audit_target.parent.mkdir(parents=True, exist_ok=True)
        write_event(
            audit_target,
            "compliance.manifest_check",
            severity="WARNING" if not result.ok else "INFO",
            tool="",
            run_id="",
            details=result.audit_dict(),
        )
    except Exception:  # noqa: BLE001
        pass


def _emit_layer_integrity_event(event_type: str, *, severity: str,
                                details: dict) -> None:
    """Best-effort emit of a Layer Integrity event. Never raises."""
    try:
        _ensure_sys_path()
        from forge.security_events import write_event  # type: ignore

        target = _audit_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        write_event(target, event_type, severity=severity, tool="", run_id="",
                    details=details)
    except Exception:
        pass


def _check_layer_integrity() -> list[CheckResult]:
    """ADR-0141 Tier 1 + Tier 3 — Layer Integrity Protocol boot check.

    Tier 3: every mandatory security capability is registered (a deleted /
    tamper-removed layer is CRITICAL).

    Tier 1: the on-disk layer files match the Corvin Labs–signed manifest.
    Severity (rollout synthesis, see ADR-0141):
      - manifest absent (pre-rollout)         -> WARNING
      - manifest present, bad signature       -> CRITICAL
      - manifest valid, layer hash mismatch   -> CRITICAL
      - everything matches                    -> INFO

    Detail policy: counts + status only. No paths, no file content, no mtimes.
    """
    out: list[CheckResult] = []
    _ensure_sys_path()

    # ── Tier 3: capability registry ────────────────────────────────────────
    try:
        import security_capabilities as _sc  # type: ignore
        state = _sc.bootstrap_core_capabilities()
        missing = sorted([k for k, v in state.items() if not v])
        if missing:
            out.append(CheckResult(
                "layer_integrity.capabilities", CRITICAL, False,
                f"{len(missing)} mandatory capability(ies) not registered: {missing}"))
            _emit_layer_integrity_event(
                "security.capability_missing", severity="CRITICAL",
                details={"reason": "boot", "missing": missing})
        else:
            out.append(CheckResult(
                "layer_integrity.capabilities", CRITICAL, True,
                f"{len(state)} mandatory capabilities registered"))
    except Exception as e:  # noqa: BLE001
        out.append(CheckResult(
            "layer_integrity.capabilities", CRITICAL, False,
            f"capability registry unavailable: {type(e).__name__}"))

    # ── Tier 1 substrate presence (fail-closed) ───────────────────────────
    # The verifier (layer_integrity.py) and the Tier-3 registry
    # (security_capabilities.py) are themselves part of the integrity substrate
    # (ADR-0141 residual-risk table). If either file is absent, the manifest pin
    # can never be evaluated — that is indistinguishable from tampering, so it is
    # CRITICAL, not the WARNING that a generic import error would produce.
    _shared_dir = _REPO_ROOT / "operator" / "bridges" / "shared"
    _substrate_missing = [
        n for n in ("layer_integrity.py", "security_capabilities.py")
        if not (_shared_dir / n).is_file()
    ]
    if _substrate_missing:
        out.append(CheckResult(
            "layer_integrity.substrate", CRITICAL, False,
            f"integrity substrate file(s) missing: {_substrate_missing}"))
        _emit_layer_integrity_event(
            "layer_integrity.mismatch", severity="CRITICAL",
            details={"reason": "substrate_missing",
                     "mismatch_count": len(_substrate_missing)})

    # ── Tier 1: signed manifest vs on-disk layer hashes ────────────────────
    try:
        import layer_integrity as _li  # type: ignore
        result = _li.verify_integrity()
        st = _li.IntegrityStatus
        if result.status == st.VERIFIED:
            out.append(CheckResult("layer_integrity.manifest", INFO, True, result.detail))
            _emit_layer_integrity_event(
                "layer_integrity.verified", severity="INFO",
                details={"reason": "boot", "layer_count": len(_li.MANDATORY_LAYER_FILES)})
        elif result.status == st.MANIFEST_ABSENT:
            # Pre-rollout state — advisory, never blocks boot.
            out.append(CheckResult("layer_integrity.manifest", WARNING, False, result.detail))
            _emit_layer_integrity_event(
                "layer_integrity.manifest_absent", severity="WARNING",
                details={"reason": "absent"})
        elif result.status == st.MANIFEST_INVALID:
            out.append(CheckResult("layer_integrity.manifest", CRITICAL, False, result.detail))
            _emit_layer_integrity_event(
                "layer_integrity.manifest_invalid", severity="CRITICAL",
                details={"reason": "bad_signature"})
        else:  # MISMATCH
            out.append(CheckResult("layer_integrity.manifest", CRITICAL, False, result.detail))
            _emit_layer_integrity_event(
                "layer_integrity.mismatch", severity="CRITICAL",
                details={"reason": "layer_hash_mismatch",
                         "mismatch_count": len(result.mismatched)})
    except Exception as e:  # noqa: BLE001
        # The verifier file exists (substrate check above) but won't import/run.
        # layer_integrity imports `cryptography` lazily inside functions, so a
        # module-level import failure means the file is broken or tampered — that
        # is indistinguishable from an attack on the verifier, hence CRITICAL.
        out.append(CheckResult(
            "layer_integrity.manifest", CRITICAL, False,
            f"integrity verifier unavailable: {type(e).__name__}"))
        _emit_layer_integrity_event(
            "layer_integrity.manifest_invalid", severity="CRITICAL",
            details={"reason": "verifier_unavailable"})

    return out


def _check_ota_shards() -> list[CheckResult]:
    """ADR-0154 M6 — Multi-Shard License Identity consistency.

    WARNING-only by design: a shard divergence on a clean free-tier install must
    never block boot (CLAUDE.md: "Don't gate Apache-core single-node"). Read-only
    and best-effort; surfaces in ``doctor`` and points the operator at
    ``corvin-license-debug`` for the unified diagnosis.
    """
    out: list[CheckResult] = []
    _ensure_sys_path()
    try:
        from license.shard_verifier import verify_shards, OK  # type: ignore
    except Exception as e:  # noqa: BLE001
        out.append(CheckResult(
            "license.ota_shards", INFO, True,
            f"OTA shard verifier unavailable ({type(e).__name__}) — skipped",
        ))
        return out
    try:
        report = verify_shards()
        agg = report.get("aggregate")
        if agg == OK:
            out.append(CheckResult(
                "license.ota_shards", INFO, True,
                f"MSLI shards consistent (tier={report.get('tier', '?')})",
            ))
        else:
            bad = [
                f"{s['shard']}={s['status']}"
                for s in report.get("shards", []) if s.get("status") != OK
            ]
            # Never CRITICAL — even a FAIL aggregate stays WARNING at boot.
            out.append(CheckResult(
                "license.ota_shards", WARNING, False,
                "MSLI shard divergence: " + ", ".join(bad)
                + " — run 'corvin-license-debug'",
            ))
    except Exception as e:  # noqa: BLE001
        out.append(CheckResult(
            "license.ota_shards", WARNING, False,
            f"OTA shard check errored: {type(e).__name__}",
        ))
    return out


# ── ADR-0159 M2 — SandboxProvider check ────────────────────────────────────


def _check_sandbox_provider() -> list[CheckResult]:
    """ADR-0159 M2 — verify Forge sandbox tier at boot.

    WARNING when ``none`` tier is active (reduced isolation).
    CRITICAL when ``none`` tier is active AND tenant has
    ``forge.require_sandbox: true`` (operator opted in to hard-require sandbox).
    INFO when bwrap or docker is available.

    No path or process detail in audit — counts and tier name only.
    """
    out: list[CheckResult] = []
    _ensure_sys_path()
    try:
        import sys as _st_sys
        _forge_path = str(_REPO_ROOT / "operator" / "forge")
        if _forge_path not in _st_sys.path:
            _st_sys.path.insert(0, _forge_path)
        from forge.sandbox_provider import detect_sandbox_tier, SandboxTier  # type: ignore
        tier = detect_sandbox_tier()
        if tier == SandboxTier.NONE:
            # Check if the tenant requires sandbox
            require_sandbox = False
            try:
                cfg = _load_tenant_config_for_self_test()
                if cfg and isinstance(cfg, dict):
                    forge_cfg = (cfg.get("spec") or {}).get("forge") or {}
                    require_sandbox = bool(forge_cfg.get("require_sandbox"))
            except Exception:  # noqa: BLE001
                pass
            sev = CRITICAL if require_sandbox else WARNING
            out.append(CheckResult(
                "forge.sandbox_tier", sev, False,
                "sandbox=none — bwrap and docker both unavailable. "
                "L10 path-gate still active; filesystem namespacing is NOT in effect. "
                + ("CRITICAL: tenant requires sandbox (forge.require_sandbox=true)."
                   if require_sandbox else
                   "Install bwrap (Linux) or docker, or set CORVIN_SANDBOX=none to suppress."),
            ))
        else:
            out.append(CheckResult("forge.sandbox_tier", INFO, True,
                                   f"sandbox={tier.value}"))
    except ImportError:
        out.append(CheckResult("forge.sandbox_tier", INFO, True,
                               "sandbox_provider not available — using direct bwrap (pre-M2)"))
    except Exception as exc:  # noqa: BLE001
        out.append(CheckResult("forge.sandbox_tier", WARNING, False,
                               f"sandbox probe failed: {type(exc).__name__}: {exc}"))
    return out


# ── Orchestration ──────────────────────────────────────────────────────────


def run_self_test(*, quick: bool = False) -> SelfTestResult:
    """Run every check group; emit a single audit event with the verdict.

    stdout is redirected to stderr for the duration of the checks so any
    debug logging emitted by lazily-imported submodules cannot pollute a
    caller that pipes our JSON output downstream (e.g. ``bridge.sh doctor
    --json | jq``).
    """
    checks: list[CheckResult] = []
    # Order matters only for human-readable output. Tenant first so subsequent
    # checks can reuse the resolved tenant id without races.
    _saved_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        checks.extend(_check_tenant_tree())
        checks.extend(_check_memory())
        checks.extend(_check_audit_chain(quick=quick))
        checks.extend(_check_nbac_genesis())
        checks.extend(_check_vault())
        checks.extend(_check_engines(quick=quick))
        checks.extend(_check_hermes_ollama())
        checks.extend(_check_copilot_cli())
        checks.extend(_check_mcp_servers(quick=quick))
        checks.extend(_check_artifacts(quick=quick))
        checks.extend(_check_license(quick=quick))
        checks.extend(_check_ota_shards())
        checks.extend(_check_clag(quick=quick))
        checks.extend(_check_chain_anchor(quick=quick))
        checks.extend(_check_instance_seed())
        checks.extend(_check_instance_key())
        checks.extend(_check_egress_preset())
        checks.extend(_check_audit_sealer())
        checks.extend(_check_social_keypair())
        checks.extend(_check_a2a_key_files())
        checks.extend(_check_a2a_network_membership())
        checks.extend(_check_layer_integrity())
        checks.extend(_check_agent_heartbeat(quick=quick))
        checks.extend(_check_operator_declaration())
        checks.extend(_check_acs_runtime())
        checks.extend(_check_compliance_manifest())
        checks.extend(_check_sandbox_provider())
    finally:
        sys.stdout = _saved_stdout
    result = SelfTestResult(checks=checks)
    _emit_audit(result, quick=quick)
    return result


def _emit_audit(result: SelfTestResult, *, quick: bool = False) -> None:
    """Best-effort audit emit. Never raises — observability is non-load-bearing.

    Detail policy: only check names + counts. No paths, no error strings, no
    user-visible content (which is already in the local log).
    """
    try:
        _ensure_sys_path()
        from forge.security_events import write_event  # type: ignore

        audit_target = _audit_path()
        audit_target.parent.mkdir(parents=True, exist_ok=True)
        details = {
            "total_checks": len(result.checks),
            "critical_failures": [c.name for c in result.critical_failures],
            "warnings": [c.name for c in result.warnings],
            "quick": quick,
        }
        if result.critical_failures:
            write_event(audit_target, "boot.self_test_failed",
                        severity="CRITICAL", tool="", run_id="",
                        details=details)
        else:
            write_event(audit_target, "boot.self_test_passed",
                        severity="INFO", tool="", run_id="",
                        details=details)
    except Exception:
        pass


# ── Human-readable output ──────────────────────────────────────────────────


def _format_check(c: CheckResult) -> str:
    icon = "OK " if c.ok else ("CRIT" if c.severity == CRITICAL else
                               "WARN" if c.severity == WARNING else "info")
    return f"  [{icon}] {c.name:<38} {c.detail}"


def format_human(result: SelfTestResult) -> str:
    lines = ["Corvin self-test"]
    lines.extend(_format_check(c) for c in result.checks)
    lines.append("")
    if result.all_green:
        lines.append("verdict: all green")
    elif result.ok:
        lines.append(f"verdict: ok ({len(result.warnings)} warning(s); no CRITICAL failures)")
    else:
        lines.append(
            f"verdict: FAIL ({len(result.critical_failures)} CRITICAL, "
            f"{len(result.warnings)} warning(s))")
        for c in result.critical_failures:
            lines.append(f"  → {c.name}: {c.detail}")
    return "\n".join(lines)


# ── CLI ────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(
        prog="self_test",
        description="Corvin boot-time self-test.")
    p.add_argument("--quick", action="store_true",
                   help="Skip slow probes (audit verify, MCP spawn, license).")
    p.add_argument("--strict", action="store_true",
                   help="Exit non-zero on any warning (default: only on CRITICAL).")
    p.add_argument("--json", action="store_true",
                   help="Emit machine-readable JSON on stdout instead of text.")
    args = p.parse_args(argv)

    result = run_self_test(quick=args.quick)
    if args.json:
        sys.stdout.write(json.dumps(result.to_dict(), indent=2) + "\n")
    else:
        sys.stdout.write(format_human(result) + "\n")

    if not result.ok:
        return 1
    if args.strict and not result.all_green:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
