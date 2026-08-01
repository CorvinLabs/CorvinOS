"""The Plugin-Builder interview: a transport-agnostic dialog (ADR-0253 / ADR-0262).

``InterviewSession`` is a plain state machine — call :meth:`InterviewSession.ask`
for the next prompt and :meth:`InterviewSession.answer` to submit one. It has no
opinion about *how* those calls reach it: a pytest driving it directly, a CLI
loop, and the ``/plugin-builder`` console command (``session_store.py``) all
call the same two methods.

**Two question flows**, selected by two independent, default-off constructor
flags (``idea_first``, ``checkpoint_enabled`` — ADR-0262). With both off, the
phase sequence and every prompt are byte-identical to the original ADR-0253
shape; this is the regression contract the flag-off test suite checks.

*Legacy flow (default)*:

1. **Problem Understanding** — five free-text questions, extracts
   :class:`~.models.ProblemStatement`.
2. **Auto-Classification** — no question; a preview classification runs the
   moment Phase 1 completes.
3. **Dependencies & Constraints** — six questions, extracts
   :class:`~.models.DependencySpec` + :class:`~.models.Constraints`.
4. **Review & Confirmation** — waits for ``confirm`` / ``cancel`` / ``restart``.

*Idea-first flow (``idea_first=True``)*:

1. **Idea** — ONE open question ("tell me about your plugin idea") plus a
   short-name question, instead of the five-question form. The free text is
   run through :func:`classifier.extract_dependency_hints` to try to resolve
   the SAFETY-relevant Dependencies fields (external libs, auth, network
   egress, egress hosts — the exact ADR-0247 Validation Gate inputs)
   automatically. Language is detected from this first answer and pinned for
   the rest of the session (:class:`~.language.LanguagePin`).
2. **Confirm gaps** — asks ONLY the fields extraction could not resolve.
   Zero unresolved fields means this phase is skipped entirely — the
   session goes straight to Review with no extra questions at all.
3. **Review & Confirmation** — same as the legacy flow.
4. **Checkpoint** (only when ``checkpoint_enabled=True``) — after Review's
   ``confirm``, the four docs are written and a summary is shown (built by
   ``checkpoint.py``, using this session's :attr:`idea`/:attr:`final_classification`
   via :meth:`result`) — a SECOND ``confirm`` is required before the code
   scaffold (and, if ``e2e_tests_enabled``, generated tests) get written.
   With ``checkpoint_enabled=False``, Review's ``confirm`` goes straight to
   ``DONE`` and docs + scaffold are written together, exactly as ADR-0253
   always did.

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

from .classifier import (
    DEPENDENCY_FIELDS,
    classify,
    extract_dependency_hints,
    problem_statement_from_idea_text,
)
from .language import DEFAULT_LANGUAGE, LanguagePin
from .models import (
    Classification,
    Constraints,
    DependencySpec,
    PluginIdea,
    ProblemStatement,
)


class InterviewPhase(str, Enum):
    # Legacy (ADR-0253) flow
    PROBLEM = "problem"
    DEPENDENCIES = "dependencies"
    # Idea-first (ADR-0262) flow
    IDEA = "idea"
    CONFIRM_GAPS = "confirm_gaps"
    # Shared tail
    REVIEW = "review"
    CHECKPOINT = "checkpoint"
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


#: Generous cap for the free-form idea description — plenty for describing a
#: plugin idea, small enough to bound the worst case of every downstream
#: text-scanning pass (classifier.extract_dependency_hints's regexes) to a
#: few milliseconds regardless of how the text is shaped. Defense-in-depth
#: on top of (not instead of) classifier.py's own per-token cap: round 1's
#: ReDoS fix (quadratic single-token blowup) plus round 2's follow-up
#: (linear-but-real cost from many moderate-length tokens, unbounded
#: without an overall cap) both showed a downstream regex fix alone isn't
#: enough — the actual system boundary (the answer a user can type) needed
#: its own limit too (ADR-0262 review round 2, Compliance finding 1).
_MAX_IDEA_TEXT_LEN = 8000


def _required_text_bounded(text: str) -> str:
    value = _required_text(text)
    if len(value) > _MAX_IDEA_TEXT_LEN:
        raise ValueError(
            f"That's a bit long ({len(value)} characters) — could you "
            f"describe it in under {_MAX_IDEA_TEXT_LEN} characters?"
        )
    return value


#: `plugin_name` is documented to the user as "a short working name" — this
#: is generous for that, not a tuned minimum. Round 4 review found the
#: idea-first flow's OWN `plugin_name` question (unlike `idea_text`, which
#: got a bound in round 2) still used unbounded `_required_text`: a
#: multi-megabyte answer was accepted whole and later spliced verbatim into
#: every generated doc plus the code scaffold — resource use, not code
#: injection (still safe per `_display_name()`'s stripping), but exactly
#: the class of gap ADR-0262 already named a principle for and then missed
#: applying to its own second free-text field.
_MAX_NAME_LEN = 200


def _required_name_bounded(text: str) -> str:
    value = _required_text(text)
    if len(value) > _MAX_NAME_LEN:
        raise ValueError(
            f"That's a bit long for a working name ({len(value)} "
            f"characters) — could you shorten it to under {_MAX_NAME_LEN}?"
        )
    return value


def _csv_list(text: str) -> tuple[str, ...]:
    value = text.strip()
    if value.lower() in ("", "none", "n/a", "-"):
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


#: Same rationale as `_MAX_IDEA_TEXT_LEN`, applied to the CONFIRM_GAPS
#: (idea-first flow) answers specifically — round 3 review found these had
#: no length bound at all, unlike `idea_text`: a deliberately huge
#: comma-separated answer would still be accepted and stored verbatim
#: (resource use, not ReDoS — these values never reach classifier.py's
#: regexes — but unbounded all the same). The legacy DEPENDENCIES-phase
#: question bank keeps its own unbounded `_csv_list`/`_optional_text`
#: unchanged — those predate this ADR and are out of its regression scope.
_MAX_GAP_ANSWER_LEN = 2000


def _csv_list_bounded(text: str) -> tuple[str, ...]:
    if len(text) > _MAX_GAP_ANSWER_LEN:
        raise ValueError(
            f"That's a bit long ({len(text)} characters) — could you keep "
            f"it under {_MAX_GAP_ANSWER_LEN} characters?"
        )
    return _csv_list(text)


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
    #: Optional ``{"de": "...", ...}`` overrides for :meth:`text_for`. Only
    #: populated for the idea-first flow's own questions (ADR-0262 language
    #: pinning) — the legacy question bank stays English-only by design,
    #: since the legacy flow makes no language-pinning promise.
    translations: dict[str, str] = field(default_factory=dict)

    def text_for(self, language: str) -> str:
        return self.translations.get(language, self.prompt)


QUESTION_BANK: tuple[Question, ...] = (
    # ── Phase 1: Problem Understanding (legacy) ─────────────────────────────
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
    # ── Phase 3: Dependencies & Constraints (legacy) ────────────────────────
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
    # ── Phase A: Idea (idea-first, ADR-0262) ────────────────────────────────
    Question("idea_text", InterviewPhase.IDEA,
              "Tell me about your plugin idea — what does it do, and who's "
              "it for?", _required_text_bounded,
              translations={
                  "de": "Erzähl mir von deiner Plugin-Idee — was soll sie "
                        "tun, und für wen?",
              }),
    Question("plugin_name", InterviewPhase.IDEA,
              "What should we call it — a short working name?",
              _required_name_bounded,
              translations={
                  "de": "Wie sollen wir sie nennen — ein kurzer Arbeitstitel?",
              }),
)

_BY_PHASE: dict[InterviewPhase, tuple[Question, ...]] = {
    phase: tuple(q for q in QUESTION_BANK if q.phase == phase)
    for phase in (InterviewPhase.PROBLEM, InterviewPhase.DEPENDENCIES, InterviewPhase.IDEA)
}

def _yesno_de_aware(text: str) -> bool:
    """Same as :func:`_yesno` but also accepts German ja/nein — used only by
    the CONFIRM_GAPS dynamic questions, which are language-pinned."""
    head = text.strip().lower().split()[:1]
    token = head[0] if head else ""
    if token in _YES or token in ("ja", "j"):
        return True
    if token in _NO or token in ("nein",):
        return False
    raise ValueError("Please answer yes or no (e.g. 'yes' or 'no, only local').")


#: Phase-B (Confirm Gaps) candidate questions, keyed by the same field name
#: :func:`classifier.extract_dependency_hints` resolves — reuses the legacy
#: Dependencies bank's exact wording/parsers for the 4 safety-relevant fields
#: it can leave unresolved, plus German translations (ADR-0262 language
#: pinning; the legacy bank's other three DEPENDENCIES fields —
#: platform_constraints/mvp_only/scope_notes — are not safety-relevant and
#: are defaulted rather than asked in this flow, see module docstring).
_GAP_QUESTIONS: dict[str, Question] = {
    "external_libraries": Question(
        "external_libraries", InterviewPhase.CONFIRM_GAPS,
        "Any external libraries or services needed? Comma-separated, or "
        "'none'.", _csv_list_bounded,
        translations={"de": "Braucht es externe Bibliotheken oder Dienste? "
                             "Mit Komma getrennt, oder 'keine'."},
    ),
    "requires_auth": Question(
        "requires_auth", InterviewPhase.CONFIRM_GAPS,
        "Does it need authentication or credentials? (yes/no)", _yesno_de_aware,
        translations={"de": "Braucht es Authentifizierung oder Zugangsdaten? "
                             "(ja/nein)"},
    ),
    "requires_network_egress": Question(
        "requires_network_egress", InterviewPhase.CONFIRM_GAPS,
        "Does it call out over the network? (yes/no)", _yesno_de_aware,
        translations={"de": "Ruft es über das Netzwerk nach außen? (ja/nein)"},
    ),
    "egress_hosts": Question(
        "egress_hosts", InterviewPhase.CONFIRM_GAPS,
        "If yes — which host(s)? Comma-separated, or 'none'.", _csv_list_bounded,
        translations={"de": "Falls ja — welche(r) Host(s)? Mit Komma "
                             "getrennt, oder 'keine'."},
    ),
}

_REVIEW_TOKENS = {
    "confirm": "confirm", "yes": "confirm", "y": "confirm",
    "cancel": "cancel", "no": "cancel", "n": "cancel",
    "restart": "restart",
}


def classify_checkpoint_decision(text: str) -> "str | None":
    """``"confirm"`` / ``"cancel"`` / ``"restart"`` if ``text`` is one of
    those tokens (same recognition ``_answer_checkpoint`` itself uses),
    else ``None``.

    Exported so ``turn.py`` can tell a genuine ``cancel``/``restart``
    DECISION apart from an opaque retry trigger when it's about to retry a
    failed checkpoint doc-write without going through ``session.answer()``
    — a prior version treated ANY text arriving in that state as "retry",
    which silently swallowed a user's ``cancel``/``restart`` (ADR-0262
    review round 2, Backend finding 1).
    """
    return _REVIEW_TOKENS.get(text.strip().lower())


@dataclass
class InterviewSession:
    """One in-progress (or finished) interview. Mutable — the whole point of a
    session object is that repeated ``answer()`` calls accumulate state.

    ``idea_first``, ``checkpoint_enabled`` and ``e2e_tests_enabled`` default
    to ``False`` — ADR-0262's three independently-toggleable feature flags.
    All three off reproduces ADR-0253's original behavior exactly; callers
    (``session_store.start()``) are responsible for reading the actual
    feature-flag values and passing them in here, this module has no
    dependency on how flags are stored.
    """

    session_id: str
    idea_first: bool = False
    checkpoint_enabled: bool = False
    e2e_tests_enabled: bool = False
    phase: InterviewPhase = InterviewPhase.PROBLEM
    language: LanguagePin = field(default_factory=LanguagePin)
    _answer_index: int = field(default=0, repr=False)
    _answers: dict[str, Any] = field(default_factory=dict, repr=False)
    _dynamic_questions: "tuple[Question, ...] | None" = field(default=None, repr=False)
    _resolved_fields: frozenset = field(default_factory=frozenset, repr=False)
    preliminary_classification: Classification | None = field(default=None, repr=False)
    final_classification: Classification | None = field(default=None, repr=False)
    idea: PluginIdea | None = field(default=None, repr=False)
    #: Set by ``turn.py`` once it has written the checkpoint docs for the
    #: CURRENT pass through CHECKPOINT — lets ``drive()`` tell "just arrived
    #: at checkpoint, docs need writing" apart from "already here, this was
    #: just an unrecognized token re-prompt", without writing the docs twice.
    #: Reset on restart so a second pass through CHECKPOINT writes again.
    checkpoint_docs_written: bool = field(default=False, repr=False)
    #: Guards answer() against concurrent calls on the SAME session — e.g. a
    #: double-submit or a client retry racing the original request. Without
    #: this, two overlapping answer() calls can interleave their read of
    #: _answer_index/_answers with their write, silently corrupting which
    #: answer lands under which question id. Excluded from repr/eq — a lock
    #: has no meaningful representation or equality.
    #: REENTRANT (RLock, not Lock): ``turn.py``'s ``drive()`` must hold this
    #: lock across its own side effects (writing checkpoint docs / the
    #: scaffold) as well as across the ``answer()`` call itself — a plain
    #: Lock would deadlock the same thread trying to acquire it twice (once
    #: in ``drive()``, once inside ``answer()``). See ADR-0262 review round 1,
    #: Backend finding 2 — without this, two concurrent ``drive()`` calls
    #: could race the CHECKPOINT->DONE transition so that NEITHER writes the
    #: docs, reproducing finding 1's data-loss path without even needing a
    #: failed write.
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.idea_first and self.phase == InterviewPhase.PROBLEM:
            self.phase = InterviewPhase.IDEA

    # ── Driving the session ─────────────────────────────────────────────────

    def _lang(self) -> str:
        return self.language.language or DEFAULT_LANGUAGE

    def current_question(self) -> Question | None:
        """The question awaiting an answer, or ``None`` in REVIEW/CHECKPOINT/
        DONE/CANCELLED (or CONFIRM_GAPS once its dynamic list is empty)."""
        if self.phase == InterviewPhase.CONFIRM_GAPS:
            if self._dynamic_questions is None or self._answer_index >= len(self._dynamic_questions):
                return None
            return self._dynamic_questions[self._answer_index]
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
            return question.text_for(self._lang())
        if self.phase == InterviewPhase.REVIEW:
            return self._review_prompt()
        if self.phase == InterviewPhase.CHECKPOINT:
            return self._checkpoint_prompt()
        raise AssertionError(f"no prompt for phase {self.phase!r}")  # pragma: no cover

    def answer(self, text: str) -> str:
        """Submit an answer (or a review/checkpoint decision). Returns the
        next prompt.

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
        if self.phase == InterviewPhase.CHECKPOINT:
            return self._answer_checkpoint(text)

        question = self.current_question()
        assert question is not None  # phase invariant: every other phase always has one while active
        if self.phase == InterviewPhase.IDEA:
            self.language.resolve(text)
        try:
            parsed = question.parse(text)
        except ValueError as exc:
            return f"{exc}\n\n{question.text_for(self._lang())}"

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

        if self.phase == InterviewPhase.IDEA:
            return self._finish_idea_phase()

        if self.phase == InterviewPhase.CONFIRM_GAPS:
            self._finish_confirm_gaps()
            return self.ask()

        raise AssertionError(f"unreachable phase {self.phase!r}")  # pragma: no cover

    def cancel(self) -> str:
        self.phase = InterviewPhase.CANCELLED
        return self.ask()

    def is_finished(self) -> bool:
        return self.phase in (InterviewPhase.DONE, InterviewPhase.CANCELLED)

    def result(self) -> tuple[PluginIdea, Classification] | None:
        """``(idea, classification)`` once the idea is finalized — ``None``
        before that.

        Available from :data:`InterviewPhase.CHECKPOINT` onward (not just
        ``DONE``): the checkpoint step needs the finalized idea to write the
        docs it presents, before the scaffold-writing step that reaches
        ``DONE``.
        """
        if self.phase not in (InterviewPhase.CHECKPOINT, InterviewPhase.DONE):
            return None
        assert self.idea is not None and self.final_classification is not None
        return self.idea, self.final_classification

    # ── Idea-first flow internals (ADR-0262) ────────────────────────────────

    def _finish_idea_phase(self) -> str:
        idea_text = self._answers.get("idea_text", "")
        problem = problem_statement_from_idea_text(idea_text)
        extracted_deps, resolved = extract_dependency_hints(idea_text)
        self._resolved_fields = resolved
        # Stash for `_finish_confirm_gaps` to merge with any explicitly-asked
        # answers — resolved fields are never re-asked, so they must survive
        # into the final DependencySpec from here, not from `_answers`.
        self._answers["__extracted_deps__"] = extracted_deps
        self._answers["__problem__"] = problem

        partial_idea = PluginIdea(
            plugin_name=self._answers.get("plugin_name", ""),
            problem=problem,
            dependencies=extracted_deps,
            constraints=Constraints(),
            raw_answers={"idea_text": idea_text},
        )
        self.preliminary_classification = classify(partial_idea)

        pending_ids = [f for f in DEPENDENCY_FIELDS if f not in resolved]
        self._dynamic_questions = tuple(_GAP_QUESTIONS[f] for f in pending_ids)
        self.phase = InterviewPhase.CONFIRM_GAPS
        self._answer_index = 0

        preview = self._classification_summary(self.preliminary_classification)
        if not self._dynamic_questions:
            # Nothing left to ask — ADR-0262's "phase skipped entirely".
            self._finish_confirm_gaps()
            skip_note = (
                "Everything needed was clear from your description — no "
                "follow-up questions."
                if self._lang() != "de" else
                "Alles Nötige ging schon aus deiner Beschreibung hervor — "
                "keine weiteren Rückfragen."
            )
            return f"{preview}\n\n{skip_note}\n\n{self.ask()}"
        return f"{preview}\n\n{self.ask()}"

    def _finish_confirm_gaps(self) -> None:
        extracted: DependencySpec = self._answers.get("__extracted_deps__", DependencySpec())
        problem: ProblemStatement = self._answers.get("__problem__")
        assert problem is not None  # invariant: only reached via _finish_idea_phase

        merged = DependencySpec(
            external_libraries=self._answers.get("external_libraries", extracted.external_libraries),
            requires_auth=self._answers.get("requires_auth", extracted.requires_auth),
            requires_network_egress=self._answers.get(
                "requires_network_egress", extracted.requires_network_egress
            ),
            egress_hosts=self._answers.get("egress_hosts", extracted.egress_hosts),
        )
        self.idea = PluginIdea(
            plugin_name=self._answers.get("plugin_name", ""),
            problem=problem,
            dependencies=merged,
            constraints=Constraints(),  # defaults: MVP-only, no platform/scope notes
            raw_answers={k: str(v) for k, v in self._answers.items() if not k.startswith("__")},
        )
        self.final_classification = classify(self.idea)
        self.phase = InterviewPhase.REVIEW
        self._answer_index = 0

    # ── Legacy-flow internals ───────────────────────────────────────────────

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

    # ── Review / Checkpoint prompts ─────────────────────────────────────────

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
        if self.checkpoint_enabled:
            lines += [
                "",
                "This generates an Idea Doc, Architecture Concept, ADR and "
                "Build Plan for you to review — the code scaffold "
                + ("and generated tests come " if self.e2e_tests_enabled else "comes ")
                + "after that, on a second confirmation. Nothing is written "
                "until you confirm.",
            ]
        else:
            lines += [
                "",
                "This generates an Idea Doc, Architecture Concept, ADR, Build "
                "Plan and a code scaffold — nothing is written to disk until "
                "you confirm.",
            ]
        lines += [
            "",
            "Reply **confirm** to write the artifacts, **restart** to redo "
            "the interview, or **cancel** to stop.",
        ]
        return "\n".join(lines)

    def _checkpoint_prompt(self) -> str:
        """Short instruction only — the rich checkpoint content (doc names,
        verbatim risk flags, voice summary) is assembled by ``checkpoint.py``
        from :meth:`result`, one layer up (``turn.py``), which is where
        filesystem access already lives in this package. This keeps
        ``interview.py`` free of doc-generation and voice concerns, same
        separation ADR-0253 already established for REVIEW."""
        return (
            "Reply **confirm** to generate the code scaffold"
            + (" and tests" if self.e2e_tests_enabled else "")
            + ", **restart** to redo the interview, or **cancel** to stop "
            "here."
        )

    def _reset_to_start(self) -> None:
        self.phase = InterviewPhase.IDEA if self.idea_first else InterviewPhase.PROBLEM
        self._answer_index = 0
        self._answers = {}
        self._dynamic_questions = None
        self._resolved_fields = frozenset()
        self.preliminary_classification = None
        self.final_classification = None
        self.idea = None
        self.checkpoint_docs_written = False

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
            self._reset_to_start()
            return self.ask()
        # confirm
        if self.checkpoint_enabled:
            self.phase = InterviewPhase.CHECKPOINT
            return "Confirmed. Generating documents for review…"
        self.phase = InterviewPhase.DONE
        return "Confirmed. Writing artifacts…"

    def _answer_checkpoint(self, text: str) -> str:
        decision = _REVIEW_TOKENS.get(text.strip().lower())
        if decision is None:
            return (
                f"I didn't understand {text!r} — reply confirm, restart or "
                "cancel.\n\n" + self._checkpoint_prompt()
            )
        if decision == "cancel":
            return self.cancel()
        if decision == "restart":
            self._reset_to_start()
            return self.ask()
        # confirm
        self.phase = InterviewPhase.DONE
        return "Confirmed. Writing the scaffold…"


__all__ = [
    "InterviewPhase",
    "InterviewError",
    "InterviewSession",
    "Question",
    "QUESTION_BANK",
    "classify_checkpoint_decision",
]
