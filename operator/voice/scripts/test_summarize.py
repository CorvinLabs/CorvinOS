#!/usr/bin/env python3
"""Tests for the LLM-free paths of summarize.py.

We can't test the LLM backends without a real API key / CLI, but we can
guarantee that:
  - The system prompt template still resolves with {max_chars}.
  - The faithfulness rule is present in every prompt variant.
  - adaptive_target sizing follows the documented formula.
  - naive_truncate (offline fallback) keeps every list item AND any outro
    after the last list item — critical for closing pick-one questions.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import summarize  # noqa: E402


# ---------------------------------------------------------------------------
# Prompt templates: faithfulness rule must be load-bearing in every variant.
# ---------------------------------------------------------------------------


def test_system_prompt_resolves_with_placeholder() -> None:
    for lang in ("de", "en"):
        for has_task in (False, True):
            s = summarize._system_for(lang, 800, has_task=has_task)
            assert "{max_chars}" not in s, f"{lang} task={has_task}: placeholder unresolved"
            assert "800" in s, f"{lang} task={has_task}: max_chars not interpolated"


def test_faithfulness_rule_present_in_every_prompt_variant() -> None:
    """The whole point of the recent rewrite: prompts forbid invention.

    A regression here would silently re-enable hallucinated content in
    voice output, which is the user-reported bug.
    """
    for lang in ("de", "en"):
        for has_task in (False, True):
            s = summarize._system_for(lang, 800, has_task=has_task)
            keyword = "TREUE" if lang == "de" else "FAITHFULNESS"
            assert keyword in s, f"{lang} task={has_task}: faithfulness rule missing"


def test_old_invention_inducing_phrases_are_gone() -> None:
    """The pre-fix prompts told Haiku to add 'mechanism or rationale' per
    point — which produced the user-reported hallucinations. Make sure
    these phrases stay out."""
    for lang in ("de", "en"):
        for has_task in (False, True):
            s = summarize._system_for(lang, 800, has_task=has_task)
            forbidden = [
                "mechanism or rationale",
                "Mechanismus oder Begründung",
                "warum er drin ist, oder welche Konsequenz",
                "what consequence it has",
            ]
            for phrase in forbidden:
                assert phrase not in s, (
                    f"{lang} task={has_task}: invention-inducing phrase reintroduced: {phrase!r}"
                )


def test_audience_block_optional_backward_compat() -> None:
    """No audience → prompt MUST be byte-identical to the pre-layer-12 path.
    This is the backward-compatibility contract for users who never touch
    /voice-user-set."""
    for lang in ("de", "en"):
        for has_task in (False, True):
            without = summarize._system_for(lang, 800, has_task=has_task)
            with_empty = summarize._system_for(lang, 800, has_task=has_task,
                                                audience="")
            assert without == with_empty, (
                f"{lang} task={has_task}: empty audience must produce "
                "byte-identical prompt vs no audience"
            )


def test_audience_block_appended_after_persona() -> None:
    """When both persona and audience are set, audience must come AFTER
    persona in the prompt — speaker first, listener second. This is what
    `_system_for`'s contract claims and what stop_hook.sh relies on."""
    s = summarize._system_for(
        "de", 800, has_task=False,
        persona="coder",
        audience="HÖRER-PROFIL — test marker",
    )
    p_idx = s.find("Persona-Stil")
    a_idx = s.find("HÖRER-PROFIL — test marker")
    assert p_idx > 0, "persona block missing"
    assert a_idx > p_idx, (
        "audience block must come AFTER persona block in the system prompt"
    )


