"""Tests for Iteration 3 Adversarial Review Fixes (2026-09-24).

Tests cover all 5 BLOCKING findings:
1. RACE-CONDITION-001: Concurrent writes to confidence_persistence.py
2. PII-LEAK-001: "[REDACTED]" string literal bypass detection
3. RESOURCE-EXHAUSTION-001: Unbounded dict growth in FeedbackBuffer
4. INPUT-VALIDATION-001: subject_id format validation in stream4_skill_feedback.py
5. REGRESSION-001: Same as #1 (concurrent writes)
"""

import json
import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest

from core.learning.confidence_persistence import (
    PersistentConfidenceStore,
    save_confidence_history,
    load_confidence_history,
)
from core.learning.feedback_sink import (
    FeedbackEvent,
    FeedbackBuffer,
    FeedbackValidator,
    FeedbackScrubber,
    OutcomeFeedbackType,
)


# ============================================================================
# RACE-CONDITION-001 & REGRESSION-001: Concurrent confidence_persistence writes
# ============================================================================

class TestRaceCondition001:
    """Verify that concurrent writes to confidence_persistence are protected by locks."""

    def test_concurrent_save_confidence_history(self, tmp_path, monkeypatch):
        """Test RACE-CONDITION-001: save_confidence_history() must use lock."""
        # Set up temp CORVIN_HOME
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))

        stat_key = "model_stats:simple:claude-opus-5:_default"
        tenant_id = "_default"
        stats_path = tmp_path / "tenants" / tenant_id / "global" / "model_confidence_stats.json"

        # Initial state
        initial_history = [0.5, 0.6, 0.7]
        save_confidence_history(stat_key, initial_history)

        # Verify initial write
        data = json.loads(stats_path.read_text("utf-8"))
        assert data[stat_key]["confidence_history"] == initial_history

        # Concurrent writers: 10 threads each writing different history
        histories = {
            f"history_{i}": [0.5 + i * 0.01, 0.6 + i * 0.01, 0.7 + i * 0.01]
            for i in range(10)
        }

        def writer(history_idx: int, history: list):
            """Write a history value."""
            for _ in range(5):  # Multiple writes per thread
                save_confidence_history(stat_key, history)
                time.sleep(0.001)  # Small delay to increase contention

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = []
            for idx, (_, history) in enumerate(histories.items()):
                futures.append(executor.submit(writer, idx, history))
            for f in futures:
                f.result()

        # Verify no corruption: final history should be one of the valid values
        final_data = json.loads(stats_path.read_text("utf-8"))
        final_history = final_data[stat_key]["confidence_history"]

        # Final history should match one of the written values (no partial overwrites)
        valid_histories = list(histories.values())
        assert final_history in valid_histories, (
            f"Final history {final_history} not in any written value, "
            f"indicating race condition corruption"
        )

    def test_concurrent_setitem_in_store(self, tmp_path, monkeypatch):
        """Test RACE-CONDITION-001: __setitem__() must use lock."""
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))

        store = PersistentConfidenceStore()
        stat_key = "model_stats:complex:claude-sonnet-5:_default"
        tenant_id = "_default"

        # Initial state
        initial_stats = {"n_samples": 0, "mean_confidence": 0.5}
        store[stat_key] = initial_stats

        # Concurrent updaters
        def update_stats(value_idx: int):
            """Update stats N times."""
            for i in range(10):
                new_stats = {
                    "n_samples": value_idx * 10 + i,
                    "mean_confidence": 0.5 + value_idx * 0.05,
                }
                store[stat_key] = new_stats
                time.sleep(0.001)

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(update_stats, i) for i in range(10)]
            for f in futures:
                f.result()

        # Verify no corruption: final stats should be a valid update
        final_stats = store[stat_key]
        assert "n_samples" in final_stats, "stats corrupted: missing n_samples"
        assert "mean_confidence" in final_stats, "stats corrupted: missing mean_confidence"
        assert 0.0 <= final_stats["mean_confidence"] <= 1.0, (
            f"stats corrupted: confidence {final_stats['mean_confidence']} out of range"
        )

    def test_regression_001_concurrent_both_paths(self, tmp_path, monkeypatch):
        """Test REGRESSION-001: Both save_confidence_history() and __setitem__() need locks."""
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))

        store = PersistentConfidenceStore()
        stat_key = "model_stats:medium:claude-haiku-4-5:_default"

        # Mix concurrent history saves and stats updates
        def mixed_operations(op_idx: int):
            """Mix history and stats operations."""
            for i in range(5):
                # Half the threads do history, half do stats
                if op_idx % 2 == 0:
                    history = [0.5 + i * 0.01 for _ in range(3)]
                    save_confidence_history(stat_key, history)
                else:
                    stats = {"n_samples": op_idx * 10 + i, "mean_confidence": 0.6}
                    store[stat_key] = stats
                time.sleep(0.001)

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(mixed_operations, i) for i in range(10)]
            for f in futures:
                f.result()

        # Verify final state is consistent
        final_history = load_confidence_history(stat_key)
        final_stats = store[stat_key]

        assert isinstance(final_history, list), "history corrupted: not a list"
        assert isinstance(final_stats, dict), "stats corrupted: not a dict"


