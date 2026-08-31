"""
Unit Tests for Queue Corruption Detection — ADR-0298

Tests for detecting corrupted messages via checksums.
"""

import pytest

from core.queue import QueueCorruptionDetector


class TestQueueCorruptionDetector:
    """Test queue corruption detection."""

    def test_compute_checksum_deterministic(self):
        """Checksum is deterministic."""
        message = {"id": "msg1", "type": "chat", "text": "hello"}
        checksum1 = QueueCorruptionDetector.compute_checksum(message)
        checksum2 = QueueCorruptionDetector.compute_checksum(message)
        assert checksum1 == checksum2

    def test_compute_checksum_field_order_independent(self):
        """Checksum ignores field order."""
        message1 = {"id": "msg1", "type": "chat", "text": "hello"}
        message2 = {"text": "hello", "id": "msg1", "type": "chat"}
        checksum1 = QueueCorruptionDetector.compute_checksum(message1)
        checksum2 = QueueCorruptionDetector.compute_checksum(message2)
        assert checksum1 == checksum2

    def test_compute_checksum_excludes_checksum_field(self):
        """Checksum computation excludes checksum field."""
        message = {"id": "msg1", "text": "hello"}
        checksum = QueueCorruptionDetector.compute_checksum(message)

        message_with_checksum = message.copy()
        message_with_checksum["checksum"] = checksum
        checksum_again = QueueCorruptionDetector.compute_checksum(message_with_checksum)

        assert checksum == checksum_again

    def test_add_checksum_includes_field(self):
        """add_checksum includes checksum field."""
        message = {"id": "msg1", "text": "hello"}
        with_checksum = QueueCorruptionDetector.add_checksum(message)

        assert "checksum" in with_checksum
        assert with_checksum["id"] == "msg1"
        assert with_checksum["text"] == "hello"

    def test_verify_checksum_valid(self):
        """Valid checksum passes verification."""
        message = {"id": "msg1", "text": "hello"}
        message_with_checksum = QueueCorruptionDetector.add_checksum(message)

        is_valid, error = QueueCorruptionDetector.verify_checksum(message_with_checksum)
        assert is_valid is True
        assert error is None

    def test_verify_checksum_missing(self):
        """Missing checksum fails verification."""
        message = {"id": "msg1", "text": "hello"}

        is_valid, error = QueueCorruptionDetector.verify_checksum(message)
        assert is_valid is False
        assert "Missing" in error

    def test_verify_checksum_corrupted(self):
        """Corrupted checksum fails verification."""
        message = {"id": "msg1", "text": "hello"}
        message_with_checksum = QueueCorruptionDetector.add_checksum(message)

        # Corrupt the message
        message_with_checksum["text"] = "goodbye"

        is_valid, error = QueueCorruptionDetector.verify_checksum(message_with_checksum)
        assert is_valid is False
        assert error is not None

    def test_verify_checksum_returns_expected(self):
        """Verification returns expected checksum."""
        message = {"id": "msg1", "text": "hello"}
        message_with_checksum = QueueCorruptionDetector.add_checksum(message)

        # Corrupt and verify
        message_with_checksum["text"] = "goodbye"
        is_valid, expected = QueueCorruptionDetector.verify_checksum(message_with_checksum)

        assert is_valid is False
        # Expected should be the correct checksum for the corrupted message
        assert expected == QueueCorruptionDetector.compute_checksum(message_with_checksum)

    def test_is_corrupted_true(self):
        """is_corrupted returns True for corrupted message."""
        message = {"id": "msg1", "text": "hello"}
        message_with_checksum = QueueCorruptionDetector.add_checksum(message)

        # Corrupt
        message_with_checksum["text"] = "goodbye"

        assert QueueCorruptionDetector.is_corrupted(message_with_checksum) is True

    def test_is_corrupted_false(self):
        """is_corrupted returns False for valid message."""
        message = {"id": "msg1", "text": "hello"}
        message_with_checksum = QueueCorruptionDetector.add_checksum(message)

        assert QueueCorruptionDetector.is_corrupted(message_with_checksum) is False

    def test_is_corrupted_missing_checksum(self):
        """is_corrupted returns True for missing checksum."""
        message = {"id": "msg1", "text": "hello"}

        assert QueueCorruptionDetector.is_corrupted(message) is True

    def test_checksum_different_content_different_hash(self):
        """Different content produces different checksum."""
        message1 = {"id": "msg1", "text": "hello"}
        message2 = {"id": "msg1", "text": "goodbye"}

        checksum1 = QueueCorruptionDetector.compute_checksum(message1)
        checksum2 = QueueCorruptionDetector.compute_checksum(message2)

        assert checksum1 != checksum2

    def test_checksum_nested_dict(self):
        """Checksum works with nested dicts."""
        message = {"id": "msg1", "data": {"nested": "value"}}
        message_with_checksum = QueueCorruptionDetector.add_checksum(message)

        is_valid, _ = QueueCorruptionDetector.verify_checksum(message_with_checksum)
        assert is_valid is True

    def test_checksum_nested_dict_corruption(self):
        """Nested dict corruption detected."""
        message = {"id": "msg1", "data": {"nested": "value"}}
        message_with_checksum = QueueCorruptionDetector.add_checksum(message)

        # Corrupt nested value
        message_with_checksum["data"]["nested"] = "corrupted"

        assert QueueCorruptionDetector.is_corrupted(message_with_checksum) is True

    def test_checksum_list_values(self):
        """Checksum works with list values."""
        message = {"id": "msg1", "items": ["a", "b", "c"]}
        message_with_checksum = QueueCorruptionDetector.add_checksum(message)

        is_valid, _ = QueueCorruptionDetector.verify_checksum(message_with_checksum)
        assert is_valid is True

    def test_checksum_list_corruption(self):
        """List corruption detected."""
        message = {"id": "msg1", "items": ["a", "b", "c"]}
        message_with_checksum = QueueCorruptionDetector.add_checksum(message)

        # Corrupt list
        message_with_checksum["items"].append("d")

        assert QueueCorruptionDetector.is_corrupted(message_with_checksum) is True

    def test_workflow_add_verify(self):
        """Typical workflow: add checksum, verify, detect corruption."""
        # 1. Add checksum when queuing
        original = {"id": "msg1", "event": "test", "data": "content"}
        queued = QueueCorruptionDetector.add_checksum(original)

        # 2. Verify when dequeuing (valid case)
        is_valid, _ = QueueCorruptionDetector.verify_checksum(queued)
        assert is_valid is True

        # 3. Detect if corrupted
        queued["data"] = "corrupted"
        assert QueueCorruptionDetector.is_corrupted(queued) is True

    def test_checksum_empty_message(self):
        """Checksum works for empty message."""
        message = {}
        message_with_checksum = QueueCorruptionDetector.add_checksum(message)

        is_valid, _ = QueueCorruptionDetector.verify_checksum(message_with_checksum)
        assert is_valid is True

    def test_checksum_special_characters(self):
        """Checksum works with special characters."""
        message = {"id": "msg1", "text": "hello\nworld\t!@#$%"}
        message_with_checksum = QueueCorruptionDetector.add_checksum(message)

        is_valid, _ = QueueCorruptionDetector.verify_checksum(message_with_checksum)
        assert is_valid is True

    def test_checksum_unicode(self):
        """Checksum works with unicode."""
        message = {"id": "msg1", "text": "你好世界 🌍"}
        message_with_checksum = QueueCorruptionDetector.add_checksum(message)

        is_valid, _ = QueueCorruptionDetector.verify_checksum(message_with_checksum)
        assert is_valid is True
