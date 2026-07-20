"""Layer 38 — Remote-trigger log view.

Read-only console endpoint that surfaces the last N ``A2A.*`` events
from the tenant's audit chain, grouped by ``origin_id`` (inbound) or
``endpoint_id`` (outbound). Useful for operators to verify that A2A
exchanges are landing in the chain with the expected shape.

ADR-0048 M3 milestone. Same backing file as :mod:`audit_tail` — we
project to a curated shape and do NOT expose ``prev_hash`` / ``hash``
internals (chain integrity surface).

The route returns JSON (no HTML rendering — the SPA renders the table).

Defaults:

  * ``limit=50``  — last 50 events overall, before grouping
  * ``per_peer=10`` — per-origin/endpoint cap after grouping

Filters:

  * ``origin_id`` — restrict to one inbound origin
  * ``endpoint_id`` — restrict to one outbound endpoint
  * ``severity``  — INFO|WARNING|CRITICAL
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from .. import auth as session_auth
from ..deps import require_session

from .. import _bootstrap
_forge_paths = _bootstrap.forge_paths
_REPO = _bootstrap._REPO

import os as _os
import sys as _sys

# Shared label sanitizer (A4 defense-in-depth, 2026-07-20): labels stored
# BEFORE the ingestion sanitizer existed (raw peer-token labels) must not
# reach the UI with ANSI escapes / bidi overrides — sanitize read-side too.
_BRIDGES_SHARED = _REPO / "operator" / "bridges" / "shared"
if str(_BRIDGES_SHARED) not in _sys.path:
    _sys.path.insert(0, str(_BRIDGES_SHARED))
try:
    from a2a_friendship import sanitize_label as _sanitize_label  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover — minimal fallback, never fail-open
    def _sanitize_label(raw: object, max_len: int = 64) -> str:
        s = str(raw)
        return "".join(ch for ch in s if ch.isprintable()).strip()[:max_len]


def _label_out(cfg: dict) -> str | None:
    """Read-side sanitized label for API/UI delivery (None stays None)."""
    raw = cfg.get("label")
    if raw is None:
        return None
    return _sanitize_label(raw, max_len=80) or None

_COWORK_ORIGINS_DEFAULT = _REPO / "operator" / "cowork" / "remote_origins"
_COWORK_ENDPOINTS_DEFAULT = _REPO / "operator" / "cowork" / "remote_endpoints"


def _origins_dir() -> Path:
    env = _os.environ.get("REMOTE_ORIGINS_DIR")
    return Path(env) if env else _COWORK_ORIGINS_DEFAULT


def _endpoints_dir() -> Path:
    env = _os.environ.get("REMOTE_ENDPOINTS_DIR")
    return Path(env) if env else _COWORK_ENDPOINTS_DEFAULT


router = APIRouter()


_A2A_EVENT_TYPES = (
    "A2A.envelope_received",
    "A2A.envelope_sent",
    "A2A.engine_spawned",
    "A2A.result_filtered",
    "A2A.response_signed",
    "A2A.response_received",
    "A2A.request_rejected",
    "A2A.response_rejected",
    # ADR-0063 — invite lifecycle
    "A2A.invite_created",
    "A2A.invite_accepted",
    "A2A.invite_revoked",
)


def _tail_lines(path: Path, *, byte_budget: int = 512 * 1024) -> list[str]:
    """Read trailing bytes; same algorithm as audit_tail.py.

    A2A activity is typically a small fraction of total chain volume,
    so 512 KB is plenty to surface the last 50 A2A events even on a
    busy chain.
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
    lines = text.splitlines()
    if start > 0 and lines:
        lines = lines[1:]
    return lines


def _project_event(rec: dict[str, Any]) -> dict[str, Any]:
    """Curated projection — never expose chain-integrity internals."""
    details = rec.get("details", {}) or {}
    return {
        "ts":            rec.get("ts"),
        "event_type":    rec.get("event_type", ""),
        "severity":      rec.get("severity", "INFO"),
        "task_id":       details.get("task_id"),
        "origin_id":     details.get("origin_id"),
        "endpoint_id":   details.get("endpoint_id"),
        "persona":       details.get("persona"),
        "engine_id":     details.get("engine_id"),
        "status":        details.get("status"),
        "reason":        details.get("reason"),
        "duration_ms":   details.get("duration_ms"),
        "nonce_prefix":  details.get("nonce_prefix"),
        "ttl_s":         details.get("ttl_s"),
        "sender_instance_id": details.get("sender_instance_id"),
        "instance_id_match":  details.get("instance_id_match"),
        "filter_pass_count":  details.get("filter_pass_count"),
        "filter_reject_count": details.get("filter_reject_count"),
    }


def _load_a2a_events(
    chain: Path,
    *,
    severity_filter: str | None,
    origin_filter: str | None,
    endpoint_filter: str | None,
    byte_budget: int = 512 * 1024,
) -> list[dict[str, Any]]:
    lines = _tail_lines(chain, byte_budget=byte_budget)
    out: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        et = rec.get("event_type", "")
        if et not in _A2A_EVENT_TYPES:
            continue
        sev = rec.get("severity", "INFO")
        if severity_filter and sev != severity_filter:
            continue
        details = rec.get("details", {}) or {}
        if origin_filter and details.get("origin_id") != origin_filter:
            continue
        if endpoint_filter and details.get("endpoint_id") != endpoint_filter:
            continue
        out.append(_project_event(rec))
    return out


