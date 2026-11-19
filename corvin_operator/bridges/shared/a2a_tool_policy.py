"""Deny-by-default tool policy for a worker that reads a peer's words (ADR-0144).

ONE derivation, used by every path that spawns ``a2a_worker.spawn_a2a_worker``:

* ``remote_trigger_receiver._spawn_and_filter`` — an inbound A2A task;
* ``core.federation.conversation._run_local_turn`` — the local turn of an agent
  conversation and ``/ask @mine``, whose prompt carries the peer agent's text.

The second path used to spawn the worker with NO tool policy at all, i.e. with
the engine's full default tool set (shell, network, file read/write, subagents)
driven by untrusted peer text. Keeping the derivation here — stdlib only, no
``agents`` import — is what lets both callers share it.

The grant comes from the peer's origin config (``allow_*`` flags). An absent or
unloadable config is the empty config: everything below is denied.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

# Tool names that execute code / drive shells in the worker (``allow_bash`` off
# denies all of them). "Bash" alone is not the shell capability. Verified
# against the installed Claude Code binary (2.1.282, tool defs carrying
# enablesCodeExecution) on 2026-09-25: Monitor (runs a command and streams its
# output), PowerShell, RemoteTrigger, CronCreate and Workflow all execute code;
# TaskStop (aliases KillShell/KillBash) and the legacy BashOutput/TaskOutput
# drive background shells. Denying an unknown name is a no-op, so older/newer
# CLIs are covered by listing both current and legacy names.
SHELL_TOOLS: tuple[str, ...] = (
    "Bash", "Monitor", "PowerShell",
    "TaskStop", "KillShell", "KillBash", "BashOutput", "TaskOutput",
    "RemoteTrigger", "CronCreate", "Workflow",
)
NETWORK_TOOLS: tuple[str, ...] = ("WebFetch", "WebSearch")
# Grep/Glob/LS are Read-bypass; NotebookRead reads .ipynb content.
READ_TOOLS: tuple[str, ...] = ("Read", "Grep", "Glob", "LS", "NotebookRead")
WRITE_TOOLS: tuple[str, ...] = ("Write", "Edit", "MultiEdit", "NotebookEdit")
# "Agent" is the current name of the subagent tool; "Task" is its legacy alias
# (Claude Code 2.1.x). Deny both.
SUBAGENT_TOOLS: tuple[str, ...] = ("Task", "Agent", "TodoWrite", "TodoRead")

# Names kept in the DENY lists for older CLIs (denying an unknown name is a
# no-op) that are left out of the ``--tools`` ALLOW list: an allow list names
# what exists, and only the current name of each tool is vouched for.
LEGACY_TOOL_NAMES: frozenset = frozenset({
    "KillShell", "KillBash", "BashOutput", "TaskOutput",   # -> TaskStop
    "Task",                                                # -> Agent
    "LS", "MultiEdit", "NotebookRead", "TodoRead",
})

# Every tool a grant can expose, by class. THE universe of the allow list: a
# tool that is in none of these classes (Skill, EnterWorktree, ExitWorktree,
# SendMessage, ToolSearch, whatever a later CLI adds) cannot be granted by any
# ``allow_*`` flag and therefore never exists for a peer-driven worker.
GRANTABLE_TOOLS: tuple[str, ...] = tuple(
    name
    for group in (SHELL_TOOLS, NETWORK_TOOLS, READ_TOOLS, WRITE_TOOLS, SUBAGENT_TOOLS)
    for name in group
    if name not in LEGACY_TOOL_NAMES
)

# Audit notes a derivation can produce: (event_type, reason).
NOTE_BASH_WITHOUT_NETWORK = ("A2A.bash_denied_without_network",
                             "allow_bash_requires_allow_network")
NOTE_SUBAGENTS_RESTRICTED = ("A2A.subagents_force_restricted",
                             "dangerous_capability_denied")


@dataclass(frozen=True)
class ToolPolicy:
    """What ``spawn_a2a_worker`` gets as ``allowed_tools`` / ``disallowed_tools``."""

    allowed_tools: "list[str] | None"
    disallowed_tools: "list[str] | None"
    # Downgrades the caller should audit (it owns the task/origin ids).
    notes: "list[tuple[str, str]]" = field(default_factory=list)


def derive_tool_policy(origin_config: "Mapping[str, Any] | None") -> ToolPolicy:
    """Derive the worker tool policy from an origin config (deny by default).

    Defaults (structural defence against credential exfiltration):

    * shell tools: blocked unless ``allow_bash`` AND ``allow_network`` — a shell
      IS network access (curl, ssh, a loopback call to this console), so
      ``allow_bash`` without ``allow_network`` made "no network" a label, not a
      restriction (review R4); the combination is refused, fail-closed;
    * WebFetch / WebSearch: blocked unless ``allow_network``;
    * Read and its bypasses: blocked unless ``allow_read_files``;
    * Write / Edit / MultiEdit / NotebookEdit: blocked unless
      ``allow_write_files`` (path_gate hooks are inactive for /tmp workers);
    * Task / Agent / Todo*: blocked unless ``allow_subagents`` — and blocked
      anyway while any of bash / network / write is denied, because the deny
      propagation into subagents is not contractually guaranteed (A5).
    """
    cfg: Mapping[str, Any] = origin_config if isinstance(origin_config, Mapping) else {}
    notes: list[tuple[str, str]] = []

    allowed = cfg.get("allowed_tools")
    allowed_tools: "list[str] | None" = (
        [str(t) for t in allowed] if isinstance(allowed, (list, tuple)) else None)
    raw_denied = cfg.get("disallowed_tools")
    denied: list[str] = (
        [str(t) for t in raw_denied] if isinstance(raw_denied, (list, tuple)) else [])

    def _deny(names: "tuple[str, ...]") -> None:
        for name in names:
            if name not in denied:
                denied.append(name)

    bash_granted = bool(cfg.get("allow_bash"))
    if bash_granted and not cfg.get("allow_network"):
        bash_granted = False
        notes.append(NOTE_BASH_WITHOUT_NETWORK)
    if not bash_granted:
        _deny(SHELL_TOOLS)
        # Keep "Bash" first (historic position; some tests read [0]).
        denied.remove("Bash")
        denied.insert(0, "Bash")
    if not cfg.get("allow_network"):
        _deny(NETWORK_TOOLS)
    if not cfg.get("allow_read_files"):
        _deny(READ_TOOLS)
    if not cfg.get("allow_write_files"):
        _deny(WRITE_TOOLS)

    allow_subagents = bool(cfg.get("allow_subagents"))
    if allow_subagents and (
        not cfg.get("allow_bash")
        or not cfg.get("allow_network")
        or not cfg.get("allow_write_files")
    ):
        notes.append(NOTE_SUBAGENTS_RESTRICTED)
        allow_subagents = False
    if not allow_subagents:
        _deny(SUBAGENT_TOOLS)

    # Always a LIST, never None (review R3-BR-8): at ``spawn_a2a_worker`` ``None`` means
    # "the caller stated no policy" and is replaced by the deny-everything default. An
    # origin that is granted every class denies nothing - that is ``[]``, a statement.
    return ToolPolicy(allowed_tools=allowed_tools,
                      disallowed_tools=list(denied), notes=notes)


def exposed_tools(allowed_tools: "list[str] | None",
                  disallowed_tools: "list[str] | None") -> list[str]:
    """The exact ``--tools=`` set of a worker that reads a peer's words.

    The engine runs with ``--dangerously-skip-permissions``, under which
    ``--disallowedTools`` is a DENY list: every tool it does not name stays
    available. The five ``allow_*`` classes name the tools an operator can
    grant; Skill, EnterWorktree, ExitWorktree, SendMessage, ToolSearch and any
    future tool are in none of them, so no flag could ever take them away. The
    worker therefore gets an ALLOW list: :data:`GRANTABLE_TOOLS` minus the
    denials, intersected with the origin's explicit ``allowed_tools`` when it
    has one. The deny list is still passed - it is the second line.

    Both spawn paths (inbound task, conversation turn) reach this through
    ``a2a_worker.spawn_a2a_worker``, with the lists ``derive_tool_policy`` made.
    An empty result is valid and means "no tools" (``--tools=``).
    """
    denied = {str(t) for t in (disallowed_tools or [])}
    out = [t for t in GRANTABLE_TOOLS if t not in denied]
    if allowed_tools is not None:
        # A scoped entry such as "Bash(git *)" grants its base tool name here;
        # the scope itself stays enforced by --allowedTools.
        allowed = {str(t).split("(", 1)[0].strip() for t in allowed_tools}
        out = [t for t in out if t in allowed]
    return out


__all__ = [
    "GRANTABLE_TOOLS", "LEGACY_TOOL_NAMES", "exposed_tools",
    "NETWORK_TOOLS", "READ_TOOLS", "SHELL_TOOLS", "SUBAGENT_TOOLS", "WRITE_TOOLS",
    "ToolPolicy", "derive_tool_policy",
]
