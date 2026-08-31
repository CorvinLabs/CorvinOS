"""PostgreSQLAdapter — psycopg2-backed SQL source (ADR-0026 Section D).

FilterExpr values are NEVER interpolated — parameterized queries only.
connect() MUST only be called inside bwrap.
"""
from __future__ import annotations

from typing import Any, Iterator, Optional

try:
    import psycopg2  # type: ignore[import]
    import psycopg2.extras  # type: ignore[import]
    PSYCOPG2_AVAILABLE = True
except ImportError:
    psycopg2 = None  # type: ignore[assignment]
    PSYCOPG2_AVAILABLE = False

from ..protocol import (
    BaseDataSourceAdapter,
    ColumnInfo,
    DataCursor,
    PingResult,
    SecretEnv,
    SourceConfig,
    SourceQuery,
    SourceSchema,
    SourceSession,
    tcp_reachability_ping,
)
from ..query import safe_to_sql


class _PGSession(SourceSession):
    def __init__(self, conn: Any) -> None:
        self.conn = conn

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass


class PostgreSQLAdapter(BaseDataSourceAdapter):
    """PostgreSQL DataSource adapter using psycopg2."""

    adapter_name = "postgresql"
    display_name = "PostgreSQL"
    description = "Query tables in a PostgreSQL database."
    supported_formats = frozenset({"postgresql"})
    locality = "any"
    network_egress = "any"
    config_schema = {
        "type": "object",
        "properties": {
                    "host":     {"type": "string"},
            "port":     {"type": "integer", "default": 5432},
            "database": {"type": "string"},
            "schema":   {"type": "string", "default": "public"},
            "ssl_mode": {"type": "string", "default": "require"},
        },
    }

    supports_streaming: bool = True
    supports_pushdown: bool = True
    supports_schema_discovery: bool = True
    supports_incremental: bool = True

    def connect(self, config: SourceConfig, secret_env: SecretEnv) -> _PGSession:
        if not PSYCOPG2_AVAILABLE:
            raise ImportError("psycopg2 is not installed. Install psycopg2-binary.")
        conn = psycopg2.connect(
            host=config.raw.get("host", secret_env.get("PGHOST", "localhost")),
            port=int(config.raw.get("port", secret_env.get("PGPORT", "5432"))),
            dbname=config.raw.get("dbname", secret_env.get("PGDATABASE", "")),
            user=secret_env.require("PGUSER"),
            password=secret_env.require("PGPASSWORD"),
        )
        return _PGSession(conn)

    def discover_schema(
        self, session: _PGSession, config: SourceConfig
    ) -> SourceSchema:
        table = config.raw.get("table", "")
        schema_name = config.raw.get("schema", "public")
        cur = session.conn.cursor()
        # Parameterized query — no interpolation
        cur.execute(
            """
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position
            """,
            (schema_name, table),
        )
        columns = [
            ColumnInfo(
                name=row[0],
                dtype=row[1],
                nullable=(row[2] == "YES"),
            )
            for row in cur.fetchall()
        ]
        # Estimate row count from pg_class
        cur.execute(
            "SELECT reltuples::bigint FROM pg_class WHERE relname = %s",
            (table,),
        )
        row = cur.fetchone()
        estimated_rows = int(row[0]) if row else None
        cur.close()
        return SourceSchema(
            columns=columns,
            estimated_row_count=estimated_rows,
            source_format="postgresql",
        )

    def create_cursor(
        self,
        session: _PGSession,
        config: SourceConfig,
        query: SourceQuery,
    ) -> DataCursor:
        table = config.raw.get("table", "")
        sql, params = safe_to_sql(query, table, "psycopg2")

        # Shard pushdown: add MOD clause when n_shards > 1
        if query.n_shards > 1:
            # Parameterized: WHERE (row_number() OVER ()) %% %s = %s
            shard_clause = "(row_number() OVER ()) %% %s = %s"
            if "WHERE" in sql:
                sql = sql + f" AND {shard_clause}"
            else:
                sql = sql + f" WHERE {shard_clause}"
            params = list(params) + [query.n_shards, query.shard_index]

        cur = session.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params)
        for row in cur:
            yield dict(row)
        cur.close()

    def estimate_rows(
        self,
        session: _PGSession,
        config: SourceConfig,
        query: SourceQuery,
    ) -> Optional[int]:
        table = config.raw.get("table", "")
        cur = session.conn.cursor()
        cur.execute(
            "SELECT reltuples::bigint FROM pg_class WHERE relname = %s",
            (table,),
        )
        row = cur.fetchone()
        cur.close()
        return int(row[0]) if row else None

    def close(self, session: _PGSession) -> None:
        session.close()

    def ping(
        self,
        timeout_s: float = 5.0,
        config: Optional[SourceConfig] = None,
    ) -> PingResult:
        """Credential-free reachability probe: TCP-connect to host:port.

        Runs outside bwrap without vault secrets, so it verifies the database
        endpoint is reachable but does NOT authenticate or run SELECT 1.
        """
        raw = config.raw if config is not None else {}
        host = raw.get("host", "")
        port = raw.get("port", 5432)
        return tcp_reachability_ping(host, port, timeout_s)


__all__ = ["PostgreSQLAdapter", "PSYCOPG2_AVAILABLE"]
