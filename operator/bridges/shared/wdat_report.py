"""wdat_report.py — ADR-0109 M5: WDAT compliance report generator.

CLI: corvin-wdat-report <run_id> [--tenant <tid>] [--include-content] [--output <path>]

Reads the L16 audit chain for acs.manager_decided / acs.worker_spawned /
acs.worker_traced events, reconstructs the delegation tree using spawn_nonce
causality links, and outputs a structured JSON compliance report.

--include-content requires the WDAT key (CORVIN_WDAT_KEY env or vault "wdat_key")
and emits audit.wdat_content_accessed BEFORE decrypting each trace file.

MUST NOT import anthropic — CI AST lint enforces.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_SHARED = Path(__file__).resolve().parent
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

_FORGE_PATH = str(Path(__file__).resolve().parents[3] / "operator" / "forge")

_WDAT_EVENTS = frozenset({
    "acs.manager_decided",
    "acs.worker_spawned",
    "acs.worker_traced",
    # ADR-0109 M6: tool-call traces from path_gate inside worker subprocs
    "forge.tool_executed",
    # ADR-0112: engine lifecycle bookends (start/complete/error)
    "acs.engine_started",
    "acs.engine_completed",
    "acs.engine_error",
})


# ---------------------------------------------------------------------------
# Audit chain reader
# ---------------------------------------------------------------------------

def _corvin_home() -> Path:
    # Prefer the explicit env var; fall back to ~/.corvin.
    # We intentionally do NOT walk ancestor directories here: the ancestor walk
    # (used by path_gate and forge.paths) returns <repo>/.corvin on developer
    # machines, which is the dev data store, not the operator's runtime chain.
    # wdat_report is an operator/compliance tool — it must read the real runtime
    # chain at ~/.corvin unless CORVIN_HOME is explicitly set.
    env = os.environ.get("CORVIN_HOME")
    if env:
        return Path(os.path.expanduser(os.path.expandvars(env)))
    return Path.home() / ".corvin"


def _audit_path(tenant_id: str) -> Path:
    return _corvin_home() / "tenants" / tenant_id / "global" / "audit.jsonl"


def _iter_audit_events(audit_path: Path, event_types: frozenset[str]) -> list[dict]:
    """Read audit.jsonl and return all events matching event_types."""
    events: list[dict] = []
    if not audit_path.exists():
        return events
    try:
        for line in audit_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("event_type") in event_types:
                events.append(obj)
    except OSError as exc:
        log.warning("wdat_report: cannot read %s: %s", audit_path, exc)
    return events


# ---------------------------------------------------------------------------
# Delegation tree reconstruction
# ---------------------------------------------------------------------------

def _build_tree(events: list[dict], run_id: str) -> dict[str, Any]:
    """Reconstruct delegation tree from WDAT audit events for a given run_id.

    Returns a dict keyed by worker_id with spawned/traced event data,
    grouped by spawn_nonce (manager batch).
    """
    manager_decisions: list[dict] = []
    spawned_by_id: dict[str, dict] = {}
    traced_by_id: dict[str, dict] = {}
    nonce_to_workers: dict[str, list[str]] = defaultdict(list)

    for ev in events:
        details = ev.get("details") or {}
        etype = ev.get("event_type")

        if etype == "acs.manager_decided":
            if details.get("run_id") == run_id:
                manager_decisions.append(details)

        elif etype == "acs.worker_spawned":
            if details.get("run_id") == run_id:
                wid = details.get("worker_id", "")
                spawned_by_id[wid] = details
                nonce = details.get("spawn_nonce")
                if nonce and wid:
                    nonce_to_workers[nonce].append(wid)

        elif etype == "acs.worker_traced":
            wid = details.get("worker_id", "")
            ev_run_id = details.get("run_id")
            if ev_run_id is not None:
                # new events (post ADR-0109 fix): filter by run_id to prevent cross-run contamination
                if ev_run_id == run_id and wid in spawned_by_id:
                    traced_by_id[wid] = details
            else:
                # legacy events without run_id: match indirectly via spawned set
                if wid in spawned_by_id:
                    traced_by_id[wid] = details

    workers: list[dict] = []
    for wid, spawned in spawned_by_id.items():
        traced = traced_by_id.get(wid, {})
        workers.append({
            "worker_id": wid,
            "iteration": spawned.get("iteration"),
            "depth": spawned.get("depth"),
            "parent_worker_id": spawned.get("parent_worker_id"),
            "can_delegate": spawned.get("can_delegate"),
            "spawn_nonce": spawned.get("spawn_nonce"),
            "engine": (traced.get("engine_attestation") or {}).get("model_id") or spawned.get("model_id"),
            "status": traced.get("status"),
            "confidence": traced.get("confidence"),
            "instruction_hash": spawned.get("instruction_hash"),
            "output_hash": traced.get("output_hash"),
            "duration_ms": traced.get("duration_ms"),
            "tokens_used": traced.get("tokens_used"),
            "engine_attestation": traced.get("engine_attestation"),
        })

    return {
        "manager_decisions": manager_decisions,
        "workers": workers,
        "nonce_groups": {nonce: wids for nonce, wids in nonce_to_workers.items()},
    }


# ---------------------------------------------------------------------------
# M4 content store reader
# ---------------------------------------------------------------------------

def _wdat_load_key() -> "bytes | None":
    """Load the 32-byte AES-256 WDAT key from env or vault."""
    hex_key = os.environ.get("CORVIN_WDAT_KEY", "").strip()
    if hex_key:
        try:
            key = bytes.fromhex(hex_key)
            if len(key) == 32:
                return key
        except ValueError:
            pass
    try:
        if _FORGE_PATH not in sys.path:
            sys.path.insert(0, _FORGE_PATH)
        from vault import get_item as _vault_get  # type: ignore[import-untyped]
        val = str(_vault_get("wdat_key", source="wdat")).strip()
        key = bytes.fromhex(val)
        if len(key) == 32:
            return key
    except Exception:
        pass
    return None


def _decrypt_trace(encrypted: bytes, key: bytes) -> "dict | None":
    """AES-256-GCM decrypt. Format: nonce(12) || ciphertext."""
    if len(encrypted) < 28:  # 12 nonce + at least 16 GCM tag
        return None
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce, ciphertext = encrypted[:12], encrypted[12:]
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, None)
        return json.loads(plaintext.decode("utf-8"))
    except Exception:
        return None


def _load_trace(
    traces_dir: Path,
    wid: str,
    key: "bytes | None",
    tenant_id: str,
    run_id: str,
    audit_path: Path,
) -> "dict | None":
    """Load and optionally decrypt a trace file. Emits wdat_content_accessed before read."""
    # Encrypted first, then plaintext fallback
    for suffix, encrypted in [(".json.enc", True), (".json", False)]:
        trace_file = traces_dir / f"{wid}{suffix}"
        if not trace_file.exists():
            continue
        # ADR-0109: emit audit event BEFORE reading content
        _emit_access_audit(tenant_id, run_id, wid, audit_path)
        if encrypted:
            if key is None:
                log.warning("wdat_report: trace %s is encrypted but no WDAT key provided", wid)
                return None
            raw = trace_file.read_bytes()
            return _decrypt_trace(raw, key)
        else:
            try:
                return json.loads(trace_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
    return None


def _real_chain_integrity(audit_path: Path, run_event_count: int) -> str:
    """Real hash-chain verification for the "Chain: ..." indicator.

    Returns one of:
      "verified"    — chain walk found no problems, and this run has events
      "empty"       — chain walk found no problems, but this run has none
      "broken"      — verify_chain found ≥1 tampered/broken-link entry
      "unavailable" — the verifier itself could not be reached/run

    A broken/tampered result is reported regardless of ``run_event_count``:
    a corrupted audit file is a whole-file problem, not scoped to one run.
    """
    try:
        if _FORGE_PATH not in sys.path:
            sys.path.insert(0, _FORGE_PATH)
        from forge import security_events as _sec  # type: ignore[import-untyped]
        ok, problems = _sec.verify_chain(audit_path)
    except Exception:
        log.warning("wdat_report: chain verification unavailable", exc_info=True)
        return "unavailable"
    if not ok:
        return "broken"
    return "verified" if run_event_count > 0 else "empty"


def _emit_access_audit(tenant_id: str, run_id: str, wid: str, audit_path: Path) -> None:
    """Write audit.wdat_content_accessed (WARNING) before decrypting any trace."""
    try:
        if _FORGE_PATH not in sys.path:
            sys.path.insert(0, _FORGE_PATH)
        from forge import security_events as _sec  # type: ignore[import-untyped]
        _sec.write_event(audit_path, "audit.wdat_content_accessed", details={
            "run_id": run_id,
            "worker_id": wid,
        })
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_report(
    run_id: str,
    tenant_id: str = "_default",
    include_content: bool = False,
    corvin_home: "Path | None" = None,
) -> dict[str, Any]:
    """Generate a WDAT compliance report for the given run_id.

    corvin_home overrides _corvin_home() so callers that already know the
    correct runtime root (e.g. the console route using forge.paths.corvin_home())
    can pass it in — avoids the ~/.corvin vs project-.corvin split on dev machines.
    """
    effective_home = corvin_home or _corvin_home()
    if corvin_home is not None:
        audit_path_obj = corvin_home / "tenants" / tenant_id / "global" / "audit.jsonl"
    else:
        audit_path_obj = _audit_path(tenant_id)
    events = _iter_audit_events(audit_path_obj, _WDAT_EVENTS)
    tree = _build_tree(events, run_id)

    workers = tree["workers"]
    manager_decisions = tree["manager_decisions"]

    # Determine run_dir for trace access — scan session directories since bridge:chat is unknown
    run_dir: Path | None = None
    if include_content:
        sessions_base = effective_home / "tenants" / tenant_id / "sessions"
        if sessions_base.exists():
            for session_dir in sessions_base.iterdir():
                candidate = session_dir / "acs" / "runs" / run_id
                if candidate.exists():
                    run_dir = candidate
                    break
        if run_dir is None:
            log.warning("wdat_report: run_dir not found for run_id=%s — content unavailable", run_id)

    wdat_key = _wdat_load_key() if include_content else None
    if include_content and wdat_key is None:
        log.warning("wdat_report: --include-content requested but no WDAT key found; traces will be omitted")

    # Build worker entries
    worker_entries: list[dict] = []
    for w in workers:
        entry: dict = {
            "worker_id": w["worker_id"],
            "iteration": w["iteration"],
            "depth": w["depth"],
            "parent_worker_id": w["parent_worker_id"],
            "spawn_nonce": w["spawn_nonce"],
            "engine": w["engine"],
            "status": w["status"],
            "confidence": w["confidence"],
            "instruction_hash": w["instruction_hash"],
            "output_hash": w["output_hash"],
            "duration_ms": w["duration_ms"],
            "tokens_used": w["tokens_used"],
            "engine_attestation": w["engine_attestation"],
        }
        if include_content and run_dir is not None:
            traces_dir = run_dir / "traces"
            trace = _load_trace(
                traces_dir, w["worker_id"], wdat_key,
                tenant_id, run_id, audit_path_obj,
            )
            if trace is not None:
                entry["content"] = {
                    "instruction": trace.get("instruction"),
                    "output": trace.get("output"),
                }
            else:
                entry["content"] = None
        worker_entries.append(entry)

    # Delegation tree: parent_id → list of child worker_ids
    delegation_tree: dict[str, list[str]] = defaultdict(list)
    top_level: list[str] = []
    for w in workers:
        pid = w.get("parent_worker_id")
        if pid:
            delegation_tree[pid].append(w["worker_id"])
        else:
            top_level.append(w["worker_id"])

    # Chain integrity indicator. This used to be COSMETIC — "verified" meant
    # only "this run has ≥1 recorded event", never an actual hash-chain walk;
    # a tampered/broken audit.jsonl still showed a permanent green
    # "Chain: verified" badge in the chat UI, since the badge was never
    # wired to the real verifier (security_events.verify_chain / audit.
    # verify_audit) that this codebase already has and already runs
    # elsewhere (the ACO integrity monitor) — just never for this endpoint
    # (adversarial review finding). Now performs a real walk of the audit
    # file every time this report is generated.
    run_event_count = len(tree["manager_decisions"]) + len(tree["workers"])
    chain_integrity = _real_chain_integrity(audit_path_obj, run_event_count)

    return {
        "run_id": run_id,
        "tenant_id": tenant_id,
        "chain_integrity": chain_integrity,
        "total_workers": len(worker_entries),
        "total_manager_decisions": len(manager_decisions),
        "manager_decisions": manager_decisions,
        "workers": worker_entries,
        "delegation_tree": {
            "top_level": top_level,
            "children": dict(delegation_tree),
        },
        "eu_ai_act": {
            "art_9_risk_management": "documented" if worker_entries else "no_workers_found",
            "art_13_transparency": "full" if worker_entries else "no_workers_found",
            "art_14_human_oversight": (
                "content_store_decrypted" if include_content
                else "content_store_available"
            ),
        },
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv: "list[str] | None" = None) -> None:
    parser = argparse.ArgumentParser(
        prog="corvin-wdat-report",
        description="ADR-0109: Generate a WDAT compliance report for an ACS run.",
    )
    parser.add_argument("run_id", help="ACS run_id to report on")
    parser.add_argument(
        "--tenant", default="_default",
        help="Tenant ID (default: _default)",
    )
    parser.add_argument(
        "--include-content", action="store_true",
        help="Decrypt and include instruction+output text from content store. "
             "Requires WDAT key (CORVIN_WDAT_KEY env or vault item 'wdat_key'). "
             "Emits audit.wdat_content_accessed before each trace read.",
    )
    parser.add_argument(
        "--output", "-o", default="-",
        help="Output path (default: stdout)",
    )
    parser.add_argument(
        "--indent", type=int, default=2,
        help="JSON indent width (default: 2)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING)

    report = generate_report(
        run_id=args.run_id,
        tenant_id=args.tenant,
        include_content=args.include_content,
    )
    report_json = json.dumps(report, ensure_ascii=False, indent=args.indent)

    if args.output == "-":
        sys.stdout.write(report_json + "\n")
    else:
        out_path = Path(args.output)
        out_path.write_text(report_json + "\n", encoding="utf-8")
        print(f"Report written to {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
