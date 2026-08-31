"""
Session Reset Fixes — Korrekte Implementation der drei Critical Fehlbehandlungen.

ADR-0368: Session-Reset-Logik muss:
1. Token-Budget vollständig ersetzen (nicht addieren)
2. Neue Session-ID + Timestamp generieren
3. Memory-Index beim Agent-Start laden

Constraints:
- Altes Budget muss verworfen werden (total_tokens != old_value + new_value)
- Session-ID muss UUID sein (eindeutige Identifikation)
- Memory muss vor User-Input-Verarbeitung verfügbar sein
"""

import json
import logging
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TokenBudget:
    """Represents token usage budget for a single session.

    IMPORTANT: total_tokens is the FRESH allocation per session.
    When resetting, replace completely — never add to old values.
    """
    total_tokens: int = 15_000_000  # Fresh allocation, not incremental
    spent_tokens: int = 0
    created_at: str = None
    session_id: str = None

    def __post_init__(self):
        if self.created_at is None:
            object.__setattr__(self, 'created_at', datetime.utcnow().isoformat())
        if self.session_id is None:
            object.__setattr__(self, 'session_id', str(uuid4()))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def fresh_allocation(cls, total_tokens: int = 15_000_000) -> "TokenBudget":
        """Create a NEW fresh budget (not inherited from previous session).

        This is the ONLY correct way to reset token budget.
        """
        return cls(
            total_tokens=total_tokens,
            spent_tokens=0,
            created_at=datetime.utcnow().isoformat(),
            session_id=str(uuid4())  # NEW session-id
        )


class SessionResetManager:
    """Manages correct session reset: token budget + session-id + memory."""

    def __init__(self, corvin_home: Optional[Path] = None):
        self.corvin_home = corvin_home or Path.home() / ".corvin"
        self.memory_dir = Path(__file__).parent.parent.parent / ".claude" / "projects" / "-home-shumway-projects-CorvinOS" / "memory"

    def reset_token_budget(self, old_budget: Optional[Dict[str, Any]] = None) -> TokenBudget:
        """
        Fix #1: Replace token budget completely.

        Args:
            old_budget: The old budget (IGNORED - only logged for audit)

        Returns:
            Fresh TokenBudget with total_tokens=15_000_000, spent_tokens=0

        Contract: old_budget is never incorporated. Complete replacement only.
        """
        if old_budget:
            logger.info(
                f"reset_token_budget: discarding old budget "
                f"(total={old_budget.get('total_tokens')}, spent={old_budget.get('spent_tokens')})"
            )

        new_budget = TokenBudget.fresh_allocation()
        logger.info(
            f"reset_token_budget: fresh allocation "
            f"(total={new_budget.total_tokens}, spent={new_budget.spent_tokens}, "
            f"session_id={new_budget.session_id})"
        )
        return new_budget

    def generate_new_session_id(self, old_session_id: Optional[str] = None) -> Dict[str, str]:
        """
        Fix #2: Generate new session-id + update timestamp.

        Args:
            old_session_id: The old session-id (IGNORED - only logged for audit)

        Returns:
            Dict with new session_id (UUID) and timestamp

        Contract: new UUID always generated, timestamp always current.
        """
        new_session_id = str(uuid4())
        new_timestamp = datetime.utcnow().isoformat()

        if old_session_id:
            logger.info(f"reset_session_id: retiring old session {old_session_id}")

        logger.info(
            f"reset_session_id: new session {new_session_id} at {new_timestamp}"
        )

        return {
            "session_id": new_session_id,
            "timestamp": new_timestamp,
        }

    def load_memory_index(self) -> Dict[str, Any]:
        """
        Fix #3: Load memory MEMORY.md index and all memory files.

        Reads /memory/MEMORY.md as index, then loads all referenced memory files
        into Agent-Context before User-Input processing.

        Returns:
            Dict with memory index + loaded file contents

        Contract: Memory is available at Agent-Start before any user input.
        """
        memory_index_path = self.memory_dir / "MEMORY.md"

        if not memory_index_path.exists():
            logger.warning(f"Memory index not found at {memory_index_path}")
            return {"index": [], "files": {}}

        try:
            index_content = memory_index_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.error(f"Failed to read memory index: {e}")
            return {"index": [], "files": {}}

        # Parse MEMORY.md index (format: "- [Title](file.md) — description")
        memory_files = {}
        index_entries = []

        for line in index_content.split("\n"):
            line = line.strip()
            if line.startswith("- ["):
                # Extract file path from "[Title](file.md)"
                import re
                match = re.search(r"\]\(([^)]+)\)", line)
                if match:
                    filename = match.group(1)
                    filepath = self.memory_dir / filename
                    index_entries.append(filename)

                    # Load the memory file
                    if filepath.exists():
                        try:
                            content = filepath.read_text(encoding="utf-8")
                            memory_files[filename] = content
                            logger.info(f"Loaded memory: {filename}")
                        except Exception as e:
                            logger.error(f"Failed to load memory {filename}: {e}")

        logger.info(f"Loaded {len(memory_files)}/{len(index_entries)} memory files")

        return {
            "index": index_entries,
            "files": memory_files,
            "index_path": str(memory_index_path),
        }


def apply_session_reset_fixes(
    old_state: Optional[Dict[str, Any]] = None,
    corvin_home: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Apply all three session-reset fixes in correct order.

    Order:
    1. Reset token budget (complete replacement)
    2. Generate new session-id + timestamp
    3. Load memory files for agent context

    Returns:
        Dict with results from all three fixes
    """
    manager = SessionResetManager(corvin_home=corvin_home)

    old_budget = old_state.get("token_budget") if old_state else None
    old_session_id = old_state.get("session_id") if old_state else None

    # Fix 1: Replace token budget
    new_budget = manager.reset_token_budget(old_budget)

    # Fix 2: New session-id + timestamp
    session_info = manager.generate_new_session_id(old_session_id)

    # Fix 3: Load memory files
    memory_state = manager.load_memory_index()

    result = {
        "fix1_token_budget": new_budget.to_dict(),
        "fix2_session_info": session_info,
        "fix3_memory_files": {
            "count": len(memory_state.get("files", {})),
            "index_entries": memory_state.get("index", []),
        },
        "status": "complete",
        "timestamp": datetime.utcnow().isoformat(),
    }

    logger.info(f"Session reset fixes applied: {result}")
    return result
