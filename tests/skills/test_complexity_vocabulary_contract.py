"""Guard: the complexity vocabulary is ONE contract, pinned across consumers.

Why this file exists
────────────────────
``ClassificationResult.complexity`` is a plain string that four subsystems
translate independently. On 2026-09-16 commit 46c3b3a7 (ADR-0845 k=2) changed
what ``_classify_complexity`` returns from ``"simple"`` to ``"SIMPLE"`` and
adjusted none of them. Every consumer is a dict lookup or an ``==`` on a
string, so nothing raised, nothing logged, and nothing failed loudly. What
actually happened over the four days it was live:

  * the console's classified-turn tally froze at 512 while the audit chain
    held 765 real records — every record after the change was silently
    skipped by ``engine_api._real_stats``;
  * ``model_selector_shadow`` never stashed a pending entry, so
    ``report_turn_outcome()`` became a no-op and the confidence store stopped
    accruing samples completely (last write 2026-09-17T23:25);
  * the operator's saved per-tier model choice stopped being applied, because
    the override lookup is keyed on this exact string — the console kept
    showing the saved choice while the classifier used its hardcoded default.

25 tests across three files were red the entire time; none of them ran.

These tests fail on the next drift. They assert the CONTRACT (one canonical
spelling, every consumer resolving it, historical records still readable),
never a particular prompt's classification — that belongs in
``test_model_selector.py``.
"""

from __future__ import annotations

import pytest

from core.models.model_selection_config import (
    COMPLEXITY_BY_TASK_TYPE,
    TASK_TYPES,
    task_type_for_complexity,
)
from core.skills.os_skills.model_selector import (
    COMPLEXITY_LEVELS,
    ModelSelector,
    ModelSelectorConfig,
    normalize_complexity,
)

#: Prompts chosen so each one lands in a different tier through a DIFFERENT
#: signal — keyword, token count, code blocks — so a regression in any one of
#: the three branches is visible here.
TIER_PROBES = {
    "simple": "Translate 'hello' to French",
    "medium": "task " * 1000,  # ~1250 tokens: over simple_max, under medium_max
    "complex": "\n".join("```python\ndef f%d(): pass\n```" % i for i in range(6)),
}


class TestCanonicalSpelling:
    """The classifier answers in exactly one spelling, and it is lowercase."""

    @pytest.mark.parametrize("expected,prompt", sorted(TIER_PROBES.items()))
    def test_classify_returns_canonical_lowercase(self, expected: str, prompt: str) -> None:
        result = ModelSelector().classify(prompt)
        assert result.complexity == expected
        assert result.complexity in COMPLEXITY_LEVELS

    def test_all_three_tiers_are_reachable(self) -> None:
        """The defect that started this: MEDIUM and COMPLEX were arithmetically
        unreachable, so the console showed an empty tier forever and it read as
        missing data rather than as an unreachable branch."""
        observed = {ModelSelector().classify(p).complexity for p in TIER_PROBES.values()}
        assert observed == set(COMPLEXITY_LEVELS)


class TestConsumersResolveWhatTheClassifierEmits:
    """Every consumer must resolve the classifier's own output. A lookup that
    misses returns None/False rather than raising, which is why this needs to
    be asserted rather than observed in production."""

    @pytest.mark.parametrize("prompt", sorted(TIER_PROBES.values()))
    def test_console_translation_resolves(self, prompt: str) -> None:
        emitted = ModelSelector().classify(prompt).complexity
        task_type = task_type_for_complexity(emitted)
        assert task_type is not None, f"engine_api would silently drop {emitted!r}"
        assert task_type in TASK_TYPES

    @pytest.mark.parametrize("prompt", sorted(TIER_PROBES.values()))
    def test_shadow_loop_resolves(self, prompt: str) -> None:
        """``model_selector_shadow`` reaches the optimizer only through this
        translation; None here means the learning loop is dead."""
        emitted = ModelSelector().classify(prompt).complexity
        assert task_type_for_complexity(emitted) is not None

    def test_operator_override_is_actually_applied(self) -> None:
        """The override dict is complexity-keyed. A spelling mismatch makes the
        lookup miss and the hardcoded default win — silently."""
        overrides = {c: {"provider": None, "model": f"pinned-{c}"} for c in COMPLEXITY_LEVELS}
        selector = ModelSelector(overrides=overrides)
        for prompt in TIER_PROBES.values():
            result = selector.classify(prompt)
            assert result.recommended_model == f"pinned-{result.complexity}"

    def test_get_stats_counts_what_was_classified(self) -> None:
        selector = ModelSelector()
        for prompt in TIER_PROBES.values():
            selector.classify(prompt)
        stats = selector.get_stats()
        assert stats["classifications"] == 3
        assert stats["simple"] + stats["medium"] + stats["complex"] == 3


