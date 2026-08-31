"""``/plugin-builder --ideas`` — AI-moderated co-ideation (ADR-0263 + its LDD
amendment).

Three LDD-governed stages, each a genuinely separate task (architect-mode's
own rule: creativity level is task-scoped, never switched mid-task — see the
ADR-0263 amendment) connected by explicit hand-offs, never a live dial:

1. **Ideation** (this module, ``IdeationStage.ACK`` → ``ROUNDS``) — bounded,
   grounded proposals. "Grounded" is enforced structurally: every
   :class:`GroundedProposal` this module can produce comes from a live
   lookup (the Extension-Surface Map's unconsumed types, or Marketplace
   category sparsity) — there is no code path that invents one, which is
   the honest, deterministic-module version of "no ungrounded wildcard
   suggestions." A round cap (:data:`ROUND_CAP`) prevents an unbounded
   back-and-forth; reaching it without convergence exits honestly rather
   than forcing a pick.
2. **Formalization** — NOT owned by this module. Convergence hands the
   agreed idea text straight into a real, normal idea-first
   :class:`~.interview.InterviewSession` (pre-answering its first
   question) via ``session_store`` — the SAME session a plain
   ``/plugin-builder`` call would create, continued by the SAME
   ``turn.continue_active`` every other turn already uses. Ideas mode is a
   front door onto that pipeline, never a parallel copy of it.
3. **Build** — further downstream still, inside ``turn.py``'s
   checkpoint/scaffold/test-generation steps. Not this module's concern at
   all.

The ADR-0263 amendment requires the ``inventive``-creativity acknowledgment
gate to be reworded into plain, non-jargon language for a possibly
non-technical user — :func:`_ack_prompt` is that wording; it never mentions
"rubric," "prior-art," or "creativity level."
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from . import session_store
from .interview import InterviewPhase
from .language import LanguagePin

ROUND_CAP = 3
MAX_SESSIONS = 256
SESSION_TTL_SECONDS = 6 * 3600


class IdeationStage(str, Enum):
    ACK = "ack"
    ROUNDS = "rounds"
    DONE = "done"


@dataclass(frozen=True)
class GroundedProposal:
    text: str
    #: Machine-stable citation — also the dedup key across rounds.
    source: str


def _find_marketplace_plugins_dir() -> "Path | None":
    """``Corvin-Marketplace/plugins/`` as a sibling of this repo checkout, if
    present. Absent in most environments (a separate repo) — every caller
    treats ``None`` as "this grounding source is unavailable," never an
    error; fewer proposals is the correct degradation, not a crash."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent.parent / "Corvin-Marketplace" / "plugins"
        if candidate.is_dir():
            return candidate
    return None


def grounded_proposals(seen_sources: frozenset, limit: int = 2) -> tuple[GroundedProposal, ...]:
    """Up to ``limit`` proposals, each citing a live, inspectable source,
    skipping anything already in ``seen_sources`` so repeated rounds don't
    repeat themselves. Never raises — a missing optional dependency (
    ``corvin_plugins``) or an unreadable Marketplace checkout just yields
    fewer proposals, same degradation shape as the rest of this package."""
    proposals: list[GroundedProposal] = []

    try:
        from corvin_plugins.surface_map import surface_for, unconsumed_types

        for plugin_type in unconsumed_types():
            source = f"surface-map-gap:{plugin_type}"
            if source in seen_sources:
                continue
            surface = surface_for(plugin_type)
            proposals.append(GroundedProposal(
                text=(
                    f"A '{plugin_type}' plugin. Nothing in CorvinOS invokes "
                    f"this type yet ({surface.dead_reason or 'no consumer wired'}) "
                    "— building one is a real, documented gap, though it "
                    "won't run automatically until that gap closes."
                ),
                source=source,
            ))
    except ImportError:
        pass

    marketplace_dir = _find_marketplace_plugins_dir()
    if marketplace_dir is not None:
        try:
            categories = sorted(p for p in marketplace_dir.iterdir() if p.is_dir())
        except OSError:
            categories = []
        if not categories:
            source = "marketplace-sparsity:plugins"
            if source not in seen_sources:
                proposals.append(GroundedProposal(
                    text=(
                        "The community Marketplace's plugins/ folder is "
                        "empty right now — any working plugin you publish "
                        "would be the first one there."
                    ),
                    source=source,
                ))
        else:
            for category in categories:
                try:
                    entry_count = sum(1 for _ in category.iterdir())
                except OSError:
                    continue
                if entry_count <= 1:
                    source = f"marketplace-sparsity:{category.name}"
                    if source in seen_sources:
                        continue
                    plural = "y" if entry_count == 1 else "ies"
                    proposals.append(GroundedProposal(
                        text=(
                            f"The '{category.name}' Marketplace category "
                            f"has {entry_count} entr{plural} so far — "
                            "there's room for more there."
                        ),
                        source=source,
                    ))

    return tuple(proposals[:limit])


