"""Tests for voice summary orchestration synthesis.

Tests deterministic templates, LLM fallback, voice synthesis,
and fail-graceful error handling.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from core.console.corvin_console.voice_summary_orchestration import (
    OrchestrationCompleteEvent,
    TaskResult,
    _format_duration,
    _generate_deterministic_summary,
    _generate_task_summary,
    _is_all_success,
    _synthesize_voice_file,
    _call_llm_fallback,
    _get_outbox_path,
    synthesize_orchestration_summary,
)


class TestFormatDuration:
    """Test duration formatting to German text."""

    def test_milliseconds(self):
        assert _format_duration(0.5) == "500 Millisekunden"

    def test_single_second(self):
        assert _format_duration(1.0) == "1 Sekunde"

    def test_multiple_seconds(self):
        assert _format_duration(7.0) == "7 Sekunden"

    def test_single_minute(self):
        assert _format_duration(60.0) == "1 Minute"

    def test_multiple_minutes(self):
        assert _format_duration(120.0) == "2 Minuten"

    def test_minute_and_seconds(self):
        assert _format_duration(323.0) == "5 Minuten 23 Sekunden"

    def test_multiple_minutes_and_seconds(self):
        assert _format_duration(67.0) == "1 Minute 7 Sekunden"


class TestGenerateTaskSummary:
    """Test individual task summary generation."""

    def test_success_task(self):
        task = TaskResult(task_name="Video Producer", status="success", duration_seconds=323.0)
        summary = _generate_task_summary(task)
        assert "Video Producer" in summary
        assert "erfolgreich" in summary
        assert "5 Minuten 23 Sekunden" in summary

    def test_failed_task_with_error(self):
        task = TaskResult(
            task_name="Knowledge Graph",
            status="failed",
            duration_seconds=134.0,
            error_message="database timeout",
        )
        summary = _generate_task_summary(task)
        assert "Knowledge Graph" in summary
        assert "fehlgeschlagen" in summary
        assert "database timeout" in summary

    def test_failed_task_without_error(self):
        task = TaskResult(
            task_name="Learning Index",
            status="failed",
            duration_seconds=50.0,
        )
        summary = _generate_task_summary(task)
        assert "Learning Index" in summary
        assert "fehlgeschlagen" in summary
        assert "unbekannter Fehler" in summary

    def test_timeout_task(self):
        task = TaskResult(
            task_name="Snapshot Service",
            status="timeout",
            duration_seconds=30.0,
        )
        summary = _generate_task_summary(task)
        assert "Snapshot Service" in summary
        assert "Timeout" in summary
        assert "30 Sekunden" in summary


class TestIsAllSuccess:
    """Test all-success detection."""

    def test_all_success(self):
        event = OrchestrationCompleteEvent(
            event_type="orchestration.completed",
            tasks=[
                TaskResult(task_name="Task 1", status="success", duration_seconds=10.0),
                TaskResult(task_name="Task 2", status="success", duration_seconds=20.0),
            ],
        )
        assert _is_all_success(event) is True

    def test_mixed_success_and_failure(self):
        event = OrchestrationCompleteEvent(
            event_type="orchestration.mixed_failure",
            tasks=[
                TaskResult(task_name="Task 1", status="success", duration_seconds=10.0),
                TaskResult(task_name="Task 2", status="failed", duration_seconds=20.0),
            ],
        )
        assert _is_all_success(event) is False

    def test_all_failed(self):
        event = OrchestrationCompleteEvent(
            event_type="orchestration.mixed_failure",
            tasks=[
                TaskResult(task_name="Task 1", status="failed", duration_seconds=10.0),
                TaskResult(task_name="Task 2", status="failed", duration_seconds=20.0),
            ],
        )
        assert _is_all_success(event) is False


class TestGenerateDeterministicSummary:
    """Test deterministic template matching."""

    def test_success_case_three_tasks(self):
        """Test success template with exactly three tasks."""
        event = OrchestrationCompleteEvent(
            event_type="orchestration.completed",
            tasks=[
                TaskResult(
                    task_name="Video Producer",
                    status="success",
                    duration_seconds=323.0,
                ),
                TaskResult(
                    task_name="Knowledge Graph snapshot",
                    status="success",
                    duration_seconds=134.0,
                ),
                TaskResult(
                    task_name="Learning Index",
                    status="success",
                    duration_seconds=67.0,
                ),
            ],
        )

        summary = _generate_deterministic_summary(event)
        assert summary is not None
        assert "Hintergrund-Tasks erfolgreich" in summary
        assert "Video Producer" in summary
        assert "Knowledge Graph snapshot" in summary
        assert "Learning Index" in summary
        assert "Alle Systeme bereit" in summary

    def test_mixed_failure_case(self):
        """Test mixed failure template."""
        event = OrchestrationCompleteEvent(
            event_type="orchestration.mixed_failure",
            tasks=[
                TaskResult(
                    task_name="Video Producer",
                    status="success",
                    duration_seconds=300.0,
                ),
                TaskResult(
                    task_name="Knowledge Graph snapshot",
                    status="failed",
                    duration_seconds=120.0,
                    error_message="database timeout",
                ),
                TaskResult(
                    task_name="Learning Index",
                    status="success",
                    duration_seconds=60.0,
                ),
            ],
        )

        summary = _generate_deterministic_summary(event)
        assert summary is not None
        assert "mit Problemen" in summary
        assert "database timeout" in summary
        assert "Bitte wiederholen" in summary

    def test_too_few_tasks(self):
        """Test that single task doesn't match template."""
        event = OrchestrationCompleteEvent(
            event_type="orchestration.completed",
            tasks=[
                TaskResult(task_name="Task 1", status="success", duration_seconds=10.0),
            ],
        )
        summary = _generate_deterministic_summary(event)
        assert summary is None

    def test_too_many_tasks(self):
        """Test that >5 tasks don't match template (too complex)."""
        event = OrchestrationCompleteEvent(
            event_type="orchestration.completed",
            tasks=[
                TaskResult(task_name=f"Task {i}", status="success", duration_seconds=10.0)
                for i in range(6)
            ],
        )
        summary = _generate_deterministic_summary(event)
        assert summary is None

    def test_unknown_task_status(self):
        """Test that unknown status doesn't match template."""
        event = OrchestrationCompleteEvent(
            event_type="orchestration.completed",
            tasks=[
                TaskResult(task_name="Task 1", status="pending", duration_seconds=10.0),
                TaskResult(task_name="Task 2", status="success", duration_seconds=20.0),
            ],
        )
        summary = _generate_deterministic_summary(event)
        assert summary is None


