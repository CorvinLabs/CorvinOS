"""Feedback ingestion with fail-closed sanitization (ADR-0534)."""

import json
import re
import base64
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime
import hashlib
import logging
import os
import threading

try:  # POSIX: cross-process exclusive lock on the log while appending
    import fcntl as _fcntl
except ImportError:  # Windows: in-process lock only
    _fcntl = None

logger = logging.getLogger(__name__)


def _lock_file(f) -> None:
    if _fcntl is not None:
        _fcntl.flock(f.fileno(), _fcntl.LOCK_EX)


def _unlock_file(f) -> None:
    if _fcntl is not None:
        _fcntl.flock(f.fileno(), _fcntl.LOCK_UN)


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

    # Recursion depth limit to prevent DoS via deeply-nested structures
    MAX_DEPTH = 50

    @staticmethod
    def _check_value_recursive(value: Any, path: str, depth: int = 0) -> bool:
        """Recursively check for PII. Return True if clean."""
        # Fail-closed: reject if recursion depth exceeded (possible DoS or malicious structure)
        if depth > Sanitizer.MAX_DEPTH:
            logger.warning(f"PII_RECURSION_DEPTH_EXCEEDED at {path}, treating as PII")
            return False

        if isinstance(value, str):
            # Text patterns
            for pattern_name, pattern in Sanitizer.PII_PATTERNS.items():
                if re.search(pattern, value):
                    logger.warning(f"PII_PATTERN: {pattern_name} at {path}")
                    return False

            # Try Base64 decode
            try:
                decoded = base64.b64decode(value, validate=True).decode('utf-8', errors='ignore')
                if not Sanitizer._check_value_recursive(decoded, f"{path}[decoded]", depth + 1):
                    return False
            except Exception:
                pass

            # Try JSON parse
            try:
                parsed = json.loads(value)
                for k, v in parsed.items():
                    if not Sanitizer._check_value_recursive(v, f"{path}.{k}", depth + 1):
                        return False
            except json.JSONDecodeError:
                pass

        elif isinstance(value, dict):
            for k, v in value.items():
                if not Sanitizer._check_value_recursive(v, f"{path}.{k}", depth + 1):
                    return False

        elif isinstance(value, list):
            for i, v in enumerate(value):
                if not Sanitizer._check_value_recursive(v, f"{path}[{i}]", depth + 1):
                    return False

        return True


class FeedbackIngester:
    """Ingest + persist feedback with hash-chain (ADR-0534)."""

    def __init__(self, skill_dir: Path):
        self.skill_dir = skill_dir
        self.feedback_log = skill_dir / 'feedback_log.jsonl'
        # Hash cache pattern (ADR-0534 audit integrity)
        # Keeps previous event's hash for chain verification. Synced atomically with feedback_log
        # via tmp→rename pattern. On recovery, assumes cache is valid; no cross-file consistency check.
        # If cache becomes stale (crash between writes), next event assumes genesis hash '0'*64,
        # breaking chain until manual verification. This is fail-closed: wrong hash chain > lost events.
        self.last_hash_file = skill_dir / '.last_hash'
        self._write_lock = threading.Lock()
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
        """Ingest one feedback event (true append, hash-chained, ADR-0534).

        Ordering contract (fail-closed, never lossy):
          1. sanitize (drop on PII);
          2. append the line to ``feedback_log.jsonl`` under an exclusive file
             lock, ``flush`` + ``fsync`` so the row is durable before we move on;
          3. only then refresh the ``.last_hash`` cache (tmp -> rename).

        A crash between 2 and 3 leaves the cache one event behind; the next
        event then chains to the previous-but-one hash and ``verify`` reports
        the break. That is the intended behaviour: a detectable chain break is
        preferred over a silently dropped event. Rewriting the whole log via
        ``tmp.replace(log)`` (the previous implementation) truncated the log to
        the newest line on every call and must never come back.
        """
        # Sanitize
        sanitized = Sanitizer.sanitize_outcome(event)
        if sanitized is None:
            logger.warning("Event dropped (PII detected)")
            return False

        # Previous hash from the O(1) cache
        try:
            if self.last_hash_file.exists():
                prev_hash = self.last_hash_file.read_text().strip() or '0' * 64
            else:
                prev_hash = '0' * 64
        except Exception as e:
            logger.error(f"Failed to read last hash cache: {e}")
            prev_hash = '0' * 64

        sanitized['sha256_prev'] = prev_hash
        json_line = json.dumps(sanitized) + '\n'

        try:
            with self._write_lock:
                with open(self.feedback_log, 'a', encoding='utf-8') as f:
                    _lock_file(f)
                    try:
                        f.write(json_line)
                        f.flush()
                        os.fsync(f.fileno())
                    finally:
                        _unlock_file(f)

                # Refresh the hash cache atomically (tmp -> rename)
                new_hash = hashlib.sha256(json_line.encode()).hexdigest()
                tmp_hash_file = self.last_hash_file.with_suffix('.tmp')
                tmp_hash_file.write_text(new_hash)
                tmp_hash_file.replace(self.last_hash_file)
            return True
        except Exception as e:
            logger.error(f"Feedback write failed: {e}")
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
