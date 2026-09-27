"""
Voice Session Store (Phase 3 Stream A)
Abstraction layer for persistent storage with fallback

Interfaces:
- VoiceSessionStore: Abstract base
- InMemoryStore: Phase 1-2 (current)
- SQLiteStore: Phase 3 (persistent)

Zero-downtime migration: Phase 5 compatible with both stores.
Graceful fallback if DB unavailable.

@date 2026-09-25
@phase Phase 3a: Persistent Storage Layer

NOT WIRED: no production caller as of 2026-09-27 (adversarial review) —
nothing imports this module.

Defused 2026-09-27: ``SQLiteStore`` was a stub that silently served every call
from an in-memory dict, so a caller that asked for PERSISTENT storage
("sqlite") lost all data on restart without being told. It now raises
``NotImplementedError``; ``store_type="sqlite"`` fails; ``"auto"`` falls back
to the in-memory store explicitly (logged). ``InMemoryStore.list_sessions``
applied ``limit`` BEFORE filtering by tenant, dropping a tenant's sessions
whenever other tenants' sessions came first.
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, List, Any
from datetime import datetime
from enum import Enum
import json
import logging

logger = logging.getLogger(__name__)


class VoiceSessionStore(ABC):
    """Abstract storage interface for voice sessions"""

    @abstractmethod
    def create_session(self, session_id: str, tenant_id: str) -> Dict[str, Any]:
        """Create new voice recording session"""
        pass

    @abstractmethod
    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve session by ID"""
        pass

    @abstractmethod
    def update_session(self, session_id: str, data: Dict[str, Any]) -> bool:
        """Update session data"""
        pass

    @abstractmethod
    def delete_session(self, session_id: str) -> bool:
        """Delete session (for cleanup)"""
        pass

    @abstractmethod
    def list_sessions(self, tenant_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """List sessions for a tenant"""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if storage is available"""
        pass


class InMemoryStore(VoiceSessionStore):
    """
    Phase 1-2: In-memory storage (current implementation).
    Fallback when DB unavailable.
    """

    def __init__(self):
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._available = True
        logger.info("InMemoryStore initialized (fallback mode)")

    def create_session(self, session_id: str, tenant_id: str) -> Dict[str, Any]:
        """Create new session in memory"""
        session = {
            "session_id": session_id,
            "tenant_id": tenant_id,
            "created_at": datetime.utcnow().isoformat(),
            "status": "RECORDING",
        }
        self._sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve session"""
        return self._sessions.get(session_id)

    def update_session(self, session_id: str, data: Dict[str, Any]) -> bool:
        """Update session data"""
        if session_id not in self._sessions:
            return False
        self._sessions[session_id].update(data)
        return True

    def delete_session(self, session_id: str) -> bool:
        """Delete session"""
        if session_id in self._sessions:
            del self._sessions[session_id]
            return True
        return False

    def list_sessions(self, tenant_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """List sessions for tenant"""
        return [
            s for s in self._sessions.values()
            if s.get("tenant_id") == tenant_id
        ][:limit]

    def is_available(self) -> bool:
        """Always available (in-memory)"""
        return self._available


class SQLiteStore(VoiceSessionStore):
    """Phase 3: Persistent SQLite storage — NOT IMPLEMENTED.

    Constructing it raises: a "persistent" store that quietly kept everything
    in memory is worse than no store.
    """

    def __init__(self, db_path: str = ""):
        raise NotImplementedError("persistent voice-session storage is not implemented")

    def create_session(self, session_id: str, tenant_id: str) -> Dict[str, Any]:  # pragma: no cover
        raise NotImplementedError

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:  # pragma: no cover
        raise NotImplementedError

    def update_session(self, session_id: str, data: Dict[str, Any]) -> bool:  # pragma: no cover
        raise NotImplementedError

    def delete_session(self, session_id: str) -> bool:  # pragma: no cover
        raise NotImplementedError

    def list_sessions(self, tenant_id: str, limit: int = 100) -> List[Dict[str, Any]]:  # pragma: no cover
        raise NotImplementedError

    def is_available(self) -> bool:  # pragma: no cover
        return False


# Singleton store (Phase 3: pluggable)
_store: Optional[VoiceSessionStore] = None


def get_voice_session_store(
    store_type: str = "memory",  # "memory" | "sqlite" | "auto"
    db_path: Optional[str] = None
) -> VoiceSessionStore:
    """
    Get or create voice session store.

    Modes:
    - "memory": Always in-memory (Phase 1-2)
    - "sqlite": Persistent SQLite (Phase 3)
    - "auto": Try SQLite, fallback to memory (Phase 3 production)
    """
    global _store

    if _store is not None:
        return _store

    if store_type == "memory":
        _store = InMemoryStore()
    elif store_type == "sqlite":
        raise NotImplementedError("store_type='sqlite' is not implemented (no persistent store)")
    elif store_type == "auto":
        logger.warning("voice session store: no persistent backend; using NON-persistent in-memory store")
        _store = InMemoryStore()
    else:
        raise ValueError(f"Unknown store type: {store_type}")

    return _store


def is_store_available() -> bool:
    """Check if storage is available"""
    store = get_voice_session_store()
    return store.is_available()