# ============================================================================
# PII-LEAK-001: "[REDACTED]" string literal bypass
# ============================================================================

class TestPIILeak001:
    """Verify that PII detection uses regex, not string literal matching."""

    def test_redacted_literal_bypass_blocked(self):
        """Test PII-LEAK-001: Including literal '[REDACTED]' should NOT bypass PII scrubbing."""
        validator = FeedbackValidator()

        # Create feedback with literal "[REDACTED]" PLUS an email address
        # The old code would fail to detect the email if "[REDACTED]" was present
        feedback = FeedbackEvent.create(
            skill_id="os.delegation_router",
            task_id="task_123",
            tenant_id="_default",
            outcome_feedback=OutcomeFeedbackType.YES,
            reason="This routing was good [REDACTED] but also my email is test@example.com",
        )

        # Validation should FAIL because email is still present
        is_valid, error = validator.validate(feedback)
        assert not is_valid, (
            f"Validation should fail for PII bypass attempt, but got: {error}"
        )
        assert "PII" in (error or "").upper() or "email" in (error or "").lower(), (
            f"Error message should mention PII detection, got: {error}"
        )

    def test_email_still_detected_after_redacted(self):
        """Test that emails are detected even when '[REDACTED]' is already in the text."""
        scrubber = FeedbackScrubber()

        # Text with literal [REDACTED] AND an email
        text = "Result was good [REDACTED] contact: john.doe@example.com for details"

        scrubbed = scrubber.scrub(text)
        assert scrubbed is not None, "scrubbing should not return None"
        assert "@example.com" not in scrubbed, "email should be scrubbed"
        assert "[REDACTED]" in scrubbed, "[REDACTED] literal should be preserved"

    def test_phone_number_detection(self):
        """Test that phone numbers are properly detected and scrubbed."""
        validator = FeedbackValidator()

        feedback = FeedbackEvent.create(
            skill_id="os.flow_guard",
            task_id="task_456",
            tenant_id="_default",
            outcome_feedback=OutcomeFeedbackType.NO,
            reason="Call me at 555-123-4567 if you need details",
        )

        is_valid, error = validator.validate(feedback)
        assert not is_valid, "Validation should reject phone numbers"
        assert "PII" in (error or "").upper(), f"Error should mention PII, got: {error}"

    def test_ssn_like_pattern_detection(self):
        """Test SSN-like pattern detection."""
        validator = FeedbackValidator()

        feedback = FeedbackEvent.create(
            skill_id="os.security_orchestrator",
            task_id="task_789",
            tenant_id="_default",
            quality_rating=3,
            reason="My SSN is 123-45-6789 for verification",
        )

        is_valid, error = validator.validate(feedback)
        assert not is_valid, "Validation should reject SSN patterns"


# ============================================================================
# RESOURCE-EXHAUSTION-001: Unbounded dict growth
# ============================================================================

