"""SQLite FTS5 Model Registry (ADR-0026).

Schema:
  (run_id, backend, primary_metric, metric_value, artifact_path_hash, tags, created_at)

FTS5 index over tags for efficient text search.

Security rule: artifact_path NEVER stored — only artifact_path_hash (sha256[:16]).
This enforces GDPR Art. 32 (data minimisation for model artefacts).

MUST NOT import anthropic / openai / google.cloud.aiplatform.
"""
from __future__ import annotations

import dataclasses
import hashlib
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterator, Optional

log = logging.getLogger(__name__)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS model_runs (
    run_id              TEXT NOT NULL,
    backend             TEXT NOT NULL,
    primary_metric      TEXT NOT NULL,
    metric_value        REAL NOT NULL,
    artifact_path_hash  TEXT NOT NULL,
    tags                TEXT NOT NULL DEFAULT '',
    created_at          REAL NOT NULL,
    PRIMARY KEY (run_id)
);

CREATE VIRTUAL TABLE IF NOT EXISTS model_runs_fts USING fts5(
    run_id,
    tags,
    content='model_runs',
    content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS model_runs_ai
AFTER INSERT ON model_runs BEGIN
    INSERT INTO model_runs_fts(rowid, run_id, tags)
    VALUES (new.rowid, new.run_id, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS model_runs_ad
AFTER DELETE ON model_runs BEGIN
    INSERT INTO model_runs_fts(model_runs_fts, rowid, run_id, tags)
    VALUES ('delete', old.rowid, old.run_id, old.tags);
END;

CREATE TRIGGER IF NOT EXISTS model_runs_au
AFTER UPDATE ON model_runs BEGIN
    INSERT INTO model_runs_fts(model_runs_fts, rowid, run_id, tags)
    VALUES ('delete', old.rowid, old.run_id, old.tags);
    INSERT INTO model_runs_fts(rowid, run_id, tags)
    VALUES (new.rowid, new.run_id, new.tags);
END;
"""


@dataclasses.dataclass
class ModelRegistryEntry:
    """One registered model run."""
    run_id: str
    backend: str
    primary_metric: str
    metric_value: float
    artifact_path_hash: str
    tags: list[str]
    created_at: float


class ModelRegistry:
    """SQLite FTS5 model registry — register() / query() / list().

    Pass db_path=":memory:" for in-process tests.
    """

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._db_path = str(db_path)
        self._conn: Optional[sqlite3.Connection] = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                self._db_path,
                check_same_thread=False,
            )
            self._conn.row_factory = sqlite3.Row
            self._ensure_schema()
        return self._conn

    def _ensure_schema(self) -> None:
        conn = self._conn
        assert conn is not None
        # FTS5 tables must be created in separate statements
        for stmt in _SCHEMA_SQL.split(";"):
            stmt = stmt.strip()
            if stmt:
                try:
                    conn.execute(stmt)
                except sqlite3.OperationalError as exc:
                    log.debug("schema: %s — %s", stmt[:60], exc)
        conn.commit()

    def register(
        self,
        *,
        run_id: str,
        backend: str,
        primary_metric: str,
        metric_value: float,
        artifact_path_hash: str,
        tags: list[str] | None = None,
        created_at: Optional[float] = None,
    ) -> None:
        """Register (or overwrite) a model run.

        artifact_path MUST NOT be passed — only artifact_path_hash.
        Duplicate run_id → UPDATE (overwrite).
        """
        if not run_id:
            raise ValueError("run_id must not be empty")
        if not artifact_path_hash:
            raise ValueError("artifact_path_hash must not be empty")
        tags_str = " ".join(tags or [])
        now = created_at if created_at is not None else time.time()
        conn = self._get_conn()
        # Check if exists (for update vs insert)
        existing = conn.execute(
            "SELECT rowid FROM model_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE model_runs
                   SET backend=?, primary_metric=?, metric_value=?,
                       artifact_path_hash=?, tags=?, created_at=?
                 WHERE run_id=?
                """,
                (backend, primary_metric, float(metric_value),
                 artifact_path_hash, tags_str, now, run_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO model_runs
                    (run_id, backend, primary_metric, metric_value,
                     artifact_path_hash, tags, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, backend, primary_metric, float(metric_value),
                 artifact_path_hash, tags_str, now),
            )
        conn.commit()

    def query(self, *, tags_fts: str) -> list[ModelRegistryEntry]:
        """Full-text search over tags.

        Returns matching entries ordered by metric_value ASC.
        """
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """
                SELECT r.run_id, r.backend, r.primary_metric, r.metric_value,
                       r.artifact_path_hash, r.tags, r.created_at
                  FROM model_runs r
                  JOIN model_runs_fts fts ON r.rowid = fts.rowid
                 WHERE model_runs_fts MATCH ?
                 ORDER BY r.metric_value ASC
                """,
                (tags_fts,),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            log.warning("FTS5 query failed: %s", exc)
            return []
        return [_row_to_entry(r) for r in rows]

    def list(
        self,
        *,
        backend: Optional[str] = None,
        order_by: str = "created_at",
        desc: bool = True,
    ) -> list[ModelRegistryEntry]:
        """Return all registered model runs, optionally filtered by backend."""
        conn = self._get_conn()
        direction = "DESC" if desc else "ASC"
        safe_cols = {"created_at", "metric_value", "run_id", "backend"}
        if order_by not in safe_cols:
            order_by = "created_at"
        if backend is not None:
            rows = conn.execute(
                f"SELECT * FROM model_runs WHERE backend=? ORDER BY {order_by} {direction}",
                (backend,),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT * FROM model_runs ORDER BY {order_by} {direction}"
            ).fetchall()
        return [_row_to_entry(r) for r in rows]

    def get(self, run_id: str) -> Optional[ModelRegistryEntry]:
        """Return a single entry by run_id, or None."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM model_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        return _row_to_entry(row) if row else None

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


def _row_to_entry(row: sqlite3.Row) -> ModelRegistryEntry:
    tags_str = row["tags"] or ""
    tags = tags_str.split() if tags_str.strip() else []
    return ModelRegistryEntry(
        run_id=row["run_id"],
        backend=row["backend"],
        primary_metric=row["primary_metric"],
        metric_value=float(row["metric_value"]),
        artifact_path_hash=row["artifact_path_hash"],
        tags=tags,
        created_at=float(row["created_at"]),
    )


__all__ = ["ModelRegistry", "ModelRegistryEntry"]
