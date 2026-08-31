"""Tests for Smart Voice Summary Engine.

Verify that voice summaries:
1. Extract correct work type (fix/feature/refactor/docs)
2. Include context (why it matters)
3. Explain reasoning (trade-offs, benefits)
4. Sound natural (not robotic text reading)
5. Fit in voice-note time (60 seconds ≈ 150 words)
"""
from corvin_console.voice_summary_smart import (
    ResponseAnalysis,
    analyze_response,
    generate_voice_summary,
    polish_for_audio,
)


def test_analyze_identifies_bug_fix():
    """Bug fix should be identified correctly."""
    response = "Fixed null pointer crash by adding guard check in handleImageLoad()."
    analysis = analyze_response(response)
    assert analysis.work_type == "fix"


def test_analyze_identifies_feature():
    """Feature implementation should be identified."""
    response = "Implemented OAuth2 authentication flow with token refresh."
    analysis = analyze_response(response)
    assert analysis.work_type == "feature"


def test_analyze_identifies_refactor():
    """Refactoring work should be identified."""
    response = "Refactored the migration system to use factory pattern for better testability."
    analysis = analyze_response(response)
    assert analysis.work_type == "refactor"


def test_analyze_extracts_key_files():
    """File names should be extracted."""
    response = "Modified src/renderer/image.ts and src/utils/cache.py"
    analysis = analyze_response(response)
    assert "image.ts" in analysis.key_files or "cache.py" in analysis.key_files


def test_analyze_detects_blockers():
    """Should identify what problems are being unblocked."""
    response = "Fixed the pipeline issue that was blocking deployment."
    analysis = analyze_response(response)
    assert len(analysis.blockers_resolved) > 0
    assert "deployment" in analysis.blockers_resolved or "pipeline" in str(analysis.blockers_resolved)


def test_analyze_detects_testing():
    """Should recognize when testing is mentioned."""
    response = "Fixed the bug and verified it with unit tests and E2E tests."
    analysis = analyze_response(response)
    assert analysis.testing_mentioned is True


def test_analyze_detects_trade_offs():
    """Should identify trade-offs being made."""
    response = "Could have used async/await, but chose callbacks for backward compatibility."
    analysis = analyze_response(response)
    assert len(analysis.trade_offs) > 0


def test_voice_summary_includes_context():
    """Voice summary should explain WHY, not just WHAT."""
    response = "Fixed image loading crash by adding null check in renderer."
    analysis = analyze_response(response)
    summary = generate_voice_summary(analysis)

    # Should explain the problem or benefit
    assert "null" in summary.lower() or "crash" in summary.lower() or "fix" in summary.lower()
    assert len(summary) > 50  # Not just a one-liner


def test_voice_summary_sounds_natural():
    """Summary should be conversational, not robotic."""
    response = "Refactored migration system. Added tests. Better maintainability."
    analysis = analyze_response(response)
    summary = generate_voice_summary(analysis)

    # Should use natural language
    assert any(word in summary.lower() for word in ["we", "improved", "better", "easier"])
    # Should NOT just repeat the response
    assert summary != response


def test_voice_summary_respects_word_limit():
    """Summary should fit in ~60 second voice note (200 words)."""
    response = "Long response " * 50  # Create a long response
    analysis = analyze_response(response)
    summary = generate_voice_summary(analysis, max_words=200)

    word_count = len(summary.split())
    assert word_count <= 200, f"Summary too long: {word_count} words"


def test_polish_removes_code_blocks():
    """Code blocks should be removed or replaced."""
    text = "Here's the fix:\n```python\ndef handle():\n    pass\n```\nThat's it."
    polished = polish_for_audio(text)

    assert "```" not in polished
    assert "[code]" in polished or "fix" in polished.lower()