class TestGetOutboxPath:
    """Test outbox path resolution."""

    def test_default_outbox_path(self, tmp_path):
        """Test outbox path uses CORVIN_HOME."""
        with mock.patch.dict(os.environ, {"CORVIN_HOME": str(tmp_path)}):
            outbox = _get_outbox_path()
            assert outbox == tmp_path / "shared" / "outbox"
            assert outbox.exists()

    def test_outbox_created_if_missing(self, tmp_path):
        """Test that outbox directory is created."""
        with mock.patch.dict(os.environ, {"CORVIN_HOME": str(tmp_path)}):
            # Ensure directory doesn't exist
            outbox_path = tmp_path / "shared" / "outbox"
            assert not outbox_path.exists()

            # Call function
            outbox = _get_outbox_path()

            # Directory should be created
            assert outbox.exists()
            assert outbox.is_dir()


class TestSynthesizeVoiceFile:
    """Test voice file synthesis."""

    def test_voice_synthesis_success(self, tmp_path):
        """Test successful voice synthesis."""
        with mock.patch.dict(os.environ, {"CORVIN_HOME": str(tmp_path)}):
            # Create mock output file
            outbox = tmp_path / "shared" / "outbox"
            outbox.mkdir(parents=True)
            expected_output = outbox / "test_output.ogg"

            # Mock say.py existence
            with mock.patch("core.console.corvin_console.voice_summary_orchestration.Path.exists", return_value=True):
                with mock.patch("subprocess.run") as mock_run:
                    # Mock successful say.py call
                    mock_run.return_value = mock.Mock(
                        returncode=0,
                        stdout=str(expected_output),
                        stderr="",
                    )

                    # Create the file so it exists
                    expected_output.touch()

                    result = _synthesize_voice_file("Test summary")

                    assert result == str(expected_output)
                    mock_run.assert_called_once()

                    # Verify call parameters
                    call_args = mock_run.call_args
                    assert "de" in call_args[0][0]  # language param
                    assert "shimmer" in call_args[0][0]  # voice param
                    assert "Test summary" in call_args[0][0]  # text param

    def test_voice_synthesis_disabled(self, tmp_path):
        """Test voice synthesis when say.py silently disables it."""
        with mock.patch.dict(os.environ, {"CORVIN_HOME": str(tmp_path)}):
            # Create the outbox directory but NOT the output file
            outbox = tmp_path / "shared" / "outbox"
            outbox.mkdir(parents=True)

            # Mock the say.py existence check but NOT the output file check
            original_exists = Path.exists

            def selective_exists(path_obj):
                """Only mock exists for say.py, not for output files."""
                if "say.py" in str(path_obj):
                    return True
                return original_exists(path_obj)

            with mock.patch.object(Path, "exists", selective_exists):
                with mock.patch("subprocess.run") as mock_run:
                    # say.py returns 0 (success) with empty stdout (silently disabled)
                    mock_run.return_value = mock.Mock(
                        returncode=0,
                        stdout="",
                        stderr="",
                    )

                    result = _synthesize_voice_file("Test summary")

                    # Should return None because stdout is empty AND output file doesn't exist
                    assert result is None

    def test_voice_synthesis_timeout(self, tmp_path):
        """Test voice synthesis timeout."""
        with mock.patch.dict(os.environ, {"CORVIN_HOME": str(tmp_path)}):
            with mock.patch("core.console.corvin_console.voice_summary_orchestration.Path.exists", return_value=True):
                with mock.patch("subprocess.run") as mock_run:
                    # Simulate timeout
                    mock_run.side_effect = subprocess.TimeoutExpired("python3", 30)

                    result = _synthesize_voice_file("Test summary")

                    assert result is None

    def test_voice_synthesis_script_not_found(self, tmp_path):
        """Test when say.py script doesn't exist."""
        with mock.patch.dict(os.environ, {"CORVIN_HOME": str(tmp_path)}):
            with mock.patch(
                "core.console.corvin_console.voice_summary_orchestration.Path.exists",
                return_value=False,
            ):
                result = _synthesize_voice_file("Test summary")

                assert result is None

    def test_voice_synthesis_usage_error(self, tmp_path):
        """Test voice synthesis when say.py returns usage error."""
        with mock.patch.dict(os.environ, {"CORVIN_HOME": str(tmp_path)}):
            with mock.patch("core.console.corvin_console.voice_summary_orchestration.Path.exists", return_value=True):
                with mock.patch("subprocess.run") as mock_run:
                    mock_run.return_value = mock.Mock(
                        returncode=2,
                        stdout="",
                        stderr="usage error",
                    )

                    result = _synthesize_voice_file("Test summary")

                    assert result is None


