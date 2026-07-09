"""CLI entry — ``python -m corvin_compute serve ...`` (ADR-0013 Phase 13.4).

Two subcommands:

- ``serve``  — launch the worker daemon for one tenant.
- ``submit`` — round-trip a single ``compute_run`` against a running worker
  (operator-side debugging tool; not used by production paths).

The CLI is intentionally minimal — production traffic enters via the
MCP bridge (Phase 13.5) or directly through ``WorkerClient``.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path

from .client import WorkerClient
from .worker import WorkerServer


log = logging.getLogger("corvin_compute.cli")


def _default_socket_path(corvin_home: Path, tenant_id: str) -> Path:
    return Path(corvin_home) / "tenants" / tenant_id / "compute" / "worker.sock"


def _default_corvin_home() -> Path:
    env = os.environ.get("CORVIN_HOME")
    if env:
        return Path(env)
    return Path.home() / ".corvin"


# ADR-0127 — persona under which datasource-bound tool runs execute. It must
# be granted ``{"network":"allow","loopback":"allow"}`` in the forge
# policy.json persona_sandbox_overrides so the sandboxed tool can reach a
# (possibly loopback) database. Non-datasource runs pass no persona and keep
# the strict network-denied default.
COMPUTE_DATASOURCE_PERSONA = "compute-datasource"


# A DSI connection name is a single path segment — no traversal, no
# separators, no leading dot. Mirrors the L38 attachment-name guard.
_DATASOURCE_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")


def _resolve_datasource_env(corvin_home, tenant_id: str,
                            names: list[str]) -> dict[str, str]:
    """Resolve DSI v1 connection manifests → connection env for the tool.

    Reads ``<corvin_home>/tenants/<tid>/datasource_connections/<name>.json``
    and maps the connection to standard driver env vars. Vault-declared
    ``secrets`` (env-var NAMES) are resolved through the forge vault.

    Fail-CLOSED: a *declared* datasource that cannot be resolved (bad name,
    missing/malformed file) raises — the run must not silently execute under
    the network-permissive datasource persona with no binding. Names are
    validated against a strict charset and the resolved path is asserted to
    stay inside the connections dir (path-traversal / symlink defence).
    """
    import json as _json
    env: dict[str, str] = {}
    conn_dir = (Path(corvin_home) / "tenants" / tenant_id
                / "datasource_connections").resolve()
    # Optional vault for secret-sourced credentials.
    try:
        from forge import secret_vault as _vault  # type: ignore
    except Exception:  # noqa: BLE001
        _vault = None  # type: ignore
    for name in names:
        if not isinstance(name, str) or not _DATASOURCE_NAME_RE.match(name) \
                or ".." in name or "/" in name or "\\" in name:
            raise ValueError(f"invalid datasource name: {name!r}")
        path = (conn_dir / f"{name}.json").resolve()
        if conn_dir not in path.parents:
            raise ValueError(f"datasource path escaped connections dir: {name!r}")
        try:
            raw = _json.loads(path.read_text())
        except Exception as exc:  # noqa: BLE001
            raise ValueError(
                f"declared datasource {name!r} not resolvable: {exc}") from exc
        adapter = str(raw.get("adapter", "")).lower()
        cfg = raw.get("config") or {}
        if adapter in ("postgresql", "postgres"):
            if cfg.get("host"):     env["PGHOST"] = str(cfg["host"])
            if cfg.get("port"):     env["PGPORT"] = str(cfg["port"])
            db = cfg.get("dbname") or cfg.get("database")
            if db:                  env["PGDATABASE"] = str(db)
            if cfg.get("user"):     env["PGUSER"] = str(cfg["user"])
        elif adapter in ("mysql", "mariadb"):
            if cfg.get("host"):     env["MYSQL_HOST"] = str(cfg["host"])
            if cfg.get("port"):     env["MYSQL_TCP_PORT"] = str(cfg["port"])
            db = cfg.get("dbname") or cfg.get("database")
            if db:                  env["MYSQL_DATABASE"] = str(db)
            if cfg.get("user"):     env["MYSQL_USER"] = str(cfg["user"])
        # Vault-sourced secrets (passwords / tokens): NAMES only on disk.
        secrets = [s for s in (raw.get("secrets") or []) if isinstance(s, str)]
        if secrets:
            if _vault is None:
                raise ValueError(
                    f"datasource {name!r} declares secrets {secrets} but the "
                    f"forge vault is unavailable")
            resolved, missing = _vault.resolve_secrets(secrets)
            if missing:
                raise ValueError(
                    f"datasource {name!r} secret(s) missing from vault: {missing}")
            env.update({k: v for k, v in resolved.items() if isinstance(v, str)})
    return env


def _build_runner_fn(tenant_id: str = "_default", corvin_home_path: Path | None = None):
    """Resolve Forge's runner.run_tool() with a user-scope registry.

    The worker's per-iteration runner instantiates a long-lived Registry
    at the user-scope forge workspace (``<corvin_home>/global/forge``)
    and routes every tool call through ``forge.runner.run_tool``. That
    invocation does the full bwrap-sandbox + budget-clamp + audit-emit
    cycle the LLM-driven path uses.

    Tools the LLM registers via mcp__forge__forge_tool (user scope) land
    in the same workspace and become visible here automatically. The
    `permission_mode="yes"` is the operator-trusted-daemon contract:
    the worker is a long-running process the operator started; per-call
    permission prompts would deadlock on it.

    Falls back to a refuse-every-call stub when the forge import fails
    (dev environments, MINIMAL bootstraps).
    """
    try:
        # Local import — keeps the dev path importable without forge.
        from forge import runner as forge_runner  # type: ignore[import]
        from forge.registry import Registry  # type: ignore[import]
        from forge.policy import Policy  # type: ignore[import]
        from forge.scope import corvin_home  # type: ignore[import]
    except ImportError:
        log.warning("forge package not importable — worker will refuse tool calls")
        return None

    user_root = corvin_home() / "global" / "forge"
    user_root.mkdir(parents=True, exist_ok=True)
    try:
        policy = Policy.load(user_root)
    except Exception:  # noqa: BLE001
        policy = Policy()
    registry = Registry(user_root, hash_chain=policy.audit_hash_chain)
    log.info("forge runner wired: registry root=%s tools=%d",
             user_root, len(registry.list()))

    ds_home = corvin_home() if corvin_home_path is None else Path(corvin_home_path)

    def adapter(tool_name: str, payload, datasources=None):
        # ADR-0127 — when the run declares datasources, resolve their
        # connection env and run the tool under the network-allowed
        # datasource persona. Without datasources the tool stays strictly
        # network-denied (the safe default).
        extra_env = None
        caller_persona = None
        if datasources:
            extra_env = _resolve_datasource_env(ds_home, tenant_id, list(datasources))
            caller_persona = COMPUTE_DATASOURCE_PERSONA
        result = forge_runner.run_tool(
            registry, tool_name, dict(payload),
            permission_mode="yes",   # daemon — auto-approve trusted tools
            policy=policy,
            caller_persona=caller_persona,
            extra_env=extra_env,
        )
        # Forge wraps non-envelope stdout into the standard AWP envelope:
        #   {"ok": true, "status": 200, "data": <tool_output>, "meta": {...}}
        # The driver's loss_metric is a dotted path over the OUTPUT (data),
        # so we unwrap here so callers don't have to use "data.loss".
        env = getattr(result, "envelope", None) or {}
        if not isinstance(env, dict):
            env = {}
        if (isinstance(env.get("data"), dict)
                and {"ok", "status", "data"}.issubset(env.keys())):
            out = dict(env["data"])
            # Preserve the envelope's meta (forge run-id, cache info, etc.)
            out["meta"] = dict(env.get("meta") or {})
        else:
            # Either a pre-wrapped envelope (data not dict) or empty —
            # pass it through verbatim so the caller can debug.
            out = dict(env)
            out.setdefault("meta", {})
        # Append the Forge RunResult's cache-hit flag so the driver's
        # cache_hit reading works.
        if isinstance(out.get("meta"), dict):
            out["meta"].setdefault("cache_hit",
                                    bool(getattr(result, "cache_hit", False)))
        return out

    return adapter


async def _cmd_serve(args: argparse.Namespace) -> int:
    home = Path(args.corvin_home)
    # Pin CORVIN_HOME to the resolved --corvin-home so every home-derived
    # resolver inside this process agrees with the flag — in particular the
    # background-completion queue (completion_notify resolves its
    # pending_notifications/ dir from CORVIN_HOME, taking no home argument). A
    # plain-CLI launch that passed only --corvin-home would otherwise write the
    # notify record under ~/.corvin while RunStore/socket used the flag's tree
    # → the record is orphaned where the bridge poller never reads it. The
    # bridge poller MUST share this same CORVIN_HOME for delivery to work.
    os.environ["CORVIN_HOME"] = str(home)
    socket_path = Path(args.socket) if args.socket \
        else _default_socket_path(home, args.tenant)
    runner_fn = _build_runner_fn(tenant_id=args.tenant, corvin_home_path=home)

    # Review FINDING 1: wire a REAL audit_emit so compute.* events reach the
    # unified hash chain (was a no-op default → all compute audit was lost in
    # production). Metadata-only via the compute allow-list in audit.emit().
    from . import audit as _compute_audit
    _audit_chain = home / "global" / "forge" / "audit.jsonl"
    _tid = args.tenant

    def _audit_emit(event: str, **fields) -> None:
        try:
            fields.setdefault("tenant_id", _tid)
            _compute_audit.emit(event, path=_audit_chain, **fields)
        except Exception:  # noqa: BLE001 — observability is best-effort
            pass

    # ADR-0029 — pipeline/HAC engines exist and are fully tested
    # (pipeline/engine.py, hac/engine.py) but were never constructed here, so
    # the production worker only ever served the flat grid/random/bayesian
    # engine; any engine="pipeline"/"hac" submit_run call failed with
    # "unknown engine" no matter how the request was submitted. The
    # module-level register_pipeline_engine()/register_hac_engine() factories
    # are not an alternative wiring — they populate engine_registry.py's
    # standalone registry, which WorkerServer never reads (it only consults
    # its own self._extra_engines, populated below).
    from .hac.engine import HACEngine
    from .pipeline.engine import PipelineEngine

    pipeline_engine = PipelineEngine(
        corvin_home=home, runner_fn=runner_fn, audit_emit=_audit_emit,
    )
    hac_engine = HACEngine(
        corvin_home=home, runner_fn=runner_fn, audit_emit=_audit_emit,
    )

    server = WorkerServer(
        tenant_id=args.tenant,
        corvin_home=home,
        socket_path=socket_path,
        max_concurrent_runs=args.max_concurrent_runs,
        runner_fn=runner_fn,
        audit_emit=_audit_emit,
        extra_engines=[pipeline_engine, hac_engine],
    )
    print(f"[corvin-compute] serving tenant={args.tenant!r} at {socket_path}")
    try:
        await server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        await server.stop()
    return 0


def _cmd_submit(args: argparse.Namespace) -> int:
    home = Path(args.corvin_home)
    socket_path = Path(args.socket) if args.socket \
        else _default_socket_path(home, args.tenant)
    client = WorkerClient(socket_path)
    payload = {
        "tenant_id":   args.tenant,
        "tool_name":   args.tool_name,
        "param_grid":  json.loads(args.param_grid),
        "loss_metric": args.loss_metric,
        "strategy":    args.strategy,
        "budget": {
            "max_iterations":  args.max_iterations,
            "max_wall_clock_s": args.max_wall_clock_s,
        },
    }
    if args.seed is not None:
        payload["seed"] = args.seed
    result = client.submit_run(**payload)
    print(json.dumps(result, indent=2))
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    socket_path = Path(args.socket) if args.socket \
        else _default_socket_path(Path(args.corvin_home), args.tenant)
    client = WorkerClient(socket_path)
    print(json.dumps(client.get_status(args.compute_handle), indent=2))
    return 0


def _cmd_result(args: argparse.Namespace) -> int:
    socket_path = Path(args.socket) if args.socket \
        else _default_socket_path(Path(args.corvin_home), args.tenant)
    client = WorkerClient(socket_path)
    print(json.dumps(client.get_result(args.compute_handle,
                                       wait_s=args.wait_s), indent=2))
    return 0


def _cmd_reap(args: argparse.Namespace) -> int:
    """Finalize orphaned non-terminal runs (operator maintenance).

    A run left non-terminal by a worker that is gone will never be picked up
    (the bridge must not auto-start the compute worker — L25), so it sits at
    ``running``/``queued`` forever and keeps counting against quota. This marks
    such stale orphans ``failed`` WITHOUT executing them — safe with no worker.
    """
    home = Path(args.corvin_home)
    older_than_s = max(0.0, float(args.older_than_hours) * 3600.0)
    from . import recovery as _recovery
    from . import state as _state

    if args.dry_run:
        import time as _time
        store = _state.RunStore(home, args.tenant)
        now = _time.time()
        candidates = []
        for run_id in _recovery.scan_orphaned(
                home, args.tenant, older_than_s=older_than_s, now=now):
            mtime = store.summary_mtime(run_id)
            try:
                state = store.read_summary(run_id).get("state")
            except (OSError, FileNotFoundError):
                state = None
            candidates.append({
                "run_id": run_id,
                "state": state,
                "stale_hours": round((now - mtime) / 3600.0, 1) if mtime else None,
            })
        print(json.dumps({"tenant": args.tenant, "dry_run": True,
                          "would_reap": candidates, "count": len(candidates)},
                         indent=2))
        return 0

    _audit_chain = home / "global" / "forge" / "audit.jsonl"
    reaped = _recovery.reap_orphaned(
        home, args.tenant,
        older_than_s=older_than_s,
        audit_path=_audit_chain,
    )
    print(json.dumps({"tenant": args.tenant, "reaped": reaped,
                      "count": len(reaped)}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="corvin_compute")
    p.add_argument("--corvin-home", default=str(_default_corvin_home()),
                   help="Path to <corvin_home> (default: $CORVIN_HOME or ~/.corvin)")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_serve = sub.add_parser("serve", help="Launch the worker daemon")
    p_serve.add_argument("--tenant", required=True)
    p_serve.add_argument("--socket", default=None)
    p_serve.add_argument("--max-concurrent-runs", type=int, default=2)
    p_serve.set_defaults(func=lambda a: asyncio.run(_cmd_serve(a)))

    p_submit = sub.add_parser("submit", help="Submit a run (debug only)")
    p_submit.add_argument("--tenant", required=True)
    p_submit.add_argument("--socket", default=None)
    p_submit.add_argument("--tool-name", required=True)
    p_submit.add_argument("--param-grid", required=True,
                          help="JSON-encoded param_grid dict")
    p_submit.add_argument("--loss-metric", default="loss")
    p_submit.add_argument("--strategy", default="grid",
                          choices=["grid", "random", "bayesian"])
    p_submit.add_argument("--max-iterations", type=int, default=100)
    p_submit.add_argument("--max-wall-clock-s", type=int, default=600)
    p_submit.add_argument("--seed", type=int, default=None)
    p_submit.set_defaults(func=_cmd_submit)

    p_status = sub.add_parser("status", help="Poll a run's status")
    p_status.add_argument("--tenant", required=True)
    p_status.add_argument("--socket", default=None)
    p_status.add_argument("compute_handle")
    p_status.set_defaults(func=_cmd_status)

    p_result = sub.add_parser("result", help="Read a run's final result")
    p_result.add_argument("--tenant", required=True)
    p_result.add_argument("--socket", default=None)
    p_result.add_argument("--wait-s", type=float, default=0.0)
    p_result.add_argument("compute_handle")
    p_result.set_defaults(func=_cmd_result)

    p_reap = sub.add_parser(
        "reap",
        help="Finalize orphaned non-terminal runs as failed (operator "
             "maintenance; no worker required)")
    p_reap.add_argument("--tenant", required=True)
    p_reap.add_argument("--older-than-hours", type=float, default=24.0,
                        help="Only reap runs whose summary.json has been "
                             "untouched this long (default: 24h). Guards "
                             "against reaping a run a live worker is iterating.")
    p_reap.add_argument("--dry-run", action="store_true",
                        help="List orphan candidates without writing.")
    p_reap.set_defaults(func=_cmd_reap)

    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