def test_audience_block_does_not_remove_faithfulness_rule() -> None:
    """The audience block is allowed to re-affirm faithfulness, but it
    must NEVER replace or weaken the base prompt's TREUE / FAITHFULNESS
    rule. This guards against a future refactor that accidentally swaps
    the base prompt for an audience-only variant."""
    audience_de = (
        "HÖRER-PROFIL — Profil: Verständnis-Niveau Anfänger. "
        "Nichts weglassen."
    )
    audience_en = (
        "AUDIENCE — Listener profile: comprehension level novice. "
        "Don't drop content."
    )
    for lang, audience in (("de", audience_de), ("en", audience_en)):
        for has_task in (False, True):
            s = summarize._system_for(lang, 800, has_task=has_task,
                                       audience=audience)
            keyword = "TREUE" if lang == "de" else "FAITHFULNESS"
            assert keyword in s, (
                f"{lang} task={has_task}: audience block must not remove "
                "the base-prompt faithfulness rule"
            )


def test_audience_does_not_double_render_when_called_twice() -> None:
    """`_system_for` is pure — repeated calls with identical args produce
    identical output. Trip-wires a future bug where someone caches the
    addendum mutably."""
    audience = "AUDIENCE — Listener profile: jargon tolerance 4/5."
    a = summarize._system_for("en", 800, has_task=False, audience=audience)
    b = summarize._system_for("en", 800, has_task=False, audience=audience)
    assert a == b
    assert a.count("AUDIENCE — Listener profile") == 1


def test_speaking_style_clause_present() -> None:
    """Spoken text must sound like a human telling the answer, not a
    recited list — this is the user-visible 'don't sound like a robot'
    requirement. Regression-locks both:
      - the SPRECHSTIL / SPEAKING STYLE header in every prompt variant
      - the explicit warning against 'Erstens / firstly' enumerations
    """
    for lang in ("de", "en"):
        for has_task in (False, True):
            s = summarize._system_for(lang, 800, has_task=has_task)
            header = "SPRECHSTIL" if lang == "de" else "SPEAKING STYLE"
            assert header in s, f"{lang} task={has_task}: {header} block missing"

            # Must explicitly discourage the schoolbook 'Erstens, zweitens,
            # drittens' / 'firstly, secondly, thirdly' pattern that gives
            # voice output its robotic feel.
            anti_recited = ("Erstens" if lang == "de" else "firstly")
            assert anti_recited in s, (
                f"{lang} task={has_task}: must explicitly call out "
                f"the recited '{anti_recited}…' pattern to forbid it"
            )

            # Faithfulness must be re-asserted INSIDE the style block so
            # the LLM can't trade content fidelity for natural flow.
            # Tolerate the multi-space artefact from Python string-line
            # continuation by collapsing whitespace before matching.
            normalized = " ".join(s.split())
            tradeoff_marker = (
                "ändert nur das Wie, niemals das Was" if lang == "de"
                else "touches only the how, never the what"
            )
            assert tradeoff_marker in normalized, (
                f"{lang} task={has_task}: style block must reaffirm that "
                f"flow does not buy omissions"
            )


