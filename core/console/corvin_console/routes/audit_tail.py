"""Audit-Tail — read-only chain inspector.

Reads from the tail of ``<tenant_home>/global/forge/audit.jsonl``,
optionally filtered by ``severity`` (INFO/WARNING/CRITICAL) and/or
``event_type`` prefix (e.g. ``console.``, ``gateway.``).

Phase B: serves the last *N* events (cap 1000). Phase D will add
SSE-streaming on top of the same backing file.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from .. import auth as session_auth
from ..deps import require_session

from .. import _bootstrap
_forge_paths = _bootstrap.forge_paths


router = APIRouter()

# Keys allowed in the 'details' blob returned to console clients.
# 'user' and raw sender identifiers are deliberately excluded — they are
# present in the chain for forensics but must not be re-served via the API
# (CLAUDE.md L16 metadata-only rule / GDPR Art. 5 data minimisation).
_SAFE_DETAIL_KEYS: frozenset[str] = frozenset({
    "tenant_id", "action", "target_kind", "target_id",
    "sid_fingerprint", "reason", "run_id", "step_id", "trigger",
    "classification", "engine_id", "persona", "channel", "chat_key",
    "matched_rule", "host", "layer_id", "found_count", "engine_ids",
    "default_engine", "engine_count", "problem_count", "first_problems",
    "wall_clock_s", "msg_id", "provider", "chars", "token_fingerprint",
    "user_agent_class", "task_id", "origin_id", "endpoint_id", "nonce_prefix",
    "status", "filter_pass_count", "filter_reject_count", "ttl_s",
    "duration_ms", "sender_instance_id", "instance_id_match", "http_status",
    # os_turn.* and delegation.* fields (bridge adapter chain)
    "turn_id", "model", "engine", "tools_called", "exit_code", "timed_out",
    "tool_name", "seq", "delegation_id", "target_engine",
})

_DETAIL_PII_KEYS: frozenset[str] = frozenset({
    "user", "email", "name", "ip", "phone", "address",
    "text", "transcript", "content", "prompt", "body",
    "error", "exception", "traceback",
})


def _sanitize_details(details: dict) -> dict:
    result = {}
    for k, v in details.items():
        if k in _DETAIL_PII_KEYS:
            continue
        if k in _SAFE_DETAIL_KEYS:
            result[k] = v
    return result


def _tail_lines(path: Path, *, byte_budget: int = 256 * 1024) -> list[str]:
    """Return the trailing lines of *path* up to ``byte_budget`` bytes.

    Reads from the end of the file and walks backwards. Cheap because
    we never load the whole chain into memory; bounded by byte_budget.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return []
    start = max(0, size - byte_budget)
    try:
        with path.open("rb") as fh:
            fh.seek(start)
            buf = fh.read()
    except OSError:
        return []
    text = buf.decode("utf-8", errors="replace")
    # If we cut into the middle of a line, drop the partial first line.
    lines = text.splitlines()
    if start > 0 and lines:
        lines = lines[1:]
    return lines


def _parse_chain_file(
    path: Path,
    *,
    severity: str | None,
    event_prefix: str | None,
) -> list[dict[str, Any]]:
    """Parse one audit chain file and return filtered event dicts."""
    raw_lines = _tail_lines(path)
    events: list[dict[str, Any]] = []
    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec_obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        sev = rec_obj.get("severity", "INFO")
        et  = rec_obj.get("event_type", "")
        if severity and sev != severity:
            continue
        if event_prefix and not et.startswith(event_prefix):
            continue
        # Project to a curated shape.  The hash prefix (first 8 hex chars) is
        # safe to surface — it is the same metadata already shown by the
        # DualTrackAuditPanel.  Full hash and prev_hash are never returned.
        h = rec_obj.get("hash") or ""
        events.append({
            "ts":           rec_obj.get("ts"),
            "event_type":   et,
            "severity":     sev,
            "hash_prefix":  h[:8] if h else None,
            "run_id":       rec_obj.get("run_id", "") or None,
            "tool":         rec_obj.get("tool", "") or None,
            "details":      _sanitize_details(rec_obj.get("details") or {}),
        })
    return events


@router.get("/audit/tail")
def audit_tail(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    limit: int = Query(default=100, ge=1, le=1000),
    severity: str | None = Query(default=None,
                                  description="Filter: INFO|WARNING|CRITICAL"),
    event_prefix: str | None = Query(default=None,
                                     description="Filter: e.g. 'console.' or 'gateway.'"),
) -> dict[str, Any]:
    """Return the last *limit* events merged from all three tenant audit chains.

    Three chains are merged for the compliance view:
    - ``<tenant>/global/forge/audit.jsonl`` — tenant forge events (console.*, engine.*, A2A.*, acs.*, …)
    - ``<tenant>/global/audit.jsonl``        — tenant-level events (chain.genesis, top-level A2A, …)
    - ``<corvin_home>/global/forge/audit.jsonl`` — bridge adapter events (os_turn.*, delegation.*, …)

    On installs with ADR-0007 backward-compat symlinks the first and third paths converge;
    the inode-based dedup guard prevents double-counting in that case.
    Events are deduplicated by (ts, event_type, hash_prefix) and returned newest-first.
    """
    tid = rec.tenant_id
    global_dir = _forge_paths.tenant_global_dir(tid)

    # Three canonical chain locations.
    # forge_chain  — tenant-scoped forge events (console.*, engine.*, A2A.*, acs.*, …)
    # tenant_chain — tenant-level events (chain.genesis, top-level A2A, acs.*)
    # bridge_chain — bridge adapter events (os_turn.*, delegation.*, compliance_assertion.*, …)
    #                written by the bridge to corvin_home()/global/forge/ — on installs with
    #                ADR-0007 backward-compat symlinks these converge; without symlinks this
    #                third path is the only way bridge events reach the console.
    forge_chain  = global_dir / "forge" / "audit.jsonl"
    tenant_chain = global_dir / "audit.jsonl"
    bridge_chain = _forge_paths.corvin_home() / "global" / "forge" / "audit.jsonl"

    all_events: list[dict[str, Any]] = []
    total_size = 0
    seen_inodes: set[int] = set()  # avoid double-counting when symlinks converge paths

    for chain in (forge_chain, tenant_chain, bridge_chain):
        if not chain.exists():
            continue
        try:
            st = chain.stat()
        except OSError:
            continue
        inode = (st.st_dev, st.st_ino)
        all_events.extend(_parse_chain_file(chain, severity=severity, event_prefix=event_prefix))
        if inode not in seen_inodes:
            seen_inodes.add(inode)
            total_size += st.st_size

    if not all_events:
        return {"tenant_id": tid, "ts": time.time(), "count": 0,
                "chain_size_b": total_size, "events": []}

    # Deduplicate by (ts, event_type) — same event may appear in both chains
    # on installs where compat symlinks converge them.
    seen: set[tuple] = set()
    deduped: list[dict[str, Any]] = []
    for ev in all_events:
        key = (ev.get("ts"), ev.get("event_type"), ev.get("hash_prefix"))
        if key not in seen:
            seen.add(key)
            deduped.append(ev)

    # Newest-first, then trim to limit.
    deduped.sort(key=lambda r: r.get("ts") or 0.0, reverse=True)
    deduped = deduped[:limit]
    return {
        "tenant_id":     tid,
        "ts":            time.time(),
        "count":         len(deduped),
        "chain_size_b":  total_size,
        "events":        deduped,
    }
