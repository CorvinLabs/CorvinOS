"""Hardened runner.

Pipeline per call:
  1. Look up the spec; refuse if the on-disk impl no longer matches the
     manifest's recorded sha256 (tamper detection).
  2. Validate the payload with the recorded JSONSchema. Prefer the
     ``jsonschema`` library; fall back to a hand-rolled subset if missing.
  3. Resolve permissions (first-call consent, sha-drift re-prompt).
  4. Execute the impl in a subprocess with:
        - stripped env
        - POSIX rlimits (CPU, address space, file size, NOFILE, no core)
        - bubblewrap jail when available (no network, ro system, fresh /tmp)
        - hard wall-clock timeout
        - bounded stdout/stderr capture (oversized output → truncated + flag)
  5. Bump the call counter and return a structured result.
"""
from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import cache as _cache
from . import secret_vault as _secret_vault
from .permissions import Mode, PermissionStore, decide
from .policy import Budget, Policy
from .registry import Registry, ToolSpec
from .runs import RunContext, begin_run, end_run
from .sandbox import (
    Limits,
    apply_rlimits,
    build_bwrap_cmd,
    ensure_requirements,
    have_bwrap,
    stripped_env,
)


class SchemaError(ValueError):
    pass


class ToolError(RuntimeError):
    pass


class PermissionDenied(ToolError):
    pass


class TamperError(ToolError):
    pass


class SecretACLDenied(ToolError):
    """Raised when the caller persona is not on the allow-list for one or
    more keys the tool declares in meta.secrets."""


class SecretMissing(ToolError):
    """Raised when the tool declares a secret in meta.secrets but the
    vault either doesn't exist or doesn't contain the key. Fail-closed:
    we never run a tool that would silently see an absent env var when
    it was supposed to receive a real secret — silent failure is the
    failure mode that produces the worst debugging experience."""


class BwrapUnavailable(ToolError):
    """Raised when bwrap is non-functional and a tool with declared secrets
    attempts to run. ADR-0052 F8: tools with secrets MUST run in the sandbox.
    No fallback to non-sandboxed execution is permitted."""


# ADR-0052 F8 — bwrap pre-flight probe helper.
# Cached per-process: bwrap availability doesn't change during a run, and
# the probe adds ~100ms latency — we don't want that per tool call.
_bwrap_preflight_cache: tuple[bool, str] | None = None


def _bwrap_preflight_check() -> tuple[bool, str]:
    """Run a 100ms bwrap health-probe. Returns (ok, error_message).

    Cached after first call. Result is (True, '') when bwrap is healthy.
    """
    global _bwrap_preflight_cache
    if _bwrap_preflight_cache is not None:
        return _bwrap_preflight_cache

    try:
        import shutil as _shutil
        bwrap_bin = _shutil.which("bwrap")
        if not bwrap_bin:
            _bwrap_preflight_cache = (False, "bwrap binary not found in PATH")
            return _bwrap_preflight_cache

        probe = subprocess.run(
            [bwrap_bin, "--ro-bind", "/", "/", "--dev", "/dev", "/bin/true"],
            capture_output=True,
            timeout=2.0,
        )
        if probe.returncode != 0:
            err = (probe.stderr or b"").decode("utf-8", errors="replace")[:200]
            _bwrap_preflight_cache = (False, f"bwrap probe returned {probe.returncode}: {err}")
            return _bwrap_preflight_cache

        _bwrap_preflight_cache = (True, "")
        return _bwrap_preflight_cache
    except Exception as exc:
        _bwrap_preflight_cache = (False, f"bwrap probe exception: {type(exc).__name__}: {exc}")
        return _bwrap_preflight_cache


# -- schema validation -------------------------------------------------------

try:
    import jsonschema
    _HAVE_JSONSCHEMA = True
except ImportError:
    _HAVE_JSONSCHEMA = False


def _validate(payload: dict[str, Any], schema: dict[str, Any]) -> None:
    if _HAVE_JSONSCHEMA:
        try:
            jsonschema.validate(payload, schema)
            return
        except jsonschema.ValidationError as e:
            # Make the message look like our hand-rolled one so callers can
            # match on the error text uniformly.
            path = ".".join(str(p) for p in e.absolute_path) or "<root>"
            raise SchemaError(f"{path}: {e.message}") from e

    # Fallback: subset validator (top-level required, type, enum).
    if schema.get("type") != "object":
        return
    props = schema.get("properties", {})
    for key in schema.get("required", []):
        if key not in payload:
            raise SchemaError(f"missing required field {key!r}")
    type_map = {
        "string": str, "integer": int, "number": (int, float),
        "boolean": bool, "array": list, "object": dict,
    }
    for key, val in payload.items():
        if key not in props:
            continue
        t = props[key].get("type")
        if t and not isinstance(val, type_map.get(t, object)):
            raise SchemaError(
                f"field {key!r} expected {t}, got {type(val).__name__}"
            )
        enum = props[key].get("enum")
        if enum is not None and val not in enum:
            raise SchemaError(f"field {key!r}: {val!r} not in {enum}")


# -- result type -------------------------------------------------------------