def test_understandability_block_present_in_every_variant() -> None:
    """The voice-summary must be more than a recited list of facts.

    The user-visible bug was: voice output sounded robotic — like a
    data sheet read aloud, with no sense of WHY/HOW/EFFECT. The fix
    adds a load-bearing UNDERSTANDABILITY (de: VERSTÄNDLICHKEIT) block
    that:
      - tells the LLM to surface existing rationale / why / effect
      - explicitly permits common metaphors as bridges to existing
        concepts
      - frames the goal as "listener walks away with a model, not a
        data sheet"

    The block sits NEXT TO faithfulness — it is a peer rule, not a
    replacement. Removing it would silently revert voice output to
    fact-recital mode, which is the regression we are guarding.
    """
    for lang in ("de", "en"):
        for has_task in (False, True):
            s = summarize._system_for(lang, 800, has_task=has_task)
            header = "VERSTÄNDLICHKEIT" if lang == "de" else "UNDERSTANDABILITY"
            assert header in s, f"{lang} task={has_task}: {header} block missing"

            # Must explicitly invite metaphors so spoken output gets
            # mental-model traction beyond bare labels.
            metaphor_marker = "Metapher" if lang == "de" else "metaphor"
            assert metaphor_marker in s, (
                f"{lang} task={has_task}: must explicitly permit metaphors"
            )

            # Must explicitly frame the goal as model-not-datasheet.
            datasheet_marker = "Datenblatt" if lang == "de" else "data sheet"
            assert datasheet_marker in s, (
                f"{lang} task={has_task}: must contrast 'model' against "
                f"'{datasheet_marker}' so the LLM knows to wrap facts in "
                f"a frame, not just chain them"
            )

            # Outcome-first lead must be in the OUTPUT SHAPE block — since
            # the outcome-first rework, the user-effect leads the summary
            # rather than closing it ("the test passes now" first, "I
            # edited X.py" never). Phrased loosely so future wording
            # tweaks don't break the lock.
            normalized = " ".join(s.split())
            effect_marker = (
                "was ist jetzt möglich" if lang == "de"
                else "what is now possible"
            )
            assert effect_marker in normalized, (
                f"{lang} task={has_task}: output shape must include a "
                f"'what is now possible' lead so the listener gets the "
                f"user-facing effect first, not the code-mental-model"
            )


def test_faithfulness_still_forbids_invention_after_understandability_added() -> None:
    """Regression-lock for the pair (UNDERSTANDABILITY, FAITHFULNESS).

    The understandability block introduces room for metaphors and
    explanatory framing — which is exactly the hole that previously
    let hallucinations through. Faithfulness must therefore explicitly
    say 'metaphors are bridges to what is there, not doors to what
    isn't' so the LLM cannot trade fidelity for flow.
    """
    for lang in ("de", "en"):
        for has_task in (False, True):
            s = summarize._system_for(lang, 800, has_task=has_task)
            normalized = " ".join(s.split())
            guard = (
                "Brücken zu Vorhandenem, nicht Türen zu Neuem" if lang == "de"
                else "bridges to what is there, not doors to what isn't"
            )
            assert guard in normalized, (
                f"{lang} task={has_task}: faithfulness must explicitly "
                f"close the metaphor-loophole opened by understandability"
            )


def test_persona_addendum_attached_for_known_personas() -> None:
    """When the active cowork persona is known, a one-line tone addendum
    is appended to the system prompt — every Bundle persona except the
    neutral 'assistant' must surface a recognizable marker.
    """
    # browser + jarvis removed from bundle in f1e3246
    known = ["coder", "research", "inbox", "forge", "skill-forge", "homeassistant"]
    for lang in ("de", "en"):
        for has_task in (False, True):
            for persona in known:
                s = summarize._system_for(lang, 800, has_task=has_task, persona=persona)
                marker = "Persona-Stil" if lang == "de" else "Persona style"
                assert marker in s, (
                    f"{lang} task={has_task} persona={persona}: addendum missing"
                )


def test_persona_addendum_silent_for_unknown_persona() -> None:
    """Typo or new-persona-not-yet-mapped → silent no-op. Voice never
    breaks because of an unknown persona name."""
    for lang in ("de", "en"):
        for has_task in (False, True):
            base = summarize._system_for(lang, 800, has_task=has_task)
            tinted = summarize._system_for(
                lang, 800, has_task=has_task, persona="totally-made-up-persona-9000"
            )
            assert tinted == base, (
                f"{lang} task={has_task}: unknown persona must be a no-op, "
                f"got a different prompt back"
            )


def test_persona_addendum_neutral_for_assistant_baseline() -> None:
    """The default fallback persona 'assistant' is the neutral baseline —
    no tone override, prompt identical to no-persona."""
    for lang in ("de", "en"):
        for has_task in (False, True):
            base = summarize._system_for(lang, 800, has_task=has_task)
            tinted = summarize._system_for(
                lang, 800, has_task=has_task, persona="assistant"
            )
            assert tinted == base, (
                f"{lang} task={has_task}: 'assistant' is the neutral baseline; "
                f"prompt must be identical to no-persona variant"
            )


