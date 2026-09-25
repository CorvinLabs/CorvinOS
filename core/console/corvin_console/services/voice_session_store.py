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
            s for s in list(self._sessions.values())[:limit]
            if s.get("tenant_id") == tenant_id
        ]

    def is_available(self) -> bool:
        """Always available (in-memory)"""
        return self._available


class SQLiteStore(VoiceSessionStore):
    """
    Phase 3: Persistent SQLite storage.
    Fallback to InMemory if DB unavailable.

    TODO: Implement SQLAlchemy integration
    Phase 3b: Cloud SQL variant (same interface)
    """

    def __init__(self, db_path: str = "~/.corvin/voice_sessions.db"):
        self._db_path = db_path
        self._fallback_store = InMemoryStore()
        self._available = False

        # Try to initialize DB
        self._init_db()

    def _init_db(self):
        """Initialize SQLite database (Phase 3b: implement)"""
        # TODO: Phase 3b - Implement SQLAlchemy engine + session factory
        # from sqlalchemy import create_engine
        # from sqlalchemy.orm import sessionmaker
        #
        # engine = create_engine(f"sqlite:///{self._db_path}")
        # Session = sessionmaker(bind=engine)
        # self._session = Session()

        logger.info(f"SQLiteStore: TODO implement DB initialization at {self._db_path}")
        # For Phase 3a MVP: use fallback
        self._available = False

    def create_session(self, session_id: str, tenant_id: str) -> Dict[str, Any]:
        """Create session (with fallback)"""
        if not self._available:
            return self._fallback_store.create_session(session_id, tenant_id)

        # TODO: Phase 3b - Implement DB write
        # db_session.add(VoiceSessionRecord(...))
        # db_session.commit()
        return {}

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get session (with fallback)"""
        if not self._available:
            return self._fallback_store.get_session(session_id)

        # TODO: Phase 3b - Query DB
        # return db_session.query(VoiceSessionRecord).filter_by(session_id=session_id).first()
        return None

    def update_session(self, session_id: str, data: Dict[str, Any]) -> bool:
        """Update session (with fallback)"""
        if not self._available:
            return self._fallback_store.update_session(session_id, data)

        # TODO: Phase 3b - Update DB
        # session = db_session.query(VoiceSessionRecord).filter_by(session_id=session_id).first()
        # if session:
        #     for key, value in data.items():
        #         setattr(session, key, value)
        #     db_session.commit()
        return False

    def delete_session(self, session_id: str) -> bool:
        """Delete session (with fallback)"""
        if not self._available:
            return self._fallback_store.delete_session(session_id)

        # TODO: Phase 3b - Delete from DB
        return False

    def list_sessions(self, tenant_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """List sessions (with fallback)"""
        if not self._available:
            return self._fallback_store.list_sessions(tenant_id, limit)

        # TODO: Phase 3b - Query DB
        return []

    def is_available(self) -> bool:
        """Check DB availability"""
        return self._available

    def fallback_to_memory(self):
        """Explicitly fallback to in-memory store"""
        logger.warning("SQLiteStore falling back to InMemory due to DB unavailability")
        self._available = False


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
        _store = SQLiteStore(db_path or "~/.corvin/voice_sessions.db")
    elif store_type == "auto":
        sqlite_store = SQLiteStore(db_path or "~/.corvin/voice_sessions.db")
        if sqlite_store.is_available():
            _store = sqlite_store
        else:
            logger.info("Auto-mode: SQLite unavailable, using InMemory fallback")
            _store = sqlite_store._fallback_store
    else:
        raise ValueError(f"Unknown store type: {store_type}")

    return _store


def is_store_available() -> bool:
    """Check if storage is available"""
    store = get_voice_session_store()
    return store.is_available()