@dataclass
class RunResult:
    ok: bool
    data: Any                                  # the unwrapped `data` field
    stdout_truncated: bool
    stderr: str
    exit_code: int
    duration_s: float
    sandbox: str                               # "bwrap" | "rlimits" | "none"
    run_id: str = ""
    artifacts: list[dict[str, Any]] = None     # type: ignore[assignment]
    envelope: dict[str, Any] = None            # type: ignore[assignment]

    def __post_init__(self):
        if self.artifacts is None:
            self.artifacts = []
        if self.envelope is None:
            self.envelope = {}

    @property
    def meta(self) -> dict[str, Any]:
        return self.envelope.get("meta") or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "data": self.data,
            "stdout_truncated": self.stdout_truncated,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "duration_s": round(self.duration_s, 4),
            "sandbox": self.sandbox,
            "run_id": self.run_id,
            "artifacts": self.artifacts,
            "envelope": self.envelope,
        }


_ENVELOPE_KEYS = {"ok", "status", "data", "error"}


def _redact_in_struct(node: Any, values: list[str]) -> Any:
    """Walk *node* (dict/list/scalar) and redact any ``str`` leaves that
    contain a literal occurrence of any ``values`` element. Returns a
    new structure — input is not mutated. Bounded recursion depth (32)
    so a self-referential payload can't blow the stack."""
    return _redact_walk(node, values, depth=0)


def _redact_walk(node: Any, values: list[str], depth: int) -> Any:
    if depth > 32:
        return node
    if isinstance(node, str):
        return _secret_vault.redact_values(node, values)
    if isinstance(node, dict):
        return {k: _redact_walk(v, values, depth + 1)
                for k, v in node.items()}
    if isinstance(node, list):
        return [_redact_walk(v, values, depth + 1) for v in node]
    return node


def _wrap_envelope(raw: Any) -> dict[str, Any]:
    """Auto-wrap a tool's stdout JSON into the standard AWP envelope.

    A tool can either:
      A) print the full envelope itself ({ok, status, data, error, meta})
         — we pass it through (with meta defaulted)
      B) print any other JSON — we wrap it as {ok: true, status: 200,
         data: <raw>, error: null, meta: {}}
    """
    if isinstance(raw, dict) and _ENVELOPE_KEYS.issubset(raw.keys()):
        env = dict(raw)
        env.setdefault("meta", {})
        return env
    return {
        "ok": True,
        "status": 200,
        "data": raw,
        "error": None,
        "meta": {},
    }


# -- main entry point --------------------------------------------------------

DEFAULT_LIMITS = Limits()
DEFAULT_OUTPUT_CAP = 4 * 1024 * 1024   # 4 MiB max captured stdout


def _audit_path_for(ctx: RunContext) -> Path:
    """Resolve the unified audit-chain path for a given run context.

    Mirrors the inline lookup the network_share path uses: prefer
    ``ctx.audit_path`` when present (newer ctx), else fall back to the
    canonical ``<corvin_home>/global/forge/audit.jsonl``.
    """
    p = getattr(ctx, "audit_path", None)
    if p is not None:
        return Path(p)
    from .paths import corvin_home  # type: ignore
    return corvin_home() / "global" / "forge" / "audit.jsonl"


def _emit_tool_executed(ctx: RunContext, *, status: str, exit_code: int,
                        duration_s: float, sandbox: str,
                        cache_hit: bool = False) -> None:
    """Emit a hash-chain ``forge.tool_executed`` event for EVERY tool run
    (success / non-zero-exit / timeout). Audit gap closure: the normal run
    path previously left no tamper-evident chain entry — only conditional
    events (network_share/secrets_injected/denials) fired. Metadata ONLY:
    tool name, run id, outcome, timing, sandbox — never payload or output.
    """
    try:
        from .security_events import write_event as _swe
        _swe(
            _audit_path_for(ctx), "forge.tool_executed",
            severity="INFO", tool=ctx.tool_name, run_id=ctx.id,
            details={"status": status, "exit_code": int(exit_code),
                     "duration_ms": int(duration_s * 1000),
                     "sandbox": sandbox, "cache_hit": bool(cache_hit)},
        )
    except Exception:  # noqa: BLE001 — observability is best-effort
        pass


def _check_tamper(spec: ToolSpec) -> None:
    impl = Path(spec.impl_path)
    if not impl.exists():
        raise ToolError(f"impl missing: {impl}")
    actual = hashlib.sha256(impl.read_text().encode()).hexdigest()[:16]
    if actual != spec.sha256:
        raise TamperError(
            f"sha256 mismatch for {spec.name}: "
            f"manifest={spec.sha256} disk={actual} — refusing to run"
        )


# Protected file basenames that may never be bind-mounted into a tool sandbox.
_BIND_PROTECTED_NAMES = (
    "audit.jsonl", "policy.json", "secrets.json",
    "audit_anchor.key", "audit_mac_active",
)


