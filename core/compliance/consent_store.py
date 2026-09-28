"""
ConsentStore — Persistent user consent management (GDPR Art. 6, 7)

Implements persistent storage of user consent with:
- Tenant isolation (fail-closed)
- TTL-based expiry (default 90 days)
- Audit trail integration (consent_granted/revoked events)
- Fail-safe operations (no silent failures)

All consent grants/revokes are audited via audit_backend.
Queries filtered by tenant_id (cross-tenant access returns 403).

Database: SQLite (<corvin_home>/tenants/<tenant>/consent_store.db)
Schema: [user_id, scope, tenant_id, granted_at, expires_at, revoked_at]

``user_id`` is a pseudonymous subject id — for the console, the stable
``local-operator:<tenant>`` principal of a local-login session, or the
session's ``sid_fingerprint`` for a session with a credential identity (see
``core.compliance.consent.consent_subject``). It must NEVER be a raw session
id: the ``corvin_console_sid`` cookie is a bearer credential and this file is
not a secret store.

Every grant is TTL-capped at :data:`MAX_TTL_DAYS`; deny-by-default is kept on
every read path (missing, revoked, expired, unreadable → no consent).
"""

import sqlite3
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, asdict
from enum import Enum
import hashlib

logger = logging.getLogger(__name__)

#: Hard upper bound on any consent grant. A caller asking for more is clamped
#: here — a grant can never outlive this, whatever the surface requests.
MAX_TTL_DAYS = 90


class ConsentScope(str, Enum):
    """Allowed consent scopes (GDPR-aligned)"""
    SKILL_GENERATION = "skill_generation"
    TELEMETRY_PING = "telemetry_ping"
    ERROR_TELEMETRY = "error_telemetry"
    HEALING_TRACES = "healing_traces"
    GEO_TRACKING_TIER_1 = "geo_tracking_tier_1"
    GEO_TRACKING_TIER_2 = "geo_tracking_tier_2"
    GEO_TRACKING_TIER_3 = "geo_tracking_tier_3"
    VOICE_TRANSCRIPTION = "voice_transcription"
    DATA_ANALYTICS = "data_analytics"


