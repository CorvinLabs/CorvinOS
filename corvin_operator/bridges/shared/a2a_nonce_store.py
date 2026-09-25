"""Layer 38 — ADR-0077 S-2: Persistent (SQLite) Nonce Store.

Replaces the in-memory :class:`NonceStore` in
:mod:`remote_trigger_receiver` with a crash-resilient SQLite backend.
After a process restart, already-seen nonces are still known, so a
captured-then-replayed envelope cannot succeed within its time window.

Schema
------
Single table ``nonces``:

  origin_id TEXT           -- authenticated origin that consumed the nonce
  nonce TEXT               --   PRIMARY KEY (origin_id, nonce)
  expires_at REAL          -- Unix timestamp after which the nonce is no
                           --   longer valid (and will be pruned)

A pre-2026-09-25 database (``nonce`` as the sole primary key, no
``origin_id``) is migrated in place on open; its rows keep
``origin_id = ''`` and are still consulted by the replay check until they
expire (at most one TTL).

The file lives at ``<tenant_home>/global/nonces/a2a_nonces.db`` (mode
0600). WAL mode is enabled for concurrent reader/writer safety.

Thread safety
-------------
:class:`PersistentNonceStore` uses a :class:`threading.Lock` around every
DB access. Each call opens, uses, and closes its own ``sqlite3.connect()``
call so the same store object is safe across threads without sharing a
connection object.

Pruning
-------
Expired nonces are pruned at construction time (``__init__``) and on each
``check_and_add`` call (before the existence check), so the table stays
bounded. A still-LIVE nonce is NEVER evicted: when the store holds
``_NONCE_MAX`` live rows, or one origin holds ``_PER_ORIGIN_MAX`` live rows,
``check_and_add`` refuses (returns False) instead. Evicting live rows (the
behaviour until 2026-09-25) let one authenticated peer push 10 000 nonces
through and re-open replay of every other peer's captured envelopes; a full
store is an availability problem for the flooding origin, never a replay
hole for everyone else. Same rule in the in-memory fallback.

Fallback
--------
If SQLite is unavailable or the DB file is not writable (e.g. read-only
filesystem in a minimal container), :class:`PersistentNonceStore` falls
back to the in-memory-only strategy but logs a WARNING to stderr.
:class:`remote_trigger_receiver.RemoteTriggerReceiver` should always
pass ``nonce_store=None`` (auto-select) or an explicit instance; tests
can inject ``NonceStore()`` directly for speed.

CI lint: MUST NOT ``import anthropic``.
"""
from __future__ import annotations

import collections
import os
import sqlite3
import stat
import threading
import time
from pathlib import Path

# Nonce store config — must match remote_trigger_receiver constants.
_NONCE_MAX: int = 10_000
# Base nonce TTL: 640 s + 60 s persist-buffer = 700 s total effective lifetime,
# matching the in-memory NonceStore's _NONCE_TTL_S = 700 s (LOW-IT4-01 safety
# margin intent).  The 40 s gap that existed when both values were 600/60 meant
# the persistent store had only 660 s effective TTL vs the intended 700 s
# (ADR-0099 iter-5 finding LOW-IT5-04).
_NONCE_TTL_S: float = 640.0
# One extra minute of slack to absorb clock drift between restarts.
_NONCE_PERSIST_BUFFER_S: float = 60.0
# One origin may hold at most this many live nonce rows (mirrors
# remote_trigger_receiver.NonceStore._PER_ORIGIN_MAX). At the default
# 60 rpm rate limit an origin produces ~700 nonces per TTL window.
_PER_ORIGIN_MAX: int = _NONCE_MAX // 4

# check_and_add_ex() outcomes.
OK = "ok"
REPLAY = "replay"
ORIGIN_QUOTA = "origin_quota_exceeded"
STORE_FULL = "store_full"
INVALID = "invalid"
ERROR = "error"