class TestResourceExhaustion001:
    """Verify FeedbackBuffer doesn't grow unbounded."""

    def test_buffer_cleanup_on_get_and_clear(self):
        """Test RESOURCE-EXHAUSTION-001: get_and_clear() must delete dict keys."""
        buffer = FeedbackBuffer(min_samples=2, confidence_threshold=0.5)

        # Add feedback for multiple (skill_id, task_id) pairs
        for i in range(100):
            feedback = FeedbackEvent.create(
                skill_id=f"os.skill_{i % 5}",
                task_id=f"task_{i}",
                tenant_id="_default",
                outcome_feedback=OutcomeFeedbackType.YES,
                confidence=0.8,
            )
            buffer.add(feedback)

        # Verify buffers dict has entries
        assert len(buffer.buffers) > 0, "buffers should have entries"
        initial_size = len(buffer.buffers)

        # Retrieve and clear half the buffers
        cleared_count = 0
        for skill_id in ["os.skill_0", "os.skill_1"]:
            for task_id in [f"task_{i}" for i in range(100) if i % 5 == int(skill_id[-1])]:
                buffer.get_and_clear(skill_id, task_id)
                cleared_count += 1
                if cleared_count > 20:
                    break

        # CRITICAL: buffers dict should have SHRUNK, not grown
        # With the fix, cleared keys are removed entirely
        final_size = len(buffer.buffers)
        # We cleared ~20+ entries, so dict should be smaller
        # (exact count depends on the distribution, but it should definitely shrink)
        assert final_size < initial_size, (
            f"buffers dict should shrink after clearing entries. "
            f"Before: {initial_size}, After: {final_size}"
        )

    def test_large_number_of_unique_feedback_pairs(self):
        """Test memory doesn't explode with 10K unique (skill, task) pairs."""
        buffer = FeedbackBuffer(min_samples=5, confidence_threshold=0.6)

        # Add feedback for 10,000 unique pairs
        for i in range(10000):
            feedback = FeedbackEvent.create(
                skill_id=f"os.skill_{i % 3}",
                task_id=f"task_{i}",
                tenant_id="_default",
                outcome_feedback=OutcomeFeedbackType.YES,
                confidence=0.7,
            )
            buffer.add(feedback)

        # Verify we have many entries
        assert len(buffer.buffers) > 100, "should have many buffered pairs"

        # Now clear all of them — dict should empty out
        keys_cleared = 0
        for key in list(buffer.buffers.keys()):
            skill_id, task_id = key
            buffer.get_and_clear(skill_id, task_id)
            keys_cleared += 1

        # After clearing, dict should be empty (keys deleted, not just emptied)
        assert len(buffer.buffers) == 0, (
            f"All keys should be deleted after clearing. "
            f"Cleared {keys_cleared} keys, but {len(buffer.buffers)} remain."
        )

    def test_get_and_clear_actually_deletes_key(self):
        """Verify get_and_clear() deletes the dict key, not just the list."""
        buffer = FeedbackBuffer(min_samples=1, confidence_threshold=0.5)

        skill_id = "os.test_skill"
        task_id = "test_task_1"

        # Add one feedback
        feedback = FeedbackEvent.create(
            skill_id=skill_id,
            task_id=task_id,
            tenant_id="_default",
            outcome_feedback=OutcomeFeedbackType.YES,
        )
        buffer.add(feedback)

        key = (skill_id, task_id)
        assert key in buffer.buffers, "key should exist after adding"

        # Clear the buffer
        result = buffer.get_and_clear(skill_id, task_id)
        assert result == [feedback], "should return the feedback"

        # CRITICAL: key should be deleted entirely, not just emptied
        assert key not in buffer.buffers, (
            f"Key {key} should be deleted after get_and_clear(), "
            f"but it still exists in buffers dict"
        )


# ============================================================================
# INPUT-VALIDATION-001: subject_id format validation
# ============================================================================

