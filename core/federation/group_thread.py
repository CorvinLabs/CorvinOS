"""Server-side command dispatcher for the GROUP chat composer (ADR-2235 follow-up).

A group message is fanned out to every ``a2a_peer`` participant as plain text
(``chat_groups._schedule_delivery``). A `/` line typed into a group composer
must therefore never take that path — it would reach every peer as ordinary
text, exactly the bug ADR-2235 closed for the 1:1 thread. Every `/` line from
the group composer is POSTed to ``/chat/groups/{id}/command`` instead and
handled here; anything unrecognised is refused fail-closed and nothing is sent.

The grammar is the peer thread's (``peer_thread.COMMANDS`` — one table, one
parser: a second copy is how the console's dispatchers drifted). A group adds
only the question the 1:1 thread never had: WHICH peer. Commands that act
through one peer (`/ask`, `/talk`) resolve it as

* the group's only ``a2a_peer`` participant, or
* the one named by a leading ``--in <participant_id>`` (`/ask --in bob @peer q`).

With several peers and no selector the line is refused, never guessed.
`/stop` and `/agents` cover every peer of the group. The permission model is
unchanged: ``require_active`` (the live friendship gate the group routes
already use) runs for every peer a command touches, and the delegation /
conversation primitives apply their own gates (federable flag, sanitizer,
L34/L35/L44) exactly as in the 1:1 thread.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from core.federation import peer_thread
from core.federation.peer_thread import PeerThreadCommandError

_IN_RE = re.compile(r"^(?P<cmd>/\S+)\s+--in\s+(?P<pid>\S+)(?P<rest>(?:\s+.*)?)$", re.DOTALL)
_ALL_PEERS = frozenset({"/stop", "/agents"})


def commands_table() -> list[dict[str, str]]:
    """The 1:1 table, with the group-only selector spelled out in the help text."""
    out = []
    for c in peer_thread.commands_table():
        if c["cmd"] in _ALL_PEERS:
            out.append({**c, "desc": f"{c['desc']} (all peers of this group)"})
        else:
            out.append({**c, "desc": f"{c['desc']} · several peers: /cmd --in <peer id> …"})
    return out


def _peers(group: dict[str, Any]) -> list[dict[str, Any]]:
    return [p for p in group.get("participants", [])
            if p.get("kind") == "a2a_peer" and p.get("peer_endpoint_id")]


def dispatch(
    tenant_id: str, group: dict[str, Any], line: str,
    *, require_active: Callable[[str], None],
) -> dict[str, Any]:
    line = (line or "").strip()
    if not line.startswith("/"):
        raise PeerThreadCommandError("not a command line")
    peers = _peers(group)
    if not peers:
        raise PeerThreadCommandError(
            "this group has no connected agent — add one under Members first")

    chosen: dict[str, Any] | None = None
    m = _IN_RE.match(line)
    if m:
        pid = m.group("pid")
        named = [p for p in peers if pid in (p["participant_id"], p.get("display_name"))]
        if len(named) != 1:
            raise PeerThreadCommandError(f"no peer {pid!r} in this group")
        chosen = named[0]
        line = f"{m.group('cmd')}{m.group('rest')}"

    word = line.split(None, 1)[0]
    if word in _ALL_PEERS and chosen is None:
        results = []
        for p in peers:
            require_active(p["peer_endpoint_id"])
            results.append({"participant": p["participant_id"],
                            **peer_thread.dispatch(tenant_id, p["peer_endpoint_id"], line)})
        return {"executed": True, "kind": word.lstrip("/"), "peers": results}

    if chosen is None:
        if len(peers) > 1:
            ids = ", ".join(p["participant_id"] for p in peers)
            raise PeerThreadCommandError(
                f"this group has several peers ({ids}) — say which with --in <peer id>")
        chosen = peers[0]
    require_active(chosen["peer_endpoint_id"])
    result = peer_thread.dispatch(tenant_id, chosen["peer_endpoint_id"], line)
    return {**result, "participant": chosen["participant_id"]}