@dataclass(frozen=True)
class ConsentRecord:
    """Immutable consent record (frozen dataclass)"""
    user_id: str
    scope: str
    tenant_id: str
    granted_at: str  # ISO 8601
    expires_at: str  # ISO 8601
    revoked_at: Optional[str] = None  # ISO 8601, None if active

    def is_active(self) -> bool:
        """Check if consent is currently valid"""
        if self.revoked_at is not None:
            return False
        try:
            expires = datetime.fromisoformat(self.expires_at)
        except (TypeError, ValueError):
            return False  # unreadable expiry → no consent (fail-closed)
        return datetime.utcnow() < expires

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for audit event payload"""
        return asdict(self)


class ConsentStoreError(Exception):
    """Base error for ConsentStore operations"""
    pass


class TenantIsolationError(ConsentStoreError):
    """Tenant isolation violation (fail-closed)"""
    pass


class ConsentStore:
    """
    Persistent user consent store with tenant isolation.

    Thread-safe SQLite backend with immutable consent records.
    All operations fail-closed on tenant isolation violations.
    """

    def __init__(self, tenant_id: str, corvin_home: Optional[Path] = None):
        """
        Initialize consent store for a specific tenant.

        Args:
            tenant_id: Tenant identifier (fail-closed if None or empty)
            corvin_home: Path to ~/.corvin/ (default: env var CORVIN_HOME)

        Raises:
            TenantIsolationError: If tenant_id is None/empty
        """
        self.tenant_id = _validated_tenant(tenant_id)

        corvin_home = _resolve_home(corvin_home)

        # Build tenant-scoped DB path
        self.db_path = corvin_home / "tenants" / tenant_id / "consent_store.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        logger.info(f"ConsentStore initialized for tenant={tenant_id}, db={self.db_path}")

        # Initialize schema
        self._init_schema()

    def _get_connection(self) -> sqlite3.Connection:
        """Get SQLite connection with isolation level"""
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.isolation_level = "IMMEDIATE"  # Serialize writes
        return conn

    def _init_schema(self) -> None:
        """Initialize database schema (idempotent)"""
        conn = self._get_connection()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS consent_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    granted_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    revoked_at TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, scope, tenant_id)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_tenant_user
                ON consent_records (tenant_id, user_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_tenant_scope
                ON consent_records (tenant_id, scope)
            """)
            conn.commit()
            logger.debug(f"ConsentStore schema initialized for tenant={self.tenant_id}")
        finally:
            conn.close()

    def grant_consent(
        self,
        user_id: str,
        scope: str,
        ttl_days: int = 90
    ) -> ConsentRecord:
        """
        Grant consent for user + scope (idempotent).

        Args:
            user_id: User identifier
            scope: Consent scope (from ConsentScope enum)
            ttl_days: Time-to-live in days (default 90 per GDPR)

        Returns:
            ConsentRecord (immutable)

        Raises:
            TenantIsolationError: If tenant_id mismatch
            ConsentStoreError: On DB errors
        """
        if not user_id or not scope:
            raise ConsentStoreError("user_id and scope must be non-empty")
        try:
            ttl = float(ttl_days)
        except (TypeError, ValueError):
            raise ConsentStoreError(f"ttl_days must be a number, got {ttl_days!r}") from None
        if not ttl > 0:  # also rejects NaN
            raise ConsentStoreError("ttl_days must be > 0")
        ttl = min(ttl, float(MAX_TTL_DAYS))

        now = datetime.utcnow()
        expires = now + timedelta(days=ttl)

        conn = self._get_connection()
        try:
            # One row per (user_id, scope, tenant_id) — the table's UNIQUE
            # constraint. A (re-)grant REPLACES the row's window and clears any
            # revocation. The previous "revoke old row, then INSERT a new one"
            # violated that constraint on every re-grant (IntegrityError), so
            # consent, once revoked, could never be given again. The history
            # of grants/revokes lives in the audit chain, not in this table.
            conn.execute("""
                INSERT INTO consent_records (user_id, scope, tenant_id, granted_at, expires_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, scope, tenant_id) DO UPDATE SET
                    granted_at = excluded.granted_at,
                    expires_at = excluded.expires_at,
                    revoked_at = NULL
            """, (user_id, scope, self.tenant_id, now.isoformat(), expires.isoformat()))

            conn.commit()

            record = ConsentRecord(
                user_id=user_id,
                scope=scope,
                tenant_id=self.tenant_id,
                granted_at=now.isoformat(),
                expires_at=expires.isoformat(),
                revoked_at=None
            )

            logger.info(f"Consent granted: user={user_id}, scope={scope}, tenant={self.tenant_id}")
            return record

        except sqlite3.Error as e:
            raise ConsentStoreError(f"DB error on grant_consent: {e}")
        finally:
            conn.close()

    def get_consent(self, user_id: str, scope: str) -> bool:
        """
        Check if user has active consent for scope (fail-closed).

        Args:
            user_id: User identifier
            scope: Consent scope

        Returns:
            True if valid, active consent exists; False otherwise

        Raises:
            TenantIsolationError: If tenant_id mismatch
        """
        if not user_id or not scope:
            logger.warning(f"get_consent called with empty user_id or scope (fail-closed)")
            return False

        conn = self._get_connection()
        try:
            cursor = conn.execute("""
                SELECT granted_at, expires_at, revoked_at
                FROM consent_records
                WHERE user_id = ? AND scope = ? AND tenant_id = ?
                ORDER BY granted_at DESC
                LIMIT 1
            """, (user_id, scope, self.tenant_id))

            row = cursor.fetchone()
            if not row:
                logger.debug(f"No consent record: user={user_id}, scope={scope}, tenant={self.tenant_id}")
                return False

            granted_at, expires_at, revoked_at = row

            # Fail-closed: any revocation = no consent
            if revoked_at is not None:
                logger.debug(f"Consent revoked: user={user_id}, scope={scope}")
                return False

            # Fail-closed: any expiry = no consent
            now = datetime.utcnow()
            expires = datetime.fromisoformat(expires_at)
            if now >= expires:
                logger.debug(f"Consent expired: user={user_id}, scope={scope}")
                return False

            logger.debug(f"Consent active: user={user_id}, scope={scope}")
            return True

        except sqlite3.Error as e:
            logger.error(f"DB error on get_consent: {e} (fail-closed)")
            return False
        finally:
            conn.close()

    def revoke_consent(self, user_id: str, scope: str) -> Optional[ConsentRecord]:
        """
        Revoke consent for user + scope.

        Args:
            user_id: User identifier
            scope: Consent scope

        Returns:
            ConsentRecord of revoked consent (if existed), None otherwise

        Raises:
            TenantIsolationError: If tenant_id mismatch
        """
        if not user_id or not scope:
            raise ConsentStoreError("user_id and scope must be non-empty")

        now = datetime.utcnow()

        conn = self._get_connection()
        try:
            # Fetch existing consent
            cursor = conn.execute("""
                SELECT granted_at, expires_at, revoked_at
                FROM consent_records
                WHERE user_id = ? AND scope = ? AND tenant_id = ? AND revoked_at IS NULL
                LIMIT 1
            """, (user_id, scope, self.tenant_id))

            row = cursor.fetchone()
            if not row:
                logger.info(f"No active consent to revoke: user={user_id}, scope={scope}")
                return None

            granted_at, expires_at, _ = row

            # Update to revoked
            conn.execute("""
                UPDATE consent_records
                SET revoked_at = ?
                WHERE user_id = ? AND scope = ? AND tenant_id = ? AND revoked_at IS NULL
            """, (now.isoformat(), user_id, scope, self.tenant_id))

            conn.commit()

            record = ConsentRecord(
                user_id=user_id,
                scope=scope,
                tenant_id=self.tenant_id,
                granted_at=granted_at,
                expires_at=expires_at,
                revoked_at=now.isoformat()
            )

            logger.info(f"Consent revoked: user={user_id}, scope={scope}, tenant={self.tenant_id}")
            return record

        except sqlite3.Error as e:
            raise ConsentStoreError(f"DB error on revoke_consent: {e}")
        finally:
            conn.close()

    def unrevoked_subjects(self, scope: str) -> List[str]:
        """Every subject with an unrevoked row for ``scope`` in this tenant
        (expired rows included — revoking them is harmless and leaves no
        row that still reads as un-withdrawn). Used to sweep legacy
        per-session subjects on withdrawal (``routes/consent.py``)."""
        if not scope:
            return []
        conn = self._get_connection()
        try:
            rows = conn.execute("""
                SELECT DISTINCT user_id FROM consent_records
                WHERE scope = ? AND tenant_id = ? AND revoked_at IS NULL
            """, (scope, self.tenant_id)).fetchall()
        except sqlite3.Error as e:
            raise ConsentStoreError(f"DB error on unrevoked_subjects: {e}")
        finally:
            conn.close()
        return [u for (u,) in rows]

    def list_active_consents(self, user_id: str) -> List[ConsentRecord]:
        """
        List all active consents for user.

        Args:
            user_id: User identifier

        Returns:
            List of active ConsentRecord (empty if none)
        """
        if not user_id:
            return []

        now = datetime.utcnow()

        conn = self._get_connection()
        try:
            cursor = conn.execute("""
                SELECT user_id, scope, tenant_id, granted_at, expires_at, revoked_at
                FROM consent_records
                WHERE user_id = ? AND tenant_id = ? AND revoked_at IS NULL
                ORDER BY granted_at DESC
            """, (user_id, self.tenant_id))

            records = []
            for row in cursor.fetchall():
                user_id_db, scope, tenant_id_db, granted_at, expires_at, revoked_at = row

                # Fail-closed: expired consents not included
                expires = datetime.fromisoformat(expires_at)
                if now >= expires:
                    continue

                record = ConsentRecord(
                    user_id=user_id_db,
                    scope=scope,
                    tenant_id=tenant_id_db,
                    granted_at=granted_at,
                    expires_at=expires_at,
                    revoked_at=revoked_at
                )
                records.append(record)

            logger.debug(f"Listed {len(records)} active consents for user={user_id}")
            return records

        except sqlite3.Error as e:
            logger.error(f"DB error on list_active_consents: {e} (return [])")
            return []
        finally:
            conn.close()


