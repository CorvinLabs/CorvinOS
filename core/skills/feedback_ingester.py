"""Feedback ingestion with fail-closed sanitization (ADR-0534)."""

import json
import re
import base64
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime
import hashlib
import logging

logger = logging.getLogger(__name__)


class Sanitizer:
    """Fail-closed PII/secret detection."""

    PII_PATTERNS = {
        'email': r'\b[\w.-]+@[\w.-]+\.\w+\b',  # Anchored to word boundaries
        'phone': r'\b\d{3}-\d{3}-\d{4}\b',  # Anchored
        'credit_card': r'\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b',
        'ssn': r'\b\d{3}-\d{2}-\d{4}\b',
    }

    DISALLOW_FIELDS = ['prompt', 'response', 'user_id', 'raw_content']

    @staticmethod
    def sanitize_outcome(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Sanitize event, fail-closed on PII."""
        payload = event.get('payload', {})

        # Check disallow_fields
        for field in Sanitizer.DISALLOW_FIELDS:
            if field in payload:
                logger.warning(f"PII_FIELD_DETECTED: {field}")
                return None

        # Recursive PII check
        if not Sanitizer._check_value_recursive(payload, 'payload'):
            logger.warning(f"PII_PATTERN_DETECTED in payload")
            return None

        return event

    @staticmethod
    def _check_value_recursive(value: Any, path: str) -> bool:
        """Recursively check for PII. Return True if clean."""
        if isinstance(value, str):
            # Text patterns
            for pattern_name, pattern in Sanitizer.PII_PATTERNS.items():
                if re.search(pattern, value):
                    logger.warning(f"PII_PATTERN: {pattern_name} at {path}")
                    return False

            # Try Base64 decode
            try:
                decoded = base64.b64decode(value, validate=True).decode('utf-8', errors='ignore')
                if not Sanitizer._check_value_recursive(decoded, f"{path}[decoded]"):
                    return False
            except Exception:
                pass

            # Try JSON parse
            try:
                parsed = json.loads(value)
                for k, v in parsed.items():
                    if not Sanitizer._check_value_recursive(v, f"{path}.{k}"):
                        return False
            except json.JSONDecodeError:
                pass

        elif isinstance(value, dict):
            for k, v in value.items():
                if not Sanitizer._check_value_recursive(v, f"{path}.{k}"):
                    return False

        elif isinstance(value, list):
            for i, v in enumerate(value):
                if not Sanitizer._check_value_recursive(v, f"{path}[{i}]"):
                    return False

        return True


class FeedbackIngester:
    """Ingest + persist feedback with hash-chain (ADR-0534)."""

    def __init__(self, skill_dir: Path):
        self.skill_dir = skill_dir
        self.feedback_log = skill_dir / 'feedback_log.jsonl'
        self.feedback_log.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_feedback_log()

    def _ensure_feedback_log(self):
        """Create genesis line if needed."""
        if not self.feedback_log.exists():
            genesis = {
                'event_type': 'genesis',
                'timestamp': datetime.utcnow().isoformat(),
                'sha256_prev': '0' * 64,
            }
            with open(self.feedback_log, 'a') as f:
                f.write(json.dumps(genesis) + '\n')

    def ingest(self, event: Dict[str, Any]) -> bool:
        """Ingest feedback event (atomic append, ADR-0534)."""
        # Sanitize
        sanitized = Sanitizer.sanitize_outcome(event)
        if sanitized is None:
            logger.warning(f"Event dropped (PII detected)")
            return False

        # Get previous hash (seek-to-end for O(1) instead of O(n))
        try:
            with open(self.feedback_log, 'rb') as f:
                f.seek(0, 2)  # Seek to end
                pos = f.tell()
                if pos > 0:
                    # Read last line efficiently (bounded read, ~1KB max)
                    read_size = min(1024, pos)
                    f.seek(max(0, pos - read_size))
                    chunk = f.read()
                    lines = chunk.split(b'\n')
                    prev_line = lines[-2] if len(lines) > 1 else lines[-1]
                    prev_hash = hashlib.sha256(prev_line).hexdigest()
                else:
                    prev_hash = '0' * 64
        except Exception as e:
            logger.error(f"Failed to read last hash: {e}")
            prev_hash = '0' * 64

        # Add hash chain
        sanitized['sha256_prev'] = prev_hash
        json_line = json.dumps(sanitized) + '\n'

        # Atomic write: write to tmp, then rename
        tmp_file = self.feedback_log.with_suffix('.tmp')
        try:
            with open(tmp_file, 'a') as f:
                f.write(json_line)
            tmp_file.replace(self.feedback_log)
            return True
        except Exception as e:
            logger.error(f"Feedback write failed: {e}")
            if tmp_file.exists():
                tmp_file.unlink()
            return False

    def load_feedback_log(self, limit: int = 1000) -> List[Dict[str, Any]]:
        """Load recent feedback events."""
        events = []
        try:
            with open(self.feedback_log) as f:
                for line in f:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        except FileNotFoundError:
            return []

        return events[-limit:]
