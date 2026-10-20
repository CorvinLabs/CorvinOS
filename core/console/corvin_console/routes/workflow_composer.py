"""Workflow composer routes (CONCEPT-0115 / ADR-2270).

The workflow editor's design chat uses the SAME composer as the main chat
(attach, folder, drag-and-drop, chips, dictation, slash commands). This module
is the server side of what that composer needs that the chat routes do not
provide:

  GET /workflow-design/commands   the design chat's slash-command table

Slash commands are a SERVER table, not a client constant (the ADR-2235 lesson):
a command the server does not parse cannot be audited or refused fail-closed,
and a client copy of the grammar is how two dispatchers drift. The design
WebSocket (``routes/workflows.py::workflow_chat_ws``) parses every ``/`` line
through ``parse_design_command`` / ``run_design_command`` below and answers it
WITHOUT spawning an engine and WITHOUT writing it into the design transcript
(a command line is not a design instruction and must not feed later prompts).
"""
from __future__ import annotations

import re
from typing import Annotated, Any, NamedTuple

from fastapi import APIRouter, Depends

from .. import auth as session_auth
from ..deps import require_session, require_session_csrf_on_mutation

router = APIRouter(dependencies=[Depends(require_session_csrf_on_mutation)])

# One entry per command the WebSocket understands. `cmd` is what the palette
# inserts, `args` the placeholder, `desc` the one-line help.
DESIGN_COMMANDS: tuple[dict[str, str], ...] = (
    {"cmd": "/help", "args": "", "desc": "List the design chat commands"},
    {"cmd": "/phase", "args": "", "desc": "Show the current design phase"},
    {"cmd": "/nodes", "args": "", "desc": "List the steps currently on the canvas"},
    {"cmd": "/voice on", "args": "", "desc": "Speak the designer's replies aloud"},
    {"cmd": "/voice off", "args": "", "desc": "Stop spoken replies"},
)

# A command token is a bare word after the slash. A path such as `/home/x/y`
# contains another slash and is ordinary text, never a command.
_CMD_TOKEN = re.compile(r"^/[a-z][a-z0-9-]*$")


class CommandResult(NamedTuple):
    reply: str
    voice: bool | None  # new voice preference when the command changed it


def parse_design_command(text: str) -> tuple[str, str] | None:
    """Return ``(command, argument)`` for a slash line, else ``None``.

    A line is a command when its first whitespace-delimited token is a bare
    ``/word``. Unknown words are still returned (the caller answers "unknown
    command") so a mistyped command is never forwarded to the designer.
    """
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    head, _, rest = stripped.partition(" ")
    head = head.lower()
    if not _CMD_TOKEN.match(head):
        return None
    return head, rest.strip()


def run_design_command(
    command: str,
    argument: str,
    *,
    phase: str,
    node_ids: list[str],
    voice_enabled: bool,
) -> CommandResult:
    """Execute one design command. Pure: no I/O, no spawn."""
    if command == "/help":
        lines = ["Design chat commands:"]
        for entry in DESIGN_COMMANDS:
            lines.append(f"{entry['cmd']} - {entry['desc']}")
        return CommandResult("\n".join(lines), None)
    if command == "/phase":
        return CommandResult(f"Current design phase: {phase}.", None)
    if command == "/nodes":
        if not node_ids:
            return CommandResult("The canvas has no steps yet.", None)
        return CommandResult("Steps on the canvas: " + " -> ".join(node_ids), None)
    if command == "/voice":
        arg = argument.lower()
        if arg in ("on", "off"):
            on = arg == "on"
            return CommandResult(f"Spoken replies are {'on' if on else 'off'}.", on)
        state = "on" if voice_enabled else "off"
        return CommandResult(f"Spoken replies are {state}. Use /voice on or /voice off.", None)
    return CommandResult(f"Unknown command {command} - not sent to the designer. Type /help.", None)


@router.get("/workflow-design/commands")
def design_commands(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """The design chat's slash-command table (same table the WebSocket parses)."""
    return {"commands": list(DESIGN_COMMANDS)}