class TestCallLLMFallback:
    """Test LLM fallback summary generation."""

    @pytest.mark.asyncio
    async def test_llm_fallback_success(self):
        """Test successful LLM fallback."""
        event = OrchestrationCompleteEvent(
            event_type="orchestration.completed",
            tasks=[
                TaskResult(task_name="Task 1", status="success", duration_seconds=10.0),
                TaskResult(task_name="Task 2", status="failed", duration_seconds=20.0, error_message="error"),
            ],
        )

        with mock.patch("anthropic.AsyncAnthropic") as mock_client:
            # Mock LLM response
            mock_instance = mock.AsyncMock()
            mock_client.return_value = mock_instance
            mock_instance.messages.create.return_value = mock.Mock(
                content=[mock.Mock(text="Zusammenfassung der Tasks...")]
            )

            result = await _call_llm_fallback(event)

            assert result == "Zusammenfassung der Tasks..."
            mock_instance.messages.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_llm_fallback_timeout(self):
        """Test LLM fallback timeout."""
        event = OrchestrationCompleteEvent(
            event_type="orchestration.completed",
            tasks=[
                TaskResult(task_name="Task 1", status="success", duration_seconds=10.0),
            ],
        )

        with mock.patch("anthropic.AsyncAnthropic") as mock_client:
            mock_instance = mock.AsyncMock()
            mock_client.return_value = mock_instance

            # Simulate timeout
            async def timeout_handler(*args, **kwargs):
                await asyncio.sleep(10)

            mock_instance.messages.create.side_effect = timeout_handler

            result = await _call_llm_fallback(event)

            # Timeout should return None
            assert result is None

    @pytest.mark.asyncio
    async def test_llm_fallback_error(self):
        """Test LLM fallback error handling."""
        event = OrchestrationCompleteEvent(
            event_type="orchestration.completed",
            tasks=[
                TaskResult(task_name="Task 1", status="success", duration_seconds=10.0),
            ],
        )

        with mock.patch("anthropic.AsyncAnthropic") as mock_client:
            mock_instance = mock.AsyncMock()
            mock_client.return_value = mock_instance
            mock_instance.messages.create.side_effect = Exception("API error")

            result = await _call_llm_fallback(event)

            assert result is None

    @pytest.mark.asyncio
    async def test_llm_fallback_empty_response(self):
        """Test LLM fallback with empty response."""
        event = OrchestrationCompleteEvent(
            event_type="orchestration.completed",
            tasks=[
                TaskResult(task_name="Task 1", status="success", duration_seconds=10.0),
            ],
        )

        with mock.patch("anthropic.AsyncAnthropic") as mock_client:
            mock_instance = mock.AsyncMock()
            mock_client.return_value = mock_instance
            mock_instance.messages.create.return_value = mock.Mock(content=[])

            result = await _call_llm_fallback(event)

            assert result is None


