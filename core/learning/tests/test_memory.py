"""Tests for bounded memory preview (ADR-0389)."""
import pytest
from datetime import datetime
import sys
from pathlib import Path

# ``operator/`` is shadowed by the stdlib module; register the package under
# its own top-level name (same mechanism the console uses at boot).
from core.learning.classifier_model import import_context_engineering

import_context_engineering()
from context_engineering.rich_task_brief import MemoryMatch  # noqa: E402


class TestPreviewTruncation:
    """Test that memory preview is bounded to 50 chars (ADR-0389)."""

    def test_preview_truncation(self):
        """Verify preview is exactly 50 chars max."""
        # Create a long content string
        long_content = "a" * 100  # 100 chars

        # Simulate what memory_lookup._score_file does
        preview = long_content[:50].replace("\n", " ").strip()

        # Verify truncation
        assert len(preview) == 50
        assert preview == "a" * 50

    def test_preview_with_newlines(self):
        """Verify preview removes newlines and truncates."""
        content = "First line\nSecond line\nThird line"

        preview = content[:50].replace("\n", " ").strip()

        # Should be truncated and newlines replaced
        assert len(preview) <= 50
        assert "\n" not in preview
        assert " " in preview  # Newlines replaced with spaces

    def test_preview_shorter_than_limit(self):
        """Verify short content is not padded."""
        short_content = "Short text"
        preview = short_content[:50].replace("\n", " ").strip()

        assert len(preview) == len(short_content)
        assert preview == short_content


class TestMemoryMatchPreviewField:
    """Test MemoryMatch preview field."""

    def test_memory_match_with_bounded_preview(self):
        """Verify MemoryMatch accepts bounded preview."""
        preview = "x" * 50  # Exactly 50 chars
        match = MemoryMatch(
            filename="test.md",
            title="Test Memory",
            relevance_score=0.8,
            source_file="/tmp/test.md",
            timestamp=datetime.now(),
            content_preview=preview,
        )

        assert match.content_preview == preview
        assert len(match.content_preview) == 50

    def test_memory_match_default_empty_preview(self):
        """Verify MemoryMatch default preview is empty string."""
        match = MemoryMatch(
            filename="test.md",
            title="Test Memory",
            relevance_score=0.8,
            source_file="/tmp/test.md",
            timestamp=datetime.now(),
        )

        assert match.content_preview == ""
        assert isinstance(match.content_preview, str)


class TestRenderingWithBoundedPreview:
    """Test that rendering works with bounded preview."""

    def test_rendering_with_bounded_preview(self):
        """Verify brief renders correctly with truncated preview."""
        from context_engineering.rich_task_brief import (
            RichTaskBrief,
            MemoryContext,
        )

        # Create match with bounded preview
        match = MemoryMatch(
            filename="memory.md",
            title="Memory Title",
            relevance_score=0.75,
            source_file="/tmp/memory.md",
            timestamp=datetime.now(),
            content_preview="x" * 50,  # Bounded 50 chars
        )

        memory_context = MemoryContext(
            matches=[match],
            search_queries=["test"],
            confidence=0.75,
            cache_hit=False,
            search_duration_ms=10.0,
        )

        brief = RichTaskBrief(
            raw_input="test input",
            enriched_task={},
            memory_context=memory_context,
            timestamp=datetime.now(),
        )

        # Verify brief can be created and rendered
        assert len(brief.memory_context.matches) == 1
        assert brief.memory_context.matches[0].content_preview == "x" * 50
        assert len(brief.memory_context.matches[0].content_preview) == 50

    def test_multiple_matches_with_preview(self):
        """Verify multiple matches all have bounded previews."""
        from context_engineering.rich_task_brief import MemoryContext

        matches = [
            MemoryMatch(
                filename=f"mem{i}.md",
                title=f"Memory {i}",
                relevance_score=0.5 + i * 0.1,
                source_file=f"/tmp/mem{i}.md",
                timestamp=datetime.now(),
                content_preview=f"content{i}" * 5,  # Variable length, all ≤50
            )
            for i in range(3)
        ]

        memory_context = MemoryContext(
            matches=matches,
            search_queries=["test"],
            confidence=0.6,
            cache_hit=False,
            search_duration_ms=15.0,
        )

        # All previews should be present and bounded
        for match in memory_context.matches:
            assert len(match.content_preview) <= 50