def test_persona_addendum_does_not_dislodge_faithfulness() -> None:
    """Tone modulation must not trade content fidelity. Even with the
    addendum, the faithfulness keyword AND the metaphor-loophole guard
    stay present so a future addendum-edit can't silently remove them."""
    for lang in ("de", "en"):
        for persona in ("coder", "research", "inbox"):
            s = summarize._system_for(lang, 800, has_task=False, persona=persona)
            normalized = " ".join(s.split())
            faith_kw = "TREUE" if lang == "de" else "FAITHFULNESS"
            assert faith_kw in s, (
                f"{lang} persona={persona}: faithfulness keyword vanished"
            )
            guard = (
                "Brücken zu Vorhandenem, nicht Türen zu Neuem" if lang == "de"
                else "bridges to what is there, not doors to what isn't"
            )
            assert guard in normalized, (
                f"{lang} persona={persona}: metaphor-loophole guard vanished"
            )


def test_persona_addendum_case_and_whitespace_insensitive() -> None:
    """CORVIN_CALLER_PERSONA may arrive with surrounding whitespace
    or odd casing; lookup must normalize both."""
    base = summarize._system_for("de", 800, has_task=False, persona="coder")
    for variant in ("CODER", "  coder  ", "Coder"):
        same = summarize._system_for("de", 800, has_task=False, persona=variant)
        assert same == base, f"persona={variant!r}: addendum lookup not normalized"


def test_with_task_uses_natural_lead_in_not_rigid_marker() -> None:
    """The two-part read-aloud (task reminder + answer) must not mandate
    a rigid 'Antwort:' / 'Answer:' marker — the user reported that the
    fixed marker style sounded robotic. The new prompts list options as
    a *suggestion*, not a requirement."""
    for lang in ("de", "en"):
        s = summarize._system_for(lang, 800, has_task=True)
        # Old hard-marker wording — must be gone:
        assert "starren 'Antwort:'-Marker" in s or "rigid 'Answer:' label" in s, (
            f"{lang}: prompt must explicitly say the Answer: marker is NOT mandatory"
        )


# ---------------------------------------------------------------------------
# adaptive_target: hint floor + 0.85 of input, no per-item multiplier.
# ---------------------------------------------------------------------------


def test_adaptive_target_uses_hint_as_floor() -> None:
    assert summarize.adaptive_target("short", 400) == 400


def test_adaptive_target_scales_with_input() -> None:
    assert summarize.adaptive_target("x" * 1000, 400) == 850  # int(1000 * 0.85)


def test_adaptive_target_no_per_item_inflation() -> None:
    """Old behaviour added 220 chars per list item; new behaviour does not.

    Padding budget invited the LLM to fill extra space with invented
    content, so we removed the multiplier.
    """
    list_text = "\n".join(f"- item {i}" for i in range(10))  # 10 items
    target = summarize.adaptive_target(list_text, 100)
    # Without multiplier, target tracks length, not item count.
    assert target == max(100, int(len(list_text) * 0.85))


# ---------------------------------------------------------------------------
# naive_truncate: every list item AND any outro after the last item must
# survive. Outro often holds the closing pick-one question.
# ---------------------------------------------------------------------------


def test_naive_truncate_keeps_every_list_item() -> None:
    txt = "Plan:\n1. Code-Review\n2. Tests\n3. Deployment"
    out = summarize.naive_truncate(txt, 800)
    for must in ("Code-Review", "Tests", "Deployment"):
        assert must in out, f"naive_truncate dropped: {must!r}"