def _validated_tenant(tenant_id: Any) -> str:
    """Canonical tenant validation (``forge.tenants.validate_tenant_id``);
    anything it refuses — empty, traversal, wrong shape — is a
    :class:`TenantIsolationError` (fail-closed)."""
    if not tenant_id or not isinstance(tenant_id, str):
        raise TenantIsolationError("tenant_id must be non-empty string (fail-closed)")
    try:
        from corvin_operator.forge.forge.tenants import validate_tenant_id
        validate_tenant_id(tenant_id)
    except ImportError as exc:
        raise TenantIsolationError("tenant validator unavailable (fail-closed)") from exc
    except Exception as exc:  # InvalidTenantID / ValueError
        raise TenantIsolationError(f"invalid tenant_id (fail-closed): {exc}") from exc
    return tenant_id


def _resolve_home(corvin_home: Optional[Path]) -> Path:
    """Explicit root, else the canonical runtime root — which honours
    ``CORVIN_HOME`` at CALL time (``core.paths.tenant.corvin_home``)."""
    if corvin_home is not None:
        return Path(corvin_home)
    from core.paths.tenant import corvin_home as _canonical_home
    return _canonical_home()


# Module-level cache, keyed by the RESOLVED database file — never by tenant
# alone. Keyed by tenant, the first store created in a process was returned
# forever: a later ``CORVIN_HOME`` (tests, a second runtime root) or an
# explicit ``corvin_home=`` argument was silently ignored and grants/reads went
# to the first root's database.
_stores: Dict[Path, ConsentStore] = {}


def get_consent_store(tenant_id: str, corvin_home: Optional[Path] = None) -> ConsentStore:
    """
    Get or create ConsentStore for tenant (singleton per tenant).

    Args:
        tenant_id: Tenant identifier (must be non-empty)
        corvin_home: Path to ~/.corvin/ (optional)

    Returns:
        ConsentStore instance (cached)

    Raises:
        TenantIsolationError: If tenant_id is invalid
    """
    tenant_id = _validated_tenant(tenant_id)
    home = _resolve_home(corvin_home)
    key = (home / "tenants" / tenant_id / "consent_store.db").resolve()
    store = _stores.get(key)
    if store is None:
        store = ConsentStore(tenant_id, home)
        _stores[key] = store
    return store