def _group_by_peer(events: list[dict[str, Any]],
                   per_peer_cap: int) -> dict[str, list[dict[str, Any]]]:
    """Bucket events by origin_id (incoming) or endpoint_id (outbound)."""
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ev in events:
        peer = ev.get("origin_id") or ev.get("endpoint_id") or "<unknown>"
        if len(buckets[peer]) < per_peer_cap:
            buckets[peer].append(ev)
    return dict(buckets)


@router.get("/remote-trigger/log")
def remote_trigger_log(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    limit: int = Query(default=50, ge=1, le=500),
    per_peer: int = Query(default=10, ge=1, le=50),
    origin_id: str | None = Query(default=None,
                                  description="filter by inbound origin"),
    endpoint_id: str | None = Query(default=None,
                                    description="filter by outbound endpoint"),
    severity: str | None = Query(default=None,
                                 description="INFO|WARNING|CRITICAL"),
) -> dict[str, Any]:
    """Return the last A2A events from the tenant's chain.

    Output shape::

        {
          "tenant_id": "...",
          "ts": 1234567890.0,
          "count": 42,
          "events": [...],       # newest-first, flat list, capped at `limit`
          "by_peer": {           # same events bucketed, capped per peer
              "cloud.corvin.eu": [...],
              "peer-foo": [...]
          }
        }
    """
    tid = rec.tenant_id
    chain = _forge_paths.tenant_global_dir(tid) / "forge" / "audit.jsonl"
    if not chain.exists():
        return {
            "tenant_id": tid, "ts": time.time(),
            "count": 0, "events": [], "by_peer": {},
        }

    events = _load_a2a_events(
        chain,
        severity_filter=severity,
        origin_filter=origin_id,
        endpoint_filter=endpoint_id,
    )
    events.sort(key=lambda r: r.get("ts") or 0.0, reverse=True)
    flat = events[:limit]
    by_peer = _group_by_peer(events, per_peer_cap=per_peer)

    return {
        "tenant_id":     tid,
        "ts":            time.time(),
        "count":         len(flat),
        "chain_size_b":  chain.stat().st_size,
        "events":        flat,
        "by_peer":       by_peer,
    }


@router.get("/remote-trigger/origins")
def remote_trigger_origins(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """List configured inbound origins (file presence + enabled flag).

    Does NOT expose hmac_key / recv_key — only metadata visible to
    operators planning a re-key.
    """
    _ = rec  # used only via Depends() side effects
    origins_dir = _origins_dir()
    out: list[dict[str, Any]] = []
    if origins_dir.exists():
        for entry in sorted(origins_dir.iterdir()):
            if not entry.is_file() or entry.suffix != ".json":
                continue
            try:
                cfg = json.loads(entry.read_text("utf-8"))
            except Exception:
                out.append({
                    "origin_id": entry.stem, "enabled": False,
                    "error": "unreadable",
                })
                continue
            out.append({
                "origin_id":  cfg.get("origin_id", entry.stem),
                "enabled":    bool(cfg.get("enabled", False)),
                "spawn_worker": bool(cfg.get("spawn_worker", False)),
                "max_ttl_s":  cfg.get("max_ttl_s"),
                "allowed_personas": cfg.get("allowed_personas", []),
                "state":      cfg.get("state") or ("ACTIVE" if cfg.get("enabled", False) else None),
                "label":      _label_out(cfg),
                "_friendship": bool(cfg.get("_friendship", False)),
                # M2 tool policy — deny-by-default opt-ins (ADR-0144)
                "allow_bash":        bool(cfg.get("allow_bash", False)),
                "allow_network":     bool(cfg.get("allow_network", False)),
                "allow_read_files":  bool(cfg.get("allow_read_files", False)),
                "allow_write_files": bool(cfg.get("allow_write_files", False)),
                "allow_subagents":   bool(cfg.get("allow_subagents", False)),
            })
    return {"ts": time.time(), "count": len(out), "origins": out}


@router.get("/remote-trigger/endpoints")
def remote_trigger_endpoints(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """List configured outbound endpoints (no secrets exposed)."""
    _ = rec
    endpoints_dir = _endpoints_dir()
    out: list[dict[str, Any]] = []
    if endpoints_dir.exists():
        for entry in sorted(endpoints_dir.iterdir()):
            if not entry.is_file() or entry.suffix != ".json":
                continue
            try:
                cfg = json.loads(entry.read_text("utf-8"))
            except Exception:
                out.append({
                    "endpoint_id": entry.stem, "enabled": False,
                    "error": "unreadable",
                })
                continue
            out.append({
                "endpoint_id":     cfg.get("endpoint_id", entry.stem),
                "url":             cfg.get("url") or None,
                "instance_id_pin": cfg.get("instance_id", ""),
                "enabled":         bool(cfg.get("enabled", False)),
                "default_ttl_s":   cfg.get("default_ttl_s"),
                "state":           cfg.get("state") or ("ACTIVE" if cfg.get("enabled", False) else None),
                "label":           _label_out(cfg),
                "_friendship":     bool(cfg.get("_friendship", False)),
            })
    return {"ts": time.time(), "count": len(out), "endpoints": out}
