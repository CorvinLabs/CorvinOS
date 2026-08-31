"""Layer 39 CorvinFed — SocialOriginRegistry.

SQLite-backed follow/unfollow/block/rate-limit registry for the social
federation layer. Stores social-graph state (public keys + relationships)
only — never HMAC keys or secrets.

DB path: tenant_global_dir(tenant_id) / "social" / "registry.db"

Audit invariant: social.follow_accepted is written to the L16 hash chain
BEFORE any registry write, consistent with the audit-first rule throughout
Corvin.

CI AST lint: MUST NOT import anthropic.
"""
from __future__ import annotations

import math
import os
import sqlite3
import sys
import time
from pathlib import Path

try:
    from .paths import tenant_global_dir  # type: ignore[import-not-found]
    from .audit import audit_event  # type: ignore[import-not-found]
except ImportError:
    _here = Path(__file__).resolve().parent
    if str(_here) not in sys.path:
        sys.path.insert(0, str(_here))
    from paths import tenant_global_dir  # type: ignore[import-not-found]
    from audit import audit_event  # type: ignore[import-not-found]


# ── Constants ─────────────────────────────────────────────────────────────────

VALID_RELATIONSHIPS = frozenset(
    {"follower", "following", "mutual", "blocked", "former_follower"}
)

_RATE_WINDOW_SECONDS = 3600  # 1-hour rolling window


# ── Exceptions ────────────────────────────────────────────────────────────────


class RegistryError(Exception):
    """Raised on fatal registry errors."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# ── Schema DDL ────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS actors (
    actor_id        TEXT PRIMARY KEY,
    inbox_url       TEXT NOT NULL,
    public_key_hex  TEXT NOT NULL,
    display_name    TEXT,
    compliance_zone TEXT,
    is_ai           INTEGER NOT NULL DEFAULT 1,
    relationship    TEXT NOT NULL,
    created_at      REAL NOT NULL,
    last_seen       REAL
);

CREATE TABLE IF NOT EXISTS rate_limits (
    actor_id        TEXT NOT NULL,
    window_start    REAL NOT NULL,
    post_count      INTEGER DEFAULT 0,
    follow_count    INTEGER DEFAULT 0,
    PRIMARY KEY (actor_id, window_start)
);
"""


# ── Path helper ───────────────────────────────────────────────────────────────


def _registry_path(tenant_id: str | None = None) -> Path:
    return tenant_global_dir(tenant_id) / "social" / "registry.db"


# ── Main class ────────────────────────────────────────────────────────────────


