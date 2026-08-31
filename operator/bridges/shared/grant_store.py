"""Layer 41 Social Capability Grants — SQLite-backed grant store.

Storage layout:
    <tenant_global>/grants/grants.db   (mode 0600)

The caller (GrantChecker or GrantIssuer) MUST emit the L16 audit event
BEFORE calling any mutating method on this store — audit-first invariant.

CI AST lint: MUST NOT import anthropic.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

try:
    from .paths import tenant_global_dir  # type: ignore[import-not-found]
except ImportError:
    import sys

    _here = Path(__file__).resolve().parent
    if str(_here) not in sys.path:
        sys.path.insert(0, str(_here))
    from paths import tenant_global_dir  # type: ignore[import-not-found]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS grants (
    grant_id        TEXT PRIMARY KEY,
    schema_version  INTEGER NOT NULL DEFAULT 1,
    grantor_actor   TEXT NOT NULL,
    grantee_actor   TEXT NOT NULL,
    capabilities    TEXT NOT NULL,
    conditions      TEXT NOT NULL,
    issued_at       INTEGER NOT NULL,
    revoked_at      INTEGER,
    signature       TEXT NOT NULL,
    raw_doc         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_grants_grantee  ON grants(grantee_actor);
CREATE INDEX IF NOT EXISTS idx_grants_grantor  ON grants(grantor_actor);
CREATE INDEX IF NOT EXISTS idx_grants_revoked  ON grants(revoked_at);

CREATE TABLE IF NOT EXISTS rate_counters (
    grant_id     TEXT NOT NULL,
    period_key   TEXT NOT NULL,
    window_start INTEGER NOT NULL,
    count        INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (grant_id, period_key)
);
"""

_PERIOD_SECONDS: dict[str, int] = {
    "second": 1,
    "minute": 60,
    "hour": 3_600,
    "day": 86_400,
}


# ── Path helpers ──────────────────────────────────────────────────────────────


def grant_dir(tenant_id: str | None = None) -> Path:
    return tenant_global_dir(tenant_id) / "grants"


def grant_db_path(tenant_id: str | None = None) -> Path:
    return grant_dir(tenant_id) / "grants.db"


# ── Exceptions ────────────────────────────────────────────────────────────────


class GrantStoreError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# ── GrantStore ────────────────────────────────────────────────────────────────


