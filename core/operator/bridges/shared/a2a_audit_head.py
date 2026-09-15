"""a2a_audit_head.py — ADR-0141 Tier 4: Audit Chain Transparency.

Two halves of a single advisory mechanism:

  * **Server side** (:func:`build_audit_head`) — expose the local audit chain's
    head hash + event count so a peer can observe that the chain is advancing.
    A fork that removed the audit module produces a structurally empty / stale
    chain. Optionally HMAC-signed with the requesting peer's ``recv_key`` so the
    requester can authenticate the response.

  * **Sender side** (:func:`check_peer_audit_head`) — after an exchange, compare
    the peer's reported head against the last value seen for that endpoint. A
    chain that does not advance across multiple turns is flagged as
    ``a2a.peer_audit_anomaly`` (WARNING).

This tier is **advisory by design** — it never hard-blocks (Tier 1/2 do that).
False positives are possible on a genuinely idle peer, so the anomaly fires only
after repeated non-advancement, never on a single observation.

CI lint: MUST NOT ``import anthropic``.
Audit contract: metadata only — never audit content, never the full chain hash
beyond the head; emit only the allow-listed fields.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import os
from pathlib import Path
from typing import Any

_REMOTE_ENDPOINTS_ENV = "CORVIN_A2A_REMOTE_ENDPOINTS_DIR"


def _corvin_home() -> Path:
    # CORVIN_HOME env → repo marker (<repo>/.corvin) → ~/.corvin, matching
    # forge.paths.corvin_home so A2A audit-head agrees with the rest of the A2A
    # stack when run from a repo checkout without CORVIN_HOME (path-audit #MED2).
    env = os.environ.get("CORVIN_HOME", "")
    if env:
        return Path(env).expanduser()
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / ".corvin_repo").exists() or (parent / "plugins").is_dir():
            return parent / ".corvin"
    return Path.home() / ".corvin"


def _audit_path() -> Path:
    """Resolve the live audit chain path (mirrors the adapter / self_test)."""
    p = os.environ.get("VOICE_AUDIT_PATH")
    if p:
        return Path(p)
    return _corvin_home() / "global" / "forge" / "audit.jsonl"


# ── Server side ─────────────────────────────────────────────────────────────


def read_audit_head(audit_path: "Path | None" = None) -> dict:
    """Return ``{chain_head, event_count, latest_ts}`` for the local chain.

    A missing / empty chain yields ``chain_head=""``, ``event_count=0`` — the
    structural signature of a node with the audit module removed or never run.
    """
    path = audit_path or _audit_path()
    chain_head = ""
    event_count = 0
    latest_ts = 0.0
    try:
        with open(path, "r", encoding="utf-8") as fh:
            last = ""
            for line in fh:
                if line.strip():
                    event_count += 1
                    last = line
            if last:
                try:
                    rec = json.loads(last)
                    chain_head = str(rec.get("hash", ""))
                    latest_ts = float(rec.get("ts", 0) or 0)
                except (ValueError, TypeError):
                    pass
    except OSError:
        pass
    return {"chain_head": chain_head, "event_count": event_count, "latest_ts": latest_ts}


def _origin_recv_key(origin_id: str) -> "bytes | None":
    """Look up an origin's ``recv_key`` (hex) for response HMAC. None if unknown."""
    if not origin_id:
        return None
    try:
        import sys as _sys
        here = str(Path(__file__).resolve().parent)
        if here not in _sys.path:
            _sys.path.insert(0, here)
        from remote_trigger_receiver import OriginRegistry  # type: ignore
        cfg = OriginRegistry().load(origin_id)
        key_hex = cfg.get("recv_key")
        return bytes.fromhex(key_hex) if key_hex else None
    except Exception:
        return None