@dataclass
class IdeationSession:
    session_id: str
    idea_first: bool
    checkpoint_enabled: bool
    e2e_tests_enabled: bool
    stage: IdeationStage = IdeationStage.ACK
    round_count: int = 0
    seen_sources: frozenset = field(default_factory=frozenset)
    language: LanguagePin = field(default_factory=LanguagePin)
    _last_shown: "tuple[GroundedProposal, ...]" = field(default_factory=tuple, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)


@dataclass
class _Entry:
    session: IdeationSession
    last_touched: float = 0.0


_store_lock = threading.Lock()
_sessions: dict[tuple[str, str], _Entry] = {}


def _remove_locked(key: tuple[str, str]) -> None:
    """The ONLY place an entry is ever deleted from ``_sessions`` — mirrors
    ``session_store._remove_locked``. No dependent state currently keys off
    ``id(IdeationSession)`` the way ``turn.py``'s ``_checkpoint_state`` does
    for the sibling ``session_store``, so there is no removal-hook registry
    here today (``session_store.register_removal_hook`` has no equivalent —
    "no subject today", same doctrine as ``user_backend`` in CLAUDE.md).
    Routing every removal through this one function anyway — rather than
    the bare dict assignment ``start()`` used before round 7 — costs
    nothing and means a future dependent only needs a hook registry added
    HERE, not a new removal path hunted down across this module first
    (ADR-0262/0263 review round 7, Gates finding: ``start()``'s replace
    path was the exact same unguarded shape ``session_store.start()`` had
    before round 6 fixed it)."""
    _sessions.pop(key, None)


def _evict_stale_locked(now: float) -> None:
    stale = [k for k, e in _sessions.items() if now - e.last_touched > SESSION_TTL_SECONDS]
    for k in stale:
        _remove_locked(k)


def get(tenant_id: str, session_key: str) -> "IdeationSession | None":
    """The in-progress ideation session for this caller, or ``None`` —
    mirrors ``session_store.get()``'s shape so callers outside this module
    (``turn.command()``) can pass the actual object to ``clear(expected=)``
    instead of only knowing whether one exists (see :func:`is_active`)."""
    with _store_lock:
        now = time.time()
        _evict_stale_locked(now)
        entry = _sessions.get((tenant_id, session_key))
        if entry is None:
            return None
        entry.last_touched = now
        return entry.session


#: Kept as the internal name several call sites in this module already use.
_get = get


def clear(
    tenant_id: str, session_key: str, *, expected: "IdeationSession | None" = None
) -> None:
    """Drop the ideation session for this caller, if any. Safe to call when
    none exists — same contract and same ``expected=`` atomic
    check-and-delete as ``session_store.clear`` (see its docstring): pass
    the specific session you're finishing/cancelling whenever you have it,
    so a concurrent ``start()`` for the same caller can't be silently wiped
    by a stale caller's cleanup (ADR-0262/0263 review round 5, Backend +
    Gates findings)."""
    with _store_lock:
        key = (tenant_id, session_key)
        if expected is not None:
            entry = _sessions.get(key)
            if entry is None or entry.session is not expected:
                return
        _remove_locked(key)


def is_active(tenant_id: str, session_key: str) -> bool:
    """Whether an ideation dialogue is currently in progress for this
    caller — a convenience boolean over :func:`get` for a caller that
    doesn't need the object itself."""
    return get(tenant_id, session_key) is not None


def _ack_prompt(language: str) -> str:
    """The ADR-0263-amendment wording — plain language, no LDD jargon.
    Functionally the same one-time consent gate architect-mode's
    ``inventive`` level requires; worded for someone who has never heard of
    LDD, not for a skill invocation."""
    if language == "de":
        return (
            "Bevor wir loslegen: Ich schlage hier auch mal unkonventionelle "
            "Ideen vor — nicht alles davon wird sofort umsetzbar sein, aber "
            "ich sage jedes Mal dazu, worauf sich ein Vorschlag stützt. "
            "Passt das für dich? (ja/nein)"
        )
    return (
        "Before we start: I'll also throw out some less-obvious ideas here "
        "— not everything will be immediately buildable, but I'll always "
        "say what each suggestion is based on. Sound good? (yes/no)"
    )


_ACK_YES = {"acknowledged", "ack", "yes", "y", "sure", "ok", "okay", "einverstanden", "ja", "j"}
_ACK_NO = {"no", "n", "nein", "skip", "später", "spaeter"}
_MORE = {"more", "mehr", "weiter"}
_CANCEL = {"cancel", "abbrechen", "stop"}


