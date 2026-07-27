"""The Plugin-Builder interview: a transport-agnostic 4-phase dialog (ADR-0253).

``InterviewSession`` is a plain state machine — call :meth:`InterviewSession.ask`
for the next prompt and :meth:`InterviewSession.answer` to submit one. It has no
opinion about *how* those calls reach it: a pytest driving it directly, a CLI
loop, and the ``/plugin-builder`` console command (``session_store.py``) all
call the same two methods. This is deliberate — ADR-0253's own "Out of scope"
list rules out a bespoke web UI for Phase 1, and a session object that doesn't
know its transport is what makes that possible without a rewrite later.

**Four phases**, matching ADR-0253 exactly:

1. **Problem Understanding** — five free-text questions, extracts
   :class:`~.models.ProblemStatement`.
2. **Auto-Classification** — no question; the moment Phase 1 completes, the
   session runs :func:`classifier.classify` against the Phase-1 answers alone
   and exposes the result as :attr:`InterviewSession.preliminary_classification`
   for the caller to show. This is a preview, not the answer that ships — Phase
   4 reclassifies with the full record once dependencies are known.
3. **Dependencies & Constraints** — six questions, extracts
   :class:`~.models.DependencySpec` + :class:`~.models.Constraints`.
4. **Review & Confirmation** — the caller shows the assembled idea and the
   final classification; the session waits for ``confirm`` / ``cancel`` /
   ``restart`` rather than free text, because writing artifacts to disk is the
   one side-effecting step in this module and it must be an explicit act.

Answer validation lives on each :class:`Question` as a ``parse`` callable that
raises :class:`ValueError` with a user-facing message on bad input — the
session re-prompts on the SAME question rather than advancing, so no invalid
answer can ever reach :class:`~.models.PluginIdea`.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from .classifier import classify
from .models import (
    Classification,
    Constraints,
    DependencySpec,
    PluginIdea,
    ProblemStatement,
)


class InterviewPhase(str, Enum):
    PROBLEM = "problem"
    DEPENDENCIES = "dependencies"
    REVIEW = "review"
    DONE = "done"
    CANCELLED = "cancelled"


class InterviewError(Exception):
    """Raised by :meth:`InterviewSession.answer` for a call made in the wrong
    phase (e.g. answering after the session already reached DONE). A bad
    ANSWER never raises — it re-prompts; only a bad CALL raises."""


def _identity(text: str) -> str:
    return text.strip()


def _required_text(text: str) -> str:
    value = text.strip()
    if not value:
        raise ValueError("This can't be empty — a short answer is fine.")
    return value


def _optional_text(text: str) -> str:
    return text.strip()


def _csv_list(text: str) -> tuple[str, ...]:
    value = text.strip()
    if value.lower() in ("", "none", "n/a", "-"):
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


_YES = {"y", "yes", "yeah", "yep", "true", "1"}
_NO = {"n", "no", "nope", "false", "0", "none"}


def _yesno(text: str) -> bool:
    head = text.strip().lower().split()[:1]
    token = head[0] if head else ""
    if token in _YES:
        return True
    if token in _NO:
        return False
    raise ValueError("Please answer yes or no (e.g. 'yes' or 'no, only local').")


def _mvp_or_full(text: str) -> bool:
    token = text.strip().lower()
    if token.startswith("mvp"):
        return True
    if token.startswith("full"):
        return False
    raise ValueError("Please answer 'mvp' or 'full'.")


@dataclass(frozen=True)
class Question:
    id: str
    phase: InterviewPhase
    prompt: str
    parse: Callable[[str], Any] = _identity


QUESTION_BANK: tuple[Question, ...] = (
    # ── Phase 1: Problem Understanding ──────────────────────────────────────
    Question("plugin_name", InterviewPhase.PROBLEM,
              "What should we call this plugin? (a short working name)",
              _required_text),
    Question("problem", InterviewPhase.PROBLEM,
              "What problem does the plugin solve?", _required_text),
    Question("target_audience", InterviewPhase.PROBLEM,
              "Who benefits from it — the target audience?", _required_text),
    Question("existing_solutions", InterviewPhase.PROBLEM,
              "What existing solutions or workarounds exist today? "
              "('none' is a fine answer)", _optional_text),
    Question("time_scope", InterviewPhase.PROBLEM,
              "What's your time/scope constraint — a quick MVP, or a fuller "
              "build?", _required_text),
    # ── Phase 3: Dependencies & Constraints ─────────────────────────────────
    Question("external_libraries", InterviewPhase.DEPENDENCIES,
              "Any external libraries or services needed? Comma-separated, "
              "or 'none'.", _csv_list),
    Question("requires_auth", InterviewPhase.DEPENDENCIES,
              "Does it need authentication or credentials? (yes/no)", _yesno),
    Question("requires_network_egress", InterviewPhase.DEPENDENCIES,
              "Does it call out over the network? (yes/no)", _yesno),
    Question("egress_hosts", InterviewPhase.DEPENDENCIES,
              "If yes — which host(s)? Comma-separated, or 'none'.", _csv_list),
    Question("platform_constraints", InterviewPhase.DEPENDENCIES,
              "Any platform or version constraints? ('none' is fine)",
              _optional_text),
    Question("mvp_only", InterviewPhase.DEPENDENCIES,
              "Building the MVP only, or full scope from day one? "
              "(mvp/full)", _mvp_or_full),
    Question("scope_notes", InterviewPhase.DEPENDENCIES,
              "Anything else that bounds the scope? ('none' is fine)",
              _optional_text),
)

_BY_PHASE: dict[InterviewPhase, tuple[Question, ...]] = {
    phase: tuple(q for q in QUESTION_BANK if q.phase == phase)
    for phase in (InterviewPhase.PROBLEM, InterviewPhase.DEPENDENCIES)
}

_REVIEW_TOKENS = {
    "confirm": "confirm", "yes": "confirm", "y": "confirm",
    "cancel": "cancel", "no": "cancel", "n": "cancel",
    "restart": "restart",
}


@dataclass
class InterviewSession:
    """One in-progress (or finished) interview. Mutable — the whole point of a
    session object is that repeated ``answer()`` calls accumulate state."""

    session_id: str
    phase: InterviewPhase = InterviewPhase.PROBLEM
    _answer_index: int = field(default=0, repr=False)
    _answers: dict[str, Any] = field(default_factory=dict, repr=False)
    preliminary_classification: Classification | None = field(default=None, repr=False)
    final_classification: Classification | None = field(default=None, repr=False)
    idea: PluginIdea | None = field(default=None, repr=False)
    #: Guards answer() against concurrent calls on the SAME session — e.g. a
    #: double-submit or a client retry racing the original request. Without
    #: this, two overlapping answer() calls can interleave their read of
    #: _answer_index/_answers with their write, silently corrupting which
    #: answer lands under which question id. Excluded from repr/eq — a lock
    #: has no meaningful representation or equality.
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    # ── Driving the session ─────────────────────────────────────────────────

    def current_question(self) -> Question | None:
        """The question awaiting an answer, or ``None`` in REVIEW/DONE/CANCELLED."""
        bank = _BY_PHASE.get(self.phase)
        if bank is None:
            return None
        if self._answer_index >= len(bank):
            return None
        return bank[self._answer_index]

    def ask(self) -> str:
        """The prompt for the caller to show right now."""
        if self.phase == InterviewPhase.CANCELLED:
            return "Interview cancelled. Nothing was written."
        if self.phase == InterviewPhase.DONE:
            return "Interview already complete."
        question = self.current_question()
        if question is not None:
            return question.prompt
        if self.phase == InterviewPhase.REVIEW:
            return self._review_prompt()
        raise AssertionError(f"no prompt for phase {self.phase!r}")  # pragma: no cover

    def answer(self, text: str) -> str:
        """Submit an answer (or a review decision). Returns the next prompt.

        Raises :class:`InterviewError` only for a call made after the session
        already finished — never for a bad answer, which re-prompts instead.
        """
        with self._lock:
            return self._answer_locked(text)

    def _answer_locked(self, text: str) -> str:
        if self.phase in (InterviewPhase.DONE, InterviewPhase.CANCELLED):
            raise InterviewError(
                f"this session is {self.phase.value} — start a new one"
            )
        if self.phase == InterviewPhase.REVIEW:
            return self._answer_review(text)

        question = self.current_question()
        assert question is not None  # phase invariant: PROBLEM/DEPENDENCIES always have one
        try:
            parsed = question.parse(text)
        except ValueError as exc:
            return f"{exc}\n\n{question.prompt}"

        self._answers[question.id] = parsed
        self._answer_index += 1

        if self.current_question() is not None:
            return self.ask()

        # Phase complete — advance.
        if self.phase == InterviewPhase.PROBLEM:
            self.preliminary_classification = classify(self._partial_idea())
            self.phase = InterviewPhase.DEPENDENCIES
            self._answer_index = 0
            preview = self._classification_summary(self.preliminary_classification)
            return f"{preview}\n\n{self.ask()}"

        if self.phase == InterviewPhase.DEPENDENCIES:
            self.idea = self._build_idea()
            self.final_classification = classify(self.idea)
            self.phase = InterviewPhase.REVIEW
            return self.ask()

        raise AssertionError(f"unreachable phase {self.phase!r}")  # pragma: no cover

    def cancel(self) -> str:
        self.phase = InterviewPhase.CANCELLED
        return self.ask()

    def is_finished(self) -> bool:
        return self.phase in (InterviewPhase.DONE, InterviewPhase.CANCELLED)

    def result(self) -> tuple[PluginIdea, Classification] | None:
        """``(idea, classification)`` once confirmed — ``None`` otherwise."""
        if self.phase != InterviewPhase.DONE:
            return None
        assert self.idea is not None and self.final_classification is not None
        return self.idea, self.final_classification

    # ── Internals ────────────────────────────────────────────────────────────

    def _partial_idea(self) -> PluginIdea:
        """Best-effort idea built from Phase-1 answers only, for the preview
        classification. Dependencies default empty — Phase 3 fills them in for
        the FINAL classification computed in :meth:`answer`."""
        return PluginIdea(
            plugin_name=self._answers.get("plugin_name", ""),
            problem=ProblemStatement(
                problem=self._answers.get("problem", ""),
                target_audience=self._answers.get("target_audience", ""),
                existing_solutions=self._answers.get("existing_solutions", ""),
                time_scope=self._answers.get("time_scope", ""),
            ),
            dependencies=DependencySpec(),
            constraints=Constraints(),
            raw_answers={k: str(v) for k, v in self._answers.items()},
        )

    def _build_idea(self) -> PluginIdea:
        a = self._answers
        return PluginIdea(
            plugin_name=a["plugin_name"],
            problem=ProblemStatement(
                problem=a["problem"],
                target_audience=a["target_audience"],
                existing_solutions=a["existing_solutions"],
                time_scope=a["time_scope"],
            ),
            dependencies=DependencySpec(
                external_libraries=a["external_libraries"],
                requires_auth=a["requires_auth"],
                requires_network_egress=a["requires_network_egress"],
                egress_hosts=a["egress_hosts"],
            ),
            constraints=Constraints(
                platform_constraints=a["platform_constraints"],
                mvp_only=a["mvp_only"],
                scope_notes=a["scope_notes"],
            ),
            raw_answers={k: str(v) for k, v in a.items()},
        )

    @staticmethod
    def _classification_summary(c: Classification) -> str:
        lines = [
            f"**Preliminary classification:** {c.kind.value} "
            f"(Tier {c.tier.value}, confidence {c.confidence:.0%})",
            c.rationale,
        ]
        if c.plugin_type:
            lines.append(f"Plugin type: `{c.plugin_type}`")
        for flag in c.risk_flags:
            lines.append(f"⚠ {flag}")
        lines.append(
            "A few more questions about dependencies and constraints, then "
            "you'll get a final classification to confirm."
        )
        return "\n".join(lines)

    def _review_prompt(self) -> str:
        assert self.idea is not None and self.final_classification is not None
        idea, c = self.idea, self.final_classification
        lines = [
            f"**Review — {idea.plugin_name}**",
            "",
            f"Problem: {idea.problem.problem}",
            f"Audience: {idea.problem.target_audience}",
            f"Scope: {idea.constraints.scope_notes or '(none noted)'} "
            f"({'MVP' if idea.constraints.mvp_only else 'full scope'})",
            "",
            f"**Classification:** {c.kind.value} (Tier {c.tier.value}, "
            f"confidence {c.confidence:.0%})",
            c.rationale,
        ]
        if c.plugin_type:
            lines.append(f"Plugin type: `{c.plugin_type}`")
        for flag in c.risk_flags:
            lines.append(f"⚠ {flag}")
        lines += [
            "",
            "This generates an Idea Doc, Architecture Concept, ADR, Build Plan "
            "and a code scaffold — nothing is written to disk until you "
            "confirm.",
            "",
            "Reply **confirm** to write the artifacts, **restart** to redo the "
            "interview, or **cancel** to stop.",
        ]
        return "\n".join(lines)

    def _answer_review(self, text: str) -> str:
        decision = _REVIEW_TOKENS.get(text.strip().lower())
        if decision is None:
            return (
                f"I didn't understand {text!r} — reply confirm, restart or "
                "cancel.\n\n" + self._review_prompt()
            )
        if decision == "cancel":
            return self.cancel()
        if decision == "restart":
            self.phase = InterviewPhase.PROBLEM
            self._answer_index = 0
            self._answers = {}
            self.preliminary_classification = None
            self.final_classification = None
            self.idea = None
            return self.ask()
        # confirm
        self.phase = InterviewPhase.DONE
        return "Confirmed. Writing artifacts…"


__all__ = [
    "InterviewPhase",
    "InterviewError",
    "InterviewSession",
    "Question",
    "QUESTION_BANK",
]
