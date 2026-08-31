"""Chain Dual-Track View — ADR-0118.

Reconstructs OS-turn and worker-delegation events from audit.jsonl
and groups them by delegation_id so the frontend can render a
two-lane swimlane (OS left, Worker right, bridge arrows in between).

Reads from three audit chain files (same three-source strategy as audit_tail.py):
  - corvin_home()/global/forge/audit.jsonl  — bridge adapter events (os_turn.*, delegation.*)
  - tenant_global_dir/forge/audit.jsonl     — tenant forge events (console.*, A2A.*, acs.*)
  - tenant_global_dir/audit.jsonl           — tenant-level events (chain.genesis, top-level A2A)

Each chain is scanned in FULL (streaming, retaining only dual-track-relevant
event types) so the view is COMPLETE — a session's events are never dropped
once the chain grows past a trailing-byte window. This matches the single-chain
os-turns reader and keeps the two views consistent.

Session filter: events are scoped to a chat_key extracted from the
``sid`` path parameter (format: ``<channel>:<chat_key>``).
Note: bridge events may store chat_key as ``"<channel>:<bare_key>"`` or as a bare key.
Both forms are matched correctly.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from .. import auth as session_auth
from ..deps import require_session

from .. import _bootstrap
_forge_paths = _bootstrap.forge_paths

router = APIRouter()

# ── Event type sets ────────────────────────────────────────────────────────────

# OS-side: lifecycle events for OS turns and delegations.
_OS_EVENTS = frozenset({
    "os_turn.started",
    "os_turn.tool_called",
    "os_turn.completed",
    "os_turn.error",
    "delegation.started",
    "delegation.ended",
    "delegation.error",
})

# Worker-side: A2A receiver + engine events.
_WORKER_EVENTS = frozenset({
    "A2A.envelope_received",
    "A2A.engine_spawned",
    "A2A.result_filtered",
    "A2A.response_signed",
    "A2A.chain_dna_verified",
    "A2A.chain_dna_mismatch",
    "A2A.chain_dna_genesis_absent",
    "A2A.request_rejected",
})

# Bridge events: appear on both sides (OS sends, Worker receives).
_BRIDGE_OS_EVENTS = frozenset({"A2A.envelope_sent", "A2A.response_received"})
_BRIDGE_WORKER_EVENTS = frozenset({"A2A.envelope_received", "A2A.response_signed"})

# Fields allowed to pass through to the client (metadata-only, per ADR-0116/0117).
_ALLOWED_FIELDS = frozenset({
    "task_id", "delegation_id", "turn_id", "origin_id", "endpoint_id",
    "persona", "channel", "chat_key", "reason", "status",
    "engine", "engine_id", "target_engine", "duration_ms",
    "filter_pass_count", "filter_reject_count",
    "sender_instance_id", "instance_id_match",
    "nonce_prefix", "http_status", "ttl_s",
    "relay_count", "exit_code", "tools_called",
    "tool_name", "seq",  # os_turn.tool_called projection fields
    # NBAC
    "network_id", "instance_id", "network_pubkey_fp",
    "peer_genesis_hash_prefix", "genesis_hash_prefix",
})

# ── Helpers ────────────────────────────────────────────────────────────────────

def _project(ev: dict[str, Any]) -> dict[str, Any]:
    """Return a client-safe projection of an audit event."""
    details = ev.get("details") or {}
    return {
        "hash_prefix":  (ev.get("hash") or "")[:8],
        "event_type":   ev.get("event_type", ""),
        "severity":     ev.get("severity", "INFO"),
        "ts":           ev.get("ts"),
        "details":      {k: v for k, v in details.items() if k in _ALLOWED_FIELDS},
    }


# Event types the dual-track view consumes. Streaming the full chain but only
# retaining these keeps memory bounded while guaranteeing COMPLETENESS — a fixed
# trailing byte budget silently dropped a session's events once the chain grew
# past it (os-turns reads the chain in full, so a truncating dual-track was both
# incomplete and inconsistent with the single-chain view).
_RELEVANT_TYPES = (
    _OS_EVENTS | _WORKER_EVENTS | _BRIDGE_OS_EVENTS | _BRIDGE_WORKER_EVENTS
    | {"chain.genesis"}
)


def _read_audit(audit_path: Path) -> list[dict[str, Any]]:
    """Read the FULL audit.jsonl, retaining only dual-track-relevant events.

    The whole file is scanned (so no session's events are ever truncated away),
    but only events whose ``event_type`` is in ``_RELEVANT_TYPES`` are kept in
    memory — bounding footprint regardless of chain size. Lines are parsed
    individually so a single corrupt line never aborts the read.
    """
    if not audit_path.exists():
        return []
    events: list[dict[str, Any]] = []
    try:
        with audit_path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                # Cheap pre-filter: skip lines that can't be a relevant event
                # before paying for json.loads on the whole chain.
                if "event_type" not in line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("event_type") in _RELEVANT_TYPES:
                    events.append(ev)
    except OSError:
        pass
    return events


def _read_all_events(tid: str) -> list[dict[str, Any]]:
    """Read events from all three audit chain files, deduplicated by inode."""
    paths = [
        _forge_paths.corvin_home() / "global" / "forge" / "audit.jsonl",
        _forge_paths.tenant_global_dir(tid) / "forge" / "audit.jsonl",
        _forge_paths.tenant_global_dir(tid) / "audit.jsonl",
    ]
    seen_inodes: set[int] = set()
    result: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            continue
        try:
            st = path.stat()
            inode = (st.st_dev, st.st_ino)
        except OSError:
            continue
        if inode in seen_inodes:
            continue
        seen_inodes.add(inode)
        result.extend(_read_audit(path))
    return result


def _verify_all_chains(tid: str) -> bool:
    """Real hash-chain verification across the same audit files
    ``_read_all_events`` reads from.

    The "chain DNA ✓ / chain DNA ✗" badge this endpoint feeds was previously
    inferred purely by scanning for an ``A2A.chain_dna_verified`` /
    ``A2A.chain_dna_mismatch`` event-type STRING already present in the raw
    JSONL — a linear scan of file content, not a signature/hash
    recomputation. Anyone able to write a line into ``audit.jsonl`` (or
    delete the mismatch line) could forge that badge (adversarial review
    finding). This performs the real, independent hash-chain walk
    (``security_events.verify_chain``) the codebase already has, so the
    route can additionally report whether the underlying chain itself is
    intact — a genesis_match reading pulled from a broken/tampered chain is
    not trustworthy regardless of which event-type string happens to be in it.
    """
    try:
        from forge import security_events as _sec  # type: ignore[import-untyped]
    except Exception:
        return False
    paths = [
        _forge_paths.corvin_home() / "global" / "forge" / "audit.jsonl",
        _forge_paths.tenant_global_dir(tid) / "forge" / "audit.jsonl",
        _forge_paths.tenant_global_dir(tid) / "audit.jsonl",
    ]
    seen_inodes: set[int] = set()
    for path in paths:
        if not path.exists():
            continue
        try:
            st = path.stat()
            inode = (st.st_dev, st.st_ino)
        except OSError:
            continue
        if inode in seen_inodes:
            continue
        seen_inodes.add(inode)
        try:
            ok, _problems = _sec.verify_chain(path)
        except Exception:
            return False
        if not ok:
            return False
    return True


def _delegation_key(ev: dict[str, Any]) -> str | None:
    """Return the delegation/task key for an event, or None."""
    details = ev.get("details") or {}
    # OS-side events use delegation_id; A2A/worker events use task_id.
    return details.get("delegation_id") or details.get("task_id") or None


# ── Route ──────────────────────────────────────────────────────────────────────

@router.get("/chat/sessions/{sid}/chain-dual-track")
def get_chain_dual_track(
    sid: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Return delegation groups reconstructed from audit.jsonl for ADR-0118 dual-track view.

    Response shape::

        {
          "session_id":    "discord:12345",
          "genesis":       {hash_prefix, network_id, instance_id} | null,
          "delegations":   [{
              "delegation_id":  "uuid4",
              "engine":         "hermes",
              "os_events":      [{hash_prefix, event_type, severity, ts, details}, ...],
              "worker_events":  [{...}, ...],
              "genesis_match":  true | null,
          }, ...],
          "os_only_events": [{...}, ...],
          "chain_verified": true | false,
          "ts": float,
        }
    """
    tid = rec.tenant_id
    all_events = _read_all_events(tid)

    # ── Genesis block ──────────────────────────────────────────────────────────
    genesis: dict[str, Any] | None = None
    for ev in all_events:
        if ev.get("event_type") == "chain.genesis":
            det = ev.get("details") or {}
            genesis = {
                "hash_prefix":      (ev.get("hash") or "")[:8],
                "network_id":       det.get("network_id", ""),
                "instance_id":      det.get("instance_id", ""),
                "network_pubkey_fp": (det.get("network_pubkey_fp") or "")[:16],
            }
            break

    # ── Session filter ─────────────────────────────────────────────────────────
    # sid format: "<channel>:<chat_key>" (e.g. "discord:1502103856740302964")
    # or bare chat_key for web console sessions (channel="web").
    if ":" in sid:
        filter_channel, filter_chat_key = sid.split(":", 1)
    else:
        filter_channel, filter_chat_key = "web", sid

    def _matches_session(ev: dict[str, Any]) -> bool:
        det = ev.get("details") or {}
        ck = det.get("chat_key")
        if not ck:
            return False
        # Exact bare match — Discord sessions store chat_key without channel prefix.
        if ck == filter_chat_key:
            return True
        # Full sid match (e.g. "web:abc123" stored verbatim as chat_key).
        if ck == sid:
            return True
        # Bridge events may prepend the channel to chat_key, yielding
        # "web:abc123" or even "web:web:abc123" (when chat_key already starts
        # with channel).  Strip exactly one occurrence of the channel prefix
        # to recover the bare key, then compare.
        prefix = filter_channel + ":"
        if ck.startswith(prefix):
            ck_bare = ck[len(prefix):]
            if ck_bare == filter_chat_key or ck_bare == sid:
                return True
        return False

    # ── Partition events ───────────────────────────────────────────────────────
    # delegation_id → {"os_events": [...], "worker_events": [], "bridge_events": []}
    groups: dict[str, dict[str, list]] = defaultdict(lambda: {
        "os_events": [], "worker_events": [], "bridge_events": []
    })
    os_only: list[dict[str, Any]] = []

    for ev in all_events:
        et = ev.get("event_type", "")
        if et == "chain.genesis":
            continue

        key = _delegation_key(ev)

        if et in _OS_EVENTS:
            if not _matches_session(ev):
                continue
            if key:
                groups[key]["os_events"].append(ev)
            else:
                os_only.append(ev)

        elif et in _BRIDGE_OS_EVENTS:
            if not _matches_session(ev):
                continue
            if key:
                groups[key]["os_events"].append(ev)

        elif et in _WORKER_EVENTS:
            # Worker-side events may or may not have channel/chat_key.
            # Accept if they match session OR if a matching delegation group
            # already exists (same task_id seen from OS side).
            if key and (key in groups or _matches_session(ev)):
                groups[key]["worker_events"].append(ev)

        # Other events (console.*, audit.*, etc.) — skip.

    # ── Build response ─────────────────────────────────────────────────────────
    delegations: list[dict[str, Any]] = []
    for del_id, grp in groups.items():
        os_evts = sorted(grp["os_events"], key=lambda e: e.get("ts") or 0.0)
        wk_evts = sorted(grp["worker_events"], key=lambda e: e.get("ts") or 0.0)

        # Infer engine from engine_spawned or delegation.started
        engine = "unknown"
        for e in wk_evts:
            if e.get("event_type") == "A2A.engine_spawned":
                engine = (e.get("details") or {}).get("engine_id", "unknown")
                break
        if engine == "unknown":
            for e in os_evts:
                if e.get("event_type") == "delegation.started":
                    engine = (e.get("details") or {}).get("target_engine", "unknown")
                    break

        # Infer genesis DNA match from A2A.chain_dna_verified present in worker events
        genesis_match: bool | None = None
        for e in wk_evts:
            if e.get("event_type") == "A2A.chain_dna_verified":
                genesis_match = True
                break
            if e.get("event_type") == "A2A.chain_dna_mismatch":
                genesis_match = False
                break

        delegations.append({
            "delegation_id": del_id,
            "engine":        engine,
            "genesis_match": genesis_match,
            "os_events":     [_project(e) for e in os_evts],
            "worker_events": [_project(e) for e in wk_evts],
        })

    # Sort delegations by timestamp of their first OS event
    delegations.sort(key=lambda d: (
        d["os_events"][0]["ts"] if d["os_events"] else
        d["worker_events"][0]["ts"] if d["worker_events"] else 0
    ))

    return {
        "session_id":    sid,
        "genesis":       genesis,
        "delegations":   delegations,
        "os_only_events": [_project(e) for e in sorted(os_only, key=lambda e: e.get("ts") or 0.0)],
        # Real hash-chain verification (see _verify_all_chains docstring) —
        # qualifies every delegation's genesis_match reading above: a
        # genesis_match pulled from a broken/tampered chain is not
        # trustworthy regardless of which event-type string is in it.
        "chain_verified": _verify_all_chains(tid),
        "ts":            time.time(),
    }
