"""Factory for Token Metrics DB Backend Selection (Phase 2.K=2).

Resolves appropriate database backend based on environment variables,
config, and defaults. Supports: SQLite (default), PostgreSQL (future).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from core.learning.token_metrics_db import SqliteMetricsDB, TokenMetricsDB


def create_metrics_db(
    tenant_id: str = "default",
    config: Optional[dict] = None,
) -> TokenMetricsDB:
    """Create and return a TokenMetricsDB backend instance.

    Selection precedence:
    1. Environment variable: CORVIN_METRICS_DB_URI
    2. Config dict key: metrics_db_uri
    3. Default: ~/.corvin/tenants/{tenant_id}/global/metrics.db (SQLite)

    Args:
        tenant_id: Tenant identifier (used for default path)
        config: Optional config dict with metrics_db_uri key

    Returns:
        TokenMetricsDB instance (SqliteMetricsDB or PostgresMetricsDB)

    Raises:
        ValueError: If URI format is invalid or backend is unsupported
    """
    # 1. Check environment variable
    db_uri = os.getenv("CORVIN_METRICS_DB_URI")

    # 2. Check config dict
    if not db_uri and config:
        db_uri = config.get("metrics_db_uri")

    # 3. Default: SQLite in tenant home
    if not db_uri:
        try:
            from core.corvin_core import tenant_home
            tenant_path = tenant_home(tenant_id)
        except Exception:
            # Fallback if corvin_core not available (tests, etc)
            home = Path.home()
            tenant_path = home / ".corvin" / "tenants" / tenant_id

        global_dir = tenant_path / "global"
        global_dir.mkdir(parents=True, exist_ok=True)
        db_path = global_dir / "metrics.db"
        db_uri = f"sqlite:///{db_path}"

    # 4. Instantiate appropriate backend
    if "postgresql" in db_uri.lower() or "postgres" in db_uri.lower():
        raise ValueError(
            "PostgreSQL backend planned for Phase 2.5. "
            f"Got: {db_uri}. Use 'sqlite:///' for now."
        )
    elif "sqlite" in db_uri.lower() or db_uri.startswith("sqlite"):
        return SqliteMetricsDB(_sqlite_uri_to_path(db_uri))
    else:
        raise ValueError(
            f"Unsupported database URI: {db_uri}. "
            "Supported: sqlite:///, postgresql://"
        )


def _sqlite_uri_to_path(db_uri: str) -> str:
    """Turn a ``sqlite:///...`` URI into the plain filesystem path.

    TokenMetricsDB takes a PATH, not a URI -- it does ``Path(db_path)`` and
    hands the result to ``sqlite3.connect``. Passing the raw URI through made
    it create a literal directory named ``sqlite:`` under the current working
    directory and put the database inside it, so every metrics write landed
    somewhere nothing else reads.
    """
    path = db_uri.split("://", 1)[1] if "://" in db_uri else db_uri
    # sqlite:///:memory: and sqlite://:memory: both mean the in-memory DB.
    if path.lstrip("/") == ":memory:":
        return ":memory:"
    # The URI is built as f"sqlite:///{absolute_path}", which yields four
    # slashes and therefore a leading "//" after the split. POSIX keeps a
    # doubled leading slash (os.path.normpath does too, by specification), so
    # collapse it explicitly -- otherwise every logged path looks wrong.
    while path.startswith("//"):
        path = path[1:]
    return path
