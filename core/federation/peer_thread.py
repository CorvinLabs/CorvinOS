"""Server-side command dispatcher for the peer-chat composer (ADR-2235 Phase 2).

A `/` line typed into a peer chat (``PeerConversation.tsx``) must never reach
the peer as plain A2A text — before this module existed it did (ADR-2235
Context point 4): there was no dispatcher, so e.g. `/ask @mine welcher agent
bist du` was sent as the literal task instruction, and the PEER's worker
answered it (it has no reason to treat `/ask` as anything but ordinary
text) — exactly the bug the operator reported.

Every peer-chat composer now POSTs a `/` line here first
(``POST /v1/console/peer-thread/{endpoint_id}/command``). A recognised
command is executed through the existing federation primitives
(``conversation.ask_mine``, ``delegation.delegate``, ``conversation.start``,
``conversation.stop``) — never sent to the peer. Anything unrecognised is
refused with "unknown command — not sent": fail-closed, so a typo or a
console-session command (`/stop`, `/delegate`, …) can never leak to the peer
as plain text (ADR-2235 Alternatives (e): the command table is parsed here,
once, not duplicated client-side).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from core.federation import conversation as conv
from core.federation.delegation import delegate
from core.federation.local_agent import LocalAgentRegistry
from core.federation.peer_catalog import PeerCatalog
from core.tenants.validation import validate_tenant_id

MAX_LINE_CHARS = 4096
DEFAULT_TALK_TURNS = 6

_AGENT_ID_RE = r"[A-Za-z0-9_.-]{1,128}"
_ASK_RE = re.compile(
    rf"^/ask\s+@(?P<who>mine|peer)(?:/(?P<id>{_AGENT_ID_RE}))?\s+(?P<text>.+)$", re.DOTALL)
_TALK_RE = re.compile(
    rf"^/talk(?:\s+@mine/(?P<local_id>{_AGENT_ID_RE}))?"
    rf"(?:\s+@peer/(?P<peer_id>{_AGENT_ID_RE}))?"
    r"(?:\s+--turns\s+(?P<turns>\d{1,2}))?\s+(?P<topic>.+)$", re.DOTALL)


class PeerThreadCommandError(ValueError):
    """The line was refused locally — it was never sent to the peer."""


@dataclass(frozen=True)
class PeerThreadCommand:
    cmd: str
    args: str
    desc: str


# One list. The console palette (SlashCommandPalette.tsx) fetches this table
# via GET /v1/console/peer-thread/commands rather than hard-coding a copy —
# a client-side copy is exactly how the console's two session dispatchers
# (slash_commands.py vs. the bridge) drifted (ADR-2235 Alternatives (e)).
COMMANDS: tuple[PeerThreadCommand, ...] = (
    PeerThreadCommand("/ask @mine", "<task>",
                      "Ask your own agent — answered inline in this thread"),
    PeerThreadCommand("/ask @peer", "<task>",
                      "Ask the peer's agent — needs their permission to answer"),
    PeerThreadCommand("/talk", "<topic>",
                      "Start an agent-to-agent conversation here (max 12 turns)"),
    PeerThreadCommand("/stop", "", "Stop the conversation running in this thread"),
    PeerThreadCommand("/agents", "", "Show your agents and this peer's federable agents"),
)


def commands_table() -> list[dict[str, str]]:
    return [{"cmd": c.cmd, "args": c.args, "desc": c.desc} for c in COMMANDS]


def _only_local_agent(tenant_id: str, agent_id: str | None) -> Any:
    registry = LocalAgentRegistry(tenant_id)
    if agent_id:
        agent = registry.get(agent_id)
        if agent is None:
            raise PeerThreadCommandError(f"no local agent named {agent_id!r}")
        return agent
    agents = registry.list_agents()
    if not agents:
        raise PeerThreadCommandError(
            "no local agent registered — add one under Federation first")
    if len(agents) > 1:
        raise PeerThreadCommandError(
            "more than one local agent is registered — say which one with @mine/<agent_id>")
    return agents[0]


def _only_peer_agent(tenant_id: str, endpoint_id: str, agent_id: str | None) -> Any:
    candidates = [a for a in PeerCatalog(tenant_id).agents() if a.endpoint_id == endpoint_id]
    if agent_id:
        named = [a for a in candidates if a.agent_id == agent_id]
        if not named:
            raise PeerThreadCommandError(f"peer has no federable agent named {agent_id!r}")
        return named[0]
    if not candidates:
        raise PeerThreadCommandError(
            "this peer has no federable agent — refresh the peer catalog first")
    if len(candidates) > 1:
        raise PeerThreadCommandError(
            "the peer offers more than one agent — say which one with @peer/<agent_id>")
    return candidates[0]


def _stop_running(tenant_id: str, endpoint_id: str) -> bool:
    running = [
        c for c in conv.list_conversations(tenant_id)
        if c.get("status") == "running" and (c.get("peer") or {}).get("endpoint_id") == endpoint_id
    ]
    if not running:
        return False
    return conv.stop(tenant_id, running[0]["conversation_id"])


def dispatch(tenant_id: str, endpoint_id: str, line: str) -> dict[str, Any]:
    """Parse and execute one peer-chat command line.

    Raises :class:`PeerThreadCommandError` for anything refused before
    reaching a federation primitive (unknown command, bad addressee,
    ambiguous agent). A primitive's own error
    (``conv.ConversationError`` / ``DelegationError`` /
    ``FederationAuditError``) propagates unchanged — the route maps it the
    same way the existing federation routes do.
    """
    tenant_id = validate_tenant_id(tenant_id)
    line = (line or "").strip()
    if not line.startswith("/"):
        raise PeerThreadCommandError("not a command line")
    if len(line) > MAX_LINE_CHARS:
        raise PeerThreadCommandError("line too long")

    m = _ASK_RE.match(line)
    if m:
        text = m.group("text").strip()
        if not text:
            raise PeerThreadCommandError("/ask needs a question")
        if m.group("who") == "mine":
            local = _only_local_agent(tenant_id, m.group("id"))
            result = conv.ask_mine(tenant_id, local_agent_id=local.agent_id,
                                   endpoint_id=endpoint_id, instruction=text)
            return {"executed": True, "kind": "ask_mine", **result}
        peer = _only_peer_agent(tenant_id, endpoint_id, m.group("id"))
        result = delegate(tenant_id, instruction=text, endpoint_id=endpoint_id,
                          agent_id=peer.agent_id).to_dict()
        return {"executed": True, "kind": "ask_peer", **result}

    m = _TALK_RE.match(line)
    if m:
        topic = m.group("topic").strip()
        if not topic:
            raise PeerThreadCommandError("/talk needs a topic")
        turns = int(m.group("turns")) if m.group("turns") else DEFAULT_TALK_TURNS
        local = _only_local_agent(tenant_id, m.group("local_id"))
        peer = _only_peer_agent(tenant_id, endpoint_id, m.group("peer_id"))
        result = conv.start(tenant_id, local_agent_id=local.agent_id, endpoint_id=endpoint_id,
                            peer_agent_id=peer.agent_id, opener=topic, max_turns=turns)
        return {"executed": True, "kind": "talk", **result}

    if line == "/stop":
        return {"executed": True, "kind": "stop", "stopped": _stop_running(tenant_id, endpoint_id)}

    if line == "/agents":
        return {
            "executed": True, "kind": "agents",
            "local": [a.agent_id for a in LocalAgentRegistry(tenant_id).list_agents()],
            "peer": [a.agent_id for a in PeerCatalog(tenant_id).agents()
                    if a.endpoint_id == endpoint_id],
        }

    raise PeerThreadCommandError("unknown command — not sent")
