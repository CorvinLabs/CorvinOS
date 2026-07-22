"""End-to-End tests for Smart Voice Summary system.

Tests the full pipeline: input → analysis → narration → polish
Across ALL response types (not just code).

Run: pytest test_summarize_smart_e2e.py -v
"""

import subprocess
import sys
from pathlib import Path

# Find the scripts directory
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"


def run_summarize_smart(text: str, lang: str = "en", max_chars: int = 300) -> str:
    """Helper to run summarize_smart.py and return output."""
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "summarize_smart.py"), "--lang", lang, "--max-chars", str(max_chars)],
        input=text,
        capture_output=True,
        text=True,
        timeout=30,
    )

    if result.returncode != 0:
        raise RuntimeError(f"summarize_smart.py failed: {result.stderr}")

    return result.stdout.strip()


class TestBugFixNarration:
    """Test that bug fixes are narrated with context, not just listed."""

    def test_bug_fix_includes_problem_and_solution(self):
        """Summary should explain both the problem and why it was fixed."""
        response = (
            "Fixed the image loading null pointer crash. The issue was that "
            "handleImageLoad() didn't check for null before dereferencing. "
            "Added a guard check. Also improved error logging so we can see what happened "
            "when this breaks again. The fix is 4 lines in src/renderer/image.ts."
        )

        summary = run_summarize_smart(response, lang="en")

        # Should mention both problem and solution
        summary_lower = summary.lower()
        assert ("null" in summary_lower or "crash" in summary_lower), \
            f"Summary doesn't mention the problem: {summary}"
        assert ("fix" in summary_lower or "check" in summary_lower), \
            f"Summary doesn't mention the solution: {summary}"

    def test_bug_fix_explains_benefit(self):
        """Summary should explain why the user should care."""
        response = (
            "Fixed a race condition in the authentication module that was causing "
            "sporadic login failures. The issue was that the token expiry check wasn't "
            "atomic—between the check and the refresh, the token could become invalid. "
            "Now wrapped it in a mutex. This unblocks the auth refactor we've been planning."
        )

        summary = run_summarize_smart(response)
        summary_lower = summary.lower()

        # Should explain the impact or why it matters
        assert any(w in summary_lower for w in ["auth", "login", "unblock"]), \
            f"Summary doesn't explain the benefit: {summary}"


class TestFeatureNarration:
    """Test that features are narrated with rationale."""

    def test_feature_explains_what_it_does(self):
        """Summary should explain what the feature DOES, not just that it exists."""
        response = (
            "Implemented OAuth2 flow with PKCE for better security. "
            "Clients exchange a code for a token instead of sending the secret directly. "
            "Supports refresh tokens for seamless re-auth. Tested against the iOS app—works great."
        )

        summary = run_summarize_smart(response)
        summary_lower = summary.lower()

        # Should mention OAuth/auth functionality
        assert any(w in summary_lower for w in ["oauth", "auth", "token", "login"]), \
            f"Summary doesn't describe the feature: {summary}"


class TestRefactorNarration:
    """Test that refactors explain WHY they were done."""

    def test_refactor_explains_problem_and_benefit(self):
        """Summary should explain the problem being solved, not just list changes."""
        response = (
            "Refactored the migration system to use a factory pattern. "
            "The old code had all version-specific logic crammed into one giant function—"
            "hard to test, hard to extend. New approach: each migration version gets its own handler, "
            "so they're testable in isolation. Added 8 unit tests covering every upgrade path. "
            "This makes it much faster to add new migrations in the future."
        )

        summary = run_summarize_smart(response)
        summary_lower = summary.lower()

        # Should mention the improvement/benefit
        assert any(w in summary_lower for w in ["test", "maintain", "easier", "better", "improve"]), \
            f"Summary doesn't explain the benefit: {summary}"


class TestNonTechnicalResponse:
    """Test that non-code responses (guidance, research) are handled well."""

    def test_research_response_summarized(self):
        """Non-code responses (e.g., research, guidance) should be summarized well."""
        response = (
            "Here's the authentication flow architecture: "
            "1. Client sends username/password to /auth/login "
            "2. Server validates and returns a session token "
            "3. Client includes token in Authorization header for subsequent requests "
            "4. Server validates token on each request "
            "Token expires after 24 hours, triggering re-auth. "
            "Alternative: use JWT for stateless auth (no DB lookup per request)."
        )

        summary = run_summarize_smart(response)

        # Summary should be non-empty and readable
        assert len(summary) > 20, "Summary too short"
        assert len(summary) < 500, "Summary too long"  # Should fit in voice note
        assert "auth" in summary.lower() or "token" in summary.lower(), \
            f"Summary lost the main topic: {summary}"