def _guard_bind_target(val: str, target: Path, *, writable: bool) -> None:
    """Confinement guard for x-bind paths (FND-21 + R2-11 + R2-12).

    Both ro and rw binds are confined. The ro branch previously had ZERO
    confinement — any existing host path was bound read-only, giving the
    untrusted tool an arbitrary host-file READ primitive (/etc/shadow,
    ~/.config/corvin-voice/secrets.json, the audit anchor key, …). Both
    directions now reject protected files, the forge/skill-forge workspaces,
    the voice secret-config tree, and any absolute path outside /tmp. A rw bind
    additionally rejects non-regular special files (R2-12): a host /tmp UNIX
    socket (Postgres/X11) bound rw would let a netns-isolated tool reach a host
    service, defeating the network isolation.

    Raises ValueError on any rejected path. ``target`` must already be
    ``Path(val).resolve()``-d by the caller.
    """
    import stat as _stat
    mode = "rw" if writable else "ro"
    _parts = str(target)
    if any(_parts.endswith(n) or ("/" + n) in _parts for n in _BIND_PROTECTED_NAMES):
        raise ValueError(
            f"x-bind:{mode} path {val!r} targets a protected file "
            f"(audit/policy/secret/anchor) and cannot be bind-mounted."
        )
    # R1 finding: the basename denylist misses a secret vault relocated via
    # CORVIN_SECRET_VAULT to a non-standard name/path (e.g. under /tmp, which
    # then passes the /tmp confinement). Reject the DYNAMICALLY-resolved vault
    # path so the vault is protected regardless of where the env points it.
    try:
        from .secret_vault import default_vault_path as _dvp  # type: ignore
        if target == _dvp().resolve():
            raise ValueError(
                f"x-bind:{mode} path {val!r} is the secret vault and cannot be "
                "bind-mounted."
            )
    except ImportError:
        pass
    try:
        from .paths import corvin_home as _corvin_home_rb  # type: ignore
        _ch = _corvin_home_rb()
        _ws_roots = [_ch / "global" / "forge", _ch / "global" / "skill-forge"]
    except ImportError:
        _ws_roots = []
    _ws_roots.append(Path(os.path.expanduser("~/.config/corvin-voice")))
    for _ws_root in _ws_roots:
        try:
            target.relative_to(_ws_root)
        except ValueError:
            continue  # NOT a subpath → safe
        raise ValueError(
            f"x-bind:{mode} path {val!r} is under a protected tree "
            f"({_ws_root}) and cannot be bind-mounted."
        )
    # Confine to /tmp (parts[1]=="tmp" avoids a /tmp.evil prefix bypass).
    _under_tmp = (
        not target.is_absolute()
        or (len(target.parts) > 1 and target.parts[1] == "tmp")
    )
    if target.is_absolute() and not _under_tmp:
        raise ValueError(
            f"x-bind:{mode} path {val!r} resolves outside /tmp ({target}). "
            "Only paths under /tmp may be bind-mounted."
        )
    # R3-6: reject non-regular special files for BOTH ro and rw binds. A
    # rw-bound socket defeats netns isolation; a RO-bound host /tmp UNIX socket
    # (Postgres/X11) is just as exploitable — the tool can connect() to it and
    # reach a host service regardless of the bind being read-only. Only regular
    # files (or a not-yet-existing file we will create) may be bind-mounted.
    if target.exists():
        _st = target.lstat()
        if not _stat.S_ISREG(_st.st_mode):
            raise ValueError(
                f"x-bind:{mode} path {val!r} is not a regular file "
                f"(mode={_stat.S_IFMT(_st.st_mode):#o}); sockets, FIFOs, and "
                "devices cannot be bind-mounted."
            )