class GrantStore:
    """Thin SQLite façade for capability grant CRUD and rate limiting.

    All mutating methods assume the caller has already written the
    corresponding L16 audit event (audit-first invariant, ADR-0054 §lifecycle).
    """

    def __init__(self, db_path: Path) -> None:
        self._db = db_path
        self._db.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        try:
            os.chmod(self._db, 0o600)
        except OSError:
            pass

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self._db))
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(_SCHEMA)

    # ── Grant CRUD ────────────────────────────────────────────────────────────

    def save_grant(self, doc: dict) -> None:
        """Persist a signed grant document (INSERT).

        Raises ``GrantStoreError`` on grant_id collision.
        Caller MUST emit ``grant.issued`` audit event BEFORE calling this.
        """
        raw = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        with self._connect() as con:
            try:
                con.execute(
                    """INSERT INTO grants
                       (grant_id, schema_version, grantor_actor, grantee_actor,
                        capabilities, conditions, issued_at, revoked_at, signature, raw_doc)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        doc["grant_id"],
                        doc.get("schema_version", 1),
                        doc["grantor_actor"],
                        doc["grantee_actor"],
                        json.dumps(doc.get("capabilities") or []),
                        json.dumps(doc.get("conditions") or {}),
                        int(doc["issued_at"]),
                        doc.get("revoked_at"),
                        doc.get("signature", ""),
                        raw,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise GrantStoreError(f"grant_id collision: {doc['grant_id']!r}") from exc

    def get_grant(self, grant_id: str) -> dict | None:
        """Return the raw grant document, or None if not found."""
        with self._connect() as con:
            row = con.execute(
                "SELECT raw_doc FROM grants WHERE grant_id=?", (grant_id,)
            ).fetchone()
        return json.loads(row["raw_doc"]) if row else None

    def set_revoked(self, grant_id: str, revoked_at: int | None = None) -> bool:
        """Mark a grant revoked (soft-delete). Returns True if the grant existed.

        Caller MUST emit ``grant.revoked`` audit event BEFORE calling this.
        """
        ts = revoked_at if revoked_at is not None else int(time.time())
        with self._connect() as con:
            cur = con.execute(
                "UPDATE grants SET revoked_at=? WHERE grant_id=? AND revoked_at IS NULL",
                (ts, grant_id),
            )
        return cur.rowcount > 0

    def revoke_all_for_actor(self, actor_id: str) -> int:
        """Soft-revoke all active grants involving an actor. Returns count changed.

        Caller MUST emit ``grant.revoked`` (bulk) audit event BEFORE calling this.
        """
        ts = int(time.time())
        with self._connect() as con:
            cur = con.execute(
                """UPDATE grants SET revoked_at=?
                   WHERE (grantee_actor=? OR grantor_actor=?) AND revoked_at IS NULL""",
                (ts, actor_id, actor_id),
            )
        return cur.rowcount

    def list_grants(
        self,
        *,
        grantee_actor: str | None = None,
        grantor_actor: str | None = None,
        include_revoked: bool = False,
    ) -> list[dict]:
        """Return grant documents matching the given filters."""
        clauses: list[str] = []
        params: list[Any] = []
        if grantee_actor is not None:
            clauses.append("grantee_actor=?")
            params.append(grantee_actor)
        if grantor_actor is not None:
            clauses.append("grantor_actor=?")
            params.append(grantor_actor)
        if not include_revoked:
            clauses.append("revoked_at IS NULL")
        sql = "SELECT raw_doc FROM grants"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        with self._connect() as con:
            rows = con.execute(sql, params).fetchall()
        return [json.loads(r["raw_doc"]) for r in rows]

    # ── Rate limiting ─────────────────────────────────────────────────────────

    @staticmethod
    def parse_rate_limit(limit_str: str) -> tuple[int, int]:
        """Return (count_limit, window_seconds) from a 'N/period' string.

        Raises ``GrantStoreError`` on invalid format.
        """
        try:
            count_str, period = limit_str.split("/", 1)
            count = int(count_str)
            seconds = _PERIOD_SECONDS[period]
            return count, seconds
        except (ValueError, KeyError) as exc:
            raise GrantStoreError(
                f"invalid rate_limit format: {limit_str!r} (expected 'N/period')"
            ) from exc

    def check_rate_limit(self, grant_id: str, limit_str: str) -> bool:
        """Return True if a request is within the rate limit (read-only check).

        Does NOT increment the counter — call ``increment_rate_counter`` after ALLOW.
        """
        count_limit, window_seconds = self.parse_rate_limit(limit_str)
        now = int(time.time())
        window_start = (now // window_seconds) * window_seconds
        with self._connect() as con:
            row = con.execute(
                """SELECT count FROM rate_counters
                   WHERE grant_id=? AND period_key=? AND window_start=?""",
                (grant_id, limit_str, window_start),
            ).fetchone()
        current = row["count"] if row else 0
        return current < count_limit

    def increment_rate_counter(self, grant_id: str, limit_str: str) -> None:
        """Increment the usage counter for a grant (call ONLY after a final ALLOW)."""
        _, window_seconds = self.parse_rate_limit(limit_str)
        now = int(time.time())
        window_start = (now // window_seconds) * window_seconds
        with self._connect() as con:
            con.execute(
                """INSERT INTO rate_counters (grant_id, period_key, window_start, count)
                   VALUES (?,?,?,1)
                   ON CONFLICT(grant_id, period_key) DO UPDATE SET
                     count = CASE
                       WHEN window_start = excluded.window_start THEN count + 1
                       ELSE 1
                     END,
                     window_start = excluded.window_start""",
                (grant_id, limit_str, window_start),
            )

    # ── Erasure (hard delete) ─────────────────────────────────────────────────

    def purge_for_actor(self, actor_id: str) -> int:
        """Hard-delete all grants involving an actor (L36 erasure path).

        Caller MUST emit L36 erasure audit event BEFORE calling this.
        """
        with self._connect() as con:
            cur = con.execute(
                "DELETE FROM grants WHERE grantee_actor=? OR grantor_actor=?",
                (actor_id, actor_id),
            )
        return cur.rowcount

    def purge_all(self) -> int:
        """Hard-delete all grants and rate counters (full tenant erasure)."""
        with self._connect() as con:
            cur = con.execute("DELETE FROM grants")
            con.execute("DELETE FROM rate_counters")
        return cur.rowcount