def test_naive_truncate_keeps_outro_after_list() -> None:
    """Regression test for the 'closing question is lost' bug.

    Before the fix, naive_truncate stopped after the last item, so
    a pick-one question right after the list was silently dropped.
    """
    txt = (
        "Hier ist mein Vorschlag:\n"
        "1. Code-Review machen\n"
        "2. Tests laufen lassen\n"
        "3. Deployment vorbereiten\n"
        "\n"
        "Welchen Schritt willst du zuerst?"
    )
    out = summarize.naive_truncate(txt, 800)
    assert "Welchen Schritt willst du zuerst?" in out, f"outro lost: {out!r}"


def test_naive_truncate_plain_prose_unchanged() -> None:
    prose = "Eine einfache Antwort ohne Listen."
    assert summarize.naive_truncate(prose, 800) == prose


def test_naive_truncate_dash_bullets_recognised() -> None:
    txt = "Optionen:\n- erste Wahl\n- zweite Wahl\n- dritte Wahl\n\nWelche willst du?"
    out = summarize.naive_truncate(txt, 800)
    for must in ("erste Wahl", "zweite Wahl", "dritte Wahl", "Welche willst du"):
        assert must in out, f"lost: {must!r}"


def test_hermes_backend_tried_when_cli_unavailable() -> None:
    """M3 regression: on a Hermes-only install (no claude CLI) summarize() must
    try the Hermes backend BEFORE falling through to structural truncation, so a
    long voice reply gets a real summary instead of being cut mid-sentence."""
    from unittest.mock import patch
    long_text = "This is a genuinely long spoken answer that must be summarised. " * 30
    with (
        patch.object(summarize, "_summarize_via_cli", return_value=None) as cli,
        patch.object(summarize, "_summarize_via_hermes",
                     return_value="A concise Hermes summary.") as herm,
    ):
        out = summarize.summarize(long_text, "en", 200, "claude-haiku-4-5")
    cli.assert_called_once()
    herm.assert_called_once()
    assert out == "A concise Hermes summary."


def test_hermes_backend_strips_think_block() -> None:
    """qwen3 emits <think>…</think> reasoning — it must never be spoken."""
    from unittest.mock import patch

    class _Resp:
        def getcode(self):
            return 200

        def read(self):
            return b'{"response": "<think>plan the reply</think>Final spoken answer."}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    with patch("urllib.request.urlopen", return_value=_Resp()):
        out = summarize._summarize_via_hermes("some long text", "", "en", 200, "m")
    assert out == "Final spoken answer."
    assert "<think>" not in (out or "")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main() -> int:
    tests = [
        test_system_prompt_resolves_with_placeholder,
        test_faithfulness_rule_present_in_every_prompt_variant,
        test_old_invention_inducing_phrases_are_gone,
        test_speaking_style_clause_present,
        test_understandability_block_present_in_every_variant,
        test_faithfulness_still_forbids_invention_after_understandability_added,
        test_persona_addendum_attached_for_known_personas,
        test_persona_addendum_silent_for_unknown_persona,
        test_persona_addendum_neutral_for_assistant_baseline,
        test_persona_addendum_does_not_dislodge_faithfulness,
        test_persona_addendum_case_and_whitespace_insensitive,
        test_with_task_uses_natural_lead_in_not_rigid_marker,
        test_adaptive_target_uses_hint_as_floor,
        test_adaptive_target_scales_with_input,
        test_adaptive_target_no_per_item_inflation,
        test_naive_truncate_keeps_every_list_item,
        test_naive_truncate_keeps_outro_after_list,
        test_naive_truncate_plain_prose_unchanged,
        test_naive_truncate_dash_bullets_recognised,
        test_audience_block_optional_backward_compat,
        test_audience_block_appended_after_persona,
        test_audience_block_does_not_remove_faithfulness_rule,
        test_audience_does_not_double_render_when_called_twice,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL  {t.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"ERROR {t.__name__}: {type(exc).__name__}: {exc}")
    if failed:
        print(f"\n{failed} test(s) failed")
        return 1
    print(f"\nAll {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