class SocialRegistry:
    """SQLite-backed social graph registry for Layer 39 CorvinFed."""

    def __init__(self, tenant_id: str | None = None) -> None:
        self._tenant_id = tenant_id
        self._db_path = _registry_path(tenant_id)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection = sqlite3.connect(
            str(self._db_path), check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        # WAL mode for concurrent readers
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_DDL)
        self._conn.commit()

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def get_actor(self, actor_id: str) -> dict | None:
        """Return actor row as dict, or None if not found."""
        cur = self._conn.execute(
            "SELECT * FROM actors WHERE actor_id = ?", (actor_id,)
        )
        row = cur.fetchone()
        if row is None:
            return None
        return dict(row)

    def list_actors(self, relationship: str | None = None) -> list[dict]:
        """Return all actor rows, optionally filtered by relationship."""
        if relationship is not None:
            cur = self._conn.execute(
                "SELECT * FROM actors WHERE relationship = ? ORDER BY created_at",
                (relationship,),
            )
        else:
            cur = self._conn.execute(
                "SELECT * FROM actors ORDER BY created_at"
            )
        return [dict(r) for r in cur.fetchall()]

    def upsert_actor(
        self,
        actor_id: str,
        inbox_url: str,
        public_key_hex: str,
        relationship: str,
        display_name: str | None = None,
        compliance_zone: str | None = None,
        is_ai: bool = True,
    ) -> None:
        """Insert or update an actor row."""
        if relationship not in VALID_RELATIONSHIPS:
            raise RegistryError(f"invalid relationship: {relationship!r}")
        now = time.time()
        self._conn.execute(
            """
            INSERT INTO actors
                (actor_id, inbox_url, public_key_hex, display_name,
                 compliance_zone, is_ai, relationship, created_at, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(actor_id) DO UPDATE SET
                inbox_url       = excluded.inbox_url,
                public_key_hex  = excluded.public_key_hex,
                display_name    = excluded.display_name,
                compliance_zone = excluded.compliance_zone,
                is_ai           = excluded.is_ai,
                relationship    = excluded.relationship,
                last_seen       = excluded.last_seen
            """,
            (
                actor_id,
                inbox_url,
                public_key_hex,
                display_name,
                compliance_zone,
                1 if is_ai else 0,
                relationship,
                now,
                now,
            ),
        )
        self._conn.commit()

    def delete_actor(self, actor_id: str) -> bool:
        """Delete an actor row. Returns True if a row was deleted."""
        cur = self._conn.execute(
            "DELETE FROM actors WHERE actor_id = ?", (actor_id,)
        )
        self._conn.commit()
        return cur.rowcount > 0

    def update_relationship(self, actor_id: str, relationship: str) -> bool:
        """Update the relationship for an existing actor. Returns True if found."""
        if relationship not in VALID_RELATIONSHIPS:
            raise RegistryError(f"invalid relationship: {relationship!r}")
        cur = self._conn.execute(
            "UPDATE actors SET relationship = ? WHERE actor_id = ?",
            (relationship, actor_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def update_last_seen(self, actor_id: str) -> None:
        """Update last_seen timestamp for an actor."""
        self._conn.execute(
            "UPDATE actors SET last_seen = ? WHERE actor_id = ?",
            (time.time(), actor_id),
        )
        self._conn.commit()

    # ── Follow protocol ───────────────────────────────────────────────────────

    def accept_follow(
        self,
        actor_id: str,
        inbox_url: str,
        public_key_hex: str,
        display_name: str | None = None,
        compliance_zone: str | None = None,
        is_ai: bool = True,
    ) -> bool:
        """Accept a follow request.

        Audit-first: writes social.follow_accepted BEFORE registry write.
        Returns False (fail-silent) if actor is blocked or compliance gate fails.
        """
        # Blocked? Fail-silent.
        if self.is_blocked(actor_id):
            return False

        # Compliance gate
        if not self.check_compliance_zone(compliance_zone, self._tenant_id):
            return False

        # Audit BEFORE registry write
        audit_event(
            "social.follow_accepted",
            severity="INFO",
            details={"actor_id_prefix": actor_id[:16]},
        )

        # Determine new relationship
        existing = self.get_actor(actor_id)
        if existing and existing["relationship"] == "following":
            new_rel = "mutual"
        else:
            new_rel = "follower"

        self.upsert_actor(
            actor_id=actor_id,
            inbox_url=inbox_url,
            public_key_hex=public_key_hex,
            relationship=new_rel,
            display_name=display_name,
            compliance_zone=compliance_zone,
            is_ai=is_ai,
        )
        return True

    def reject_follow(
        self,
        actor_id: str,
        reason: str,
        channel: str = "",
        chat_key: str = "",
    ) -> None:
        """Write social.follow_rejected (WARNING) to audit. Never raises."""
        try:
            audit_event(
                "social.follow_rejected",
                channel=channel,
                chat_key=chat_key,
                severity="WARNING",
                details={"actor_id_prefix": actor_id[:16]},
            )
        except Exception:
            pass  # best-effort; never raise

    def add_following(
        self,
        actor_id: str,
        inbox_url: str,
        public_key_hex: str,
        display_name: str | None = None,
        compliance_zone: str | None = None,
        is_ai: bool = True,
    ) -> None:
        """Record that WE are following actor_id."""
        existing = self.get_actor(actor_id)
        if existing and existing["relationship"] == "follower":
            new_rel = "mutual"
        else:
            new_rel = "following"

        self.upsert_actor(
            actor_id=actor_id,
            inbox_url=inbox_url,
            public_key_hex=public_key_hex,
            relationship=new_rel,
            display_name=display_name,
            compliance_zone=compliance_zone,
            is_ai=is_ai,
        )

    def unfollow(self, actor_id: str) -> bool:
        """Remove or transition to former_follower. Returns True if actor was found."""
        existing = self.get_actor(actor_id)
        if existing is None:
            return False
        rel = existing["relationship"]
        if rel == "mutual":
            # We stop following; they remain as a follower
            return self.update_relationship(actor_id, "follower")
        elif rel in ("following",):
            return self.update_relationship(actor_id, "former_follower")
        elif rel == "follower":
            # They followed us; we never followed them — nothing to unfollow on our side
            return True
        else:
            return self.update_relationship(actor_id, "former_follower")

    def block(self, actor_id: str) -> None:
        """Upsert with relationship=blocked. Creates minimal row if actor doesn't exist."""
        existing = self.get_actor(actor_id)
        if existing is None:
            # Create minimal row — inbox_url and public_key_hex unknown
            now = time.time()
            self._conn.execute(
                """
                INSERT INTO actors
                    (actor_id, inbox_url, public_key_hex, relationship, created_at)
                VALUES (?, '', '', 'blocked', ?)
                ON CONFLICT(actor_id) DO UPDATE SET relationship = 'blocked'
                """,
                (actor_id, now),
            )
            self._conn.commit()
        else:
            self.update_relationship(actor_id, "blocked")

    def unblock(self, actor_id: str) -> bool:
        """Remove block. Returns True if actor was blocked."""
        existing = self.get_actor(actor_id)
        if existing is None or existing["relationship"] != "blocked":
            return False
        # Transition to former_follower (neutral) — caller can re-follow later
        return self.update_relationship(actor_id, "former_follower")

    def is_blocked(self, actor_id: str) -> bool:
        """Return True if actor_id has relationship == blocked."""
        cur = self._conn.execute(
            "SELECT relationship FROM actors WHERE actor_id = ?", (actor_id,)
        )
        row = cur.fetchone()
        if row is None:
            return False
        return row["relationship"] == "blocked"

    def is_follower(self, actor_id: str) -> bool:
        """Return True if actor_id has relationship 'follower' or 'mutual'.

        Used by the GrantChecker capability layer (FIX-6) to determine
        whether a capability grant for a follower applies to this actor.
        """
        cur = self._conn.execute(
            "SELECT relationship FROM actors WHERE actor_id = ?", (actor_id,)
        )
        row = cur.fetchone()
        if row is None:
            return False
        return row["relationship"] in ("follower", "mutual")

    # ── Compliance zone gate ─────────────────────────────────────────────────

    def check_compliance_zone(
        self,
        compliance_zone: str | None,
        tenant_id: str | None = None,
    ) -> bool:
        """Return True if federation is allowed given compliance_zone.

        If CORVIN_SOCIAL_ALLOW_NON_EU=true: always True.
        Otherwise: if local data_residency is "eu" and actor's compliance_zone
        is not None/"eu", returns False (gate fails).
        """
        if os.environ.get("CORVIN_SOCIAL_ALLOW_NON_EU", "false").lower() == "true":
            return True

        local_residency = os.environ.get("CORVIN_DATA_RESIDENCY", "eu").lower()
        if local_residency == "eu":
            if compliance_zone is not None and compliance_zone.lower() != "eu":
                return False
        return True

    # ── Rate limits ───────────────────────────────────────────────────────────

    def _current_window_start(self) -> float:
        """Return the start of the current 1-hour window (floored to hour)."""
        now = time.time()
        return math.floor(now / _RATE_WINDOW_SECONDS) * _RATE_WINDOW_SECONDS

    def _ensure_rate_row(self, actor_id: str, window_start: float) -> None:
        """Insert a rate_limits row if it doesn't exist yet."""
        self._conn.execute(
            """
            INSERT OR IGNORE INTO rate_limits (actor_id, window_start, post_count, follow_count)
            VALUES (?, ?, 0, 0)
            """,
            (actor_id, window_start),
        )

    def check_and_record_post(
        self,
        actor_id: str,
        per_actor_limit: int = 100,
        global_limit: int = 1000,
    ) -> bool:
        """Check and record a post event. Returns True if within rate limits.

        Uses the current 1-hour window. Checks both per-actor and global limits.
        Records the event only if within limits.
        """
        window = self._current_window_start()
        self._ensure_rate_row(actor_id, window)
        self._conn.commit()

        # Per-actor check
        cur = self._conn.execute(
            "SELECT post_count FROM rate_limits WHERE actor_id = ? AND window_start = ?",
            (actor_id, window),
        )
        row = cur.fetchone()
        actor_count = row["post_count"] if row else 0
        if actor_count >= per_actor_limit:
            return False

        # Global check (sum across all actors in this window)
        cur2 = self._conn.execute(
            "SELECT COALESCE(SUM(post_count), 0) AS total FROM rate_limits WHERE window_start = ?",
            (window,),
        )
        total = cur2.fetchone()["total"]
        if total >= global_limit:
            return False

        # Record
        self._conn.execute(
            """
            UPDATE rate_limits
            SET post_count = post_count + 1
            WHERE actor_id = ? AND window_start = ?
            """,
            (actor_id, window),
        )
        self._conn.commit()
        return True

    def check_and_record_follow(
        self,
        actor_id: str,
        limit: int = 10,
    ) -> bool:
        """Follow request rate limit check and record. Returns True if allowed."""
        window = self._current_window_start()
        self._ensure_rate_row(actor_id, window)
        self._conn.commit()

        cur = self._conn.execute(
            "SELECT follow_count FROM rate_limits WHERE actor_id = ? AND window_start = ?",
            (actor_id, window),
        )
        row = cur.fetchone()
        follow_count = row["follow_count"] if row else 0
        if follow_count >= limit:
            return False

        self._conn.execute(
            """
            UPDATE rate_limits
            SET follow_count = follow_count + 1
            WHERE actor_id = ? AND window_start = ?
            """,
            (actor_id, window),
        )
        self._conn.commit()
        return True

    # ── Followers / following ─────────────────────────────────────────────────

    def get_followers(self) -> list[dict]:
        """Return all actors with relationship in ('follower', 'mutual')."""
        cur = self._conn.execute(
            "SELECT * FROM actors WHERE relationship IN ('follower', 'mutual') ORDER BY created_at"
        )
        return [dict(r) for r in cur.fetchall()]

    def get_following(self) -> list[dict]:
        """Return all actors with relationship in ('following', 'mutual')."""
        cur = self._conn.execute(
            "SELECT * FROM actors WHERE relationship IN ('following', 'mutual') ORDER BY created_at"
        )
        return [dict(r) for r in cur.fetchall()]

    def follower_count(self) -> int:
        """Count of actors currently following us."""
        cur = self._conn.execute(
            "SELECT COUNT(*) AS c FROM actors WHERE relationship IN ('follower', 'mutual')"
        )
        return cur.fetchone()["c"]

    def following_count(self) -> int:
        """Count of actors we are currently following."""
        cur = self._conn.execute(
            "SELECT COUNT(*) AS c FROM actors WHERE relationship IN ('following', 'mutual')"
        )
        return cur.fetchone()["c"]

    # ── Purge ──────────────────────────────────────────────────────────────────

    def purge_all(self) -> int:
        """Delete all rows from actors table (for /social-leave). Returns row count deleted."""
        cur = self._conn.execute("DELETE FROM actors")
        self._conn.execute("DELETE FROM rate_limits")
        self._conn.commit()
        return cur.rowcount

    # ── Context manager support ───────────────────────────────────────────────

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        try:
            self._conn.close()
        except Exception:
            pass

    def __enter__(self) -> "SocialRegistry":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


__all__ = [
    "RegistryError",
    "SocialRegistry",
    "VALID_RELATIONSHIPS",
]