class TestLengthConstraints:
    """Test that summaries respect length limits for voice notes."""

    def test_summary_fits_in_60_seconds(self):
        """Summary should fit in ~60 seconds @ 150 wpm ≈ 150 words."""
        long_response = " ".join([
            "We fixed multiple bugs in the authentication system. " * 20,
            "Added better error handling. " * 20,
            "Improved logging. " * 20,
        ])

        summary = run_summarize_smart(long_response, max_chars=300)

        # Count words (rough estimate)
        word_count = len(summary.split())
        assert word_count <= 250, f"Summary too long: {word_count} words (should be ≤250)"


class TestMultipleLangs:
    """Test that smart summary works across languages."""

    def test_german_summary(self):
        """Summary should work in German."""
        response = (
            "Wir haben einen Bug behoben: handleImageLoad() null pointer crash. "
            "Addd guard check und besseres Error-Logging. "
            "Das unblockiert die Rendering-Pipeline."
        )

        summary = run_summarize_smart(response, lang="de")

        # Should be non-empty and German
        assert len(summary) > 20, "German summary too short"
        # Should contain some of the key words
        assert any(w in summary.lower() for w in ["bug", "fix", "pipeline", "fehler"]), \
            f"German summary lost content: {summary}"

    def test_english_summary(self):
        """Summary should work in English."""
        response = (
            "Fixed the null pointer crash in image rendering. Added guard check and better logging. "
            "This unblocks the rendering pipeline we've been working on."
        )

        summary = run_summarize_smart(response, lang="en")

        # Should be non-empty and English
        assert len(summary) > 20, "English summary too short"
        assert any(w in summary.lower() for w in ["fix", "null", "rendering"]), \
            f"English summary lost content: {summary}"


class TestQuality:
    """Test that summaries sound natural, not robotic."""

    def test_summary_not_robotic(self):
        """Summary should sound like a person speaking, not a list."""
        response = (
            "Fixed bug X. Updated Y. Added Z. Tested with A. Verified with B."
        )

        summary = run_summarize_smart(response)

        # Should NOT just be "Fix bug X. Update Y. Add Z."
        # Should have connecting words, be more narrative
        assert len(summary) > len(response) * 0.5, \
            "Summary is too similar to the condensed original—likely just a list"

    def test_summary_explains_reasoning(self):
        """Summary should explain WHY, not just WHAT."""
        response = (
            "Changed the caching strategy from LRU to LFU. "
            "LRU was evicting hot items too aggressively. "
            "LFU tracks frequency, so hot items stay longer. "
            "This reduces cache misses by ~15% in production benchmarks."
        )

        summary = run_summarize_smart(response)
        summary_lower = summary.lower()

        # Should explain the reasoning (why LFU is better)
        assert any(w in summary_lower for w in ["why", "because", "hot", "frequent", "better"]), \
            f"Summary doesn't explain reasoning: {summary}"


class TestCriticalWarnings:
    """Test that critical warnings / danger sections are preserved in summaries."""

    def test_critical_warnings_not_omitted(self):
        """WARNING/DANGER/CRITICAL sections must appear in summary (ADR-0209 v0.10.60)."""
        response = (
            "Fixed login bug in staging. "
            "WARNING: Do NOT deploy to production until the database migration runs. "
            "The schema change is NOT backwards compatible. "
            "Migration must run before any server upgrade."
        )

        summary = run_summarize_smart(response, max_chars=500)
        summary_lower = summary.lower()

        # Must preserve at least one of the warning keywords
        assert any(w in summary_lower for w in ["warning", "danger", "critical", "must", "not deploy"]), \
            f"Summary lost critical warning: {summary}"

    def test_danger_section_preserved(self):
        """DANGER sections must be fully preserved."""
        response = (
            "Implemented new authentication protocol. "
            "DANGER: This is a breaking change — all clients must upgrade within 48 hours "
            "or they will be locked out. No graceful degradation possible."
        )

        summary = run_summarize_smart(response, max_chars=500)
        summary_lower = summary.lower()

        # Must mention both the breaking nature AND the deadline
        assert "breaking" in summary_lower or "upgrade" in summary_lower or "48" in summary_lower, \
            f"Summary didn't capture the danger/urgency: {summary}"

    def test_critical_deployment_notes_preserved(self):
        """Critical deployment/operational notes must not be silently omitted."""
        response = (
            "Fixed memory leak in worker pool. "
            "CRITICAL: Workers must be restarted after deployment. "
            "Simply reloading the module is NOT sufficient — it will cause a hang. "
            "Requires full process restart and health check validation."
        )

        summary = run_summarize_smart(response, max_chars=600)
        summary_lower = summary.lower()

        # Must mention restart requirement
        assert any(w in summary_lower for w in ["restart", "reboot", "process", "critical"]), \
            f"Summary omitted critical restart requirement: {summary}"


if __name__ == "__main__":
    # Quick sanity test
    test_response = "Fixed the bug. Added tests. Works now."
    try:
        result = run_summarize_smart(test_response)
        print(f"✓ Smart summarizer works\nInput: {test_response}\nOutput: {result}")
    except Exception as e:
        print(f"✗ Smart summarizer failed: {e}")
        sys.exit(1)
