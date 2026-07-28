"""Transport-agnostic ``/plugin-builder`` turn handling (ADR-0253).

Both the Console (``corvin_console/slash_commands.py``) and the messenger
bridges (``operator/bridges/shared/adapter.py``) drive the SAME interview
through this module. ``session_store`` is keyed by ``(tenant_id,
session_key)`` — a console "fingerprint" and a bridge "chat_key" are both just
an opaque per-caller identity string as far as this module is concerned, the
same way ``interview.py``'s docstring already promised: "a pytest driving it
directly, a CLI loop, and the ``/plugin-builder`` console command all call the
same two methods" — this module is what lets a THIRD transport (a messenger
bridge) join that list without a second copy of the artifact-writing logic.

Feature-flag gating is deliberately NOT this module's job — it stays with
each caller (``feature_flags.is_enabled("plugin_builder_enabled", ...)``),
exactly as it already was for the console. Moving it here would pull
``corvin_console`` into ``core/plugins``' dependency graph for a flag lookup
one line long; both callers already have that flag reachable on their own.
"""
from __future__ import annotations

import logging

from . import index_store, session_store
from .generators import write_artifacts
from .interview import InterviewSession

log = logging.getLogger("corvin.plugin_builder.turn")


def output_dir(tenant_id: str):
    from forge import paths as _forge_paths  # noqa: PLC0415
    return _forge_paths.tenant_home(tenant_id) / "plugin-builder"


def _write_artifacts_reply(session: InterviewSession, *, tenant_id: str) -> str:
    result = session.result()
    if result is None:  # pragma: no cover — defensive; only reached on a state bug
        return "Something went wrong — the interview did not reach a final result."
    idea, classification = result
    try:
        scaffold = write_artifacts(idea, classification, output_dir(tenant_id))
    except FileExistsError:
        return (
            f"A plugin named **{idea.plugin_name}** was already scaffolded "
            "earlier — rename the idea and run /plugin-builder again."
        )
    except Exception as exc:  # noqa: BLE001 — never break the turn on a write failure
        log.error("plugin-builder artifact write failed: %s", type(exc).__name__)
        return (
            "The interview finished but writing the artifacts failed "
            f"({type(exc).__name__}). Nothing was left half-written; try "
            "/plugin-builder again."
        )

    try:
        index_store.record(tenant_id, idea, scaffold)
    except Exception as exc:  # noqa: BLE001 — the scaffold is already on disk;
        # a listing-index failure must never look like the write itself failed.
        log.error("plugin-builder index record failed: %s", type(exc).__name__)

    # Plain text, no markdown. The console renders markdown and the previous
    # console-only version used **bold** / `code`, but this reply now also goes
    # to seven messengers with three mutually incompatible dialects (Discord and
    # Telegram want **bold**, WhatsApp wants *bold*, Signal and email want
    # neither), so literal asterisks would leak on some transports. The lowest
    # common denominator is the only shape that is correct everywhere.
    lines = [
        f"Done — classified as {classification.kind.value} "
        f"(Tier {classification.tier.value}, "
        f"confidence {classification.confidence:.0%}).",
        f"Written to {scaffold.dest}:",
        *(f"- {p.name}" for p in scaffold.scaffold_files),
        *(f"- docs/{p.name}" for p in scaffold.doc_files),
    ]
    lines.extend(f"⚠ {w}" for w in scaffold.warnings)
    # Restored 2026-07-28: the console reply carried this pointer before the
    # move into this shared module and lost it in the refactor. It is not
    # decoration — `index_store.record` above is what makes the scaffold appear
    # there (routes/plugins.py::list_scaffolds → the Plugins page's
    # ScaffoldCard), and without the pointer nothing tells the author that a
    # generated scaffold has a home in the UI at all.
    lines.append("")
    lines.append("Listed under Settings → Plugins → Scaffolded by Plugin-Builder.")
    return "\n".join(lines)


def drive(session: InterviewSession, text: str, *, tenant_id: str, session_key: str) -> str:
    """Submit one answer to an in-progress session; clears it once finished."""
    reply = session.answer(text)
    if session.is_finished():
        if session.phase.value == "done":
            reply = f"{reply}\n\n{_write_artifacts_reply(session, tenant_id=tenant_id)}"
        session_store.clear(tenant_id, session_key)
    return reply


def continue_active(text: str, *, tenant_id: str, session_key: str) -> "str | None":
    """Drive the active session for ``(tenant_id, session_key)``, if any.

    Returns ``None`` when there is no active session, so the caller can use
    this as a cheap first check before any feature-flag lookup: no active
    session and no explicit ``/plugin-builder`` invocation is the overwhelming
    common case on every turn.
    """
    session = session_store.get(tenant_id, session_key)
    if session is None:
        return None
    return drive(session, text, tenant_id=tenant_id, session_key=session_key)


def command(arg: str, *, tenant_id: str, session_key: str) -> str:
    """Handle the literal ``/plugin-builder [status|cancel]`` invocation.

    Caller is responsible for the feature-flag check before calling this —
    see module docstring.
    """
    sub = arg.strip().lower()
    if sub in ("cancel", "stop"):
        existing = session_store.get(tenant_id, session_key)
        if existing is None:
            return "No Plugin-Builder interview is active."
        session_store.clear(tenant_id, session_key)
        return "Plugin-Builder interview cancelled. Nothing was written."
    if sub == "status":
        existing = session_store.get(tenant_id, session_key)
        if existing is None:
            return "No Plugin-Builder interview is active. Type /plugin-builder to start one."
        return f"Interview in progress (phase: {existing.phase.value}).\n\n{existing.ask()}"

    session = session_store.start(tenant_id, session_key)
    return (
        "Plugin-Builder (ADR-0253) — a few questions, then I'll classify "
        "your idea and generate an Idea Doc, Architecture Concept, ADR and "
        "Build Plan plus a code scaffold. Reply /plugin-builder cancel any "
        "time to stop.\n\n" + session.ask()
    )


__all__ = ["output_dir", "drive", "continue_active", "command"]
