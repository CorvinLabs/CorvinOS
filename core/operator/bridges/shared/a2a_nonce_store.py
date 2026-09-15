"""Layer 38 — ADR-0077 S-2: Persistent (SQLite) Nonce Store.

Replaces the in-memory :class:`NonceStore` in
:mod:`remote_trigger_receiver` with a crash-resilient SQLite backend.
After a process restart, already-seen nonces are still known, so a
captured-then-replayed envelope cannot succeed within its time window.

Schema
------
Single table ``nonces``:

  nonce TEXT PRIMARY KEY
  expires_at REAL          -- Unix timestamp after which the nonce is no
                           --   longer valid (and will be pruned)

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
bounded. LRU eviction at 10 000 rows applies in addition to TTL-based
pruning, matching the in-memory store's behaviour.

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


class PersistentNonceStore:
    """SQLite-backed nonce store; crash-resilient.

    Falls back to in-memory-only if the DB path is not writable.
    """

    _DDL = """
        CREATE TABLE IF NOT EXISTS nonces (
            nonce      TEXT    NOT NULL PRIMARY KEY,
            expires_at REAL    NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_expires ON nonces(expires_at);
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
        """Return True if nonce is fresh (added). False = replay."""
        if self._fallback is not None:
            return self._fallback.check_and_add(nonce, origin_id=origin_id)

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
                    row = con.execute(
                        "SELECT 1 FROM nonces WHERE nonce = ?", (nonce,)
                    ).fetchone()
                    if row is not None:
                        # Nonce already present → replay.
                        con.execute("ROLLBACK")
                        return False
                    con.execute(
                        "INSERT INTO nonces (nonce, expires_at) VALUES (?, ?)",
                        (nonce, expires_at),
                    )
                    # LRU eviction: drop oldest if over cap.
                    count = con.execute(
                        "SELECT COUNT(*) FROM nonces"
                    ).fetchone()[0]
                    if count > _NONCE_MAX:
                        overshoot = count - _NONCE_MAX
                        con.execute(
                            "DELETE FROM nonces WHERE nonce IN "
                            "(SELECT nonce FROM nonces ORDER BY expires_at ASC LIMIT ?)",
                            (overshoot,),
                        )
                    con.execute("COMMIT")
                    return True
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
                return False

    def remove(self, nonce: str) -> None:
        """Remove a nonce — used to roll back after a failed audit-first write."""
        if self._fallback is not None:
            self._fallback.remove(nonce)
            return
        with self._lock:
            try:
                con = self._open()
                try:
                    con.execute("BEGIN IMMEDIATE")
                    con.execute("DELETE FROM nonces WHERE nonce = ?", (nonce,))
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
    """Thin in-memory fallback (matches original NonceStore API)."""

    def __init__(self, ttl_s: float | None = None) -> None:
        self._ttl_s = ttl_s if ttl_s is not None else _NONCE_TTL_S
        self._store: collections.OrderedDict[str, float] = collections.OrderedDict()
        self._lock = threading.Lock()

    def check_and_add(self, nonce: str, origin_id: str = "") -> bool:
        now = time.time()
        with self._lock:
            expired = [k for k, exp in self._store.items() if exp <= now]
            for k in expired:
                del self._store[k]
            if nonce in self._store:
                return False
            while len(self._store) >= _NONCE_MAX:
                self._store.popitem(last=False)
            self._store[nonce] = now + self._ttl_s
            return True

    def remove(self, nonce: str) -> None:
        with self._lock:
            self._store.pop(nonce, None)


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
