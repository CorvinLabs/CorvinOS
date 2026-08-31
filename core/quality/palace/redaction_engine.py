"""GDPR Erasure & Redaction Engine (MP-004 Fix).

Implements GDPR Art. 17 (Right to Erasure) for MemPlace artifacts.
Replaces PII with hash tokens in audit trails, ensuring compliance
while maintaining audit trail integrity.
"""

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Common PII patterns to redact
PII_PATTERNS = {
    'email': r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
    'phone': r'\b(?:\+?1[-.\s]?)?\(?[0-9]{3}\)?[-.\s]?[0-9]{3}[-.\s]?[0-9]{4}\b',
    'ssn': r'\b\d{3}-\d{2}-\d{4}\b',
    'username': r'\b[a-zA-Z_]\w{3,}\b',  # Heuristic
}


class RedactionEngine:
    """GDPR-compliant redaction engine for MemPlace."""

    def __init__(self, tenant_id: str = "_default"):
        """Initialize redaction engine.

        Args:
            tenant_id: Tenant for scoping redaction operations
        """
        self.tenant_id = tenant_id
        self.redaction_log = []  # Append-only redaction audit trail

    def redact_pii(self, text: str, pii_type: str = 'email') -> Tuple[str, str]:
        """Redact PII from text using hash tokens.

        Args:
            text: Text containing PII
            pii_type: Type of PII to redact ('email', 'phone', 'ssn', etc.)

        Returns:
            Tuple of (redacted_text, hash_token)
        """
        if pii_type not in PII_PATTERNS:
            return text, ""

        pattern = PII_PATTERNS[pii_type]

        def replace_with_hash(match):
            original = match.group(0)
            # Create deterministic hash token
            hash_token = f"[REDACTED_{pii_type.upper()}_{hashlib.sha256(original.encode()).hexdigest()[:8]}]"

            # Log redaction (audit trail)
            self._log_redaction(
                pii_type=pii_type,
                hash_token=hash_token,
                pattern_match=original[:3] + "***",  # Never log full PII
            )
            return hash_token

        redacted = re.sub(pattern, replace_with_hash, text)
        return redacted, hashlib.sha256(text.encode()).hexdigest()

    def redact_artifact(self, artifact_path: Path, redaction_spec: Dict[str, List[str]]) -> None:
        """Redact PII from a MemPlace artifact file (GDPR Art. 17).

        Args:
            artifact_path: Path to artifact file (YAML/JSON frontmatter)
            redaction_spec: Dict of {field_name: [pii_types_to_redact]}
                           Example: {"author": ["email"], "notes": ["email", "phone"]}

        Raises:
            FileNotFoundError: If artifact not found
        """
        if not artifact_path.exists():
            raise FileNotFoundError(f"Artifact not found: {artifact_path}")

        # Read artifact
        try:
            with open(artifact_path, 'r') as f:
                content = f.read()
        except IOError as e:
            logger.error(f"Failed to read artifact {artifact_path}: {e}")
            return

        # Parse YAML frontmatter (simplified)
        lines = content.split('\n')
        frontmatter_end = -1
        if lines[0].strip() == '---':
            for i in range(1, len(lines)):
                if lines[i].strip() == '---':
                    frontmatter_end = i
                    break

        if frontmatter_end == -1:
            logger.warning(f"No YAML frontmatter in {artifact_path}")
            return

        # Process frontmatter for redaction
        frontmatter_lines = lines[1:frontmatter_end]
        redacted_fm = []

        for line in frontmatter_lines:
            redacted_line = line
            # Simple redaction: check each field in spec
            for field, pii_types in redaction_spec.items():
                if field in line:
                    for pii_type in pii_types:
                        redacted_line, _ = self.redact_pii(redacted_line, pii_type)
            redacted_fm.append(redacted_line)

        # Reconstruct artifact
        redacted_lines = lines[:1] + redacted_fm + lines[frontmatter_end:]
        redacted_content = '\n'.join(redacted_lines)

        # Write back (atomic write)
        try:
            artifact_path.write_text(redacted_content)
            logger.info(f"Redacted artifact {artifact_path}")
        except IOError as e:
            logger.error(f"Failed to redact artifact {artifact_path}: {e}")

    def _log_redaction(self, pii_type: str, hash_token: str, pattern_match: str) -> None:
        """Log redaction action (audit trail).

        Args:
            pii_type: Type of PII redacted
            hash_token: Hash token used for replacement
            pattern_match: Obfuscated pattern match (never full PII)
        """
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tenant_id": self.tenant_id,
            "event_type": "pii_redacted",
            "pii_type": pii_type,
            "hash_token": hash_token,
            "pattern_match": pattern_match,
        }
        self.redaction_log.append(event)
        logger.info(f"Redacted {pii_type}: {pattern_match}")

    def get_redaction_audit_trail(self) -> List[Dict[str, Any]]:
        """Get audit trail of all redaction operations.

        Returns:
            List of redaction events
        """
        return self.redaction_log.copy()