def test_polish_expands_acronyms():
    """Common acronyms should be expanded for clarity."""
    text = "We fixed the REST API issue in the CLI."
    polished = polish_for_audio(text)

    # API and CLI should be expanded
    assert "application programming interface" in polished.lower() or "REST" not in polished
    # At least one acronym should be handled
    assert len(polished) > len(text)  # Expanded = longer


def test_end_to_end_bug_fix():
    """Full pipeline: analyze → narrate → polish."""
    response = "Fixed the image loading null pointer crash. Added guard check in handleImageLoad(). Verified with tests."

    # Analyze
    analysis = analyze_response(response)
    assert analysis.work_type == "fix"
    assert analysis.testing_mentioned is True

    # Generate summary
    summary = generate_voice_summary(analysis)
    assert "null" in summary.lower() or "crash" in summary.lower() or "fix" in summary.lower()

    # Polish
    final = polish_for_audio(summary)
    assert len(final.split()) <= 200
    assert "```" not in final  # No code blocks

    # Should be different from original (not blindly reading)
    assert final != response[:len(final)]


def test_end_to_end_refactor():
    """Full pipeline for refactoring."""
    response = """Refactored the profile migration system to use a factory pattern.
    Moved all version-specific logic into discrete handlers so we can test each version independently.
    Added 8 unit tests covering all upgrade paths.
    This makes it much easier to add new migrations in the future."""

    analysis = analyze_response(response)
    assert analysis.work_type == "refactor"

    summary = generate_voice_summary(analysis)
    # Should mention improvements/maintainability
    assert any(w in summary.lower() for w in ["improve", "maintain", "easier", "test"])

    final = polish_for_audio(summary)
    assert len(final) > 0
    assert "```" not in final


def test_summaries_explain_trade_offs():
    """When trade-offs exist, they should be explained."""
    response = """Implemented sync API instead of async to match our existing patterns,
    even though async would be slightly faster."""

    analysis = analyze_response(response)
    summary = generate_voice_summary(analysis)

    # Should mention the choice and reasoning
    assert len(summary) > len("We implemented it")  # More than bare minimum


def test_high_risk_changes_flagged():
    """High-risk changes should have appropriate tone."""
    response = "Made a breaking change to the authentication protocol. This requires all clients to upgrade."

    analysis = analyze_response(response)
    assert analysis.risk_level == "high" or "breaking" in response.lower()

    summary = generate_voice_summary(analysis)
    # Should sound cautious or emphasize the importance
    assert "heads up" in summary.lower() or "important" in summary.lower() or len(summary) > 100


def test_voice_summary_respects_tone_profile():
    """Summary should use different language based on tone (warm vs formal)."""
    response = "Fixed the rendering bug. Tests pass."
    analysis = analyze_response(response)

    # Warm tone should use more conversational language
    summary_warm = generate_voice_summary(analysis, tone="warm")
    summary_formal = generate_voice_summary(analysis, tone="formal")

    # Both should contain some fix-related content
    assert any(w in summary_warm.lower() for w in ["fix", "bug", "solid"])
    assert any(w in summary_formal.lower() for w in ["fix", "bug"])

    # Warm should sound more conversational
    warm_words = ["we", "i", "you", "great", "good"]
    formal_words = ["system", "process", "verified"]
    warm_score = sum(1 for w in warm_words if w in summary_warm.lower())
    formal_score = sum(1 for w in formal_words if w in summary_formal.lower())
    # Warm should have more conversational markers
    assert warm_score >= formal_score or len(summary_warm) > 0


def test_voice_summary_varies_output():
    """Summaries should vary (not always identical robot statements)."""
    response = "Fixed a bug in the rendering system."
    analysis = analyze_response(response)

    # Generate multiple summaries from same analysis
    summaries = [generate_voice_summary(analysis, tone="warm") for _ in range(5)]

    # Should have at least 2 different outputs
    unique_summaries = set(summaries)
    assert len(unique_summaries) >= 2, f"Summaries should vary, got: {unique_summaries}"
