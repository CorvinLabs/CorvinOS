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
from dataclasses import dataclass
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


def analyze_response(text: str, user_context: str = "") -> ResponseAnalysis:
    """Heuristic analysis of the response.

    In production, this would use an LLM to truly understand semantics.
    For MVP, we use regex patterns and keyword matching.

    **SECURITY: Secrets are stripped before analysis (ADR-0209 v0.10.60 fix).**
    """
    # Strip secrets BEFORE any analysis (fail-closed against info leakage)
    text_cleaned = _strip_secrets_from_text(text)
    text_lower = text_cleaned.lower()

    # Classify work type
    if any(w in text_lower for w in ["fix", "fixed", "bug", "crash", "error", "handle"]):
        work_type = "fix"
    elif any(w in text_lower for w in ["add", "implement", "implement", "feature", "new"]):
        work_type = "feature"
    elif any(w in text_lower for w in ["refactor", "clean", "simplify", "rename"]):
        work_type = "refactor"
    elif any(w in text_lower for w in ["doc", "comment", "readme", "guide"]):
        work_type = "docs"
    else:
        work_type = "work"

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
    key_insight = sentences[0].strip() if sentences else text_cleaned[:100]

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

    # Trade-offs (look for "could have", "instead", "vs")
    trade_offs = []
    if "could" in text_lower or "instead" in text_lower or "trade" in text_lower:
        # Extract sentences with trade-off language
        for sent in sentences:
            if any(w in sent.lower() for w in ["could", "instead", "vs", "trade"]):
                trade_offs.append(sent.strip()[:80])  # First 80 chars

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
    opening = None
    if analysis.blockers_resolved:
        if tone == "warm":
            candidates = openings_warm
        elif tone == "formal":
            candidates = openings_formal
        else:
            candidates = openings_neutral
        opening = random.choice(candidates)(analysis.blockers_resolved)

    if not opening:
        fallback = openings_fallback_warm if tone == "warm" else openings_fallback_neutral
        opening = random.choice(fallback)

    parts.append(opening)

    # Body: Contextual narrative (not "The issue was causing problems—we fixed it")
    body_templates_warm = {
        "fix": [
            f"There was a bug that needed tackling. {analysis.key_insight}",
            f"Spotted an issue and patched it. {analysis.key_insight}",
            f"Fixed something that was broken. {analysis.key_insight}",
        ],
        "feature": [
            f"Added something new that should be useful. {analysis.key_insight}",
            f"Built out a new feature. {analysis.key_insight}",
            f"Implemented new functionality. {analysis.key_insight}",
        ],
        "refactor": [
            f"Tidied up the code to make it cleaner going forward. {analysis.key_insight}",
            f"Refactored some internals for better maintainability. {analysis.key_insight}",
            f"Improved the structure so it's easier to work with. {analysis.key_insight}",
        ],
    }

    body_templates_formal = {
        "fix": [f"Issue identified and resolved. {analysis.key_insight}"],
        "feature": [f"Feature implemented. {analysis.key_insight}"],
        "refactor": [f"Code architecture improved. {analysis.key_insight}"],
    }

    body_templates_neutral = body_templates_formal

    if tone == "warm":
        body_source = body_templates_warm
    elif tone == "formal":
        body_source = body_templates_formal
    else:
        body_source = body_templates_neutral

    body = random.choice(body_source.get(analysis.work_type, [analysis.key_insight]))
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
    full_text = " ".join(parts)
    words = full_text.split()
    if len(words) > max_words:
        full_text = " ".join(words[:max_words]) + "…"

    return full_text


def polish_for_audio(text: str, max_length: int = 300) -> str:
    """Optimize for text-to-speech playback.

    - Remove markdown/code
    - Expand acronyms
    - Ensure readability
    """
    # Remove code blocks
    text = re.sub(r'```[\s\S]*?```', '[code]', text)
    text = re.sub(r'`([^`]+)`', r'\1', text)

    # Expand common acronyms (can be enhanced per user profile)
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
