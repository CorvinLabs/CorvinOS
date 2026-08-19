"""Conflict Detection & Resolution for Concurrent Edits.

Detects competing changes and applies merge strategies.
"""

import json
from datetime import datetime
from typing import Dict, List, Any, Tuple, Optional
from enum import Enum
import logging
from pathlib import Path
import hashlib

logger = logging.getLogger(__name__)


class MergeStrategy(str, Enum):
    """Conflict resolution strategies."""
    LAST_WRITE_WINS = "last_write_wins"
    VECTOR_CLOCK = "vector_clock"
    CRDT = "crdt"  # Conflict-free replicated data type
    CUSTOM = "custom"  # User-defined merge logic


class ConflictType(str, Enum):
    """Types of conflicts."""
    CONCURRENT_UPDATE = "concurrent_update"
    DELETE_UPDATE = "delete_update"
    INCOMPATIBLE_MERGE = "incompatible_merge"
    FIELD_OVERLAP = "field_overlap"


class SkillVersion:
    """Versioned skill with metadata."""

    def __init__(self, skill_id: str, content: str, version: int = 1,
                 author: str = "", timestamp: str = ""):
        self.skill_id = skill_id
        self.content = content
        self.version = version
        self.author = author
        self.timestamp = timestamp or datetime.utcnow().isoformat() + "Z"
        self.hash = self._compute_hash()

    def _compute_hash(self) -> str:
        """Compute content hash."""
        return hashlib.sha256(self.content.encode()).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict."""
        return {
            "skill_id": self.skill_id,
            "version": self.version,
            "hash": self.hash,
            "author": self.author,
            "timestamp": self.timestamp,
            "size": len(self.content)
        }


class ConflictDetector:
    """Detect conflicting changes."""

    @staticmethod
    def detect_conflict(local: SkillVersion, remote: SkillVersion,
                        base: Optional[SkillVersion] = None) -> Tuple[bool, Optional[ConflictType], str]:
        """
        Detect conflict between local and remote versions.
        Returns: (has_conflict, conflict_type, reason)
        """

        # Same content - no conflict
        if local.hash == remote.hash:
            return False, None, "identical"

        # Both deleted - no conflict
        if local.content == "" and remote.content == "":
            return False, None, "both_deleted"

        # One deleted, one updated - DELETE_UPDATE conflict
        if (local.content == "" and remote.content != "") or \
           (local.content != "" and remote.content == ""):
            return True, ConflictType.DELETE_UPDATE, "one_side_deleted"

        # Different content - CONCURRENT_UPDATE conflict
        if base and local.hash != base.hash and remote.hash != base.hash:
            return True, ConflictType.CONCURRENT_UPDATE, "both_sides_updated_differently"

        # Default: no conflict
        return False, None, "no_conflict"

    @staticmethod
    def detect_field_overlap(local: Dict[str, Any], remote: Dict[str, Any],
                             fields: List[str]) -> List[str]:
        """Detect overlapping field changes."""
        overlapping = []
        for field in fields:
            if local.get(field) != remote.get(field):
                overlapping.append(field)
        return overlapping


class ConflictResolver:
    """Resolve conflicts using various strategies."""

    def __init__(self, strategy: MergeStrategy = MergeStrategy.LAST_WRITE_WINS,
                 tenant_id: str = "_default"):
        self.strategy = strategy
        self.tenant_id = tenant_id
        self.tenant_path = Path.home() / '.corvin' / 'tenants' / tenant_id
        self.conflict_log = self.tenant_path / 'conflicts.jsonl'

    def resolve(self, local: SkillVersion, remote: SkillVersion,
                base: Optional[SkillVersion] = None) -> Tuple[SkillVersion, bool]:
        """
        Resolve conflict and return merged version.
        Returns: (merged_version, escalated_to_user)
        """

        # Detect conflict
        has_conflict, conflict_type, reason = ConflictDetector.detect_conflict(local, remote, base)

        if not has_conflict:
            # No conflict - accept remote (latest)
            merged = remote
            escalate = False
        elif self.strategy == MergeStrategy.LAST_WRITE_WINS:
            # Last write wins
            merged = remote if remote.timestamp > local.timestamp else local
            escalate = False
        elif self.strategy == MergeStrategy.VECTOR_CLOCK:
            # Vector clock based resolution (simplified)
            merged = self._resolve_vector_clock(local, remote)
            escalate = False
        elif self.strategy == MergeStrategy.CRDT:
            # CRDT merge (operational transform)
            merged = self._resolve_crdt(local, remote)
            escalate = False
        else:
            # Unknown strategy - escalate to user
            merged = remote
            escalate = True

        # Log conflict
        self._log_conflict({
            "skill_id": local.skill_id,
            "conflict_type": conflict_type.value if conflict_type else None,
            "reason": reason,
            "local_version": local.version,
            "remote_version": remote.version,
            "strategy": self.strategy.value,
            "escalated": escalate,
            "merged_hash": merged.hash
        })

        return merged, escalate

    def _resolve_vector_clock(self, local: SkillVersion, remote: SkillVersion) -> SkillVersion:
        """Simple vector clock: newer version wins."""
        return remote if remote.version > local.version else local

    def _resolve_crdt(self, local: SkillVersion, remote: SkillVersion) -> SkillVersion:
        """CRDT merge: combine operations (simplified - just take newer)."""
        # Full CRDT would track individual operations and merge them
        return remote if remote.timestamp > local.timestamp else local

    def _log_conflict(self, details: Dict[str, Any]):
        """Log conflict to audit trail."""
        try:
            event = {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "tenant_id": self.tenant_id,
                "event_type": "conflict_detected",
                **details
            }
            with open(self.conflict_log, 'a') as f:
                f.write(json.dumps(event) + '\n')
            logger.info(f"Conflict logged: {details['skill_id']}")
        except Exception as e:
            logger.error(f"Failed to log conflict: {e}")


class MergeOrchestrator:
    """Coordinate conflict resolution across multiple skills."""

    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        self.resolver = ConflictResolver(tenant_id=tenant_id)

    def merge_skills(self, local_skills: Dict[str, str],
                    remote_skills: Dict[str, str]) -> Dict[str, Any]:
        """Merge sets of skills from two sources."""

        results = {
            "merged": {},
            "conflicts": [],
            "deleted": [],
            "added": [],
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }

        # Process each skill
        all_skills = set(local_skills.keys()) | set(remote_skills.keys())

        for skill_id in all_skills:
            local_content = local_skills.get(skill_id, "")
            remote_content = remote_skills.get(skill_id, "")

            if skill_id not in local_skills:
                # Added in remote
                results["added"].append(skill_id)
                results["merged"][skill_id] = remote_content
            elif skill_id not in remote_skills:
                # Deleted in remote
                results["deleted"].append(skill_id)
            else:
                # Both exist - check for conflict
                local_v = SkillVersion(skill_id, local_content, author="local")
                remote_v = SkillVersion(skill_id, remote_content, author="remote")

                has_conflict, conflict_type, _ = ConflictDetector.detect_conflict(local_v, remote_v)

                if has_conflict:
                    results["conflicts"].append({
                        "skill_id": skill_id,
                        "conflict_type": conflict_type.value if conflict_type else None,
                        "requires_review": True
                    })
                    # For now, take remote
                    results["merged"][skill_id] = remote_content
                else:
                    # No conflict
                    results["merged"][skill_id] = remote_content

        return results