class PersistentNonceStore:
    """SQLite-backed nonce store; crash-resilient.

    Falls back to in-memory-only if the DB path is not writable.
    """

    _DDL = """
        CREATE TABLE IF NOT EXISTS nonces (
            origin_id  TEXT    NOT NULL DEFAULT '',
            nonce      TEXT    NOT NULL,
            expires_at REAL    NOT NULL,
            PRIMARY KEY (origin_id, nonce)
        );
        CREATE INDEX IF NOT EXISTS idx_expires ON nonces(expires_at);
        CREATE INDEX IF NOT EXISTS idx_nonce ON nonces(nonce);
    """

    def __init__(
        self,
        db_path: Path | str,
        ttl_s: float | None = None,
    ) -> None:
        self._ttl_s = ttl_s if ttl_s is not None else _NONCE_TTL_S
        self._db_path = Path(db_path)
        self._lock = threading.Lock()
        self._fallback: _InMemoryNonceStore | None = None
        # Per-instance flag so each tenant's degradation emits its own warning.
        # Using a ClassVar caused the second-tenant failure to be silently
        # swallowed when the first tenant had already set the flag.
        self._warn_emitted: bool = False

        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._init_db()
            self._prune_expired()
        except Exception as exc:  # noqa: BLE001
            if not self._warn_emitted:
                import sys
                print(
                    f"[a2a_nonce_store] WARNING: could not open SQLite nonce "
                    f"store at {self._db_path} ({exc}); falling back to "
                    f"in-memory store (not crash-resilient).",
                    file=sys.stderr,
                    flush=True,
                )
                self._warn_emitted = True
            self._fallback = _InMemoryNonceStore(ttl_s=self._ttl_s)

    # ── Public API ────────────────────────────────────────────────────

    def check_and_add(self, nonce: str, origin_id: str = "") -> bool:
        """Return True if nonce is fresh (added). False = replay, quota
        exhausted, store full, or DB error (all fail-closed).

        Args:
            nonce: Unique nonce to check/add
            origin_id: Origin identifier (REQUIRED for per-origin quota enforcement).
                      Empty origin_id is rejected to prevent quota bypass attacks.
        """
        return self.check_and_add_ex(nonce, origin_id=origin_id) == OK

    def check_and_add_ex(self, nonce: str, origin_id: str = "",
                         per_origin_max: int | None = None) -> str:
        """Like :meth:`check_and_add` but returns WHY a nonce was refused
        (``ok`` / ``replay`` / ``origin_quota_exceeded`` / ``store_full`` /
        ``invalid`` / ``error``) so the caller can audit the real reason."""
        if not origin_id or not origin_id.strip():
            # SECURITY: Empty origin_id bypasses per-origin quota enforcement.
            # Fail-closed: reject empty/whitespace-only origin_id.
            return INVALID

        if self._fallback is not None:
            return self._fallback.check_and_add_ex(nonce, origin_id=origin_id,
                                                   per_origin_max=per_origin_max)

        now = time.time()
        expires_at = now + self._ttl_s + _NONCE_PERSIST_BUFFER_S

        with self._lock:
            try:
                con = self._open()
                try:
                    # BEGIN IMMEDIATE acquires a reserved lock immediately,
                    # preventing concurrent writers across processes from
                    # racing between the existence check and the INSERT
                    # (CRIT-03: SQLite UNIQUE alone is insufficient without
                    # an exclusive transaction boundary).
                    con.execute("BEGIN IMMEDIATE")
                    # Prune expired nonces inside the transaction so the
                    # pruning and the insert are atomic — avoids a race
                    # where another process prunes+reuses the same nonce.
                    con.execute("DELETE FROM nonces WHERE expires_at <= ?", (now,))
                    # Legacy rows (pre-migration) carry origin_id '' and are
                    # still honoured until they expire.
                    row = con.execute(
                        "SELECT 1 FROM nonces WHERE nonce = ? "
                        "AND origin_id IN (?, '')", (nonce, origin_id),
                    ).fetchone()
                    if row is not None:
                        # Nonce already present → replay.
                        con.execute("ROLLBACK")
                        return REPLAY
                    per_origin = con.execute(
                        "SELECT COUNT(*) FROM nonces WHERE origin_id = ?",
                        (origin_id,),
                    ).fetchone()[0]
                    if per_origin >= (per_origin_max or _PER_ORIGIN_MAX):
                        con.execute("ROLLBACK")
                        return ORIGIN_QUOTA
                    total = con.execute(
                        "SELECT COUNT(*) FROM nonces"
                    ).fetchone()[0]
                    if total >= _NONCE_MAX:
                        # Every remaining row is live (expired ones were just
                        # pruned). Refuse — evicting a live row would re-open
                        # replay of that envelope.
                        con.execute("ROLLBACK")
                        return STORE_FULL
                    con.execute(
                        "INSERT INTO nonces (origin_id, nonce, expires_at) "
                        "VALUES (?, ?, ?)",
                        (origin_id, nonce, expires_at),
                    )
                    con.execute("COMMIT")
                    return OK
                except Exception:  # noqa: BLE001
                    try:
                        con.execute("ROLLBACK")
                    except Exception:  # noqa: BLE001
                        pass
                    raise
                finally:
                    con.close()
            except Exception:  # noqa: BLE001
                # DB error after init — degrade to reject (conservative).
                return ERROR

    def remove(self, nonce: str, origin_id: str | None = None) -> None:
        """Remove a nonce — used to roll back after a failed audit-first write
        (or a post-consumption rate-limit refusal).

        ``origin_id`` scopes the delete to that origin's row; ``None`` (legacy
        callers) deletes the nonce for every origin.

        AUDIT-FIRST INVARIANT: If this fails (DB error), the nonce stays consumed
        and a retry with the same nonce will be rejected as replay until TTL expires.
        This is safe but should be logged for operational visibility.
        """
        if self._fallback is not None:
            self._fallback.remove(nonce, origin_id=origin_id)
            return
        with self._lock:
            try:
                con = self._open()
                try:
                    con.execute("BEGIN IMMEDIATE")
                    if origin_id is None:
                        con.execute("DELETE FROM nonces WHERE nonce = ?", (nonce,))
                    else:
                        con.execute(
                            "DELETE FROM nonces WHERE nonce = ? AND origin_id = ?",
                            (nonce, origin_id),
                        )
                    con.execute("COMMIT")
                except Exception as exc:  # noqa: BLE001
                    try:
                        con.execute("ROLLBACK")
                    except Exception:  # noqa: BLE001
                        pass
                    # Log failure so operators can see DB issues (don't silent-swallow)
                    import sys
                    import logging
                    log = logging.getLogger(__name__)
                    log.warning(
                        "[a2a_nonce_store] Failed to remove nonce during rollback "
                        "(DB error: %s) — retry with same nonce will fail as replay "
                        "until TTL expires (~700s). Check database state.",
                        exc
                    )
                finally:
                    con.close()
            except Exception as exc:  # noqa: BLE001
                # Final fallback: log but don't crash
                import sys
                import logging
                log = logging.getLogger(__name__)
                log.error("[a2a_nonce_store] Unexpected error in nonce.remove(): %s", exc)

    # ── Internals ─────────────────────────────────────────────────────

    def _open(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self._db_path), timeout=5.0)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        # isolation_level=None → autocommit mode. We issue explicit
        # BEGIN IMMEDIATE ... COMMIT/ROLLBACK in check_and_add() to
        # ensure atomicity across processes (WAL + UNIQUE PRIMARY KEY
        # alone cannot prevent a SELECT-then-INSERT race in autocommit).
        con.isolation_level = None
        return con

    def _init_db(self) -> None:
        with self._lock:
            con = self._open()
            try:
                self._migrate_legacy_schema(con)
                # executescript commits any pending transaction and then
                # runs the DDL in its own implicit transaction.
                con.executescript(self._DDL)
            finally:
                con.close()
        # Mode 0600 — no group/other read.
        try:
            os.chmod(self._db_path, 0o600)
        except OSError:
            pass

    @staticmethod
    def _migrate_legacy_schema(con: sqlite3.Connection) -> None:
        """Rebuild a pre-2026-09-25 ``nonces`` table (``nonce`` sole PK, no
        ``origin_id``) into the (origin_id, nonce) schema. Idempotent and
        safe against a concurrent process doing the same (BEGIN IMMEDIATE +
        re-check inside the transaction). Legacy rows keep ``origin_id=''``."""
        cols = [r[1] for r in con.execute("PRAGMA table_info(nonces)").fetchall()]
        if not cols or "origin_id" in cols:
            return
        con.execute("BEGIN IMMEDIATE")
        try:
            cols = [r[1] for r in con.execute("PRAGMA table_info(nonces)").fetchall()]
            if cols and "origin_id" not in cols:
                con.execute(
                    "CREATE TABLE nonces_v2 ("
                    " origin_id TEXT NOT NULL DEFAULT '',"
                    " nonce TEXT NOT NULL,"
                    " expires_at REAL NOT NULL,"
                    " PRIMARY KEY (origin_id, nonce))"
                )
                con.execute(
                    "INSERT OR IGNORE INTO nonces_v2 (origin_id, nonce, expires_at) "
                    "SELECT '', nonce, expires_at FROM nonces"
                )
                con.execute("DROP TABLE nonces")
                con.execute("ALTER TABLE nonces_v2 RENAME TO nonces")
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise

    def _prune_expired(self) -> None:
        now = time.time()
        with self._lock:
            try:
                con = self._open()
                try:
                    con.execute("BEGIN IMMEDIATE")
                    con.execute("DELETE FROM nonces WHERE expires_at <= ?", (now,))
                    con.execute("COMMIT")
                except Exception:  # noqa: BLE001
                    try:
                        con.execute("ROLLBACK")
                    except Exception:  # noqa: BLE001
                        pass
                finally:
                    con.close()
            except Exception:  # noqa: BLE001
                pass


