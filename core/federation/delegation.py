"""Cross-instance agent selection, task delegation and trace (ADR-2232, CONCEPT-0097 Phases 3-4).

Selection ranks local agents (``LocalAgentRegistry``) and fresh peer agents
(``PeerCatalog``) for one capability. Delegation sends a signed
``federation={"v": 1, "op": "task", ...}`` TaskEnvelope to ONE peer agent;
the peer runs it through its unchanged A2A task path (audit-first, L34/L35/
L44, tool policy, result filter).

Audit-first: ``federation.task_delegated`` is written to this tenant's chain
BEFORE the envelope leaves; if that write fails nothing is sent.

Trace (Phase 4): every delegation appends metadata-only records to
``<tenant>/global/federation/delegations.jsonl`` — ids, peer instance,
agent address, status and the two ADR-0116 chain anchors of the exchange
(our chain tail sent in the envelope, the peer's tail from its signed
response). ``trace()`` rebuilds the hop tree from ``parent_task_id`` links,
so each hop this installation took part in is cited in BOTH chains by hash.
Hops between two OTHER peers are not visible here — by design: no peer's
audit chain is ever queried remotely.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from core.federation.local_agent import LocalAgentRegistry, _VALID_CAPABILITIES
from core.federation.peer_catalog import CATALOG_TTL_S, PeerCatalog, agent_address, default_sender
from core.federation.protocol import MAX_HOPS, closed_reason, closed_status, valid_instance_id
from core.paths import tenant_home
from core.tenants.validation import validate_tenant_id

_LEDGER_RELATIVE_PATH = "global/federation/delegations.jsonl"


class DelegationError(ValueError):
    """The delegation request was invalid or could not be routed."""


@dataclass(frozen=True)
class Candidate:
    scope: str                 # "local" | "peer"
    agent_id: str
    address: str
    model: str
    capabilities: tuple[str, ...]
    cost_per_task_usd: float
    endpoint_id: str | None = None
    peer_instance_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["capabilities"] = list(self.capabilities)
        return d


def _local_instance_id() -> str:
    try:
        from instance_identity import get_instance_id  # type: ignore[import-not-found]
    except ImportError:
        import sys
        shared = Path(__file__).resolve().parents[2] / "corvin_operator" / "bridges" / "shared"
        if str(shared) not in sys.path:
            sys.path.insert(0, str(shared))
        try:
            from instance_identity import get_instance_id  # type: ignore[import-not-found]
        except ImportError:
            return "local"
    try:
        return get_instance_id() or "local"
    except Exception:  # noqa: BLE001
        return "local"


def rank_candidates(
    tenant_id: str, capability: str, *, model: str | None = None,
    include_local: bool = True, include_peers: bool = True,
    prefer_local: bool = True, peer_max_age_s: float = CATALOG_TTL_S,
) -> list[Candidate]:
    if capability not in _VALID_CAPABILITIES:
        raise DelegationError(f"unknown capability: {capability}")
    out: list[Candidate] = []
    if include_local:
        iid = _local_instance_id()
        for a in LocalAgentRegistry(tenant_id).list_agents_by_capability(capability):
            out.append(Candidate(
                scope="local", agent_id=a.agent_id, address=agent_address(iid, a.agent_id),
                model=a.model, capabilities=tuple(sorted(a.capabilities)),
                cost_per_task_usd=a.cost_per_task_usd))
    if include_peers:
        for p in PeerCatalog(tenant_id).agents(max_age_s=peer_max_age_s):
            if capability in p.capabilities:
                out.append(Candidate(
                    scope="peer", agent_id=p.agent_id, address=p.address, model=p.model,
                    capabilities=tuple(sorted(p.capabilities)),
                    cost_per_task_usd=p.cost_per_task_usd,
                    endpoint_id=p.endpoint_id, peer_instance_id=p.peer_instance_id))

    def _key(c: Candidate) -> tuple:
        return (
            0 if (prefer_local and c.scope == "local") else 1,
            0 if (model is None or c.model == model) else 1,
            c.cost_per_task_usd,
            c.address,
        )

    return sorted(out, key=_key)


@dataclass
class DelegationResult:
    task_id: str
    ok: bool
    status: str
    endpoint_id: str
    peer_instance_id: str
    agent_id: str | None
    address: str | None
    data: dict = field(default_factory=dict)
    error: str | None = None
    duration_ms: int = 0
    our_chain_tail: str = ""
    peer_chain_tail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DelegationLedger:
    def __init__(self, tenant_id: str, corvin_home_override: Path | None = None):
        self.tenant_id = validate_tenant_id(tenant_id)
        base = (corvin_home_override / "tenants" / self.tenant_id) if corvin_home_override \
            else tenant_home(self.tenant_id)
        self.path = base / _LEDGER_RELATIVE_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    def append(self, record: dict) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def records(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict) and isinstance(rec.get("task_id"), str):
                    out.append(rec)
        return out

    def latest_by_task(self) -> dict[str, dict]:
        merged: dict[str, dict] = {}
        for rec in self.records():
            merged.setdefault(rec["task_id"], {}).update(rec)
        return merged


def delegate(
    tenant_id: str, *, instruction: str, capability: str | None = None,
    endpoint_id: str | None = None, agent_id: str | None = None,
    parent_task_id: str | None = None, origin_agent_id: str | None = None,
    hop: int = 0, ttl_s: int | None = None, timeout_s: int | None = None,
    task_id: str | None = None, sender: Any = None,
) -> DelegationResult:
    """Send one task to one peer agent. Raises DelegationError on bad input."""
    from core.federation import audit as federation_audit

    tenant_id = validate_tenant_id(tenant_id)
    if not (instruction or "").strip():
        raise DelegationError("instruction must not be empty")
    if capability is not None and capability not in _VALID_CAPABILITIES:
        raise DelegationError(f"unknown capability: {capability}")
    if isinstance(hop, bool) or not isinstance(hop, int) or hop < 0:
        raise DelegationError("hop must be a non-negative integer")
    if hop > MAX_HOPS:
        raise DelegationError(f"hop limit exceeded (max {MAX_HOPS})")

    peer_iid = ""
    if endpoint_id is None:
        if capability is None:
            raise DelegationError("either endpoint_id or capability is required")
        peers = rank_candidates(tenant_id, capability, include_local=False)
        if agent_id is not None:
            peers = [c for c in peers if c.agent_id == agent_id]
        if not peers:
            raise DelegationError("no fresh peer agent offers this capability — refresh a peer catalog first")
        endpoint_id, agent_id, peer_iid = peers[0].endpoint_id, peers[0].agent_id, peers[0].peer_instance_id or ""
    elif agent_id is None and capability is None:
        raise DelegationError("agent_id or capability is required")
    if not peer_iid:
        for p in PeerCatalog(tenant_id).agents(max_age_s=None):
            if p.endpoint_id == endpoint_id:
                peer_iid = p.peer_instance_id
                break

    task_id = task_id or str(uuid.uuid4())
    fed: dict[str, Any] = {"v": 1, "op": "task", "hop": hop}
    if agent_id is not None:
        fed["target_agent_id"] = agent_id
    if capability is not None:
        fed["capability"] = capability
    if parent_task_id is not None:
        fed["parent_task_id"] = parent_task_id
    if origin_agent_id is not None:
        fed["origin_agent_id"] = origin_agent_id

    # Audit-first: no chained record → nothing leaves this installation.
    federation_audit.emit(
        "federation.task_delegated", tenant_id=tenant_id, task_id=task_id,
        endpoint_id=endpoint_id, peer_instance_id=peer_iid, agent_id=agent_id or "",
        capability=capability or "", hop=hop, parent_task_id=parent_task_id or "",
    )
    ledger = DelegationLedger(tenant_id)
    ledger.append({
        "task_id": task_id, "parent_task_id": parent_task_id, "hop": hop,
        "endpoint_id": endpoint_id, "peer_instance_id": peer_iid,
        "agent_id": agent_id, "capability": capability, "origin_agent_id": origin_agent_id,
        "status": "sent", "sent_at": time.time(),
    })

    sender = sender or default_sender()
    res = sender.send(endpoint_id, instruction, task_id=task_id, ttl_s=ttl_s,
                      timeout_s=timeout_s, federation=fed)
    # Peer-supplied strings never reach our chain, ledger or API verbatim
    # (review 2026-10-06, finding 3): closed status, closed reason, else the
    # sender's own closed error category.
    status = closed_status(res.status)
    if res.ok:
        error = None
    elif (res.data or {}).get("reason") is not None:
        error = closed_reason((res.data or {}).get("reason"))
    else:
        error = closed_reason(None, fallback=str(res.error_category or "failed")[:40])
    answered_iid = res.instance_id if valid_instance_id(res.instance_id) else ""
    if res.instance_id and not answered_iid:
        status, error = "error", "instance_id_invalid"
    elif res.ok and answered_iid and peer_iid and answered_iid != peer_iid:
        status, error = "error", "instance_id_mismatch"
    peer_iid = answered_iid or peer_iid
    address = agent_address(peer_iid, agent_id) if (agent_id and peer_iid) else None

    result = DelegationResult(
        task_id=task_id, ok=res.ok and error is None, status=status,
        endpoint_id=endpoint_id, peer_instance_id=peer_iid, agent_id=agent_id,
        address=address, data=dict(res.data or {}) if res.ok else {}, error=error,
        duration_ms=int(res.duration_ms or 0),
        our_chain_tail=getattr(res, "our_chain_tail", "") or "",
        peer_chain_tail=getattr(res, "peer_chain_tail", "") or "",
    )
    ledger.append({
        "task_id": task_id, "status": result.status, "ok": result.ok, "error": error,
        "peer_instance_id": peer_iid, "completed_at": time.time(),
        "duration_ms": result.duration_ms,
        "our_chain_tail": result.our_chain_tail, "peer_chain_tail": result.peer_chain_tail,
    })
    try:
        federation_audit.emit(
            "federation.task_result_received", tenant_id=tenant_id, task_id=task_id,
            endpoint_id=endpoint_id, peer_instance_id=peer_iid, agent_id=agent_id or "",
            status=result.status, duration_ms=result.duration_ms,
            our_chain_tail=result.our_chain_tail[:16], peer_chain_tail=result.peer_chain_tail[:16],
        )
    except Exception:  # noqa: BLE001 — the exchange happened; the ledger holds it
        pass
    return result


def trace(tenant_id: str, task_id: str) -> dict[str, Any]:
    """Hop tree around ``task_id`` from this installation's delegation ledger."""
    by_task = DelegationLedger(tenant_id).latest_by_task()
    if task_id not in by_task and not any(r.get("parent_task_id") == task_id for r in by_task.values()):
        raise DelegationError("task not found in this installation's delegation ledger")

    root = task_id
    seen: set[str] = set()
    while root in by_task and by_task[root].get("parent_task_id") and root not in seen:
        seen.add(root)
        parent = by_task[root]["parent_task_id"]
        if parent not in by_task:
            root = parent
            break
        root = parent

    def _node(tid: str, depth: int) -> dict[str, Any]:
        rec = by_task.get(tid, {})
        children = [t for t, r in by_task.items() if r.get("parent_task_id") == tid and t != tid]
        node = {
            "task_id": tid,
            "known_here": tid in by_task,
            "endpoint_id": rec.get("endpoint_id"),
            "peer_instance_id": rec.get("peer_instance_id"),
            "agent_id": rec.get("agent_id"),
            "address": (agent_address(rec["peer_instance_id"], rec["agent_id"])
                        if rec.get("peer_instance_id") and rec.get("agent_id") else None),
            "hop": rec.get("hop"),
            "status": rec.get("status"),
            "error": rec.get("error"),
            "sent_at": rec.get("sent_at"),
            "completed_at": rec.get("completed_at"),
            "anchors": {"our_chain_tail": rec.get("our_chain_tail") or "",
                        "peer_chain_tail": rec.get("peer_chain_tail") or ""},
            "children": [],
        }
        if depth < MAX_HOPS + 2:
            node["children"] = [_node(c, depth + 1) for c in sorted(children)]
        return node

    return {"root_task_id": root, "tree": _node(root, 0)}
