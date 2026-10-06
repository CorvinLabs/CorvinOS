"""Local Agent Registry — CONCEPT-0097 Phase 1.

Tracks which worker-engine deployments ("agents") exist on THIS CorvinOS
installation, so they can later be advertised to A2A peers and selected for
task delegation. This module is deliberately LOCAL-ONLY: it does not emit
anything onto the A2A wire, and it does not read or write any A2A state.

Storage: one append-only JSONL ledger per tenant
(``<corvin_home>/tenants/<tenant_id>/global/federation/local_agents.jsonl``),
replayed into an in-memory dict on construction. The last record for a given
``agent_id`` wins (registration/deregistration are both appends, never
rewrites), matching the append-only convention used elsewhere in this repo
(``session_ledger.py``, ``a2a_invite_registry.py``) rather than mutating a
single JSON file in place.
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from core.paths import tenant_home
from core.tenants.validation import validate_tenant_id

_LEDGER_RELATIVE_PATH = "global/federation/local_agents.jsonl"

# Mirrored in corvin_operator/forge/forge/security_events.py (EVENT_SEVERITY +
# _EVENT_ALLOWLIST) — same two-dict convention as core/forge_bundle/audit.py.
_VALID_CAPABILITIES = frozenset({
    "code_execution", "analysis", "inference", "vision", "classification",
})


_AGENT_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")


class LocalAgentError(ValueError):
    """A LocalAgent registration/lookup request was malformed or invalid."""


@dataclass(frozen=True)
class LocalAgent:
    """One worker-engine deployment registered on this installation."""

    agent_id: str
    engine_type: str                       # "claude_code", "codex_cli", "acs", ...
    capabilities: frozenset[str]
    model: str
    tenant_id: str
    max_concurrent: int = 1
    cost_per_task_usd: float = 0.0
    registered_at: float = field(default_factory=time.time)
    deregistered: bool = False
    # ADR-2232: offered to paired A2A peers only after this explicit opt-in.
    # Default False — registering an agent never exposes it to peers by itself.
    federable: bool = False

    def to_json_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["capabilities"] = sorted(self.capabilities)
        return d

    @staticmethod
    def from_json_dict(d: dict[str, Any]) -> "LocalAgent":
        return LocalAgent(
            agent_id=d["agent_id"],
            engine_type=d["engine_type"],
            capabilities=frozenset(d["capabilities"]),
            model=d["model"],
            tenant_id=d["tenant_id"],
            max_concurrent=d.get("max_concurrent", 1),
            cost_per_task_usd=d.get("cost_per_task_usd", 0.0),
            registered_at=d.get("registered_at", 0.0),
            deregistered=d.get("deregistered", False),
            federable=bool(d.get("federable", False)),
        )


def _validate_agent_id(agent_id: str) -> str:
    agent_id = (agent_id or "").strip()
    if not agent_id:
        raise LocalAgentError("agent_id must not be empty")
    if len(agent_id) > 128:
        raise LocalAgentError("agent_id must be <= 128 characters")
    # ASCII only: the A2A federation layer addresses agents with this exact
    # charset; a Unicode-alnum id would register but be unaddressable.
    if not all(c in _AGENT_ID_CHARS for c in agent_id):
        raise LocalAgentError("agent_id may only contain A-Z, a-z, 0-9, '-', '_', '.'")
    return agent_id


def _validate_capabilities(capabilities: Any) -> frozenset[str]:
    caps = frozenset(capabilities or ())
    if not caps:
        raise LocalAgentError("capabilities must not be empty")
    unknown = caps - _VALID_CAPABILITIES
    if unknown:
        raise LocalAgentError(
            f"unknown capabilities: {sorted(unknown)} "
            f"(valid: {sorted(_VALID_CAPABILITIES)})"
        )
    return caps


class LocalAgentRegistry:
    """Per-tenant registry of local agents, backed by an append-only ledger."""

    def __init__(self, tenant_id: str, corvin_home_override: Path | None = None):
        self.tenant_id = validate_tenant_id(tenant_id)
        if corvin_home_override is not None:
            self._ledger_path = corvin_home_override / "tenants" / self.tenant_id / _LEDGER_RELATIVE_PATH
        else:
            self._ledger_path = tenant_home(self.tenant_id) / _LEDGER_RELATIVE_PATH
        self._ledger_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _replay(self) -> dict[str, LocalAgent]:
        agents: dict[str, LocalAgent] = {}
        if not self._ledger_path.exists():
            return agents
        with self._ledger_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue  # a torn final line from a crash mid-write; skip it
                try:
                    agent = LocalAgent.from_json_dict(record)
                except (KeyError, TypeError):
                    continue
                agents[agent.agent_id] = agent  # last record for this id wins
        return agents

    def _append(self, agent: LocalAgent) -> None:
        with self._ledger_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(agent.to_json_dict(), sort_keys=True))
            fh.write("\n")
        try:
            self._ledger_path.chmod(0o600)
        except OSError:
            pass

    def register(
        self,
        *,
        agent_id: str,
        engine_type: str,
        capabilities: list[str] | frozenset[str],
        model: str,
        max_concurrent: int = 1,
        cost_per_task_usd: float = 0.0,
        federable: bool = False,
    ) -> LocalAgent:
        agent_id = _validate_agent_id(agent_id)
        caps = _validate_capabilities(capabilities)
        if not (engine_type or "").strip():
            raise LocalAgentError("engine_type must not be empty")
        if not (model or "").strip():
            raise LocalAgentError("model must not be empty")
        if max_concurrent < 1:
            raise LocalAgentError("max_concurrent must be >= 1")
        if not math.isfinite(cost_per_task_usd) or cost_per_task_usd < 0:
            raise LocalAgentError("cost_per_task_usd must be a finite number >= 0")

        agent = LocalAgent(
            agent_id=agent_id,
            engine_type=engine_type.strip(),
            capabilities=caps,
            model=model.strip(),
            tenant_id=self.tenant_id,
            max_concurrent=max_concurrent,
            cost_per_task_usd=cost_per_task_usd,
            registered_at=time.time(),
            deregistered=False,
            federable=bool(federable),
        )
        from core.federation import audit as federation_audit

        federation_audit.emit(
            "federation.local_agent_registered",
            tenant_id=self.tenant_id,
            agent_id=agent.agent_id,
            engine_type=agent.engine_type,
            capabilities=sorted(agent.capabilities),
            model=agent.model,
            federable=agent.federable,
        )
        self._append(agent)
        return agent

    def deregister(self, agent_id: str) -> bool:
        agent_id = _validate_agent_id(agent_id)
        current = self._replay().get(agent_id)
        if current is None or current.deregistered:
            return False
        tombstone = LocalAgent(
            agent_id=current.agent_id,
            engine_type=current.engine_type,
            capabilities=current.capabilities,
            model=current.model,
            tenant_id=current.tenant_id,
            max_concurrent=current.max_concurrent,
            cost_per_task_usd=current.cost_per_task_usd,
            registered_at=current.registered_at,
            deregistered=True,
            federable=current.federable,
        )
        from core.federation import audit as federation_audit

        federation_audit.emit(
            "federation.local_agent_deregistered",
            tenant_id=self.tenant_id,
            agent_id=current.agent_id,
        )
        self._append(tombstone)
        return True

    def get(self, agent_id: str) -> LocalAgent | None:
        agent = self._replay().get(_validate_agent_id(agent_id))
        if agent is None or agent.deregistered:
            return None
        return agent

    def list_agents(self) -> list[LocalAgent]:
        return sorted(
            (a for a in self._replay().values() if not a.deregistered),
            key=lambda a: a.agent_id,
        )

    def list_agents_by_capability(self, capability: str) -> list[LocalAgent]:
        return [a for a in self.list_agents() if capability in a.capabilities]