def _format_round(proposals: "tuple[GroundedProposal, ...]", language: str) -> str:
    if not proposals:
        header = (
            "Mir fällt gerade nichts Neues ein, das sich an etwas Konkretem "
            "festmachen lässt."
            if language == "de" else
            "I'm out of grounded suggestions right now — nothing concrete "
            "left to point at."
        )
        return header
    header = "Ein paar Ideen, jede mit Quelle:" if language == "de" else "A couple of ideas, each with its source:"
    lines = [header]
    for i, p in enumerate(proposals, start=1):
        lines.append(f"{i}. {p.text} [{p.source}]")
    footer = (
        "\nAntworte mit einer Nummer, um damit weiterzumachen, mit 'mehr' "
        "für weitere Vorschläge, oder beschreib einfach deine eigene Idee."
        if language == "de" else
        "\nReply with a number to run with one, 'more' for further "
        "suggestions, or just describe your own idea."
    )
    return "\n".join(lines) + footer


def _handoff_to_interview(session: IdeationSession, idea_text: str, *, tenant_id: str, session_key: str) -> str:
    """Convergence (or an explicit decline of the ack gate) — hand the idea
    text into a REAL idea-first :class:`~.interview.InterviewSession` via
    ``session_store``, pre-answering its first question, so every turn after
    this one is driven by the exact same ``turn.continue_active`` path a
    plain ``/plugin-builder`` session already uses. Nothing here writes to
    disk — same "converging writes nothing" rule the ADR requires.

    Runs under ``session_store.cross_store_lock`` — a THIRD writer to
    ``session_store`` alongside ``ideation.start()`` and ``turn.command()``,
    both of which already hold this lock across their own check-clear-write
    sequence (ADR-0262/0263 review round 3). This function does the same
    clear-then-write shape but was missed in that round — a concurrent plain
    ``/plugin-builder`` call could otherwise land its ``session_store.start()``
    in the same window as this one and get silently clobbered (ADR-0262/0263
    review round 4, Gates finding, reproduced with a monkeypatched-timing
    thread repro). Safe against ``continue_active``'s own ``session._lock``
    (held by the caller for this whole call): no other path acquires an
    ``IdeationSession``/``InterviewSession`` instance lock while holding
    ``cross_store_lock``, so the acquisition order (instance lock, if any,
    then ``cross_store_lock``) never reverses anywhere else.
    """
    with session_store.cross_store_lock:
        clear(tenant_id, session_key, expected=session)
        interview_session = session_store.start(
            tenant_id, session_key,
            idea_first=True,  # ideas-mode always feeds the idea-first pipeline
            checkpoint_enabled=session.checkpoint_enabled,
            e2e_tests_enabled=session.e2e_tests_enabled,
        )
    lead_in = (
        "Übernommen — weiter im normalen Ablauf."
        if session.language.language == "de" else
        "Got it — continuing in the normal flow."
    )
    if not idea_text:
        return f"{lead_in}\n\n{interview_session.ask()}"
    assert interview_session.phase == InterviewPhase.IDEA
    next_prompt = interview_session.answer(idea_text)
    return f"{lead_in}\n\n{next_prompt}"


def start(
    tenant_id: str,
    session_key: str,
    *,
    idea_first: bool,
    checkpoint_enabled: bool = False,
    e2e_tests_enabled: bool = False,
) -> str:
    """Start (or restart) an ideation dialogue, replacing any prior one.

    Also replaces any IN-PROGRESS plain interview (``session_store``) for
    the same caller. The two stores are separate (ideation.py owns its own
    session type/TTL, distinct from ``InterviewSession``), and nothing
    previously enforced they couldn't both hold a live session for the same
    ``(tenant_id, session_key)`` at once — a caller who ran ``/plugin-builder``
    partway, then typed ``/plugin-builder --ideas``, silently abandoned their
    in-progress interview with no notice (ADR-0262/0263 review round 1,
    Backend finding 4). ``session_store.start()`` already documents
    "replacing any prior one" for the symmetric case; this makes the
    cross-store case explicit and tells the caller, rather than losing their
    progress quietly.

    The check-other/clear-other/write-own sequence below runs under
    ``session_store.cross_store_lock`` — held across BOTH stores' halves of
    this swap, not just each store's own internal lock — because two
    concurrent calls for the same caller (one starting plain, one starting
    ``--ideas``) could otherwise each pass their "is the other active?"
    check before either had written, leaving both stores holding a session
    at once (ADR-0262/0263 review round 3, Gates finding — reproduced with
    a real thread-barrier test before this lock was added).
    """
    with session_store.cross_store_lock:
        existing_interview = session_store.get(tenant_id, session_key)
        replaced_interview = existing_interview is not None
        if replaced_interview:
            # expected=: consistent with every OTHER clear() call site that
            # has a session object in hand, even though this one is already
            # provably safe without it — the whole point of `expected=`
            # (round 5) is that a caller with an object never has a reason
            # to reach for the unconditional form (ADR-0262/0263 review
            # round 6, Gates finding).
            session_store.clear(tenant_id, session_key, expected=existing_interview)

        with _store_lock:
            now = time.time()
            _evict_stale_locked(now)
            key = (tenant_id, session_key)
            if key in _sessions:
                # Replacing an existing ideation session for this caller —
                # route through _remove_locked, same as every other removal,
                # instead of the bare dict assignment this used before round
                # 7 (see _remove_locked's docstring).
                _remove_locked(key)
            if len(_sessions) >= MAX_SESSIONS:
                oldest_key = min(_sessions, key=lambda k: _sessions[k].last_touched)
                _remove_locked(oldest_key)
            session = IdeationSession(
                session_id=f"{tenant_id}:{session_key}:ideas",
                idea_first=idea_first,
                checkpoint_enabled=checkpoint_enabled,
                e2e_tests_enabled=e2e_tests_enabled,
            )
            _sessions[key] = _Entry(session=session, last_touched=now)

    prompt = _ack_prompt("en")
    if replaced_interview:
        prompt = (
            "(Your in-progress /plugin-builder interview was replaced by "
            "this --ideas session — nothing was written from it.)\n\n" + prompt
        )
    return prompt