class _InMemoryNonceStore:
    """In-memory fallback with the SAME admission rules as the SQLite store:
    keyed (origin_id, nonce), per-origin live-slot cap, and a full store
    refuses instead of evicting a still-live nonce (evicting the oldest live
    entry — the pre-2026-09-25 behaviour — re-opened replay of it)."""

    def __init__(self, ttl_s: float | None = None) -> None:
        self._ttl_s = ttl_s if ttl_s is not None else _NONCE_TTL_S
        # (origin_id, nonce) -> expires_at, insertion-ordered == expiry-ordered
        # (constant TTL), so expiry pops from the front.
        self._store: collections.OrderedDict[tuple[str, str], float] = (
            collections.OrderedDict()
        )
        self._origin_count: dict[str, int] = {}
        self._lock = threading.Lock()

    def _expire(self, now: float) -> None:
        while self._store:
            key, exp = next(iter(self._store.items()))
            if exp > now:
                break
            del self._store[key]
            left = self._origin_count.get(key[0], 0) - 1
            if left > 0:
                self._origin_count[key[0]] = left
            else:
                self._origin_count.pop(key[0], None)

    def check_and_add(self, nonce: str, origin_id: str = "") -> bool:
        return self.check_and_add_ex(nonce, origin_id=origin_id) == OK

    def check_and_add_ex(self, nonce: str, origin_id: str = "",
                         per_origin_max: int | None = None) -> str:
        if not origin_id or not origin_id.strip():
            return INVALID
        now = time.time()
        key = (origin_id, nonce)
        with self._lock:
            self._expire(now)
            if key in self._store:
                return REPLAY
            if self._origin_count.get(origin_id, 0) >= (per_origin_max or _PER_ORIGIN_MAX):
                return ORIGIN_QUOTA
            if len(self._store) >= _NONCE_MAX:
                return STORE_FULL
            self._store[key] = now + self._ttl_s
            self._origin_count[origin_id] = self._origin_count.get(origin_id, 0) + 1
            return OK

    def remove(self, nonce: str, origin_id: str | None = None) -> None:
        with self._lock:
            keys = (
                [(origin_id, nonce)] if origin_id is not None
                else [k for k in self._store if k[1] == nonce]
            )
            for key in keys:
                if self._store.pop(key, None) is not None:
                    left = self._origin_count.get(key[0], 0) - 1
                    if left > 0:
                        self._origin_count[key[0]] = left
                    else:
                        self._origin_count.pop(key[0], None)


def default_nonce_store(tenant_home: Path | str | None = None) -> PersistentNonceStore:
    """Build the production-default nonce store for a given tenant home.

    Falls back to ``~/.corvin`` when ``tenant_home`` is None.
    """
    if tenant_home is None:
        tenant_home = Path(os.environ.get("CORVIN_HOME", Path.home() / ".corvin"))
    db_path = Path(tenant_home) / "global" / "nonces" / "a2a_nonces.db"
    return PersistentNonceStore(db_path)


__all__ = [
    "PersistentNonceStore",
    "default_nonce_store",
]
