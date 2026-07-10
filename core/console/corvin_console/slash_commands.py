"""Console chat slash-command dispatcher (server-side).

The web-console "command center" advertises a slash-command palette. Before this
module, only the CCC entity commands (/create*, /erase, /audit) were handled —
every OTHER slash-command was sent verbatim to the LLM, which then "answered" the
literal string (a confusing, sometimes fabricated reply). This dispatcher makes
EVERY slash-command deterministic: it never leaks to the model.

Routing (``handle`` return value):
  * ``None``  → not handled here; the caller proceeds normally:
                - CCC commands (/create*, /erase, /audit) fall through to the
                  entity-extract pipeline in stream_turn (their own workstream),
                - any non-slash text is a normal engine prompt.
  * ``str``   → a result message to render as the assistant reply for this turn
                (the caller emits it as delta+done; the engine is NOT invoked).

Functional (real action / real data): /help, /whoami, /role, /quota, /engine
(show). Informational pointers (the action lives in a dedicated tab or is
tenant-wide, not per-web-chat): /engine <name>, /persona, /dialectic-*, /skills,
/memory. Honest "not in the console" for bridge-only runtime commands
(/go, /propose, /btw, /share, /forget). Client-side actions (/stop, /new, /clear,
/reset) are performed by the frontend; if one still reaches the server we return
a short pointer rather than the model.
"""
from __future__ import annotations

# CCC commands are handled downstream (entity_extract) — never intercept them.
_CCC_CMDS = frozenset({"/create", "/erase", "/audit"})

# Force-delegation verb (ADR-0114). Handled downstream by stream_turn's
# ``_force_delegate`` branch — it must pass THROUGH this dispatcher, not be
# rejected as "Unknown command". Without this entry the console command-center's
# flagship delegation verb is dead (the slash handler runs before stream_turn).
_PASSTHROUGH_CMDS = frozenset({"/delegate"})

# Performed by the frontend (abort the live stream / navigate sessions). If they
# reach the server, give a pointer instead of an LLM turn.
_CLIENT_SIDE = frozenset({"/stop", "/cancel", "/halt", "/new", "/clear", "/reset"})

# Bridge/messenger runtime concepts with no web-console equivalent.
_BRIDGE_ONLY = frozenset({"/go", "/propose", "/btw", "/share"})


def is_ccc(text: str) -> bool:
    """True if *text* is a CCC entity command (handled downstream, not here)."""
    if not text.startswith("/"):
        return False
    return text.split(maxsplit=1)[0].lower() in _CCC_CMDS


def _chat_turn_limit() -> "int | None":
    """The chat_turns_per_day license limit, or None when unlimited."""
    try:
        from license.validator import get_limit  # type: ignore  # noqa: PLC0415
        return get_limit("chat_turns_per_day")
    except Exception:  # noqa: BLE001
        return None


_HELP = (
    "**Console commands**\n"
    "- `/help` — this list\n"
    "- `/whoami`, `/role` — your identity, tier and role\n"
    "- `/quota` — your daily chat-turn limit\n"
    "- `/engine [name]` — show the configured engine (change it in the Engines tab)\n"
    "- `/persona`, `/skills`, `/memory` — open the matching tab to manage these\n"
    "- `/dialectic-on`, `/dialectic-off` — toggle in the Engines/Settings tab\n"
    "- `/create workflow|task|tool|skill`, `/erase`, `/audit` — CCC entity actions\n"
    "- `/stop` (Stop button), `/new`, `/clear`, `/reset` — session controls\n"
)


def handle(text: str, *, tier: str | None, tenant_id: str,
           fingerprint: str, configured_engine: str) -> "str | None":
    """Dispatch a slash-command. Returns a result string, or None to pass through
    (CCC command or non-slash prompt). Pure function of its inputs — testable."""
    text = (text or "").strip()
    if not text.startswith("/"):
        return None  # normal prompt

    head, _, arg = text.partition(" ")
    cmd = head.lower().strip()
    arg = arg.strip()

    # CCC → downstream entity-extract pipeline.
    if cmd in _CCC_CMDS:
        return None

    # Force-delegation → downstream stream_turn._force_delegate branch.
    if cmd in _PASSTHROUGH_CMDS:
        return None

    if cmd == "/help":
        return _HELP

    if cmd in ("/whoami", "/role"):
        role = "owner"  # console sessions are owner-authenticated (whitelist)
        return (f"You are signed in as the **{role}** of tenant "
                f"`{tenant_id}` (tier: {tier or 'unknown'}, session "
                f"`{fingerprint}`).")

    if cmd == "/quota":
        lim = _chat_turn_limit()
        if lim is None:
            return "Your chat is **unlimited** (no daily chat-turn cap on this tier)."
        return f"Your daily chat-turn limit is **{lim}** (chat_turns_per_day)."

    if cmd == "/engine":
        base = f"The configured engine for this tenant is **{configured_engine}**."
        if arg:
            return (base + " The console engine is set **tenant-wide** in the "
                    "**Engines** tab, not per chat — change it there.")
        return base + " Change it in the **Engines** tab."

    if cmd == "/persona":
        return ("Personas are managed in the **Personas** tab (create, edit, "
                "assign an engine, enable/disable). Per-web-chat persona pinning "
                "is not available in this console session.")

    if cmd in ("/dialectic-on", "/dialectic-off"):
        return ("Dialectic reasoning is toggled in the **Engines / Settings** "
                "tab for this console.")

    if cmd == "/skills":
        return "Active skills are listed in the **Skills** tab."

    if cmd == "/memory":
        return "Your memory is shown in the **Memory** tab."

    if cmd == "/forget":
        return ("To delete your data (GDPR Art. 17), use `/erase` or the "
                "**Memory** tab — this performs the audited erasure flow.")

    if cmd in _BRIDGE_ONLY:
        return (f"`{cmd}` is a messaging-bridge command (Discord/WhatsApp) and "
                "is not available in the web console.")

    if cmd in _CLIENT_SIDE:
        _hint = {
            "/stop": "the **Stop** button", "/cancel": "the **Stop** button",
            "/halt": "the **Stop** button", "/new": "the **New chat** button",
            "/clear": "the **New chat** button", "/reset": "the **New chat** button",
        }[cmd]
        return f"Use {_hint} to {cmd.lstrip('/')} this session."

    return f"Unknown command `{cmd}`. Type `/help` for the list."
