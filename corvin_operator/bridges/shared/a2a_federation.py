"""A2A federation — receiver side of the optional ``federation`` envelope field (ADR-2232).

Called by ``RemoteTriggerReceiver._receive_federated`` only AFTER the
envelope passed HMAC, origin trust, time-window and replay validation, so
nothing here re-derives trust and an unauthenticated caller can never reach
any branch below (no catalog or agent-name oracle).

Two operations:

* ``catalog`` — control plane. Answers with this installation's federable
  local agents (``core.federation.local_agent``). No worker is spawned.
* ``task``    — selects one local agent (explicit ``target_agent_id`` or the
  cheapest agent offering ``capability``), reserves one of its
  ``max_concurrent`` slots, and lets the receiver run its UNCHANGED task path
  (audit-first, L34/L35/L44 gates, tool policy, result filter) with that
  agent's model. ``finish()`` releases the slot and audits the outcome on
  every return path.

Agent identity (ADR-2231): an agent is addressed as
``agent://<receiver instance_id>/<agent_id>``. ``agent_id`` is a name the
receiving installation chose, not a credential — the trust anchor is the
pairing (HMAC key) and the instance pin that already sign every response.
Everything a peer says about its own agents is attributed to that peer.

Only agents whose ``engine_type`` is ``claude_code`` are federable: the A2A
worker's L34/L35 gates, MCP isolation and tool-deny policy are written for
that engine only, and serving another engine over A2A would bypass them.

At-most-once: every accepted federated task is remembered (memory, 1 h) by
(tenant, origin_id, task_id) from the moment it is ACCEPTED — not when it
finishes. A second envelope with the same task_id, whether the first run is
still going or done, is refused (``federation_duplicate_task``) and never runs
again. The answer is deliberately not cached and re-sent: a re-send would
skip the audit-first write, the consent re-check and the chain gate that the
first delivery went through (review 2026-10-06, findings 1+2). Nothing about
a task's content is stored by this module.

Only agents the operator explicitly marked ``federable`` (ADR-2230 registry)
AND that run on ``claude_code`` are offered or runnable, and only for an
origin that may run workers at all (``spawn_worker``).

MUST NOT import the anthropic SDK (CI AST lint enforces this).
"""
from __future__ import annotations

import collections
import hashlib
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

FEDERABLE_ENGINE = "claude_code"
_SEEN_TTL_S = 3600.0
_SEEN_MAX_PER_ORIGIN = 2000
_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")



def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


if str(_repo_root()) not in sys.path:
    sys.path.insert(0, str(_repo_root()))
from core.federation.protocol import (  # noqa: E402
    FEDERATION_VERSION, MAX_HOPS, PUBLIC_REASONS,
)


def _registry(tenant_id: str):
    root = str(_repo_root())
    if root not in sys.path:
        sys.path.insert(0, root)
    from core.federation.local_agent import LocalAgentRegistry  # noqa: PLC0415
    return LocalAgentRegistry(tenant_id)


def _valid_capabilities() -> frozenset[str]:
    root = str(_repo_root())
    if root not in sys.path:
        sys.path.insert(0, root)
    from core.federation.local_agent import _VALID_CAPABILITIES  # noqa: PLC0415
    return _VALID_CAPABILITIES


def _is_id(value: Any, max_len: int) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= max_len
            and all(c in _ID_CHARS for c in value))


# ── Field semantics ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class FederationRequest:
    op: str
    target_agent_id: str | None = None
    capability: str | None = None
    hop: int = 0
    parent_task_id: str | None = None
    origin_agent_id: str | None = None


def parse_request(fed: dict) -> tuple[FederationRequest | None, str | None]:
    """(request, None) or (None, public_reason). Never raises."""
    try:
        if fed.get("v") != FEDERATION_VERSION:
            return None, "federation_unsupported_version"
        op = fed.get("op")
        allowed_keys = {"v", "op"} if op == "catalog" else {
            "v", "op", "target_agent_id", "capability", "hop",
            "parent_task_id", "origin_agent_id"}
        if op not in ("catalog", "task") or set(fed) - allowed_keys:
            return None, "federation_bad_request"
        if op == "catalog":
            return FederationRequest(op="catalog"), None
        target = fed.get("target_agent_id")
        capability = fed.get("capability")
        hop = fed.get("hop", 0)
        parent = fed.get("parent_task_id")
        origin_agent = fed.get("origin_agent_id")
        if target is not None and not _is_id(target, 128):
            return None, "federation_bad_request"
        if capability is not None and capability not in _valid_capabilities():
            return None, "federation_bad_request"
        if target is None and capability is None:
            return None, "federation_bad_request"
        if isinstance(hop, bool) or not isinstance(hop, int) or hop < 0:
            return None, "federation_bad_request"
        if hop > MAX_HOPS:
            return None, "federation_hop_limit"
        if parent is not None and not _is_id(parent, 256):
            return None, "federation_bad_request"
        if origin_agent is not None and not _is_id(origin_agent, 128):
            return None, "federation_bad_request"
        return FederationRequest(
            op="task", target_agent_id=target, capability=capability, hop=hop,
            parent_task_id=parent, origin_agent_id=origin_agent), None
    except Exception:  # noqa: BLE001
        return None, "federation_bad_request"