def run_tool(
    registry: Registry,
    name: str,
    payload: dict[str, Any],
    *,
    timeout: float | None = None,
    permission_mode: Mode = "ask",
    output_cap: int | None = None,
    limits: Limits | None = None,
    use_sandbox: bool = True,
    extra_ro_paths: list[Path] | None = None,
    extra_rw_paths: list[Path] | None = None,
    policy: Policy | None = None,
    caller_persona: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> RunResult:
    spec = registry.get(name)
    if spec is None:
        raise ToolError(f"unknown tool {name!r}")

    _check_tamper(spec)
    _validate(payload, spec.input_schema)

    # Resolve the per-call budget: spec.meta.budget (if any) → policy-clamped
    # → applied as rlimits + timeout + output_cap. If no policy is supplied,
    # we still build a policy with strict built-in defaults so the budget
    # path always runs (no special-case branching for the unconfigured case).
    effective_policy = policy or Policy()
    requested_budget = spec.meta.get("budget") if isinstance(spec.meta, dict) else None
    applied_budget, budget_clamp_info = effective_policy.clamp_budget(requested_budget)
    # Caller-supplied limits/timeout/output_cap still beat the budget if they
    # are set explicitly — the budget is the *default* for the spec.
    if limits is None:
        limits = Limits(
            cpu_seconds=applied_budget.cpu_seconds,
            address_space_mb=DEFAULT_LIMITS.address_space_mb,
            file_size_mb=max(1, applied_budget.artifact_bytes // (1024 * 1024)),
            open_files=DEFAULT_LIMITS.open_files,
            core_size=DEFAULT_LIMITS.core_size,
        )
    if timeout is None:
        timeout = float(applied_budget.wall_seconds)
    if output_cap is None:
        output_cap = applied_budget.output_bytes

    # Permission gate
    impl_path = Path(spec.impl_path)
    impl_text = impl_path.read_text()
    perms = PermissionStore(registry.root)
    decision = decide(
        store=perms,
        name=spec.name,
        sha=spec.sha256,
        impl_text=impl_text,
        mode=permission_mode,
    )
    if not decision.approved:
        raise PermissionDenied(f"{spec.name}: {decision.reason}")

    # Allocate the run workspace BEFORE we exec, so manifest is on disk
    # even if the run crashes. The augment hook injects _artifacts_dir into
    # the payload before the manifest is written, so the manifest reflects
    # exactly what the tool will receive on stdin. The redact hook strips
    # x-redact: true fields from the manifest copy only — the tool still
    # receives the real value, and the cache key is computed from the real
    # payload so identical real inputs still cache-hit.
    def _inject_artifacts(p: dict, adir: Path) -> dict:
        out = dict(p)
        out.setdefault("_artifacts_dir", str(adir))
        return out

    def _redact_secrets(p: dict) -> dict:
        out = dict(p)
        props = spec.input_schema.get("properties", {})
        for key, prop in props.items():
            if isinstance(prop, dict) and prop.get("x-redact") is True \
               and key in out:
                out[key] = "<redacted>"
        return out

    ctx = begin_run(
        registry.root,
        tool_name=spec.name,
        tool_sha=spec.sha256,
        input_payload=payload,
        augment=_inject_artifacts,
        redact=_redact_secrets,
    )
    payload = ctx.payload

    # Determinism cache: if the tool's spec.meta says deterministic, look up
    # any prior run with matching (tool_sha, payload-without-artifacts_dir,
    # python-version). On hit, replay the envelope without execing anything.
    cache_hit_key: str | None = None
    if spec.meta.get("deterministic") is True:
        cache_hit_key = _cache.cache_key(
            tool_sha=spec.sha256, payload=payload
        )
        cached = _cache.lookup(registry.root, cache_hit_key)
        if cached is not None:
            envelope = dict(cached.get("envelope") or {})
            replay_meta = dict(envelope.get("meta") or {})
            replay_meta["replayed_from"] = cached.get("run_id")
            envelope["meta"] = replay_meta
            registry.bump_call(name)
            completion = end_run(
                ctx,
                status="replayed",
                exit_code=0,
                duration_s=0.0,
                sandbox="cache",
                stdout_data=envelope,
                stderr_text="",
                summary=f"replayed from {cached.get('run_id')}",
            )
            _emit_tool_executed(ctx, status="replayed", exit_code=0,
                                duration_s=0.0, sandbox="cache", cache_hit=True)
            return RunResult(
                ok=True,
                data=envelope.get("data"),
                stdout_truncated=False,
                stderr="",
                exit_code=0,
                duration_s=0.0,
                sandbox="cache",
                run_id=ctx.id,
                artifacts=completion.get("artifacts", []),
                envelope=envelope,
            )

    # Build inner cmd
    if spec.runtime == "python":
        inner = [sys.executable, str(impl_path)]
    elif spec.runtime == "bash":
        inner = ["/bin/bash", str(impl_path)]
    else:
        raise ToolError(f"unsupported runtime: {spec.runtime}")

    # Resolve per-tool requirements (meta.requirements) into a cached
    # --target dir.  The dir is bound read-only into the sandbox and
    # prepended to PYTHONPATH so the tool can import the packages without
    # them being installed in the host venv.  Fail-soft: a pip error is
    # printed to stderr but does not abort tool execution — the tool will
    # raise an ImportError if the package is genuinely missing.
    req_site_dir: Path | None = None
    if spec.runtime == "python" and isinstance(spec.meta, dict):
        reqs = spec.meta.get("requirements")
        if reqs and isinstance(reqs, list):
            reqs = [r for r in reqs if isinstance(r, str) and r.strip()]
            if reqs:
                try:
                    from .paths import corvin_home as _corvin_home
                    _cache_root = _corvin_home() / "global" / "forge"
                    req_site_dir = ensure_requirements(reqs, _cache_root)
                except Exception as _exc:
                    print(
                        f"[forge] requirements resolve error for {name!r}: {_exc}",
                        file=sys.stderr,
                    )

    # Wrap in bwrap if available and requested
    sandbox_label = "rlimits"
    if use_sandbox and have_bwrap():
        ro_extras = list(extra_ro_paths or [])
        rw_extras = list(extra_rw_paths or [])
        # Always rw-bind this run's artifacts dir so the tool can write into it.
        rw_extras.append(ctx.artifacts_dir)
        # If python lives outside /usr (e.g. a venv), expose it read-only.
        # IMPORTANT: do NOT resolve symlinks before the prefix check —
        # a venv's `bin/python` is typically a symlink to /usr/bin/python3,
        # but the venv ROOT (containing site-packages + bin/) is what
        # the inner cmd needs in the sandbox. Resolving first would bind
        # /usr and miss the venv tree, breaking spawns that use
        # sys.executable from the venv.
        if spec.runtime == "python":
            py_exec = Path(sys.executable)
            venv_root = py_exec.parent.parent
            if (py_exec.parent.name == "bin"
                    and not str(venv_root).startswith("/usr")):
                ro_extras.append(venv_root)
            # A uv-managed venv symlinks bin/python3 to an interpreter that
            # lives OUTSIDE the venv tree, THROUGH SEVERAL HOPS — e.g.
            #   .venv/bin/python3 → python
            #   .venv/bin/python  → ~/.local/share/uv/python/cpython-3.11-…/bin/python3.11
            #   …/cpython-3.11-…  → …/cpython-3.11.15-…            (dir symlink)
            # Binding only venv_root (or even only the fully-resolved prefix)
            # leaves an intermediate hop dangling inside the jail ("bwrap:
            # execvp .../python3: No such file"), which silently breaks EVERY
            # forged tool on a uv install — the DEFAULT installer path. Bind the
            # interpreter version STORE (the parent that holds both the
            # short-name symlink dir and the real cpython-X.Y.Z dir) read-only,
            # so every hop resolves. Guarded so we never bind a system-wide or
            # home-root directory. A classic `python -m venv` whose bin/python
            # points at /usr is unaffected (real_exec is under /usr → skipped).
            real_exec = py_exec.resolve()
            if (not str(real_exec).startswith("/usr")
                    and not real_exec.is_relative_to(venv_root)):
                store = real_exec.parent.parent.parent  # …/uv/python
                # Only bind a sufficiently-specific store dir — never '/',
                # '/home', the user's home root, or a two-level path.
                if (len(store.parts) >= 4
                        and store != Path.home()
                        and str(store) not in ("/", "/usr", "/home", "/opt")):
                    ro_extras.append(store)
                else:
                    # Fallback: the resolved interpreter's own prefix only.
                    ro_extras.append(real_exec.parent.parent)
        # Bind the per-tool requirements target dir (if any) read-only.
        if req_site_dir is not None:
            ro_extras.append(req_site_dir)
        # Bind any input file paths the schema marked with x-bind.
        # ro: must exist, exposed read-only.
        # rw: if the file exists it's bound rw; otherwise the parent dir is
        #     bound rw so the tool can create the file. Be deliberate when
        #     you mark a field rw — anything reachable through that dir is
        #     reachable to the tool.
        for key, val in payload.items():
            prop = spec.input_schema.get("properties", {}).get(key, {})
            bind = prop.get("x-bind")
            if not isinstance(val, str):
                continue
            if bind == "ro":
                p = Path(val).resolve()
                # R2-11: confine ro binds just like rw — no arbitrary host read.
                _guard_bind_target(val, p, writable=False)
                if p.exists():
                    ro_extras.append(p)
            elif bind == "rw":
                p = Path(val).resolve()
                # FND-21: bind the resolved FILE itself, never its parent dir.
                # The old `target = p if p.exists() else p.parent` rw-bound the
                # WHOLE parent directory when the file did not exist yet, and
                # resolve-then-bind left a TOCTOU window on shared /tmp. Run all
                # guards on `p`, then create `p` (0600) only after it passes, so
                # bwrap binds exactly that one file.
                target = p
                _guard_bind_target(val, target, writable=True)
                # All guards passed — ensure the FILE exists at 0600 so bwrap
                # binds exactly it (never the parent dir).
                try:
                    if not target.exists():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.touch(mode=0o600, exist_ok=True)
                except OSError:
                    continue
                rw_extras.append(target)
        # Persona-aware sandbox. Trust order:
        #   1. Explicit kwarg ``caller_persona`` (set by the MCP server from
        #      ``self.forge_persona``, which is fixed at MCP-startup-time and
        #      cannot be tampered with at runtime).
        #   2. ``FORGE_PERSONA`` env var (covers direct CLI invocations of
        #      run_tool — operator-driven, env-trust is acceptable there).
        # Personas not listed in policy.persona_sandbox_overrides keep the
        # strict default (no network). Default deny is the safe fallback.
        effective_persona = (caller_persona
                             if caller_persona is not None
                             else (os.environ.get("FORGE_PERSONA") or ""))
        allow_network = effective_policy.network_for_persona(effective_persona)
        # FND-13: the loopback-deny shim (sitecustomize) and forbidden_imports
        # are PYTHON-ONLY. A bash tool granted network would have unrestricted
        # host-net access — reaching host loopback services (Postgres/Redis) and
        # link-local cloud IMDS — bypassing both guards. Deny network to bash
        # tools so they run netns-isolated (only their own loopback). Convert a
        # tool to runtime=python to get network + the loopback shim.
        if allow_network and spec.runtime == "bash":
            allow_network = False
            try:
                import logging as _lg
                _lg.getLogger("corvin.forge").warning(
                    "forge tool %r: runtime=bash + network denied (bash bypasses "
                    "the python-only loopback/import sandbox) — running "
                    "netns-isolated", getattr(spec, "name", "?"),
                )
            except Exception:  # noqa: BLE001
                pass
        deny_loopback = effective_policy.deny_loopback_for_persona(effective_persona)
        cmd = build_bwrap_cmd(
            inner,
            impl_path,
            extra_ro_binds=ro_extras,
            extra_rw_binds=rw_extras,
            allow_network=allow_network,
            deny_loopback=deny_loopback,
            extra_pythonpath=[req_site_dir] if req_site_dir else None,
        )
        if allow_network:
            sandbox_label = "bwrap+net-noloop" if deny_loopback else "bwrap+net"
        else:
            sandbox_label = "bwrap"
        # Layer-16 v2 — visibility for network-allowing tool runs. The
        # sandbox_label flips between bwrap+net (loopback reachable) and
        # bwrap+net-noloop (loopback denied via sitecustomize hook) so the
        # audit chain reflects the policy intent for every browser /
        # research run.
        if allow_network:
            try:
                from .security_events import write_event as _swe  # type: ignore
                _audit_path = ctx.audit_path if hasattr(ctx, "audit_path") else None
                if _audit_path is None:
                    from .paths import corvin_home  # type: ignore
                    _audit_path = corvin_home() / "global" / "forge" / "audit.jsonl"
                _swe(
                    _audit_path, "tool.network_share",
                    severity="INFO", tool=name, run_id=ctx.id,
                    details={"persona": effective_persona,
                             "sandbox": sandbox_label,
                             "deny_loopback": deny_loopback},
                )
            except Exception:
                pass
    else:
        cmd = inner

    env = stripped_env()
    # Non-bwrap path: inject requirements dir into PYTHONPATH via env dict.
    # The bwrap path uses --setenv inside build_bwrap_cmd instead.
    if req_site_dir is not None and not (use_sandbox and have_bwrap()):
        env = dict(env)
        env["PYTHONPATH"] = str(req_site_dir)

    # Layer-16 v3 — Secret-Injection. Resolve meta.secrets through the vault
    # and the persona allow-list, then merge the values into env. Values
    # never touch the spec on disk, the payload, the cache key, or the
    # audit details — only the *names* of secrets actually injected go
    # into the audit event. Best-effort secret-value redaction in stdout/
    # stderr below catches accidental ``print(env)`` leaks from the tool.
    declared_secrets: list[str] = []
    if isinstance(spec.meta, dict):
        raw = spec.meta.get("secrets")
        if isinstance(raw, list):
            # Defensive: spec already validated at create-time, but a
            # registry that was hand-edited could still hold garbage.
            declared_secrets = [
                k for k in raw
                if isinstance(k, str) and _secret_vault.is_valid_key(k)
            ]
    secret_values: list[str] = []
    if declared_secrets:
        # Persona ACL — fail-closed, persona without an allow-list entry
        # gets nothing. The persona is the same effective_persona the
        # sandbox-network gate uses; we resolve it once here for both.
        secret_persona = (caller_persona
                          if caller_persona is not None
                          else (os.environ.get("FORGE_PERSONA") or ""))
        ok, denied = effective_policy.secret_check(
            secret_persona, declared_secrets,
        )
        if not ok:
            try:
                from .security_events import write_event as _swe
                _audit_path = _audit_path_for(ctx)
                _swe(
                    _audit_path, "acl.persona_secret_denied",
                    severity="WARNING", tool=name, run_id=ctx.id,
                    details={"persona": secret_persona,
                             "denied": denied,
                             "declared": declared_secrets},
                )
            except Exception:
                pass
            end_run(
                ctx, status="error", exit_code=-1,
                duration_s=0.0, sandbox="none",
                stdout_data=None, stderr_text="",
                summary=f"acl.persona_secret_denied: {denied}",
            )
            raise SecretACLDenied(
                f"persona {secret_persona!r} is not permitted to use "
                f"secret(s) {denied!r} (allow-list: "
                f"{effective_policy.secrets_for_persona(secret_persona)!r}). "
                f"Add the keys to policy.json under "
                f"persona_secret_allow.{secret_persona} to authorise."
            )

        # Vault lookup. Missing vault file → vault returns {} and
        # resolve_secrets reports every requested key as missing.
        try:
            resolved, missing = _secret_vault.resolve_secrets(
                declared_secrets,
            )
        except _secret_vault.VaultError as exc:
            try:
                from .security_events import write_event as _swe
                _audit_path = _audit_path_for(ctx)
                _swe(
                    _audit_path, "secret.vault_malformed",
                    severity="ERROR", tool=name, run_id=ctx.id,
                    details={"error": str(exc)[:300]},
                )
            except Exception:
                pass
            end_run(
                ctx, status="error", exit_code=-1,
                duration_s=0.0, sandbox="none",
                stdout_data=None, stderr_text="",
                summary=f"secret.vault_malformed: {exc}",
            )
            raise SecretMissing(f"vault unreadable: {exc}") from exc

        if missing:
            try:
                from .security_events import write_event as _swe
                _audit_path = _audit_path_for(ctx)
                _swe(
                    _audit_path, "secret.vault_missing",
                    severity="WARNING", tool=name, run_id=ctx.id,
                    details={"persona": secret_persona,
                             "missing": missing,
                             "vault_path": str(
                                 _secret_vault.default_vault_path()
                             )},
                )
            except Exception:
                pass
            end_run(
                ctx, status="error", exit_code=-1,
                duration_s=0.0, sandbox="none",
                stdout_data=None, stderr_text="",
                summary=f"secret.vault_missing: {missing}",
            )
            raise SecretMissing(
                f"tool {name!r} requires secret(s) {missing!r} but the "
                f"vault at {_secret_vault.default_vault_path()} does not "
                f"contain them. Add the keys to the vault (mode 0600 JSON) "
                f"or remove them from meta.secrets."
            )

        # ADR-0052 F8 — bwrap pre-flight probe before injecting secrets.
        # If bwrap is unavailable/broken we must never fall back to running
        # the tool without the sandbox — that would expose secrets in a
        # non-isolated environment. Fail CRITICAL and refuse execution.
        if use_sandbox:
            _bwrap_ok, _bwrap_err = _bwrap_preflight_check()
            if not _bwrap_ok:
                try:
                    from .security_events import write_event as _swe2
                    _ap2 = _audit_path_for(ctx)
                    _swe2(
                        _ap2, "forge.bwrap_unavailable",
                        severity="CRITICAL", tool=name, run_id=ctx.id,
                        details={"error": _bwrap_err[:300]},
                    )
                except Exception:
                    pass
                end_run(
                    ctx, status="error", exit_code=-1,
                    duration_s=0.0, sandbox="none",
                    stdout_data=None, stderr_text="",
                    summary="bwrap_unavailable",
                )
                raise BwrapUnavailable(
                    "sandbox launch failed (bwrap not functional). "
                    "Tools with declared secrets may NOT run outside the "
                    "sandbox. Fix bwrap or remove meta.secrets."
                )

        # ADR-0052 F8 — fail-closed guard: secrets must never reach an
        # unsandboxed subprocess. If the caller explicitly disabled the
        # sandbox (use_sandbox=False) but the tool declares meta.secrets,
        # refuse execution before any secret value is injected.
        if not use_sandbox and spec.meta.get("secrets"):
            try:
                from .security_events import write_event as _swe_ns
                _ap_ns = _audit_path_for(ctx)
                _swe_ns(
                    _ap_ns, "forge.secrets_no_sandbox",
                    severity="WARNING", tool=name, run_id=ctx.id,
                    details={"secrets_declared": sorted(spec.meta["secrets"])},
                )
            except Exception:
                pass
            end_run(
                ctx, status="error", exit_code=-1,
                duration_s=0.0, sandbox="none",
                stdout_data=None, stderr_text="",
                summary="secrets_require_sandbox",
            )
            raise ValueError(
                f"tool {name!r} declares meta.secrets but use_sandbox=False "
                "was requested. Tools with declared secrets require sandbox "
                "isolation; re-enable the sandbox or remove meta.secrets."
            )

        # Inject. Note: env is the dict we'll pass to subprocess.Popen;
        # the values land in the bwrap subprocess and nowhere else.
        env = dict(env)
        env.update(resolved)
        # ADR-0052 F8 — CORVIN_VAULT_INJECTED sentinel.
        # The tool checks for this variable at startup. Exit-code 2 =
        # vault_injection_failed (mapped to structured error by the runner,
        # see below). Never includes secret names or values.
        env["CORVIN_VAULT_INJECTED"] = "1"
        secret_values = [v for v in resolved.values() if isinstance(v, str)]

        # Audit — names only, never values.
        try:
            from .security_events import write_event as _swe
            _audit_path = _audit_path_for(ctx)
            _swe(
                _audit_path, "tool.secrets_injected",
                severity="INFO", tool=name, run_id=ctx.id,
                details={"persona": secret_persona,
                         "secrets_used": sorted(resolved.keys())},
            )
        except Exception:
            pass

    # ADR-0127 — datasource connection env injection. The compute runner
    # resolves a DSI v1 connection manifest (+ vault secrets) to a dict like
    # {PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD} and passes it here.
    # bwrap inherits the parent env (no --clearenv), so merging into the
    # Popen env dict is sufficient — the values land only in the sandboxed
    # subprocess. Values are added to the stdout/stderr redaction set so an
    # injected credential never leaks through a tool's debug print.
    if extra_env:
        # Never let injected datasource env clobber sandbox-critical keys.
        _reserved = {"PATH", "PYTHONPATH", "TMPDIR", "HOME", "LANG", "LC_ALL",
                     "LD_PRELOAD", "LD_LIBRARY_PATH", "CORVIN_VAULT_INJECTED"}
        env = dict(env)
        _injected_keys: list[str] = []
        for k, v in extra_env.items():
            if isinstance(k, str) and isinstance(v, str) and k not in _reserved:
                env[k] = v
                secret_values.append(v)
                _injected_keys.append(k)
        # ADR-0127 audit (review gap G1): the datasource extra_env path
        # bypassed the `tool.secrets_injected` event that the meta.secrets
        # path emits. Record the injection — env-var NAMES only, never values
        # (mirrors L16 v3 secret-vault capability-split traceability).
        if _injected_keys:
            try:
                from .security_events import write_event as _swe_ds
                _swe_ds(
                    _audit_path_for(ctx), "tool.datasource_env_injected",
                    severity="INFO", tool=name, run_id=ctx.id,
                    details={"persona": caller_persona or "",
                             "env_keys": sorted(_injected_keys)},
                )
            except Exception:
                pass

    payload_bytes = json.dumps(payload).encode()
    import time as _time
    t0 = _time.monotonic()

    # We use Popen so we own the cleanup. apply_rlimits calls setsid(),
    # which makes the child the leader of a new process group. That pgid
    # equals proc.pid, so killpg(proc.pid, ...) tears down the whole tree
    # — including bwrap's nested python — without touching our own pgrp.
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        preexec_fn=lambda: apply_rlimits(limits),
    )
    try:
        stdout_b, stderr_b = proc.communicate(input=payload_bytes, timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
        _dur_to = _time.monotonic() - t0
        end_run(
            ctx, status="timeout", exit_code=-1,
            duration_s=_dur_to,
            sandbox=sandbox_label, stdout_data=None,
            stderr_text="", summary="timed out",
        )
        _emit_tool_executed(ctx, status="timeout", exit_code=-1,
                            duration_s=_dur_to, sandbox=sandbox_label)
        raise ToolError(f"tool {name!r} timed out after {timeout}s")

    duration = _time.monotonic() - t0
    stdout = stdout_b or b""
    stderr_text = (stderr_b or b"").decode(errors="replace")[-8192:]
    # Layer-16 v3 — best-effort literal redaction of secret values in
    # stderr before it lands anywhere observable. The stdout is JSON the
    # tool emits intentionally; we redact it after JSON parsing below so
    # the structure is preserved while any string-valued field that
    # happens to contain a secret value is sanitized. Doing it here for
    # stderr is enough because stderr is plain text — we don't risk
    # breaking JSON.
    if secret_values:
        stderr_text = _secret_vault.redact_values(stderr_text, secret_values)
        # R1 finding: the full_stdout.bin artifact (written below on truncation)
        # got RAW stdout — defeating stdout redaction and leaking secret values
        # to disk. Redact the secret bytes from stdout HERE so both the artifact
        # AND the returned text are scrubbed. Bytes-level literal replace
        # preserves binary content while removing the literal secret bytes.
        for _sv in secret_values:
            try:
                stdout = stdout.replace(_sv.encode("utf-8"), b"[REDACTED]")
            except Exception:  # noqa: BLE001
                pass
    truncated = False
    total_stdout_bytes = len(stdout)
    truncated_artifact_path: str | None = None
    if len(stdout) > output_cap:
        # S8 — preserve the full stdout as an artifact before truncation,
        # so callers can fetch the missing bytes via filesystem read or a
        # chunked MCP call. Best-effort: a write failure (disk full, FS
        # error) does not change the truncation semantics — the caller
        # still gets the truncated envelope plus the truncated flag.
        try:
            full_path = ctx.artifacts_dir / "full_stdout.bin"
            full_path.write_bytes(stdout)
            truncated_artifact_path = str(full_path)
        except OSError:
            truncated_artifact_path = None
        stdout = stdout[:output_cap]
        truncated = True
    text = stdout.decode(errors="replace").strip()

    registry.bump_call(name)

    rc = proc.returncode
    if rc != 0:
        end_run(
            ctx, status="error", exit_code=rc,
            duration_s=duration, sandbox=sandbox_label,
            stdout_data=None, stderr_text=stderr_text,
            summary=f"non-zero exit {rc}",
        )
        _emit_tool_executed(ctx, status="error", exit_code=rc,
                            duration_s=duration, sandbox=sandbox_label)
        raise ToolError(
            f"tool {name!r} exited {rc} "
            f"(sandbox={sandbox_label})\nstderr:\n{stderr_text}"
        )

    if not text:
        raw: Any = None
    else:
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            raw = {"raw_stdout": text}

    # Layer-16 v3 — recursive literal redaction of secret values in the
    # parsed envelope. The structure (keys, types) is preserved; any
    # string field whose content contains a secret value gets it
    # replaced with ``<redacted>``. Defense against accidental
    # ``print(json.dumps({"env": dict(os.environ)}))`` patterns.
    if secret_values and raw is not None:
        raw = _redact_in_struct(raw, secret_values)

    envelope = _wrap_envelope(raw)
    data = envelope.get("data")
    error = envelope.get("error")

    # If the tool itself reports an error in the envelope, surface it as
    # a tool error to the caller (preserves MCP isError semantics).
    if envelope.get("ok") is False or (error is not None and error != ""):
        end_run(
            ctx, status="error", exit_code=rc,
            duration_s=duration, sandbox=sandbox_label,
            stdout_data=envelope, stderr_text=stderr_text,
            summary=str(error) if error else "tool reported ok=false",
        )
        raise ToolError(
            f"tool {name!r} reported error: {error or 'ok=false'}"
        )

    # Build a one-liner summary for the run digest
    summary_text = ""
    if isinstance(data, dict):
        if isinstance(data.get("summary"), str):
            summary_text = data["summary"]
    if not summary_text and isinstance(envelope.get("meta"), dict):
        s = envelope["meta"].get("summary")
        if isinstance(s, str):
            summary_text = s

    # Artifact-size budget check — the kernel's RLIMIT_FSIZE caps individual
    # writes, but artifact_bytes is the *total* the tool may have produced.
    artifact_total = sum(
        f.stat().st_size for f in ctx.artifacts_dir.rglob("*") if f.is_file()
    )
    if artifact_total > applied_budget.artifact_bytes:
        end_run(
            ctx, status="error", exit_code=rc,
            duration_s=duration, sandbox=sandbox_label,
            stdout_data=envelope, stderr_text=stderr_text,
            summary=(f"artifact_budget.exceeded: "
                     f"{artifact_total} > {applied_budget.artifact_bytes}"),
        )
        raise ToolError(
            f"tool {name!r} artifact_budget.exceeded: "
            f"wrote {artifact_total} bytes, budget {applied_budget.artifact_bytes}"
        )

    # Surface clamp info to the caller and the on-disk completion record.
    if budget_clamp_info:
        envelope = dict(envelope)
        meta = dict(envelope.get("meta") or {})
        meta["policy_clamped"] = {
            k: {"requested": req, "applied": app}
            for k, (req, app) in budget_clamp_info.items()
        }
        envelope["meta"] = meta

    # S8 — surface truncation in meta so callers can detect the missing
    # bytes and fetch them from the artifact. Existing stdout_truncated
    # boolean on RunResult stays as the structural flag.
    if truncated:
        envelope = dict(envelope)
        meta = dict(envelope.get("meta") or {})
        meta["stdout_truncated"] = True
        meta["stdout_truncated_at_bytes"] = output_cap
        meta["stdout_total_bytes"] = total_stdout_bytes
        if truncated_artifact_path:
            meta["stdout_full_artifact"] = truncated_artifact_path
        envelope["meta"] = meta

    completion = end_run(
        ctx,
        status="ok",
        exit_code=rc,
        duration_s=duration,
        sandbox=sandbox_label,
        stdout_data=envelope,
        stderr_text=stderr_text,
        summary=summary_text,
    )
    _emit_tool_executed(ctx, status="ok", exit_code=rc, duration_s=duration,
                        sandbox=sandbox_label, cache_hit=False)

    # Store cache entry for future deterministic replays.
    if cache_hit_key is not None:
        try:
            _cache.store(
                registry.root, cache_hit_key,
                envelope=envelope, run_id=ctx.id,
            )
        except Exception:
            pass

    return RunResult(
        ok=True,
        data=data,
        stdout_truncated=truncated,
        stderr=stderr_text,
        exit_code=rc,
        duration_s=duration,
        sandbox=sandbox_label,
        run_id=ctx.id,
        artifacts=completion.get("artifacts", []),
        envelope=envelope,
    )
