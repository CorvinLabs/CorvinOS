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
a short pointer rather than the model. /plugin-builder (ADR-0253, behind the
plugin_builder_enabled flag) is the one STATEFUL command: it runs a multi-turn
interview via plugin_builder.session_store, so plain-text turns are checked
against an active session before the non-slash fallthrough below. That
session is keyed by ``session_key`` — the caller's chat-CONVERSATION id
(the console's per-tab/per-chat ``sid``, or the bridge's own ``channel:
chat_key``) — deliberately NOT ``fingerprint`` (the login-cookie hash,
shared by every conversation the same browser login has open). Using
``fingerprint`` here would let an interview started in one chat tab bleed
into every other tab/conversation under the same login (found 2026-08-01:
the console was the one caller still passing ``fingerprint`` where the
bridge adapter already used a conversation-scoped key).
"""
from __future__ import annotations

import logging

log = logging.getLogger("corvin.console.slash_commands")

# CCC commands are handled downstream (entity_extract) — never intercept them.
_CCC_CMDS = frozenset({"/create", "/erase", "/audit"})

# Force-delegation verb (ADR-0114). Handled downstream by stream_turn's
# ``_force_delegate`` branch — it must pass THROUGH this dispatcher, not be
# rejected as "Unknown command". Without this entry the console command-center's
# flagship delegation verb is dead (the slash handler runs before stream_turn).
# /use-engine (ADR-0214) is the same trap class: stream_turn's `_tde_force` /
# engine-override branch handles it — found dead-on-arrival in the 2026-07-23
# round-2 refutation because this entry was missing.
# /engine-auto and /debug-engine (ADR-0214's SlashCommandParser also
# recognizes these) had the SAME trap: they were never added here, so both
# advertised commands (see slash_command_parser.format_help()) fell into the
# generic "Unknown command" branch below before stream_turn ever saw them —
# found by the ADR-0215 adversarial review, 2026-07-23.
_PASSTHROUGH_CMDS = frozenset({"/delegate", "/use-engine", "/engine-auto", "/debug-engine"})

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
    "- `/delegate <task>` — force ACS delegation for this turn\n"
    "- `/use-engine tiered_delegation|acs|claude_code <task>` — force a specific "
    "engine for this turn\n"
    "- `/engine-auto <task>` — explicit auto-detection (normal behavior)\n"
    "- `/debug-engine <task>` — show engine-selection signals for this turn\n"
    "- `/plugin-builder` — interview-driven plugin design (Idea/Architecture/ADR"
    "/Plan + scaffold), `/plugin-builder status|cancel` — check or stop it\n"
    "- `/stop` (Stop button), `/new`, `/clear`, `/reset` — session controls\n"
)


# ── /plugin-builder (ADR-0253) ───────────────────────────────────────────────
#
# The one stateful command in this dispatcher. Everything else here is a pure
# function of its arguments; a multi-turn interview genuinely needs session
# state, so it gets its own small store (plugin_builder.session_store) keyed
# by (tenant_id, fingerprint) — the same identity slash_commands already uses.
# Ships behind `plugin_builder_enabled` (default off): with the flag off this
# behaves exactly like an unknown command would, and no session is ever
# created or consulted.

def _plugin_builder_enabled(tenant_id: str) -> bool:
    try:
        from . import feature_flags
        return feature_flags.is_enabled("plugin_builder_enabled", tenant_id)
    except Exception:  # noqa: BLE001 — a broken flag lookup must not break a turn
        return False


def _plugin_builder_continue(text: str, *, tenant_id: str, session_key: str) -> "str | None":
    """Route a non-slash turn to an active interview, if one exists.

    Returns ``None`` for every other case (flag off, module absent, no active
    session) so the caller falls through to its normal prompt handling —
    exactly the same contract as ``handle()`` itself.

    Checks for an active session BEFORE the flag — a cheap in-memory dict
    lookup — rather than the other way round. `feature_flags.is_enabled()`
    reads `features.json` from disk on every call (uncached, unlike
    `_tenant_spec`); checking it first would put a disk read on EVERY plain-
    text turn on EVERY install, flag on or off, when the overwhelming common
    case (no active interview) never needs one.

    The interview state machine, artifact writing and reply text all live in
    ``plugin_builder.turn`` — the messenger bridges drive the exact same
    module via ``operator/bridges/shared/adapter.py``'s own thin wrapper, so
    the two transports can never drift.
    """
    try:
        from plugin_builder import session_store  # noqa: PLC0415
        from plugin_builder import turn as _pb_turn
    except ImportError:
        return None
    if not _plugin_builder_enabled(tenant_id):
        # Flag was toggled off mid-interview — drop any orphaned session
        # rather than let a now-disabled feature keep consuming turns.
        session_store.clear(tenant_id, session_key)
        try:
            from plugin_builder import ideation as _pb_ideation  # noqa: PLC0415
            _pb_ideation.clear(tenant_id, session_key)
        except ImportError:
            pass
        return None

    # An active ideation dialogue is checked BEFORE the plain interview —
    # the two are mutually exclusive per caller (ideation hands off into a
    # real interview session and clears itself in the same turn, see
    # ideation._handoff_to_interview), so at most one of these is ever
    # non-None for a given (tenant_id, session_key).
    try:
        from plugin_builder import ideation as _pb_ideation  # noqa: PLC0415
        ideation_reply = _pb_ideation.continue_active(text, tenant_id=tenant_id, session_key=session_key)
        if ideation_reply is not None:
            return ideation_reply
    except ImportError:
        pass

    session = session_store.get(tenant_id, session_key)
    if session is None:
        return None
    return _pb_turn.drive(session, text, tenant_id=tenant_id, session_key=session_key)


def _plugin_builder_flag(flag_id: str, tenant_id: str) -> bool:
    try:
        from . import feature_flags
        return feature_flags.is_enabled(flag_id, tenant_id)
    except Exception:  # noqa: BLE001 — a broken flag lookup must not break a turn
        return False


def _plugin_builder_command(arg: str, *, tenant_id: str, session_key: str) -> str:
    if not _plugin_builder_enabled(tenant_id):
        return (
            "Plugin Builder is off. An operator can enable it in "
            "**Settings → Features** (`plugin_builder_enabled`)."
        )
    try:
        from plugin_builder import turn as _pb_turn  # noqa: PLC0415
    except ImportError:
        return "Plugin Builder is enabled but not installed in this build."
    idea_first = _plugin_builder_flag("plugin_builder_idea_first_interview", tenant_id)
    checkpoint_enabled = _plugin_builder_flag("plugin_builder_checkpoint_review", tenant_id)
    e2e_tests_enabled = _plugin_builder_flag("plugin_builder_generate_e2e_tests", tenant_id)
    if arg.strip().lower() == "--ideas":
        if not _plugin_builder_flag("plugin_builder_ideas_mode", tenant_id):
            return (
                "Plugin Builder's --ideas co-ideation mode is off. An "
                "operator can enable it in **Settings → Features** "
                "(`plugin_builder_ideas_mode`)."
            )
        try:
            from plugin_builder import ideation as _pb_ideation  # noqa: PLC0415
        except ImportError:
            return "Plugin Builder --ideas mode is enabled but not installed in this build."
        return _pb_ideation.start(
            tenant_id=tenant_id,
            session_key=session_key,
            idea_first=idea_first,
            checkpoint_enabled=checkpoint_enabled,
            e2e_tests_enabled=e2e_tests_enabled,
        )
    return _pb_turn.command(
        arg, tenant_id=tenant_id, session_key=session_key,
        idea_first=idea_first, checkpoint_enabled=checkpoint_enabled,
        e2e_tests_enabled=e2e_tests_enabled,
    )


def handle(text: str, *, tier: str | None, tenant_id: str,
           fingerprint: str, session_key: str, configured_engine: str) -> "str | None":
    """Dispatch a slash-command. Returns a result string, or None to pass through
    (CCC command or non-slash prompt). Pure function of its inputs — testable.

    ``fingerprint`` identifies the LOGIN (shown in ``/whoami``); ``session_key``
    identifies the CHAT CONVERSATION the turn belongs to (the console's per-tab
    ``sid``, the bridge's ``channel:chat_key``). The two are usually different
    strings from the same login — /plugin-builder state is keyed by
    ``session_key`` so an interview never bleeds into another tab/conversation
    under the same login.
    """
    text = (text or "").strip()
    if not text.startswith("/"):
        # An active /plugin-builder interview captures plain-text turns (its
        # questions, and the confirm/restart/cancel review answers) — checked
        # before the normal-prompt fallthrough so mid-interview turns never
        # reach the engine. Returns None immediately when the flag is off or
        # no interview is active, so this costs nothing on a stock install.
        pb_reply = _plugin_builder_continue(text, tenant_id=tenant_id, session_key=session_key)
        if pb_reply is not None:
            return pb_reply
        return None  # normal prompt

    head, _, arg = text.partition(" ")
    cmd = head.lower().strip()
    arg = arg.strip()

    # CCC → downstream entity-extract pipeline.
    if cmd in _CCC_CMDS:
        return None

    if cmd == "/plugin-builder":
        return _plugin_builder_command(arg, tenant_id=tenant_id, session_key=session_key)

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

    if cmd == "/browser":
        # ADR-0193 retired the classifier-routed /browser dispatch in favour of
        # the native corvin-browser tool. Long-time users still type it — a
        # bare "Unknown command" is a dead end for a feature that still exists.
        return ("Browser automation no longer needs a command — just describe "
                "the browsing task in a normal message (e.g. \"open example.com "
                "and check the pricing page\") and the assistant drives the "
                "browser natively. Live view appears in the **Browser** tab.")

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