class TestHistoricalRecordsStayReadable:
    """The tenant audit chain is append-only and must never be rewritten
    (CLAUDE.md / ADR-0232), so it permanently holds records in BOTH spellings —
    512 lowercase and 253 uppercase on the reference install. A reader that
    accepts only the canonical form cannot count a quarter of its own history."""

    @pytest.mark.parametrize("spelling", ["simple", "SIMPLE", "Simple", " simple "])
    def test_normalize_accepts_every_historical_spelling(self, spelling: str) -> None:
        assert normalize_complexity(spelling) == "simple"

    @pytest.mark.parametrize("spelling", ["SIMPLE", "MEDIUM", "COMPLEX"])
    def test_uppercase_chain_records_still_translate(self, spelling: str) -> None:
        assert task_type_for_complexity(spelling) == spelling

    @pytest.mark.parametrize("junk", [None, "", "SIMPLEX", 3, "corvinOS", object()])
    def test_unknown_values_are_rejected_not_guessed(self, junk: object) -> None:
        assert normalize_complexity(junk) is None
        assert task_type_for_complexity(junk) is None

    def test_the_two_vocabularies_stay_distinct(self) -> None:
        """Console task types and classifier complexities are NOT two spellings
        of one vocabulary: "corvinOS" is a task type with no complexity at all.
        Collapsing them (e.g. by making the classifier answer in task-type
        casing) loses that distinction."""
        assert "corvinOS" in TASK_TYPES
        assert "corvinOS" not in COMPLEXITY_BY_TASK_TYPE
        assert task_type_for_complexity("corvinOS") is None


class TestConfiguredThresholdsAreActuallyRead:
    """Point 2 of the fix: ``simple_max_tokens``/``medium_max_tokens`` were
    declared, documented in the decision-rule docstring, and never read by the
    classification — it used a weighted average against hardcoded 5000/20/50
    instead. A config field nothing reads is indistinguishable from a working
    one until someone changes it and nothing happens."""

    def test_raising_simple_max_tokens_moves_the_boundary(self) -> None:
        prompt = "task " * 400  # ~500 tokens
        default = ModelSelector().classify(prompt).complexity
        raised = ModelSelector(
            ModelSelectorConfig(simple_max_tokens=5000, medium_max_tokens=20000)
        ).classify(prompt).complexity
        assert default == "medium"
        assert raised == "simple", "simple_max_tokens is not being read"

    def test_lowering_medium_max_tokens_moves_the_boundary(self) -> None:
        prompt = "task " * 400  # ~500 tokens
        assert ModelSelector().classify(prompt).complexity == "medium"
        lowered = ModelSelector(
            ModelSelectorConfig(simple_max_tokens=10, medium_max_tokens=100)
        ).classify(prompt).complexity
        assert lowered == "complex", "medium_max_tokens is not being read"

    def test_code_blocks_and_dependencies_are_read(self) -> None:
        many_blocks = "\n".join("```python\ndef f%d(): pass\n```" % i for i in range(6))
        assert ModelSelector().classify(many_blocks).complexity == "complex"
        tolerant = ModelSelector(
            ModelSelectorConfig(medium_max_code_blocks=50)
        ).classify(many_blocks).complexity
        assert tolerant != "complex", "medium_max_code_blocks is not being read"


class TestLearnedThresholdStaysNeutralWithoutAnOptimizer:
    """ADR-0377's learned threshold scales the token bounds around its 0.5
    neutral point. With no optimizer supplied — every production path today —
    the scale must be exactly 1.0, so the documented rule holds verbatim."""

    def test_no_optimizer_means_documented_rule_verbatim(self) -> None:
        cfg = ModelSelectorConfig(simple_max_tokens=500, medium_max_tokens=3000)
        selector = ModelSelector(cfg)
        assert selector.cost_variance_optimizer is None
        assert selector.classify("task " * 100).complexity == "simple"   # ~125 tok
        assert selector.classify("task " * 1000).complexity == "medium"  # ~1250 tok
        assert selector.classify("task " * 4000).complexity == "complex"  # ~5000 tok

    def test_a_degenerate_learned_threshold_cannot_collapse_the_bounds(self) -> None:
        """A learned value of 0 would scale both token bounds to zero and
        reclassify every prompt — including an empty one — as COMPLEX."""

        class _ZeroOptimizer:
            def get_threshold_recommendation(self, **_: object) -> float:
                return 0.0

        selector = ModelSelector(cost_variance_optimizer=_ZeroOptimizer())
        assert selector.classify("Translate hello", task_type="code_gen").complexity != "complex"
