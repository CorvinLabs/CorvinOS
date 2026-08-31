"""Smart Voice Summary Engine — Generate conversational summaries with context & reasoning.

Instead of blindly reading the assistant's response, analyze it for:
- Type of work (feature/fix/refactor/docs)
- Why it matters (impact, blockers resolved)
- Trade-offs considered
- Risk level

Then narrate it naturally, explaining the reasoning—not just the facts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# Placeholder for real LLM-based analysis in production.
# For MVP, use regex + heuristics. Later, use an LLM.


@dataclass
class ResponseAnalysis:
    """What the assistant did: type, scope, impact, trade-offs."""
    work_type: str  # "feature" | "fix" | "refactor" | "docs" | "unknown"
    scope: str  # "local" | "component" | "system"
    risk_level: str  # "trivial" | "moderate" | "high"
    key_files: list[str]
    key_insight: str  # One-sentence explanation of what was done
    blockers_resolved: list[str]  # What problems does this unblock?
    testing_mentioned: bool  # Did the assistant mention tests?
    trade_offs: list[str]  # What was considered but rejected?
    user_benefit: str  # Why should the user care?
    #: Sentences the listener must not lose — WARNING / DANGER / CRITICAL and the
    #: like, verbatim (secrets already stripped).
    #:
    #: Every other field here is a CLASSIFICATION: a label the narrative is then
    #: generated from. That is the right shape for "what kind of work was this",
    #: and the wrong shape for an operational warning, because a label cannot
    #: carry "restart the workers or it hangs". Until this field existed the
    #: generator had nowhere to put such a sentence, so it dropped it in silence:
    #:
    #:   in  "Fixed memory leak in worker pool. CRITICAL: Workers must be
    #:        restarted after deployment. … it will cause a hang."
    #:   out "Alright, deployment is no longer in our way. Spotted an issue and
    #:        patched it. Fixed memory leak in worker pool …"
    #:
    #: Voice is the one surface where that is unrecoverable — there is nothing to
    #: scroll back to. Hence verbatim, and hence spoken early (see
    #: :func:`generate_voice_summary`) so truncation can never reach it.
    critical_warnings: list[str] = field(default_factory=list)


def _strip_secrets_from_text(text: str) -> str:
    """Remove AWS/OpenAI/GCP API keys and credentials before analysis.

    Fail-closed: if a secret pattern is detected, replace with [REDACTED].
    This runs BEFORE semantic analysis so secrets never enter the LLM context.
    """
    # Common secret patterns (non-exhaustive, but covers high-risk cases)
    patterns = {
        r'(?i)aws_[a-z_]*key["\']?\s*[:=]\s*["\']?[A-Z0-9]{20,}["\']?': '[REDACTED-AWS]',
        r'(?i)openai_?api_?key["\']?\s*[:=]\s*["\']?sk-[A-Za-z0-9]{20,}["\']?': '[REDACTED-OPENAI]',
        r'(?i)gcp_?key["\']?\s*[:=]\s*["\']?[a-z0-9]{32,}["\']?': '[REDACTED-GCP]',
        r'(?i)password["\']?\s*[:=]\s*["\']?[^\s"\']{8,}["\']?': '[REDACTED-PASSWORD]',
        r'(?i)auth[_-]?token["\']?\s*[:=]\s*["\']?[A-Za-z0-9\-_.]{20,}["\']?': '[REDACTED-TOKEN]',
    }

    cleaned = text
    for pattern, replacement in patterns.items():
        cleaned = re.sub(pattern, replacement, cleaned)

    return cleaned


#: Markers that make a sentence non-droppable.
#:
#: Deliberately narrow. A broad list ("note", "remember", "please") would pull
#: half of a normal response into the must-say block and the warning would drown
#: in it — a summary where everything is critical says nothing is.
_CRITICAL_MARKERS = (
    "warning", "danger", "critical", "caution", "attention",
    "achtung", "warnung", "wichtig",
    "breaking change", "do not", "don't ", "never ",
)

#: How many warning sentences reach the listener, and how long each may be.
#: A summary is spoken, so an unbounded must-say block is its own failure mode:
#: 30 seconds of warnings is not more safety, it is a skipped message.
MAX_CRITICAL_WARNINGS = 3
MAX_CRITICAL_WARNING_CHARS = 220


def _earliest_category(
    text_lower: str, categories: dict[str, tuple[str, ...]], *, default: str
) -> str:
    """The category whose first whole-word hit appears earliest in the text.

    WHOLE words, not substrings: the classifier used plain ``in``, so "handlers"
    matched "handle" and reported a refactor as a fix. Failures of that kind read
    as judgement calls rather than bugs, which is why nobody checked the reason.
    Inflections are listed explicitly by the caller rather than matched by
    prefix — a prefix rule would take "handlers" straight back.

    Ties (the same offset) are impossible in practice — two categories cannot
    match at the same character — so no tie-break rule is needed. Returns
    ``default`` when nothing matches.
    """
    best_pos: Optional[int] = None
    best_name = default
    for name, words in categories.items():
        pattern = r"\b(?:" + "|".join(re.escape(w) for w in words) + r")\b"
        m = re.search(pattern, text_lower)
        if m is not None and (best_pos is None or m.start() < best_pos):
            best_pos, best_name = m.start(), name
    return best_name


def _as_sentence(fragment: str) -> str:
    """Give a split-off fragment its terminator back, for speech.

    ``re.split(r'[.!?]+', …)`` CONSUMES the terminator, so every fragment this
    module lifts out of the response — the key insight, a reason, a warning —
    arrives without one and runs into whatever the generator appends next
    ("…memory leak in worker pool This unblocks the next steps"). Harmless in
    text, not in audio: a TTS engine reads it as a single unbroken clause.
    """
    s = fragment.strip()
    if s and s[-1] not in ".!?…:;,":
        s += "."
    return s


def _extract_critical_warnings(sentences: list[str]) -> list[str]:
    """Sentences carrying a critical marker, verbatim and in order.

    Verbatim on purpose: the whole point is the content the classifier cannot
    represent. "Workers must be restarted after deployment" survives only if it
    is carried, not re-derived from a label.

    Takes the sentence list the caller already split (post secret-stripping), so
    a redacted credential can never re-enter through this path.
    """
    out: list[str] = []
    for raw in sentences:
        s = raw.strip()
        if not s:
            continue
        low = s.lower()
        if not any(m in low for m in _CRITICAL_MARKERS):
            continue
        if len(s) > MAX_CRITICAL_WARNING_CHARS:
            s = s[:MAX_CRITICAL_WARNING_CHARS].rstrip() + "…"
        else:
            s = _as_sentence(s)
        if s not in out:
            out.append(s)
        if len(out) >= MAX_CRITICAL_WARNINGS:
            break
    return out


def analyze_response(text: str, user_context: str = "") -> ResponseAnalysis:
    """Heuristic analysis of the response.

    In production, this would use an LLM to truly understand semantics.
    For MVP, we use regex patterns and keyword matching.

    **SECURITY: Secrets are stripped before analysis (ADR-0209 v0.10.60 fix).**
    """
    # Strip secrets BEFORE any analysis (fail-closed against info leakage)
    text_cleaned = _strip_secrets_from_text(text)
    text_lower = text_cleaned.lower()

    # Classify work type.
    #
    # WHOLE WORDS, not substrings. `"handle" in text_lower` also matches
    # "handlers", so "Moved all version-specific logic into discrete handlers"
    # classified a refactor as a "fix" — and because `fix` is tested first, the
    # refactor branch never got a look. The same trap sits in every other row:
    # "add" matches "address", "new" matches "renew", "doc" matches "docker".
    # Inflections are listed explicitly rather than matched by prefix, because a
    # prefix rule reintroduces exactly this bug ("handle" would take "handlers"
    # straight back).
    # EARLIEST mention wins, not first branch in this file.
    #
    # A real response names its main verb up front and its supporting detail
    # after: "Refactored the profile migration system … Added 8 unit tests …".
    # Both categories match, so branch order decided it — and branch order is
    # arbitrary, which is why that text reported "feature". Position is not
    # arbitrary: it is the author saying what the work WAS before saying what
    # else it involved.
    work_type = _earliest_category(text_lower, {
        "fix": ("fix", "fixes", "fixed", "fixing", "bug", "bugs",
                "crash", "crashes", "crashed", "error", "errors"),
        "feature": ("add", "adds", "added", "adding", "implement", "implements",
                    "implemented", "implementing", "feature", "features", "new"),
        "refactor": ("refactor", "refactors", "refactored", "refactoring",
                     "clean", "cleaned", "cleanup", "simplify", "simplified",
                     "rename", "renamed"),
        "docs": ("doc", "docs", "documentation", "comment", "comments",
                 "readme", "guide"),
    }, default="work")

    # Scope estimation
    if any(w in text_lower for w in ["file", "function", "method", "variable"]):
        scope = "local"
    elif any(w in text_lower for w in ["component", "service", "module"]):
        scope = "component"
    elif any(w in text_lower for w in ["system", "architecture", "protocol"]):
        scope = "system"
    else:
        scope = "unknown"

    # Risk level
    risk_level = "trivial"
    if any(w in text_lower for w in ["careful", "complex", "edge case", "race", "concurrent"]):
        risk_level = "moderate"
    if any(w in text_lower for w in ["high risk", "breaking", "migration", "unsafe"]):
        risk_level = "high"

    # Extract file names (crude regex)
    files = re.findall(r'\b[\w\-\.]+\.(?:py|ts|tsx|js|go|rs)\b', text_cleaned)
    key_files = list(set(files))[:3]  # Top 3 unique files

    # Extract key insight (first sentence or summary)
    sentences = re.split(r'[.!?]+', text_cleaned)
    key_insight = _as_sentence(sentences[0]) if sentences else text_cleaned[:100]

    # Detect blockers being resolved
    blockers = []
    if any(w in text_lower for w in ["unblock", "resolve", "fix", "allow"]):
        if "pipeline" in text_lower:
            blockers.append("rendering pipeline")
        if "auth" in text_lower or "login" in text_lower:
            blockers.append("authentication flow")
        if "deploy" in text_lower:
            blockers.append("deployment")

    # Testing
    testing_mentioned = any(w in text_lower for w in ["test", "unit", "e2e", "verified"])

    # Reasoning — why this, over the alternative.
    #
    # The generator introduces this slot with "I went with this because:", so it
    # is the WHY slot, not merely a rejected-alternatives list. It only matched
    # comparative language ("could", "instead", "vs", "trade"), which misses the
    # commonest way a reason is actually written — causally:
    #
    #   "LRU was evicting hot items too aggressively. LFU tracks frequency, SO
    #    hot items stay longer."
    #
    # With no match the slot stayed empty and the summary explained nothing. The
    # suite's `test_summary_explains_reasoning` still passed about half the time,
    # because one of the two random closing templates happens to contain the word
    # "better" — a quality assertion satisfied by a coin flip. Adding the causal
    # markers makes the reason actually carried, and the test deterministic.
    _REASON_MARKERS = (
        "could", "instead", " vs", "trade",
        "because", "since ", " so ", "reduces", "avoids", "prevents",
        "too aggressively", "which means",
    )
    trade_offs = []
    for sent in sentences:
        low = sent.lower()
        if any(w in low for w in _REASON_MARKERS):
            cleaned = sent.strip()
            if cleaned:
                trade_offs.append(_as_sentence(cleaned[:80]))  # First 80 chars

    # User benefit (why they should care)
    user_benefit = ""
    if blockers:
        user_benefit = f"Unblocks {', '.join(blockers)}"
    elif "improve" in text_lower or "faster" in text_lower:
        user_benefit = "Performance improvement"
    elif "bug" in text_lower or "crash" in text_lower:
        user_benefit = "Stability improvement"
    else:
        user_benefit = "Code quality improvement"

    # Must-say sentences. Extracted from the SAME already-secret-stripped split
    # the rest of this function uses.
    critical_warnings = _extract_critical_warnings(sentences)

    # A response that carries an explicit warning is not "trivial", whatever the
    # keyword heuristics above concluded — risk_level drives the closing line, and
    # "This makes things better" under a DANGER notice is the wrong register.
    if critical_warnings and risk_level == "trivial":
        risk_level = "moderate"

    return ResponseAnalysis(
        work_type=work_type,
        scope=scope,
        risk_level=risk_level,
        key_files=key_files,
        key_insight=key_insight,
        blockers_resolved=blockers,
        testing_mentioned=testing_mentioned,
        trade_offs=trade_offs,
        user_benefit=user_benefit,
        critical_warnings=critical_warnings,
    )


def generate_voice_summary(
    analysis: ResponseAnalysis,
    max_words: int = 200,
    tone: str = "warm",
    user_name: str = "",
) -> str:
    """Convert analysis into a natural spoken narrative.

    Respects voice profile tone (warm/formal/casual) and generates varied,
    conversational language instead of rigid templates.

    Args:
        analysis: Response classification and insights
        max_words: Max words for the summary
        tone: Voice tone from profile (warm/formal/casual)
        user_name: User's name for personalization (optional)
    """
    import random

    parts = []

    # Openings: Varied based on tone, not rigid template
    openings_warm = [
        (lambda b: f"Great news! We've unblocked {b[0]} for you." if b else None),
        (lambda b: f"Good call—we just fixed something that was holding us back." if b else None),
        (lambda b: f"Alright, {b[0]} is no longer in our way." if b else None),
    ]

    openings_formal = [
        (lambda b: f"Blocker resolved: {b[0]}." if b else None),
        (lambda b: f"{b[0].capitalize()} has been unblocked." if b else None),
    ]

    openings_neutral = openings_formal  # Formal fallback

    openings_fallback_warm = [
        f"I just wrapped up a {analysis.work_type}.",
        f"Completed a {analysis.work_type} that I think you'll appreciate.",
        f"Finished a meaningful {analysis.work_type}.",
    ]

    openings_fallback_neutral = [
        f"A {analysis.work_type} has been completed.",
        f"Completed: {analysis.work_type}.",
    ]

    # Pick opening based on blockers and tone
    #
    # A celebratory opening is suppressed when the response carries a warning.
    # "Great news! We've unblocked deployment for you. Heads up: CRITICAL:
    # Workers must be restarted…" is not merely awkward — the opening tells the
    # listener how to hear what follows, and cheerfulness ahead of a
    # must-not-miss instruction actively works against it.
    opening = None
    if analysis.blockers_resolved and not analysis.critical_warnings:
        if tone == "warm":
            candidates = openings_warm
        elif tone == "formal":
            candidates = openings_formal
        else:
            candidates = openings_neutral
        opening = random.choice(candidates)(analysis.blockers_resolved)

    if not opening:
        if analysis.critical_warnings:
            # Sober and short. Every other opening in this function is chosen for
            # warmth or variety; here the opening's only job is to get out of the
            # way of the sentence after it. "Completed a feature that I think
            # you'll appreciate" ahead of a DANGER notice sets the wrong
            # expectation before the listener has heard the warning at all.
            fallback = [f"A {analysis.work_type} is done, with something you need to know."]
        elif tone == "warm":
            fallback = openings_fallback_warm
        else:
            fallback = openings_fallback_neutral
        opening = random.choice(fallback)

    parts.append(opening)

    # Must-say block — SECOND, immediately after the opening.
    #
    # Position is the guarantee, not a preference. Both truncations in this
    # pipeline cut from the END (`words[:max_words]` here, `words[:200]` in
    # polish_for_audio), so anything that must survive has to be near the front.
    # Putting warnings at the end — where a written summary would put them — is
    # exactly how a long response would lose them again, and silently.
    #
    # Spoken with a lead-in so the listener knows the register changed; the
    # sentence itself stays verbatim.
    spoken_warnings = ""
    if analysis.critical_warnings:
        lead = "Heads up:" if tone == "warm" else "Important:"
        spoken_warnings = " ".join(analysis.critical_warnings)
        parts.append(f"{lead} {spoken_warnings}")

    # The body templates below all embed `key_insight`, which is the response's
    # FIRST sentence. When that first sentence is itself the warning — a reply
    # that opens with "WARNING: …" — the listener would hear it twice in a row.
    # Blank it in that case; the templates degrade to their lead-in, which still
    # says what kind of work it was.
    insight = analysis.key_insight
    if insight and insight.strip().rstrip(".") in spoken_warnings:
        insight = ""

    # Body: Contextual narrative (not "The issue was causing problems—we fixed it")
    body_templates_warm = {
        "fix": [
            f"There was a bug that needed tackling. {insight}",
            f"Spotted an issue and patched it. {insight}",
            f"Fixed something that was broken. {insight}",
        ],
        "feature": [
            f"Added something new that should be useful. {insight}",
            f"Built out a new feature. {insight}",
            f"Implemented new functionality. {insight}",
        ],
        "refactor": [
            f"Tidied up the code to make it cleaner going forward. {insight}",
            f"Refactored some internals for better maintainability. {insight}",
            f"Improved the structure so it's easier to work with. {insight}",
        ],
    }

    body_templates_formal = {
        "fix": [f"Issue identified and resolved. {insight}"],
        "feature": [f"Feature implemented. {insight}"],
        "refactor": [f"Code architecture improved. {insight}"],
    }

    body_templates_neutral = body_templates_formal

    if tone == "warm":
        body_source = body_templates_warm
    elif tone == "formal":
        body_source = body_templates_formal
    else:
        body_source = body_templates_neutral

    body = random.choice(body_source.get(analysis.work_type, [insight]))
    parts.append(body)

    # Reasoning & trade-offs (show thinking, not just "considered other approaches")
    if analysis.trade_offs and tone == "warm":
        tradeoff_openers = [
            "The approach made sense because:",
            "I went with this because:",
            "Why this way?",
        ]
        parts.append(f"{random.choice(tradeoff_openers)} {analysis.trade_offs[0]}")

    # Testing: Conversational, not "We verified it with tests, so it's solid"
    if analysis.testing_mentioned:
        test_claims_warm = [
            "Tests are passing, so we're good.",
            "Verified the changes, it's working as expected.",
            "Tests confirm it's solid.",
        ]
        test_claims_neutral = [
            "Tests verified.",
            "Verified with tests.",
        ]
        test_source = test_claims_warm if tone == "warm" else test_claims_neutral
        parts.append(random.choice(test_source))

    # Closing: Impact on user (personalized if possible)
    closing_templates_warm = {
        "high_risk": [
            "This is significant work, but it's well thought through.",
            "Worth paying attention to—solid engineering though.",
        ],
        "unblocked": [
            f"You should be able to move forward now.",
            f"This unblocks the next steps.",
        ],
        "default": [
            f"This makes things better: {analysis.user_benefit.lower()}.",
            f"{analysis.user_benefit}—that's what matters here.",
        ],
    }

    closing_templates_formal = {
        "high_risk": ["Significant architectural change, well documented."],
        "unblocked": ["Blocker resolution complete."],
        "default": [analysis.user_benefit],
    }

    closing_templates_neutral = closing_templates_formal

    if tone == "warm":
        closing_source = closing_templates_warm
    elif tone == "formal":
        closing_source = closing_templates_formal
    else:
        closing_source = closing_templates_neutral

    if analysis.risk_level == "high":
        closing = random.choice(closing_source["high_risk"])
    elif analysis.blockers_resolved:
        closing = random.choice(closing_source["unblocked"])
    else:
        closing = random.choice(closing_source["default"])

    parts.append(closing)

    # Join and truncate if needed
    # Drop empties before joining: a body template whose `insight` was blanked
    # (because the insight WAS the warning) otherwise contributes a bare space,
    # and the result is a double gap the TTS reads as an extra pause.
    full_text = " ".join(p.strip() for p in parts if p and p.strip())
    words = full_text.split()
    if len(words) > max_words:
        full_text = " ".join(words[:max_words]) + "…"

    return full_text


def polish_for_audio(text: str, max_length: int = 300, lang: str = "en") -> str:
    """Optimize for text-to-speech playback.

    - Remove markdown/code
    - Expand acronyms (respecting language)
    - Ensure readability

    ``lang`` defaults to ``"en"``, matching what this module actually produces:
    every narrative template in :func:`generate_voice_summary` is English. The
    default used to be ``"de"``, so an unqualified call ran English prose through
    German expansions and emitted "We fixed the REST Programmierschnittstelle
    issue in the Kommandozeile" — mixed-language output that is worse for a
    listener than no expansion at all. The one production caller
    (``summarize_smart.py``) passes ``lang`` explicitly and is unaffected either
    way; the callers that were affected were the ones that trusted the default.
    """
    # Remove code blocks
    text = re.sub(r'```[\s\S]*?```', '[code]', text)
    text = re.sub(r'`([^`]+)`', r'\1', text)

    # Expand common acronyms (language-aware)
    if lang == "de":
        acronyms = {
            "API": "Programmierschnittstelle",
            "REST": "REST",  # Keep technical term
            "JSON": "Jason",
            "HTTP": "HTTP",  # Keep technical
            "CLI": "Kommandozeile",
            "UI": "Benutzeroberfläche",
            "UX": "Nutzererlebnis",
            "CI/CD": "kontinuierliche Integration Continuous Deployment",
        }
    else:  # en
        acronyms = {
            "API": "application programming interface",
            "REST": "representational state transfer",
            "JSON": "jay-san",
            "HTTP": "hypertext transfer protocol",
            "CLI": "command line interface",
            "UI": "user interface",
            "UX": "user experience",
            "CI/CD": "continuous integration continuous deployment",
        }

    for acronym, expanded in acronyms.items():
        text = re.sub(rf"\b{acronym}\b", expanded, text, flags=re.IGNORECASE)

    # Truncate if too long (>80 seconds @ 150 wpm ≈ 200 words)
    words = text.split()
    if len(words) > 200:
        text = " ".join(words[:200]) + " [full details available in the chat]"

    return text


# Example usage for testing
if __name__ == "__main__":
    # Test 1: Bug fix
    response1 = """Fixed the image loading null pointer crash. Added a guard check in handleImageLoad()
    and improved error logging so we can see what's happening when this breaks again. The fix is 4 lines
    of code in src/renderer/image.ts. Verified with existing unit tests."""

    analysis1 = analyze_response(response1)
    summary1 = generate_voice_summary(analysis1, tone="warm")
    final1 = polish_for_audio(summary1)

    print("=== BUG FIX EXAMPLE (WARM TONE) ===")
    print(f"Original: {response1[:100]}...")
    print(f"\nVoice Summary:\n{final1}\n")

    # Test 2: Refactor
    response2 = """Refactored the profile migration system to use a factory pattern. Moved all
    version-specific logic into discrete handlers so we can test each version independently. Added
    8 unit tests covering all upgrade paths. The old monolithic function is gone. This makes it much
    easier to add new migrations in the future."""

    analysis2 = analyze_response(response2)
    summary2_warm = generate_voice_summary(analysis2, tone="warm")
    summary2_formal = generate_voice_summary(analysis2, tone="formal")
    final2_warm = polish_for_audio(summary2_warm)
    final2_formal = polish_for_audio(summary2_formal)

    print("=== REFACTOR EXAMPLE (WARM vs FORMAL) ===")
    print(f"Original: {response2[:100]}...")
    print(f"\nWarm Tone:\n{final2_warm}\n")
    print(f"Formal Tone:\n{final2_formal}\n")

    # Test 3: Feature
    response3 = """Implemented geo-tracking Tier 3 with 10km grid rasterization. This enables
    instance location tracking while maintaining full anonymization (100+ users per grid cell).
    Added comprehensive tests. Unblocks the analytics dashboard feature."""

    analysis3 = analyze_response(response3)
    summary3 = generate_voice_summary(analysis3, tone="warm")
    final3 = polish_for_audio(summary3)

    print("=== FEATURE EXAMPLE (WARM TONE) ===")
    print(f"Original: {response3[:100]}...")
    print(f"\nVoice Summary:\n{final3}\n")

    # Test 4: Show variability
    print("=== VARIABILITY TEST (same input, multiple outputs) ===")
    for i in range(3):
        varied = generate_voice_summary(analysis1, tone="warm")
        print(f"Attempt {i+1}: {varied}\n")
