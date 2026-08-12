"""Unit tests for ConfidenceGrader (ADR-0307)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.skills.graders.confidence import ConfidenceGrader


class TestConfidenceGrader:
    """ConfidenceGrader LLM-based scoring tests."""

    def test_init_default(self):
        grader = ConfidenceGrader()
        assert grader.model == "claude-opus-5"

    def test_init_custom_model(self):
        grader = ConfidenceGrader(model="claude-sonnet-5")
        assert grader.model == "claude-sonnet-5"

    @pytest.mark.asyncio
    async def test_grade_success(self):
        grader = ConfidenceGrader()

        # Mock anthropic client
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="0.85")]
        mock_client.messages.create.return_value = mock_response

        grader.client = mock_client

        request = {
            "skill_name": "test-skill",
            "output": "result output",
            "exception": None,
        }
        grade = await grader.grade(request)

        assert grade is not None
        assert grade.value == 0.85
        assert "0.85" in grade.feedback
        mock_client.messages.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_grade_with_exception(self):
        grader = ConfidenceGrader()

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="0.3")]
        mock_client.messages.create.return_value = mock_response

        grader.client = mock_client

        request = {
            "skill_name": "test-skill",
            "exception": "RuntimeError",
        }
        grade = await grader.grade(request)

        assert grade is not None
        assert grade.value == 0.3
        # Verify prompt mentions exception
        call_args = mock_client.messages.create.call_args
        prompt = call_args[1]["messages"][0]["content"]
        assert "RuntimeError" in prompt

    @pytest.mark.asyncio
    async def test_grade_parse_score_clamped(self):
        grader = ConfidenceGrader()

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="1.5")]  # Out of bounds
        mock_client.messages.create.return_value = mock_response

        grader.client = mock_client

        request = {"skill_name": "test", "output": "result", "exception": None}
        grade = await grader.grade(request)

        assert grade is not None
        assert grade.value == 1.0  # Clamped to 1.0

    @pytest.mark.asyncio
    async def test_grade_parse_score_unparseable(self):
        grader = ConfidenceGrader()

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="no number here")]  # Unparseable
        mock_client.messages.create.return_value = mock_response

        grader.client = mock_client

        request = {"skill_name": "test", "output": "result", "exception": None}
        grade = await grader.grade(request)

        assert grade is not None
        assert grade.value == 0.5  # Neutral fallback

    @pytest.mark.asyncio
    async def test_grade_api_failure(self):
        grader = ConfidenceGrader()

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API error")

        grader.client = mock_client

        request = {"skill_name": "test", "output": "result", "exception": None}
        grade = await grader.grade(request)

        assert grade is not None
        assert grade.value == 0.5  # Neutral fallback
        assert "error" in grade.feedback.lower()

    @pytest.mark.asyncio
    async def test_grade_parse_score_from_text(self):
        grader = ConfidenceGrader()

        # Test various formats
        test_cases = [
            ("The score is 0.75", 0.75),
            ("0.5", 0.5),
            ("excellent, 0.9", 0.9),
            ("1", 1.0),
            ("rating 0.0", 0.0),
        ]

        for text, expected in test_cases:
            score = ConfidenceGrader._parse_score(text)
            assert score == expected, f"Failed for text: {text}"

    @pytest.mark.asyncio
    async def test_grade_parse_score_invalid(self):
        grader = ConfidenceGrader()

        # Invalid formats
        test_cases = [
            "no number here",
            "2.5 out of bounds",
            "empty string",
        ]

        for text in test_cases:
            score = ConfidenceGrader._parse_score(text)
            assert score is None, f"Should be None for text: {text}"

    @pytest.mark.asyncio
    async def test_grade_invalid_api_key(self):
        grader = ConfidenceGrader(api_key="fake-key")
        grader.client = None  # Force client to be None to simulate init failure

        # Simulate grading with no client
        request = {"skill_name": "test", "output": "result", "exception": None}

        # When client is None, grade() returns None (no client available)
        # When client exists but fails, grade() returns neutral Grade(0.5)
        # In this test, client is None so we get None
        grade = await grader.grade(request)

        # Should return None if client could not be initialized
        if grader.client is None:
            assert grade is None
        else:
            # If client was somehow created, we get a neutral grade on error
            assert grade is not None
