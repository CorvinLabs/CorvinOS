"""Transport-agnostic ``/plugin-builder`` turn handling (ADR-0253).

Both the Console (``corvin_console/slash_commands.py``) and the messenger
bridges (``operator/bridges/shared/adapter.py``) drive the SAME interview
through this module. ``session_store`` is keyed by ``(tenant_id,
session_key)`` — the console's per-tab/per-chat ``sid`` and the bridge's
``channel:chat_key`` are both just an opaque per-CONVERSATION identity string
as far as this module is concerned (deliberately NOT the console's login
fingerprint, which is shared across every conversation the same login has
open — see ``slash_commands.handle()``'s docstring), the
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
import threading
import time
from pathlib import Path

from . import checkpoint as checkpoint_mod
from . import index_store, session_store
from .generators import (
    generate_e2e_tests,
    write_artifacts,
    write_idea_docs,
    write_scaffold_after_checkpoint,
)
from .interview import InterviewPhase, InterviewSession, classify_checkpoint_decision

log = logging.getLogger("corvin.plugin_builder.turn")

#: (dest, plugin_id, last_touched) recorded by the checkpoint doc-write step,
#: consumed by the later scaffold-write step for the SAME session — keyed by
#: ``id(session)`` (Python object identity), NOT ``session.session_id``.
#: ``session.session_id`` is a DETERMINISTIC string
#: (``f"{tenant_id}:{session_key}"``, see ``session_store.start()``) shared
#: by every ``InterviewSession`` OBJECT ever created for the same caller —
#: it identifies the SLOT, not a particular attempt. Two objects can hold
#: that identical string at once (a double-click/bridge-retry starts a new
#: object while an old one for the same caller is still finishing its
#: CHECKPOINT/DONE transition) — keying this dict by the string let one
#: session's checkpoint state silently overwrite another's, so a later
#: `_finish_reply` could write session A's finished idea into session B's
#: scaffold directory (ADR-0262/0263 review round 4, Backend finding,
#: reproduced with a live thread race: 22/40 runs cross-contaminated).
#: ``id()`` is safe here ONLY because ``_forget_checkpoint_state`` is
#: registered with ``session_store.register_removal_hook`` below and so runs
#: for EVERY way a session ever leaves that store — not just the call sites
#: that happened to pair their own ``clear()`` with an explicit forget. A
#: round-4 version of this comment claimed the object "cannot be garbage-
#: collected while still present here" as an inherent property of
#: process-local storage — that was false: three removal paths (two
#: feature-flag-off cleanups, one bridge exception handler) cleared
#: ``session_store`` without telling this dict, so a freed session's
#: ``id()`` could be reused by an UNRELATED new session (same tenant or a
#: different one — this dict carries no tenant scoping) before its stale
#: entry was cleaned up, corrupting that new session's scaffold destination
#: (ADR-0262/0263 review round 6, Backend finding, reproduced
#: deterministically — no concurrency needed). The hook makes "session left
#: the store" and "checkpoint state is gone" the same event structurally,
#: instead of two events every caller had to remember to trigger together.
#:
#: Entries are ALSO removed the moment the session finishes or restarts (via
#: the same hook) — but a session simply abandoned mid-checkpoint (never
#: restarted, never cancelled, browser closed) would otherwise sit here
#: forever, unlike ``session_store``/``ideation.py``'s own stores, which
#: both TTL-evict and bound themselves. Same discipline applied here
#: (ADR-0262 review round 1, Backend finding 3).
#:
#: ``_checkpoint_reply`` only WRITES an entry after re-checking that
#: ``session_store`` still holds this exact object for its ``(tenant_id,
#: session_key)`` slot (see the check inside it). Without that re-check, a
#: concurrent ``session_store.start()`` replacing this session while
#: ``write_idea_docs()`` (disk I/O, unbounded duration) was still in flight
#: could fire the removal hook — a no-op, because the entry did not exist
#: yet — and this function would then write an entry for an object no
#: future removal event will ever fire for again: a permanent orphan,
#: bounded only by the TTL/MAX eviction above, not eliminated by the hook
#: the way the docstring on that mechanism otherwise promises (ADR-0262
#: review round 7, Backend finding 1, reproduced deterministically with a
#: 2-thread barrier and organically once in an 8x60 randomized stress run).
_CHECKPOINT_STATE_TTL_SECONDS = session_store.SESSION_TTL_SECONDS
_CHECKPOINT_STATE_MAX = session_store.MAX_SESSIONS
_checkpoint_state_lock = threading.Lock()
_checkpoint_state: dict[int, tuple[Path, str, float]] = {}


def output_dir(tenant_id: str):
    from forge import paths as _forge_paths  # noqa: PLC0415
    return _forge_paths.tenant_home(tenant_id) / "plugin-builder"


def _evict_stale_checkpoint_state_locked(now: float) -> None:
    stale = [
        k for k, (_, _, touched) in _checkpoint_state.items()
        if now - touched > _CHECKPOINT_STATE_TTL_SECONDS
    ]
    for k in stale:
        del _checkpoint_state[k]


def _forget_checkpoint_state(session: InterviewSession) -> None:
    with _checkpoint_state_lock:
        _checkpoint_state.pop(id(session), None)


# Registered once at import time — see the module-level _checkpoint_state
# comment above for why every removal path must call this, not just the
# ones a caller remembers to pair it with.
session_store.register_removal_hook(_forget_checkpoint_state)


def _checkpoint_reply(session: InterviewSession, *, tenant_id: str, session_key: str) -> str:
    """First arrival (or a retry after a failed first attempt) at CHECKPOINT:
    write the four docs, build the text+voice summary, remember where the
    docs went for the later scaffold-write step.

    Sets ``session.checkpoint_docs_written = True`` itself, and ONLY on the
    success path — ``drive()`` must not set it ahead of the write (a prior
    version did; if the write then failed, the session looked like it had
    already written its docs, a later retry skipped straight to the missing
    ``_checkpoint_state`` entry, and the whole interview was discarded with
    no way to recover the idea. See ADR-0262 review round 1, Backend
    finding 1.).

    Only CACHES that write (and only flips ``checkpoint_docs_written``) if
    ``session_store`` still holds this exact session object for
    ``(tenant_id, session_key)`` at that moment — see the
    ``_checkpoint_state`` module comment for why (ADR-0262 review round 7,
    Backend finding 1). If it doesn't (a concurrent restart/cancel/new
    ``/plugin-builder`` replaced it while the doc write above was still in
    flight), the docs are still correctly on disk; this session object is
    simply abandoned, same as the "browser closed mid-checkpoint" case the
    TTL eviction above already tolerates — no scaffold step will ever run
    for it, which is exactly what ``_finish_reply``'s existing "no cached
    entry" defensive branch already handles.

    **Voice note (ADR-0262 review round 1, Gates finding):** this builds a
    real, tested ``voice_text`` (``checkpoint.py::build_checkpoint``), but
    neither the console (`corvin_console/slash_commands.py`) nor the bridge
    reply path (`operator/bridges/shared/adapter.py`'s
    `_plugin_builder_bridge_reply` writes straight to the outbox, before the
    point where `extract_voice_override()` runs for a normal engine turn)
    currently extracts a `<voice>` tag from a Plugin-Builder reply at all —
    appending one here would leak the raw tag into the visible chat text
    instead of being spoken, which is worse than not having it. `voice_text`
    is real and unit-tested (`tests/test_checkpoint.py`) and is the building
    block a future fix would need, but it is NOT reachable from a live
    turn today — named explicitly rather than claimed as wired.
    """
    result = session.result()
    if result is None:  # pragma: no cover — defensive; only reached on a state bug
        return "Something went wrong — no finalized idea to write docs for."
    idea, classification = result
    try:
        dest, plugin_id, doc_files = write_idea_docs(idea, classification, output_dir(tenant_id))
    except Exception as exc:  # noqa: BLE001 — never break the turn on a write failure
        log.error("plugin-builder checkpoint doc write failed: %s", type(exc).__name__)
        return (
            "Generating the review documents failed "
            f"({type(exc).__name__}). Nothing was left half-written; try "
            "/plugin-builder again."
        )
    with _checkpoint_state_lock:
        now = time.time()
        _evict_stale_checkpoint_state_locked(now)
        if session_store.get(tenant_id, session_key) is session:
            if len(_checkpoint_state) >= _CHECKPOINT_STATE_MAX:
                oldest_key = min(_checkpoint_state, key=lambda k: _checkpoint_state[k][2])
                del _checkpoint_state[oldest_key]
            _checkpoint_state[id(session)] = (dest, plugin_id, now)
            session.checkpoint_docs_written = True

    summary = checkpoint_mod.build_checkpoint(
        idea, classification, doc_files, language=session.language.language or "en"
    )
    # `summary.voice_text` intentionally unused here — see docstring above.
    return summary.text


def _finish_reply(session: InterviewSession, *, tenant_id: str) -> str:
    result = session.result()
    if result is None:  # pragma: no cover — defensive; only reached on a state bug
        return "Something went wrong — the interview did not reach a final result."
    idea, classification = result

    if session.checkpoint_enabled:
        with _checkpoint_state_lock:
            cached = _checkpoint_state.pop(id(session), None)
        if cached is None:  # pragma: no cover — defensive; checkpoint always runs first
            return (
                "Something went wrong — the checkpoint step didn't record "
                "where the documents were written. Try /plugin-builder again."
            )
        dest, plugin_id, _touched = cached
        try:
            scaffold = write_scaffold_after_checkpoint(idea, classification, plugin_id, dest)
        except FileExistsError:
            return (
                f"A plugin named **{idea.plugin_name}** was already "
                "scaffolded earlier — rename the idea and run "
                "/plugin-builder again."
            )
        except Exception as exc:  # noqa: BLE001
            log.error("plugin-builder scaffold write failed: %s", type(exc).__name__)
            return (
                "The checkpoint documents were written, but the scaffold "
                f"failed ({type(exc).__name__}). Try /plugin-builder again."
            )
    else:
        try:
            scaffold = write_artifacts(idea, classification, output_dir(tenant_id))
        except FileExistsError:
            return (
                f"A plugin named **{idea.plugin_name}** was already "
                "scaffolded earlier — rename the idea and run "
                "/plugin-builder again."
            )
        except Exception as exc:  # noqa: BLE001 — never break the turn on a write failure
            log.error("plugin-builder artifact write failed: %s", type(exc).__name__)
            return (
                "The interview finished but writing the artifacts failed "
                f"({type(exc).__name__}). Nothing was left half-written; "
                "try /plugin-builder again."
            )

    test_file_note: str | None = None
    if session.e2e_tests_enabled and scaffold.scaffold_files:
        try:
            test_path = generate_e2e_tests(
                classification, scaffold.plugin_id, scaffold.scaffold_files[0], scaffold.dest
            )
            if test_path is not None:
                test_file_note = f"tests/{test_path.name}"
        except Exception as exc:  # noqa: BLE001 — the scaffold is already on disk and
            # usable; a test-generation failure must never look like the
            # scaffold write itself failed.
            log.error("plugin-builder e2e test generation failed: %s", type(exc).__name__)

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
    if test_file_note:
        lines.append(f"- {test_file_note}")
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
    """Submit one answer to an in-progress session; clears it once finished.

    Held under ``session._lock`` (an RLock — see its field comment in
    ``interview.py``) for its ENTIRE body, not just the ``session.answer()``
    call inside it: two side-effecting phase transitions live here, not in
    ``interview.py`` (which stays filesystem-free by design) — arriving at
    CHECKPOINT writes the review docs, arriving at DONE writes the scaffold
    (+ generated tests, if enabled) — and both must be atomic with the phase
    check that decides whether to run them, or two concurrent calls (a
    client retry racing the original request) could both skip the write
    (ADR-0262 review round 1, Backend finding 2).
    """
    with session._lock:  # noqa: SLF001 — same reentrant lock ideation.py's own
        # driver already takes on its own session type; turn.py needs the
        # same discipline on InterviewSession, which is a package-internal
        # type this module already reads several other underscore-prefixed
        # fields of (checkpoint_docs_written, phase, language).
        if (
            session.phase == InterviewPhase.CHECKPOINT
            and not session.checkpoint_docs_written
            and classify_checkpoint_decision(text) not in ("cancel", "restart")
        ):
            # A PRIOR call already moved REVIEW -> CHECKPOINT and attempted
            # the doc write, which failed (see _checkpoint_reply) — the
            # session is still sitting in CHECKPOINT with nothing cached.
            # Retry the write for anything that isn't an explicit cancel/
            # restart — a plain "confirm" (or any other text) means "try
            # again". A round-1 version of this retried on ANY text at all,
            # which silently ate a genuine "cancel"/"restart" DECISION
            # instead of honoring it (ADR-0262 review round 2, Backend
            # finding 1) — those two tokens fall through to session.answer()
            # below instead, which _answer_checkpoint handles correctly.
            return _checkpoint_reply(session, tenant_id=tenant_id, session_key=session_key)

        reply = session.answer(text)

        if session.phase == InterviewPhase.CHECKPOINT and not session.checkpoint_docs_written:
            # First-ever arrival — this answer() call just transitioned
            # REVIEW -> CHECKPOINT inside itself.
            reply = f"{reply}\n\n{_checkpoint_reply(session, tenant_id=tenant_id, session_key=session_key)}"
            return reply

        if session.is_finished():
            if session.phase.value == "done":
                reply = f"{reply}\n\n{_finish_reply(session, tenant_id=tenant_id)}"
            # No explicit _forget_checkpoint_state() call needed here — it's
            # a registered session_store removal hook now (see the
            # _checkpoint_state module comment) and fires automatically the
            # moment this clear() actually removes the entry, or already
            # fired earlier if a newer session replaced this one first.
            # expected=session: atomic check-and-clear under session_store's
            # own lock — a get()-then-clear() (even with a re-check in
            # between, as an earlier `_owns_slot()` helper here did) leaves a
            # window between the two separate lock acquisitions where a
            # concurrent /plugin-builder start for the same caller can
            # install a newer, legitimate session that this stale session's
            # clear() would then wipe. `expected=` closes that window inside
            # session_store.clear() itself (ADR-0262/0263 review round 5,
            # Backend finding — the round-4 `_owns_slot()` version of this
            # guard was itself TOCTOU-vulnerable, reproduced live). `session`'s
            # own result (idea, classification, scaffold) is unaffected
            # either way — only the SHARED store's bookkeeping needs this.
            session_store.clear(tenant_id, session_key, expected=session)
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


def command(
    arg: str,
    *,
    tenant_id: str,
    session_key: str,
    idea_first: bool = False,
    checkpoint_enabled: bool = False,
    e2e_tests_enabled: bool = False,
) -> str:
    """Handle the literal ``/plugin-builder [status|cancel]`` invocation.

    Caller is responsible for the feature-flag check before calling this —
    see module docstring. The three ADR-0262 flag values are the caller's
    own feature-flag lookups, passed straight through to
    ``session_store.start()`` for a freshly-started session; they have no
    effect on ``status``/``cancel`` against an already-running one (a
    session's flags are fixed at start time, same as ADR-0253's original
    design never let mid-interview state depend on a flag re-read).
    """
    sub = arg.strip().lower()
    if sub in ("cancel", "stop"):
        existing = session_store.get(tenant_id, session_key)
        if existing is None:
            return "No Plugin-Builder interview is active."
        # No explicit _forget_checkpoint_state() call needed — registered
        # session_store removal hook, see the _checkpoint_state comment.
        # expected=existing: same atomic guard as drive()'s finish path — a
        # delayed/retried "cancel" (bridge retry, double-click) must not be
        # able to wipe a brand-new, unrelated session that was started for
        # this caller in between this get() and the clear() below
        # (ADR-0262/0263 review round 5, Gates + Backend findings, both
        # reproduced live: this exact branch had no ownership guard at all,
        # not even the TOCTOU-vulnerable one drive() had).
        session_store.clear(tenant_id, session_key, expected=existing)
        return "Plugin-Builder interview cancelled. Nothing was written."
    if sub == "status":
        existing = session_store.get(tenant_id, session_key)
        if existing is None:
            return "No Plugin-Builder interview is active. Type /plugin-builder to start one."
        return f"Interview in progress (phase: {existing.phase.value}).\n\n{existing.ask()}"

    # Symmetric with ideation.start()'s own cross-store check (which clears
    # an in-progress plain interview when --ideas starts) — a plain
    # /plugin-builder used to leave an active `--ideas` dialogue for the
    # same caller running, orphaned: `_plugin_builder_continue` checks
    # ideation BEFORE session_store, so every answer to this NEW interview
    # kept getting silently swallowed by the stale ideation session instead
    # (ADR-0262/0263 review round 2, Gates finding 1 — reproduced live
    # through the real bridge entry point). Held under the SAME
    # `session_store.cross_store_lock` `ideation.start()` uses for its own
    # half of this swap — two concurrent calls (one plain, one --ideas) for
    # the same caller could otherwise both pass their "is the other store
    # active?" check before either had written (round 3, Gates finding;
    # reproduced with a real thread-barrier test).
    from . import ideation as _ideation  # noqa: PLC0415 — see module docstring's
    # existing lazy-import convention (output_dir's forge.paths import)
    with session_store.cross_store_lock:
        existing_ideation = _ideation.get(tenant_id, session_key)
        replaced_ideas = existing_ideation is not None
        if replaced_ideas:
            # expected=: same reasoning as ideation.start()'s symmetric
            # fix — this specific call is already safe under
            # cross_store_lock, but passing the object we have in hand
            # keeps every call site consistent rather than judgment-call
            # exceptions (ADR-0262/0263 review round 6, Gates finding).
            _ideation.clear(tenant_id, session_key, expected=existing_ideation)

        session = session_store.start(
            tenant_id, session_key,
            idea_first=idea_first,
            checkpoint_enabled=checkpoint_enabled,
            e2e_tests_enabled=e2e_tests_enabled,
        )
    replaced_note = (
        "(Your in-progress --ideas session was replaced by this interview "
        "— nothing was written from it.)\n\n" if replaced_ideas else ""
    )
    return (
        replaced_note +
        "Plugin-Builder (ADR-0253/ADR-0262) — a few questions, then I'll "
        "classify your idea and generate an Idea Doc, Architecture Concept, "
        "ADR and Build Plan plus a code scaffold. Reply /plugin-builder "
        "cancel any time to stop.\n\n" + session.ask()
    )


__all__ = ["output_dir", "drive", "continue_active", "command"]