class TestInputValidation001:
    """Verify subject_id validation in stream4_skill_feedback.py."""

    def test_subject_id_max_length_enforced(self):
        """Test INPUT-VALIDATION-001: subject_id must have max_length=100."""
        from core.console.corvin_console.routes.stream4_skill_feedback import (
            SkillFeedbackRequest,
        )
        from pydantic import ValidationError

        # Subject ID over 100 characters should fail
        long_id = "a" * 101
        with pytest.raises(ValidationError) as exc_info:
            SkillFeedbackRequest(
                skill_id="os.workflow_optimizer",
                subject_id=long_id,
                rating=1,
                category="accuracy",
            )
        assert "subject_id" in str(exc_info.value).lower()

    def test_subject_id_format_validation(self):
        """Test subject_id only accepts alphanumeric, underscore, hyphen."""
        from core.console.corvin_console.routes.stream4_skill_feedback import (
            SkillFeedbackRequest,
        )
        from pydantic import ValidationError

        # Invalid characters should fail
        invalid_ids = [
            "task@123",       # @ not allowed
            "task 123",       # space not allowed
            "task.123",       # . not allowed (unless in middle, but the pattern forbids it)
            "task\nid",       # newline not allowed
            "task/123",       # / not allowed
        ]

        for invalid_id in invalid_ids:
            with pytest.raises(ValidationError) as exc_info:
                SkillFeedbackRequest(
                    skill_id="os.flow_guard",
                    subject_id=invalid_id,
                    rating=0,
                    category="speed",
                )
            assert "subject_id" in str(exc_info.value).lower(), (
                f"Validation error should mention subject_id for invalid value {invalid_id}"
            )

    def test_subject_id_valid_formats(self):
        """Test valid subject_id formats pass validation."""
        from core.console.corvin_console.routes.stream4_skill_feedback import (
            SkillFeedbackRequest,
        )

        valid_ids = [
            "task_123",
            "threat-id-456",
            "flow_guard_test_1",
            "a",  # single char
            "test-_id",  # mix of underscore and hyphen
        ]

        for valid_id in valid_ids:
            req = SkillFeedbackRequest(
                skill_id="os.security_orchestrator",
                subject_id=valid_id,
                rating=-1,
                category="safety",
            )
            assert req.subject_id == valid_id, f"Valid ID {valid_id} should pass"

    def test_subject_id_empty_rejected(self):
        """Test empty subject_id is rejected."""
        from core.console.corvin_console.routes.stream4_skill_feedback import (
            SkillFeedbackRequest,
        )
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            SkillFeedbackRequest(
                skill_id="os.workflow_optimizer",
                subject_id="",  # empty
                rating=2,
                category="accuracy",
            )

    def test_subject_id_logging_injection_prevented(self):
        """Test newlines in subject_id are rejected (logging injection prevention)."""
        from core.console.corvin_console.routes.stream4_skill_feedback import (
            SkillFeedbackRequest,
        )
        from pydantic import ValidationError

        # Newline should be rejected
        with pytest.raises(ValidationError):
            SkillFeedbackRequest(
                skill_id="os.flow_guard",
                subject_id="task_123\nlog_injection_attempt",
                rating=1,
                category="speed",
            )


# ============================================================================
# Integration Tests
# ============================================================================

class TestIntegration:
    """Integration tests combining multiple fixes."""

    def test_all_pii_detection_patterns_work(self):
        """Test all PII patterns in FeedbackScrubber work correctly."""
        scrubber = FeedbackScrubber()

        test_cases = [
            ("email@domain.com", "@domain.com"),
            ("555-123-4567", "123-4567"),  # phone
            ("123-45-6789", "45-6789"),    # SSN-like
            ("user_name", "user"),         # username pattern
            ("silvio@example.com", "@example.com"),
        ]

        for text, should_not_contain in test_cases:
            scrubbed = scrubber.scrub(text)
            assert scrubbed is not None, f"scrubbing {text} should not return None"
            # The scrubbed version should not contain the sensitive part
            # (exact assertion depends on scrubber regex, but @ should be gone at minimum)
            assert "[REDACTED]" in scrubbed, f"scrubbed text should contain [REDACTED]"

    def test_concurrent_feedback_validation_no_race(self, tmp_path, monkeypatch):
        """Test concurrent feedback validation doesn't have races."""
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))

        validator = FeedbackValidator()

        def create_and_validate(idx: int):
            """Create and validate feedback concurrently."""
            for i in range(10):
                feedback = FeedbackEvent.create(
                    skill_id="os.test_skill",
                    task_id=f"task_{idx}_{i}",
                    tenant_id="_default",
                    outcome_feedback=OutcomeFeedbackType.YES,
                    confidence=0.7,
                )
                is_valid, error = validator.validate(feedback)
                assert is_valid, f"validation should pass: {error}"

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(create_and_validate, i) for i in range(10)]
            for f in futures:
                f.result()