class TestSynthesizeOrchestrationSummary:
    """Test full orchestration summary synthesis pipeline."""

    @pytest.mark.asyncio
    async def test_full_pipeline_success_deterministic(self, tmp_path):
        """Test full pipeline with deterministic template match."""
        with mock.patch.dict(os.environ, {"CORVIN_HOME": str(tmp_path)}):
            event = OrchestrationCompleteEvent(
                event_type="orchestration.completed",
                tasks=[
                    TaskResult(task_name="Video Producer", status="success", duration_seconds=323.0),
                    TaskResult(task_name="Knowledge Graph", status="success", duration_seconds=134.0),
                    TaskResult(task_name="Learning Index", status="success", duration_seconds=67.0),
                ],
            )

            with mock.patch("core.console.corvin_console.voice_summary_orchestration.Path.exists", return_value=True):
                with mock.patch("subprocess.run") as mock_run:
                    outbox = tmp_path / "shared" / "outbox"
                    outbox.mkdir(parents=True)
                    expected_output = outbox / "orchestration_summary_20260922_120000.ogg"

                    mock_run.return_value = mock.Mock(
                        returncode=0,
                        stdout=str(expected_output),
                        stderr="",
                    )
                    expected_output.touch()

                    result = await synthesize_orchestration_summary(event)

                    assert result is not None
                    assert "ogg" in result.lower()
                    assert event.voice_attachment_path == result

    @pytest.mark.asyncio
    async def test_full_pipeline_llm_fallback(self, tmp_path):
        """Test full pipeline with LLM fallback."""
        with mock.patch.dict(os.environ, {"CORVIN_HOME": str(tmp_path)}):
            # Event too complex for deterministic template (6 tasks)
            event = OrchestrationCompleteEvent(
                event_type="orchestration.completed",
                tasks=[
                    TaskResult(task_name=f"Task {i}", status="success", duration_seconds=10.0)
                    for i in range(6)
                ],
            )

            with mock.patch("anthropic.AsyncAnthropic") as mock_llm:
                mock_instance = mock.AsyncMock()
                mock_llm.return_value = mock_instance
                mock_instance.messages.create.return_value = mock.Mock(
                    content=[mock.Mock(text="LLM-generated summary")]
                )

                with mock.patch("core.console.corvin_console.voice_summary_orchestration.Path.exists", return_value=True):
                    with mock.patch("subprocess.run") as mock_run:
                        outbox = tmp_path / "shared" / "outbox"
                        outbox.mkdir(parents=True)
                        expected_output = outbox / "test.ogg"

                        mock_run.return_value = mock.Mock(
                            returncode=0,
                            stdout=str(expected_output),
                            stderr="",
                        )
                        expected_output.touch()

                        result = await synthesize_orchestration_summary(event)

                        assert result is not None
                        # LLM should have been called since template didn't match
                        mock_instance.messages.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_full_pipeline_empty_event(self):
        """Test full pipeline with empty event."""
        event = OrchestrationCompleteEvent(event_type="orchestration.completed", tasks=[])

        result = await synthesize_orchestration_summary(event)

        assert result is None

    @pytest.mark.asyncio
    async def test_full_pipeline_all_methods_fail(self, tmp_path):
        """Test full pipeline when all methods fail."""
        with mock.patch.dict(os.environ, {"CORVIN_HOME": str(tmp_path)}):
            # Event too complex for deterministic template
            event = OrchestrationCompleteEvent(
                event_type="orchestration.completed",
                tasks=[
                    TaskResult(task_name=f"Task {i}", status="success", duration_seconds=10.0)
                    for i in range(6)
                ],
            )

            with mock.patch("anthropic.AsyncAnthropic") as mock_llm:
                mock_instance = mock.AsyncMock()
                mock_llm.return_value = mock_instance
                # LLM fails
                mock_instance.messages.create.side_effect = Exception("LLM error")

                result = await synthesize_orchestration_summary(event)

                # Should return None (fail-graceful)
                assert result is None