# ── Catalog ──────────────────────────────────────────────────────────────

def build_catalog(tenant_id: str) -> list[dict]:
    """Federable local agents, metadata only."""
    return [
        {
            "agent_id": a.agent_id,
            "capabilities": sorted(a.capabilities),
            "model": a.model,
            "max_concurrent": a.max_concurrent,
            "cost_per_task_usd": a.cost_per_task_usd,
        }
        for a in _registry(tenant_id).list_agents()
        if _is_federable(a)
    ]


def _is_federable(agent: Any) -> bool:
    return agent.engine_type == FEDERABLE_ENGINE and bool(getattr(agent, "federable", False))


# ── Agent selection + slots ──────────────────────────────────────────────

def select_agent(tenant_id: str, req: FederationRequest) -> tuple[Any, str | None]:
    reg = _registry(tenant_id)
    if req.target_agent_id is not None:
        agent = reg.get(req.target_agent_id)
        if agent is None:
            return None, "federation_unknown_agent"
        if not _is_federable(agent):
            return None, "federation_agent_not_federable"
        if req.capability is not None and req.capability not in agent.capabilities:
            return None, "federation_capability_mismatch"
        return agent, None
    candidates = [a for a in reg.list_agents_by_capability(req.capability)
                  if _is_federable(a)]
    if not candidates:
        return None, "federation_no_agent"
    candidates.sort(key=lambda a: (a.cost_per_task_usd, a.agent_id))
    for agent in candidates:
        if _SLOTS.available(tenant_id, agent.agent_id, agent.max_concurrent):
            return agent, None
    return candidates[0], None  # all busy — acquire() below reports it


class _AgentSlots:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._in_use: dict[tuple[str, str], int] = collections.defaultdict(int)

    def available(self, tenant_id: str, agent_id: str, limit: int) -> bool:
        with self._lock:
            return self._in_use[(tenant_id, agent_id)] < max(1, limit)

    def acquire(self, tenant_id: str, agent_id: str, limit: int) -> bool:
        with self._lock:
            key = (tenant_id, agent_id)
            if self._in_use[key] >= max(1, limit):
                return False
            self._in_use[key] += 1
            return True

    def release(self, tenant_id: str, agent_id: str) -> None:
        with self._lock:
            key = (tenant_id, agent_id)
            if self._in_use[key] > 0:
                self._in_use[key] -= 1
            if self._in_use[key] == 0:
                del self._in_use[key]


_SLOTS = _AgentSlots()


# ── At-most-once (memory only) ───────────────────────────────────────────