def continue_active(text: str, *, tenant_id: str, session_key: str) -> "str | None":
    """Drive the active ideation session for ``(tenant_id, session_key)``,
    if any — ``None`` when none is active, same contract as
    ``turn.continue_active``."""
    session = _get(tenant_id, session_key)
    if session is None:
        return None
    with session._lock:
        return _drive_locked(session, text, tenant_id=tenant_id, session_key=session_key)


def _drive_locked(session: IdeationSession, text: str, *, tenant_id: str, session_key: str) -> str:
    stripped = text.strip()
    token = stripped.lower()

    if session.stage == IdeationStage.ACK:
        if token in _ACK_NO:
            session.stage = IdeationStage.DONE
            return _handoff_to_interview(session, "", tenant_id=tenant_id, session_key=session_key)
        if token in _ACK_YES:
            session.language.resolve(stripped)
            session.stage = IdeationStage.ROUNDS
            proposals = grounded_proposals(session.seen_sources)
            session._last_shown = proposals
            session.seen_sources = session.seen_sources | {p.source for p in proposals}
            return _format_round(proposals, session.language.language or "en")
        return _ack_prompt("en")

    if session.stage == IdeationStage.ROUNDS:
        session.language.resolve(stripped)
        lang = session.language.language or "en"

        if token in _CANCEL:
            session.stage = IdeationStage.DONE
            clear(tenant_id, session_key, expected=session)
            return "Abgebrochen. Nichts wurde geschrieben." if lang == "de" else "Cancelled. Nothing was written."

        if stripped.isdigit() and session._last_shown:
            idx = int(stripped) - 1
            if 0 <= idx < len(session._last_shown):
                chosen = session._last_shown[idx]
                session.stage = IdeationStage.DONE
                return _handoff_to_interview(session, chosen.text, tenant_id=tenant_id, session_key=session_key)
            return _format_round(session._last_shown, lang)

        if token in _MORE:
            session.round_count += 1
            if session.round_count >= ROUND_CAP:
                session.stage = IdeationStage.DONE
                exit_msg = (
                    "Wir sind auf nichts Konkretes gekommen — magst du mir "
                    "deine eigene Idee direkt beschreiben?"
                    if lang == "de" else
                    "We didn't land on anything concrete — want to just "
                    "describe your own idea directly?"
                )
                return f"{exit_msg}\n\n{_handoff_to_interview(session, '', tenant_id=tenant_id, session_key=session_key)}"
            proposals = grounded_proposals(session.seen_sources)
            session._last_shown = proposals
            session.seen_sources = session.seen_sources | {p.source for p in proposals}
            return _format_round(proposals, lang)

        # Any other free text is read as the user's own contribution — per
        # ADR-0263, "both sides contribute," and the most natural signal
        # that a user has converged is that they just said what they want.
        session.stage = IdeationStage.DONE
        return _handoff_to_interview(session, stripped, tenant_id=tenant_id, session_key=session_key)

    return "Something went wrong — start over with /plugin-builder --ideas."  # pragma: no cover


__all__ = [
    "IdeationStage",
    "IdeationSession",
    "GroundedProposal",
    "ROUND_CAP",
    "grounded_proposals",
    "start",
    "continue_active",
    "clear",
    "get",
    "is_active",
]