def build_audit_head(origin_id: "str | None" = None,
                     instance_id: "str | None" = None,
                     audit_path: "Path | None" = None) -> dict:
    """Assemble the ``/v1/a2a/audit-head`` response body.

    When ``origin_id`` resolves to a known origin, the body is HMAC-SHA256-signed
    with that origin's ``recv_key`` so the requesting peer can authenticate it.
    Otherwise ``signature`` is ``""`` (the peer falls back to transport trust).
    """
    if instance_id is None:
        try:
            import sys as _sys
            here = str(Path(__file__).resolve().parent)
            if here not in _sys.path:
                _sys.path.insert(0, here)
            import instance_identity  # type: ignore
            instance_id = instance_identity.get_instance_id()
        except Exception:
            instance_id = ""

    head = read_audit_head(audit_path)
    body = {
        "chain_head": head["chain_head"],
        "event_count": head["event_count"],
        "latest_ts": head["latest_ts"],
        "instance_id": instance_id or "",
    }
    signature = ""
    key = _origin_recv_key(origin_id or "")
    if key is not None:
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=True).encode()
        signature = _hmac.new(key, canonical, hashlib.sha256).hexdigest()
    return {**body, "signature": signature}


# ── Sender side (anomaly detection) ─────────────────────────────────────────


def _peer_state_path() -> Path:
    return _corvin_home() / "global" / "a2a_peer_audit.json"


def _load_peer_state() -> dict:
    try:
        return json.loads(_peer_state_path().read_text("utf-8"))
    except Exception:
        return {}


def _save_peer_state(state: dict) -> None:
    try:
        p = _peer_state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, sort_keys=True, indent=2), "utf-8")
        tmp.chmod(0o600)
        tmp.replace(p)
    except Exception:
        pass


# Fire the anomaly only after this many consecutive non-advancing observations.
_ANOMALY_STREAK = 2


def check_peer_audit_head(endpoint_id: str, head: dict, *, emit: bool = True) -> "dict | None":
    """Compare a peer's reported audit-head against the last value seen.

    Returns an anomaly dict (and emits ``a2a.peer_audit_anomaly`` WARNING) when
    the peer's chain has not advanced across :data:`_ANOMALY_STREAK` consecutive
    observations; otherwise returns None. Advisory — never raises, never blocks.
    """
    if not endpoint_id or not isinstance(head, dict):
        return None
    state = _load_peer_state()
    prev = state.get(endpoint_id) or {}

    cur_count = int(head.get("event_count", 0) or 0)
    cur_head = str(head.get("chain_head", ""))
    prev_count = int(prev.get("event_count", -1))
    prev_head = str(prev.get("chain_head", ""))

    streak = int(prev.get("stall_streak", 0))
    advanced = cur_count > prev_count or (cur_head and cur_head != prev_head)
    if prev_count >= 0 and not advanced:
        streak += 1
    else:
        streak = 0

    state[endpoint_id] = {
        "event_count": cur_count,
        "chain_head": cur_head,
        "instance_id": str(head.get("instance_id", "")),
        "stall_streak": streak,
    }
    _save_peer_state(state)

    if streak >= _ANOMALY_STREAK:
        anomaly = {
            "endpoint_id": endpoint_id,
            "reason": "chain_not_advancing",
            "instance_id_match": str(head.get("instance_id", "")) != "",
            "stall_streak": streak,
        }
        if emit:
            _emit_anomaly(endpoint_id, head)
        return anomaly
    return None


def _emit_anomaly(endpoint_id: str, head: dict) -> None:
    try:
        import sys as _sys
        forge = str(Path(__file__).resolve().parents[2] / "forge")
        if forge not in _sys.path:
            _sys.path.insert(0, forge)
        from forge.security_events import write_event  # type: ignore

        write_event(
            _audit_path(), "a2a.peer_audit_anomaly", severity="WARNING",
            details={
                "endpoint_id": endpoint_id,
                "reason": "chain_not_advancing",
                "instance_id_match": str(head.get("instance_id", "")) != "",
            },
        )
    except Exception:
        pass
