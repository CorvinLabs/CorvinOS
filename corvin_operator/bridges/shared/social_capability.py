"""Layer 41 Social Capability Grants (ADR-0054).

SQLite-backed GrantStore and GrantChecker for the CorvinFed social layer.
Implements Ed25519-signed, deny-by-default capability grants that control
what remote social actors may do on the local instance.

DB path: tenant_global_dir(tenant_id) / "social" / "grants.db"  (mode 0600)

Design invariants
-----------------
- Deny-by-default: no grant ⇒ deny.
- Wildcard `"*"` grantee grants apply to any current follower; revoked
  follower immediately loses the benefit (unfollow = grant invalid).
- Ed25519 signature is verified from the *local* grant store on every
  check — the grantor's signing key is the canonical source of truth.
- Audit-first: ``grant.issued`` / ``grant.revoked`` are written to the
  L16 hash chain BEFORE any SQLite write.
- NEVER put full actor_ids, URLs, or instruction text in audit details.
- MUST NOT ``import anthropic`` (CI AST lint enforces this).
"""
from __future__ import annotations

import json
import math
import os
import re
import secrets
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

try:
    from .paths import tenant_global_dir  # type: ignore[import-not-found]
    from .audit import audit_event  # type: ignore[import-not-found]
except ImportError:
    _here = Path(__file__).resolve().parent
    if str(_here) not in sys.path:
        sys.path.insert(0, str(_here))
    from paths import tenant_global_dir  # type: ignore[import-not-found]
    from audit import audit_event  # type: ignore[import-not-found]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GRANT_ID_PREFIX = "grnt_"
SCHEMA_VERSION = 1

# Period names for rate-limit parsing
_PERIOD_SECONDS: dict[str, int] = {
    "second": 1,
    "minute": 60,
    "hour": 3600,
    "day": 86400,
}

_RATE_LIMIT_RE = re.compile(r"^(\d+)/(second|minute|hour|day)$")

VALID_DATA_CLASSES = frozenset({"PUBLIC", "INTERNAL", "CONFIDENTIAL", "SECRET"})
_DATA_CLASS_ORDER = ["PUBLIC", "INTERNAL", "CONFIDENTIAL", "SECRET"]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class GrantError(Exception):
    """Raised on fatal grant store errors."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# ---------------------------------------------------------------------------
# Grant dataclass
# ---------------------------------------------------------------------------


@dataclass
class CapabilityGrant:
    """A signed capability grant document.

    ``grantee_actor`` may be the literal ``"*"`` for all-followers grants.
    ``signature`` is a hex-encoded Ed25519 signature over the canonical
    payload (all fields except ``signature``, JSON serialised with
    sort_keys=True, separators=(",", ":"), ensure_ascii=True).
    """

    grant_id: str
    schema_version: int
    grantor_actor: str
    grantee_actor: str
    capabilities: list[str]
    issued_at: float
    revoked_at: float | None
    valid_until: float | None
    rate_limit: str | None
    data_class_ceiling: str | None
    signature: str


# ---------------------------------------------------------------------------
# Signing helpers (mirrors social_envelope.py)
# ---------------------------------------------------------------------------


def _canonical_grant_payload(grant: CapabilityGrant) -> bytes:
    """Return the bytes to sign (all fields except ``signature``)."""
    d = {
        "grant_id": grant.grant_id,
        "schema_version": grant.schema_version,
        "grantor_actor": grant.grantor_actor,
        "grantee_actor": grant.grantee_actor,
        "capabilities": grant.capabilities,
        "issued_at": grant.issued_at,
        "revoked_at": grant.revoked_at,
        "valid_until": grant.valid_until,
        "rate_limit": grant.rate_limit,
        "data_class_ceiling": grant.data_class_ceiling,
    }
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def sign_grant(grant: CapabilityGrant, private_key_hex: str) -> str:
    """Return an Ed25519 hex signature over the canonical grant payload."""
    private_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))
    payload = _canonical_grant_payload(grant)
    return private_key.sign(payload).hex()


def verify_grant_signature(grant: CapabilityGrant, public_key_hex: str) -> bool:
    """Verify the Ed25519 signature on a grant. Returns False on any failure."""
    try:
        public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        payload = _canonical_grant_payload(grant)
        sig = bytes.fromhex(grant.signature)
        public_key.verify(sig, payload)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Grant ID factory
# ---------------------------------------------------------------------------


def _new_grant_id() -> str:
    return f"{GRANT_ID_PREFIX}{secrets.token_hex(8)}"


# ---------------------------------------------------------------------------
# Rate-limit helpers
# ---------------------------------------------------------------------------


def _parse_rate_limit(rate_limit: str) -> tuple[int, int]:
    """Parse ``"N/period"`` → ``(n, period_seconds)``. Raises GrantError."""
    m = _RATE_LIMIT_RE.match(rate_limit)
    if not m:
        raise GrantError(
            f"invalid rate_limit format: {rate_limit!r}; expected 'N/period'"
        )
    n = int(m.group(1))
    period = _PERIOD_SECONDS[m.group(2)]
    return n, period


def _window_start(period_seconds: int) -> float:
    """Floor the current time to the period boundary."""
    now = time.time()
    return math.floor(now / period_seconds) * period_seconds


# ---------------------------------------------------------------------------
# SQLite DDL
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS grants (
    grant_id          TEXT PRIMARY KEY,
    schema_version    INTEGER NOT NULL DEFAULT 1,
    grantor_actor     TEXT NOT NULL,
    grantee_actor     TEXT NOT NULL,
    capabilities      TEXT NOT NULL,
    issued_at         REAL NOT NULL,
    revoked_at        REAL,
    valid_until       REAL,
    rate_limit        TEXT,
    data_class_ceiling TEXT,
    signature         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rate_limit_counters (
    grant_id      TEXT NOT NULL,
    window_start  REAL NOT NULL,
    count         INTEGER DEFAULT 0,
    PRIMARY KEY (grant_id, window_start)
);
"""