class _SeenTasks:
    """Claims per (tenant, origin): one origin can only ever evict its OWN
    oldest claims, never another origin's (review round 2)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[tuple[str, str], "collections.OrderedDict[str, float]"] = {}

    def claim(self, key: tuple) -> bool:
        """True if ``key`` was not seen within the TTL (and claims it now)."""
        tenant_id, origin_id, task_id = key
        now = time.time()
        with self._lock:
            bucket = self._items.setdefault((tenant_id, origin_id), collections.OrderedDict())
            ts = bucket.get(task_id)
            if ts is not None and now - ts <= _SEEN_TTL_S:
                return False
            bucket[task_id] = now
            bucket.move_to_end(task_id)
            while len(bucket) > _SEEN_MAX_PER_ORIGIN:
                bucket.popitem(last=False)
            return True

    def forget(self, key: tuple) -> None:
        tenant_id, origin_id, task_id = key
        with self._lock:
            bucket = self._items.get((tenant_id, origin_id))
            if bucket is not None:
                bucket.pop(task_id, None)
                if not bucket:
                    del self._items[(tenant_id, origin_id)]


_SEEN = _SeenTasks()


# ── Receiver integration ─────────────────────────────────────────────────

@dataclass
class FederationContext:
    tenant_id: str
    agent_id: str
    model: str
    seen_key: tuple = ()
    spawned: bool = False
    released: bool = False

    def mark_spawned(self) -> None:
        self.spawned = True


@dataclass
class PreflightOutcome:
    reject_reason: str | None = None
    rollback_nonce: bool = False
    status: str = "ok"
    data: dict | None = None
    ctx: FederationContext | None = None


AuditFn = Callable[[str, str, dict], None]


def preflight(env: Any, origin_config: dict, *, tenant_id: str, worker_enabled: bool,
              audit_strict: AuditFn, audit_best_effort: AuditFn) -> PreflightOutcome:
    """Decide a federated envelope before any worker runs. Never raises."""
    base = {"task_id": env.task_id, "origin_id": env.origin_id, "tenant_id": tenant_id}
    is_task = isinstance(env.federation, dict) and env.federation.get("op") == "task"

    def _reject(reason: str, *, rollback: bool = False) -> PreflightOutcome:
        audit_best_effort("federation.task_rejected" if is_task else "federation.catalog_refused",
                          "WARNING", {**base, "reason": reason})
        return PreflightOutcome(reject_reason=reason, rollback_nonce=rollback)

    if origin_config.get("allow_federation") is False:
        return _reject("federation_disabled")
    # An origin that may not run workers gets neither the catalog nor a run —
    # the no-worker path would answer a signed "ok" for a task that never ran.
    if not worker_enabled:
        return _reject("federation_no_worker")
    if getattr(env, "group_id", None):
        return _reject("federation_bad_request")
    req, reason = parse_request(env.federation or {})
    if req is None:
        return _reject(reason or "federation_bad_request")

    if req.op == "catalog":
        try:
            agents = build_catalog(tenant_id)
        except Exception:  # noqa: BLE001
            return _reject("federation_bad_request")
        try:
            audit_strict("federation.catalog_served", "INFO", {**base, "agent_count": len(agents)})
        except Exception:  # noqa: BLE001 — no committed record, no data out
            return PreflightOutcome(reject_reason="federation_audit_unavailable", rollback_nonce=True)
        return PreflightOutcome(data={"federation_version": FEDERATION_VERSION, "agents": agents})

    try:
        agent, reason = select_agent(tenant_id, req)
    except Exception:  # noqa: BLE001
        agent, reason = None, "federation_no_agent"
    if agent is None:
        return _reject(reason or "federation_no_agent")
    seen_key = (tenant_id, env.origin_id, env.task_id)
    if not _SEEN.claim(seen_key):
        return _reject("federation_duplicate_task")
    if not _SLOTS.acquire(tenant_id, agent.agent_id, agent.max_concurrent):
        # The task never started: let the SAME task_id be tried again later,
        # but consume this envelope's nonce (a captured copy must not replay).
        _SEEN.forget(seen_key)
        return _reject("federation_agent_busy")
    try:
        audit_strict("federation.task_received", "INFO", {
            **base, "agent_id": agent.agent_id,
            "capability": req.capability or "", "hop": req.hop,
            "parent_task_id": req.parent_task_id or "",
        })
    except Exception:  # noqa: BLE001 — audit-first: no record, no run
        _SLOTS.release(tenant_id, agent.agent_id)
        _SEEN.forget(seen_key)
        return PreflightOutcome(reject_reason="federation_audit_unavailable",
                                rollback_nonce=True)
    return PreflightOutcome(ctx=FederationContext(
        tenant_id=tenant_id, agent_id=agent.agent_id, model=agent.model,
        seen_key=seen_key))


def finish(env: Any, ctx: FederationContext | None, resp: Any, *, tenant_id: str,
           duration_ms: int, audit_best_effort: AuditFn) -> None:
    """Release the agent slot and record the outcome. Never raises."""
    if ctx is None or ctx.released:
        return
    ctx.released = True
    try:
        _SLOTS.release(ctx.tenant_id, ctx.agent_id)
    except Exception:  # noqa: BLE001
        pass
    # A task the receiver refused before its engine started (origin worker
    # slot, consent, chain gate, audit write, sanitiser, ...) never ran: give
    # the task_id back so a retry is possible. Once the engine started the
    # claim stays — at-most-once, because a run may have had effects.
    if not ctx.spawned and ctx.seen_key:
        _SEEN.forget(ctx.seen_key)
    status = getattr(resp, "status", None) or "error"
    audit_best_effort("federation.task_completed", "INFO", {
        "task_id": env.task_id, "origin_id": env.origin_id, "agent_id": ctx.agent_id,
        "status": status, "duration_ms": duration_ms, "tenant_id": tenant_id,
    })
