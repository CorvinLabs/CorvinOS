"""Peer Agent Catalog — cross-peer discovery over A2A (ADR-2232, CONCEPT-0097 Phase 2).

Asks a paired peer for its federable agents with a signed
``federation={"v": 1, "op": "catalog"}`` TaskEnvelope over the existing A2A
transport (``RemoteTriggerSender`` — same HMAC, instance pin, relay fallback
and audit as every A2A task), and keeps the answers as append-only snapshots
per tenant: ``<tenant>/global/federation/peer_agents.jsonl``.

A peer's catalog is UNTRUSTED input. It is accepted only from a verified
response whose ``instance_id`` matched the endpoint pin, and every entry is
re-validated here (id charset, known capabilities, bounded numbers) — a
malformed entry is dropped, never stored. A peer's agent is addressed as
``agent://<peer instance_id>/<agent_id>`` (ADR-2231): attributed to that
peer, not independently verified.
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.federation.local_agent import _VALID_CAPABILITIES
from core.federation.protocol import closed_reason, valid_instance_id
from core.paths import tenant_home
from core.tenants.validation import validate_tenant_id

_LEDGER_RELATIVE_PATH = "global/federation/peer_agents.jsonl"
CATALOG_TTL_S = 300.0
_MAX_AGENTS_PER_PEER = 64
_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")
CATALOG_INSTRUCTION = "federation.catalog"


class PeerCatalogError(RuntimeError):
    """A catalog fetch failed; ``reason`` is a closed, operator-readable token."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def agent_address(instance_id: str, agent_id: str) -> str:
    return f"agent://{instance_id}/{agent_id}"


def _is_id(value: Any, max_len: int) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= max_len
            and all(c in _ID_CHARS for c in value))


@dataclass(frozen=True)
class PeerAgent:
    endpoint_id: str
    peer_instance_id: str
    agent_id: str
    capabilities: frozenset[str]
    model: str
    max_concurrent: int
    cost_per_task_usd: float
    fetched_at: float

    @property
    def address(self) -> str:
        return agent_address(self.peer_instance_id, self.agent_id)


def sanitize_catalog(raw: Any) -> list[dict]:
    """Keep only well-formed agent entries from an untrusted peer payload."""
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for item in raw[:_MAX_AGENTS_PER_PEER]:
        if not isinstance(item, dict):
            continue
        agent_id = item.get("agent_id")
        caps = item.get("capabilities")
        model = item.get("model")
        max_c = item.get("max_concurrent", 1)
        cost = item.get("cost_per_task_usd", 0.0)
        if not _is_id(agent_id, 128) or agent_id in seen:
            continue
        if not isinstance(caps, list) or not caps or any(c not in _VALID_CAPABILITIES for c in caps):
            continue
        if not isinstance(model, str) or not (0 < len(model) <= 128) or not model.isprintable():
            continue
        if isinstance(max_c, bool) or not isinstance(max_c, int) or not (1 <= max_c <= 1000):
            continue
        if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(cost) or cost < 0:
            continue
        seen.add(agent_id)
        out.append({
            "agent_id": agent_id, "capabilities": sorted(set(caps)), "model": model,
            "max_concurrent": max_c, "cost_per_task_usd": float(cost),
        })
    return out


def default_sender() -> Any:
    try:
        from remote_trigger_sender import RemoteTriggerSender  # type: ignore[import-not-found]
    except ImportError:
        import sys
        shared = Path(__file__).resolve().parents[2] / "corvin_operator" / "bridges" / "shared"
        if str(shared) not in sys.path:
            sys.path.insert(0, str(shared))
        from remote_trigger_sender import RemoteTriggerSender  # type: ignore[import-not-found]
    return RemoteTriggerSender()


class PeerCatalog:
    def __init__(self, tenant_id: str, corvin_home_override: Path | None = None):
        self.tenant_id = validate_tenant_id(tenant_id)
        base = (corvin_home_override / "tenants" / self.tenant_id) if corvin_home_override \
            else tenant_home(self.tenant_id)
        self._ledger_path = base / _LEDGER_RELATIVE_PATH
        self._ledger_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _snapshots(self) -> dict[str, dict]:
        latest: dict[str, dict] = {}
        if not self._ledger_path.exists():
            return latest
        with self._ledger_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict) and isinstance(rec.get("endpoint_id"), str):
                    latest[rec["endpoint_id"]] = rec
        return latest

    def _append(self, record: dict) -> None:
        with self._ledger_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
        try:
            self._ledger_path.chmod(0o600)
        except OSError:
            pass

    def refresh(self, endpoint_id: str, *, sender: Any = None, timeout_s: int = 30) -> list[PeerAgent]:
        from core.federation import audit as federation_audit

        sender = sender or default_sender()
        res = sender.send(
            endpoint_id, CATALOG_INSTRUCTION, result_schema={}, ttl_s=60,
            timeout_s=timeout_s, federation={"v": 1, "op": "catalog"},
        )
        status = "ok"
        reason = None
        if not res.ok:
            status = "rejected" if res.status == "rejected" else "error"
            peer_reason = (res.data or {}).get("reason")
            reason = (closed_reason(peer_reason) if peer_reason is not None
                      else str(res.error_category or "peer_unreachable")[:40])
        elif not res.instance_id_match or not valid_instance_id(res.instance_id):
            status, reason = "error", "instance_id_mismatch"
        agents = sanitize_catalog((res.data or {}).get("agents")) if status == "ok" else []
        federation_audit.emit(
            "federation.catalog_fetched", tenant_id=self.tenant_id,
            endpoint_id=endpoint_id,
            peer_instance_id=res.instance_id if valid_instance_id(res.instance_id) else "",
            agent_count=len(agents), status=status,
        )
        if status != "ok":
            raise PeerCatalogError(reason or "catalog_failed")
        now = time.time()
        self._append({
            "endpoint_id": endpoint_id, "peer_instance_id": res.instance_id,
            "fetched_at": now, "agents": agents,
        })
        return [self._to_agent(endpoint_id, res.instance_id, now, a) for a in agents]

    @staticmethod
    def _to_agent(endpoint_id: str, iid: str, fetched_at: float, a: dict) -> PeerAgent:
        return PeerAgent(
            endpoint_id=endpoint_id, peer_instance_id=iid, agent_id=a["agent_id"],
            capabilities=frozenset(a["capabilities"]), model=a["model"],
            max_concurrent=a["max_concurrent"], cost_per_task_usd=a["cost_per_task_usd"],
            fetched_at=fetched_at,
        )

    def agents(self, *, max_age_s: float | None = CATALOG_TTL_S) -> list[PeerAgent]:
        now = time.time()
        out: list[PeerAgent] = []
        for endpoint_id, rec in sorted(self._snapshots().items()):
            fetched_at = float(rec.get("fetched_at") or 0.0)
            if max_age_s is not None and now - fetched_at > max_age_s:
                continue
            for a in sanitize_catalog(rec.get("agents")):
                out.append(self._to_agent(endpoint_id, str(rec.get("peer_instance_id") or ""),
                                          fetched_at, a))
        return out

    def peers(self) -> list[dict]:
        now = time.time()
        return [
            {"endpoint_id": eid, "peer_instance_id": rec.get("peer_instance_id") or "",
             "fetched_at": rec.get("fetched_at"),
             "agent_count": len(sanitize_catalog(rec.get("agents"))),
             "fresh": now - float(rec.get("fetched_at") or 0.0) <= CATALOG_TTL_S}
            for eid, rec in sorted(self._snapshots().items())
        ]