# ---------------------------------------------------------------------------
# Path helper
# ---------------------------------------------------------------------------


def _grants_db_path(tenant_id: str | None = None) -> Path:
    return tenant_global_dir(tenant_id) / "social" / "grants.db"


# ---------------------------------------------------------------------------
# GrantStore
# ---------------------------------------------------------------------------


class GrantStore:
    """SQLite-backed store for Layer 41 capability grants.

    The database is created at ``<corvin_home>/tenants/<tid>/global/social/grants.db``
    with mode 0600 so secrets never leak to other OS users.
    """

    def __init__(self, tenant_id: str | None = None, db_path: Path | None = None) -> None:
        self._tenant_id = tenant_id
        self._db_path: Path = db_path if db_path is not None else _grants_db_path(tenant_id)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection = sqlite3.connect(
            str(self._db_path), check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_DDL)
        self._conn.commit()
        # Restrict permissions on the database file (best-effort)
        try:
            os.chmod(self._db_path, 0o600)
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_grant(row: sqlite3.Row) -> CapabilityGrant:
        return CapabilityGrant(
            grant_id=row["grant_id"],
            schema_version=row["schema_version"],
            grantor_actor=row["grantor_actor"],
            grantee_actor=row["grantee_actor"],
            capabilities=json.loads(row["capabilities"]),
            issued_at=row["issued_at"],
            revoked_at=row["revoked_at"],
            valid_until=row["valid_until"],
            rate_limit=row["rate_limit"],
            data_class_ceiling=row["data_class_ceiling"],
            signature=row["signature"],
        )

    # ------------------------------------------------------------------
    # Issue
    # ------------------------------------------------------------------

    def issue(self, grant: CapabilityGrant, private_key_hex: str) -> CapabilityGrant:
        """Sign the grant, write to audit chain, then persist to SQLite.

        Returns the grant with the ``signature`` field populated.

        Audit-first: ``grant.issued`` is written to the L16 hash chain
        BEFORE the SQLite INSERT.
        """
        # Fill in defaults if needed
        if not grant.grant_id:
            grant.grant_id = _new_grant_id()
        if not grant.schema_version:
            grant.schema_version = SCHEMA_VERSION
        if not grant.issued_at:
            grant.issued_at = time.time()
        grant.revoked_at = None

        # Sign
        grant.signature = sign_grant(grant, private_key_hex)

        # Audit BEFORE SQLite write
        audit_event(
            "grant.issued",
            severity="INFO",
            details={
                "grant_id": grant.grant_id,
                "capability_count": len(grant.capabilities),
                "grantee_prefix": grant.grantee_actor[:16],
            },
        )

        # Persist
        self._conn.execute(
            """
            INSERT INTO grants
                (grant_id, schema_version, grantor_actor, grantee_actor,
                 capabilities, issued_at, revoked_at, valid_until,
                 rate_limit, data_class_ceiling, signature)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                grant.grant_id,
                grant.schema_version,
                grant.grantor_actor,
                grant.grantee_actor,
                json.dumps(grant.capabilities),
                grant.issued_at,
                grant.revoked_at,
                grant.valid_until,
                grant.rate_limit,
                grant.data_class_ceiling,
                grant.signature,
            ),
        )
        self._conn.commit()
        return grant

    # ------------------------------------------------------------------
    # Revoke
    # ------------------------------------------------------------------

    def revoke(self, grant_id: str) -> None:
        """Set ``revoked_at`` for a grant. Emits ``grant.revoked`` BEFORE the update."""
        grant = self.get(grant_id)
        if grant is None:
            return

        # Audit BEFORE write
        audit_event(
            "grant.revoked",
            severity="WARNING",
            details={
                "grant_id": grant_id,
                "grantee_prefix": grant.grantee_actor[:16],
            },
        )

        now = time.time()
        self._conn.execute(
            "UPDATE grants SET revoked_at = ? WHERE grant_id = ?",
            (now, grant_id),
        )
        self._conn.commit()

    def revoke_all_for_actor(self, grantee_actor: str) -> int:
        """Revoke all active grants for a grantee. Returns the number revoked."""
        cur = self._conn.execute(
            "SELECT grant_id, grantee_actor FROM grants WHERE grantee_actor = ? AND revoked_at IS NULL",
            (grantee_actor,),
        )
        rows = cur.fetchall()
        if not rows:
            return 0

        now = time.time()
        for row in rows:
            # Audit each revocation
            audit_event(
                "grant.revoked",
                severity="WARNING",
                details={
                    "grant_id": row["grant_id"],
                    "grantee_prefix": grantee_actor[:16],
                },
            )

        self._conn.execute(
            "UPDATE grants SET revoked_at = ? WHERE grantee_actor = ? AND revoked_at IS NULL",
            (now, grantee_actor),
        )
        self._conn.commit()
        return len(rows)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get(self, grant_id: str) -> CapabilityGrant | None:
        """Return a grant by ID, or None if not found."""
        cur = self._conn.execute(
            "SELECT * FROM grants WHERE grant_id = ?", (grant_id,)
        )
        row = cur.fetchone()
        if row is None:
            return None
        return self._row_to_grant(row)

    def list_for_grantee(self, grantee_actor: str) -> list[CapabilityGrant]:
        """Return all grants (including revoked) for a grantee."""
        cur = self._conn.execute(
            "SELECT * FROM grants WHERE grantee_actor = ? ORDER BY issued_at",
            (grantee_actor,),
        )
        return [self._row_to_grant(r) for r in cur.fetchall()]

    def list_active_for_grantee(self, grantee_actor: str) -> list[CapabilityGrant]:
        """Return active (not revoked) grants for a specific actor or wildcard."""
        cur = self._conn.execute(
            "SELECT * FROM grants WHERE grantee_actor IN (?, '*') AND revoked_at IS NULL ORDER BY issued_at",
            (grantee_actor,),
        )
        return [self._row_to_grant(r) for r in cur.fetchall()]

    # ------------------------------------------------------------------
    # Rate-limit counter
    # ------------------------------------------------------------------

    def check_and_increment_rate_limit(
        self, grant_id: str, rate_limit: str
    ) -> bool:
        """Check the rate-limit counter for a grant.

        Returns True if within limit (and increments counter).
        Returns False if limit exceeded (counter NOT incremented).
        """
        n, period = _parse_rate_limit(rate_limit)
        window = _window_start(period)

        self._conn.execute(
            """
            INSERT OR IGNORE INTO rate_limit_counters (grant_id, window_start, count)
            VALUES (?, ?, 0)
            """,
            (grant_id, window),
        )
        self._conn.commit()

        cur = self._conn.execute(
            "SELECT count FROM rate_limit_counters WHERE grant_id = ? AND window_start = ?",
            (grant_id, window),
        )
        row = cur.fetchone()
        current = row["count"] if row else 0

        if current >= n:
            return False

        self._conn.execute(
            """
            UPDATE rate_limit_counters
            SET count = count + 1
            WHERE grant_id = ? AND window_start = ?
            """,
            (grant_id, window),
        )
        self._conn.commit()
        return True

    # ------------------------------------------------------------------
    # Context manager / close
    # ------------------------------------------------------------------

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    def __enter__(self) -> "GrantStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


# ---------------------------------------------------------------------------
# GrantChecker
# ---------------------------------------------------------------------------


class GrantChecker:
    """Capability check engine for Layer 41.

    ``follower_check_fn`` must accept an ``actor_id`` string and return
    True if the actor is currently a follower of the local instance.
    This is the social-graph gate: unfollow = immediate loss of all grants.
    """

    def __init__(
        self,
        grant_store: GrantStore,
        follower_check_fn: Callable[[str], bool],
    ) -> None:
        self._store = grant_store
        self._is_follower = follower_check_fn

    def check(
        self,
        grantee: str,
        capability: str,
        public_key_hex: str | None = None,
        grant_document: dict | None = None,  # reserved for future M2 remote-presentation
    ) -> bool:
        """Check whether ``grantee`` holds ``capability``.

        Steps:
        1. Load all active (not revoked) grants for grantee + wildcard "*".
        2. For each grant: if public_key_hex provided, verify Ed25519 signature.
        3. Confirm grantee is a current follower (for wildcard: always check).
        4. Check revoked_at is None.
        5. Check valid_until not expired.
        6. Check rate_limit not exceeded (increment counter if passing).
        7. Check requested capability matches a granted capability.
        8. Emit grant.allowed (INFO) or grant.denied (WARNING) to audit chain.

        Returns True on the first matching grant. Returns False (deny) if
        no grant matches.

        Deny-by-default: every exit path except an explicit capability match
        returns False.
        """
        grantee_prefix = grantee[:16]
        active_grants = self._store.list_active_for_grantee(grantee)

        for grant in active_grants:
            # ----------------------------------------------------------
            # Step 1: is this grant addressed to this actor or wildcard?
            # ----------------------------------------------------------
            is_wildcard = grant.grantee_actor == "*"

            # ----------------------------------------------------------
            # Step 2: signature verification (if key provided)
            # ----------------------------------------------------------
            if public_key_hex is not None:
                if not verify_grant_signature(grant, public_key_hex):
                    audit_event(
                        "grant.signature_invalid",
                        severity="WARNING",
                        details={
                            "grant_id": grant.grant_id,
                            "grantee_prefix": grantee_prefix,
                        },
                    )
                    continue  # skip this grant, try others

            # ----------------------------------------------------------
            # Step 3: follower check
            # ----------------------------------------------------------
            if not self._is_follower(grantee):
                # Emit a single denied event and return False (no follower
                # can satisfy any grant, so stop iterating)
                audit_event(
                    "grant.denied",
                    severity="WARNING",
                    details={
                        "capability": capability,
                        "grantee_prefix": grantee_prefix,
                        "reason": "not_a_follower",
                    },
                )
                return False

            # ----------------------------------------------------------
            # Step 4: revoked_at (belt-and-suspenders; list_active already filters)
            # ----------------------------------------------------------
            if grant.revoked_at is not None:
                continue

            # ----------------------------------------------------------
            # Step 5: TTL check
            # ----------------------------------------------------------
            if grant.valid_until is not None and time.time() > grant.valid_until:
                continue  # expired; try next grant

            # ----------------------------------------------------------
            # Step 6: rate-limit check
            # ----------------------------------------------------------
            if grant.rate_limit is not None:
                if not self._store.check_and_increment_rate_limit(
                    grant.grant_id, grant.rate_limit
                ):
                    continue  # rate-exceeded; try next grant

            # ----------------------------------------------------------
            # Step 7: capability match
            # ----------------------------------------------------------
            if not _capability_matches(capability, grant.capabilities):
                continue

            # ----------------------------------------------------------
            # ALLOW
            # ----------------------------------------------------------
            audit_event(
                "grant.allowed",
                severity="INFO",
                details={
                    "grant_id": grant.grant_id,
                    "capability": capability,
                    "grantee_prefix": grantee_prefix,
                },
            )
            return True

        # No matching grant found → DENY
        audit_event(
            "grant.denied",
            severity="WARNING",
            details={
                "capability": capability,
                "grantee_prefix": grantee_prefix,
                "reason": "no_matching_grant",
            },
        )
        return False


# ---------------------------------------------------------------------------
# Capability matching
# ---------------------------------------------------------------------------


def _capability_matches(requested: str, granted: list[str]) -> bool:
    """Return True if ``requested`` is satisfied by any entry in ``granted``.

    Matching rules:
    - Exact string match always satisfies.
    - A granted wildcard ``"*"`` satisfies anything.
    - A granted pattern ending in ``".*"`` satisfies any ``requested``
      that starts with the prefix (e.g. ``"domain.*"`` satisfies
      ``"domain.research.read"``).
    """
    for g in granted:
        if g == "*":
            return True
        if g == requested:
            return True
        # Prefix wildcard: e.g. granted="domain.*" matches "domain.research.read"
        if g.endswith(".*"):
            prefix = g[:-2]  # strip the ".*"
            if requested == prefix or requested.startswith(prefix + "."):
                return True
    return False


# ---------------------------------------------------------------------------
# L36 Erasure Handler
# ---------------------------------------------------------------------------


@dataclass
class L41GrantHandler:
    """L36 ErasureHandler for Layer 41 capability grants.

    Purges all grants where ``grantee_actor`` has a prefix matching the
    subject_id. This covers the GDPR Art. 17 right-to-erasure requirement:
    the grant store contains the grantee's actor identifier which, while
    pseudonymous, may map to the subject when the identity mapping is present.

    Registered data classes: ``social.participation``, ``social.capability_grant``.
    """

    layer_id: str = "L41-social-grants"
    data_classes: frozenset = frozenset({"social.participation", "social.capability_grant"})
    _tenant_id: str | None = None
    _db_path: Path | None = None

    def __init__(
        self,
        tenant_id: str | None = None,
        db_path: Path | None = None,
    ) -> None:
        self.layer_id = "L41-social-grants"
        self.data_classes = frozenset({"social.participation", "social.capability_grant"})
        self._tenant_id = tenant_id
        self._db_path = db_path

    def purge(self, subject_id: str, request_id: str) -> object:
        """Delete all grants for grantee actors whose prefix matches subject_id.

        Returns an ``ErasureLayerResult``-compatible object with counts.
        """
        # Lazy import to avoid circular dependencies at module load time
        try:
            from .erasure_orchestrator import (  # type: ignore[import-not-found]
                ErasureLayerResult,
                LayerStatus,
            )
        except ImportError:
            _h = Path(__file__).resolve().parent
            if str(_h) not in sys.path:
                sys.path.insert(0, str(_h))
            from erasure_orchestrator import (  # type: ignore[import-not-found]
                ErasureLayerResult,
                LayerStatus,
            )

        try:
            store = GrantStore(tenant_id=self._tenant_id, db_path=self._db_path)
            try:
                # Find all grants whose grantee_actor starts with subject_id
                # (subject_id is a pseudonymous identifier, e.g. "user_42" or
                # a full actor prefix).  We check both prefix and exact match.
                cur = store._conn.execute(
                    "SELECT grant_id, grantee_actor FROM grants WHERE revoked_at IS NULL"
                )
                rows = cur.fetchall()

                to_revoke: list[tuple[str, str]] = []
                for row in rows:
                    actor = row["grantee_actor"]
                    if actor == subject_id or actor.startswith(subject_id):
                        to_revoke.append((row["grant_id"], actor))

                if not to_revoke:
                    return ErasureLayerResult(
                        layer_id=self.layer_id,
                        status=LayerStatus.SKIPPED,
                        reason="no grants found for subject",
                    )

                now = time.time()
                for grant_id, actor in to_revoke:
                    # Emit per-grant audit event (audit-first on each revocation)
                    audit_event(
                        "grant.revoked",
                        severity="WARNING",
                        details={
                            "grant_id": grant_id,
                            "grantee_prefix": actor[:16],
                        },
                    )

                # Bulk update after auditing
                store._conn.execute(
                    """
                    UPDATE grants SET revoked_at = ?
                    WHERE revoked_at IS NULL AND (
                        grantee_actor = ? OR grantee_actor LIKE ?
                    )
                    """,
                    (now, subject_id, f"{subject_id}%"),
                )
                store._conn.commit()

                return ErasureLayerResult(
                    layer_id=self.layer_id,
                    status=LayerStatus.APPLIED,
                    count=len(to_revoke),
                    reason=f"revoked {len(to_revoke)} grant(s) for subject",
                )
            finally:
                store.close()
        except Exception as exc:
            return _erasure_failed(self.layer_id, exc)


def _erasure_failed(layer_id: str, exc: Exception) -> object:
    """Build an ErasureLayerResult for a handler exception."""
    try:
        from .erasure_orchestrator import ErasureLayerResult, LayerStatus  # type: ignore
    except ImportError:
        from erasure_orchestrator import ErasureLayerResult, LayerStatus  # type: ignore
    return ErasureLayerResult(
        layer_id=layer_id,
        status=LayerStatus.FAILED,
        reason=str(exc)[:500],
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "CapabilityGrant",
    "GrantChecker",
    "GrantError",
    "GrantStore",
    "L41GrantHandler",
    "sign_grant",
    "verify_grant_signature",
]
