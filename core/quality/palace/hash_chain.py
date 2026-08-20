"""Hash-Chained Artifact Integrity (MP-008 Fix).

Extends MemPlace Ideas/Concepts with cryptographic hash-chaining to ensure
tamper-detection and audit trail integrity (GDPR Art. 30, 32).

Similar to the hash-chaining in audit trails but scoped to the knowledge graph.
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


class HashChainEntry:
    """Single entry in a hash chain."""

    def __init__(
        self,
        artifact_id: str,
        content_hash: str,
        previous_hash: str,
        timestamp: str,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """Initialize hash chain entry.

        Args:
            artifact_id: Unique identifier for artifact (e.g., "CONCEPT-0001")
            content_hash: SHA256 hash of artifact content
            previous_hash: Hash of previous entry (or genesis hash for first entry)
            timestamp: ISO timestamp
            metadata: Additional metadata (author, status, etc.)
        """
        self.artifact_id = artifact_id
        self.content_hash = content_hash
        self.previous_hash = previous_hash
        self.timestamp = timestamp
        self.metadata = metadata or {}
        self.chain_hash = self._compute_chain_hash()

    def _compute_chain_hash(self) -> str:
        """Compute hash of this entry (chain link).

        Formula: H(previous_hash || content_hash || timestamp)
        """
        data = f"{self.previous_hash}||{self.content_hash}||{self.timestamp}"
        return hashlib.sha256(data.encode()).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict."""
        return {
            "artifact_id": self.artifact_id,
            "content_hash": self.content_hash,
            "previous_hash": self.previous_hash,
            "chain_hash": self.chain_hash,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }

    def to_json(self) -> str:
        """Serialize to JSON."""
        return json.dumps(self.to_dict(), sort_keys=True)


class HashChain:
    """Hash chain for artifact integrity verification."""

    GENESIS_HASH = "0" * 64  # Initial hash for first entry

    def __init__(self, tenant_id: str = "_default", chain_file: Optional[Path] = None):
        """Initialize hash chain.

        Args:
            tenant_id: Tenant identifier
            chain_file: Path to persist chain (optional, for recovery)
        """
        self.tenant_id = tenant_id
        self.chain_file = chain_file
        self.entries: List[HashChainEntry] = []
        self.last_hash = self.GENESIS_HASH
        self._load_chain()

    def append(
        self,
        artifact_id: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Append artifact to chain.

        Args:
            artifact_id: Artifact ID (e.g., "CONCEPT-0001")
            content: Artifact content
            metadata: Additional metadata

        Returns:
            Chain hash of new entry
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        content_hash = hashlib.sha256(content.encode()).hexdigest()

        entry = HashChainEntry(
            artifact_id=artifact_id,
            content_hash=content_hash,
            previous_hash=self.last_hash,
            timestamp=timestamp,
            metadata=metadata,
        )

        self.entries.append(entry)
        self.last_hash = entry.chain_hash
        self._persist()

        logger.info(f"Appended {artifact_id} to hash chain: {entry.chain_hash[:8]}...")
        return entry.chain_hash

    def verify_integrity(self) -> bool:
        """Verify chain integrity (all hashes valid, no tampering).

        Returns:
            True if chain is valid, False if any entry was tampered with
        """
        current_hash = self.GENESIS_HASH

        for entry in self.entries:
            # Recompute what the chain hash should be
            expected_data = f"{entry.previous_hash}||{entry.content_hash}||{entry.timestamp}"
            expected_hash = hashlib.sha256(expected_data.encode()).hexdigest()

            # Verify linkage
            if entry.previous_hash != current_hash:
                logger.error(
                    f"Chain integrity check failed for {entry.artifact_id}: "
                    f"expected previous_hash={current_hash}, got {entry.previous_hash}"
                )
                return False

            # Verify chain hash
            if entry.chain_hash != expected_hash:
                logger.error(
                    f"Chain integrity check failed for {entry.artifact_id}: "
                    f"chain_hash mismatch (tampered)"
                )
                return False

            current_hash = entry.chain_hash

        logger.info("Hash chain integrity verified")
        return True

    def get_entry(self, artifact_id: str) -> Optional[HashChainEntry]:
        """Retrieve entry by artifact ID.

        Args:
            artifact_id: Artifact ID to find

        Returns:
            HashChainEntry if found, None otherwise
        """
        for entry in self.entries:
            if entry.artifact_id == artifact_id:
                return entry
        return None

    def _persist(self) -> None:
        """Persist chain to disk (if chain_file set)."""
        if not self.chain_file:
            return

        try:
            self.chain_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.chain_file, 'w') as f:
                for entry in self.entries:
                    f.write(entry.to_json() + '\n')
        except IOError as e:
            logger.error(f"Failed to persist hash chain: {e}")

    def _load_chain(self) -> None:
        """Load chain from disk (if exists)."""
        if not self.chain_file or not self.chain_file.exists():
            return

        try:
            with open(self.chain_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    entry = HashChainEntry(
                        artifact_id=data['artifact_id'],
                        content_hash=data['content_hash'],
                        previous_hash=data['previous_hash'],
                        timestamp=data['timestamp'],
                        metadata=data.get('metadata'),
                    )
                    self.entries.append(entry)
                    self.last_hash = entry.chain_hash

            logger.info(f"Loaded hash chain with {len(self.entries)} entries")
        except (IOError, json.JSONDecodeError) as e:
            logger.error(f"Failed to load hash chain: {e}")
