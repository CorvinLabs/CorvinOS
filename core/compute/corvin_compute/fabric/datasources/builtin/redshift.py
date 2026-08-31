"""RedshiftAdapter — psycopg2-backed Redshift SQL source (ADR-0026 Section D).

Similar to PostgreSQLAdapter but targets Amazon Redshift.
FilterExpr values NEVER interpolated — parameterized queries only.
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


class _RSSession(SourceSession):
    def __init__(self, conn: Any) -> None:
        self.conn = conn

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass


class RedshiftAdapter(BaseDataSourceAdapter):
    """Amazon Redshift DataSource adapter using psycopg2."""

    adapter_name = "redshift"
    display_name = "Amazon Redshift"
    description = "Query tables in an Amazon Redshift cluster."
    supported_formats = frozenset({"redshift"})
    locality = "any"
    network_egress = "any"
    config_schema = {
        "type": "object",
        "properties": {
                    "host":     {"type": "string"},
            "port":     {"type": "integer", "default": 5439},
            "database": {"type": "string"},
            "schema":   {"type": "string", "default": "public"},
        },
    }

    supports_streaming: bool = True
    supports_pushdown: bool = True
    supports_schema_discovery: bool = True
    supports_incremental: bool = True

    def connect(self, config: SourceConfig, secret_env: SecretEnv) -> _RSSession:
        if not PSYCOPG2_AVAILABLE:
            raise ImportError("psycopg2 is not installed.")
        conn = psycopg2.connect(
            host=config.raw.get("host", secret_env.get("REDSHIFT_HOST", "")),
            port=int(config.raw.get("port", secret_env.get("REDSHIFT_PORT", "5439"))),
            dbname=config.raw.get("dbname", secret_env.get("REDSHIFT_DATABASE", "")),
            user=secret_env.require("REDSHIFT_USER"),
            password=secret_env.require("REDSHIFT_PASSWORD"),
            sslmode=config.raw.get("sslmode", "require"),
        )
        return _RSSession(conn)

    def discover_schema(
        self, session: _RSSession, config: SourceConfig
    ) -> SourceSchema:
        table = config.raw.get("table", "")
        schema_name = config.raw.get("schema", "public")
        cur = session.conn.cursor()
        # Parameterized — no interpolation
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
        # Redshift-specific row count
        cur.execute(
            """
            SELECT tbl_rows FROM svv_table_info
            WHERE schema = %s AND "table" = %s
            """,
            (schema_name, table),
        )
        row = cur.fetchone()
        estimated_rows = int(row[0]) if row and row[0] else None
        cur.close()
        return SourceSchema(
            columns=columns,
            estimated_row_count=estimated_rows,
            source_format="redshift",
        )

    def create_cursor(
        self,
        session: _RSSession,
        config: SourceConfig,
        query: SourceQuery,
    ) -> DataCursor:
        table = config.raw.get("table", "")
        sql, params = safe_to_sql(query, table, "psycopg2")

        # Shard pushdown
        if query.n_shards > 1:
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
        session: _RSSession,
        config: SourceConfig,
        query: SourceQuery,
    ) -> Optional[int]:
        table = config.raw.get("table", "")
        schema_name = config.raw.get("schema", "public")
        cur = session.conn.cursor()
        cur.execute(
            "SELECT tbl_rows FROM svv_table_info WHERE schema = %s AND \"table\" = %s",
            (schema_name, table),
        )
        row = cur.fetchone()
        cur.close()
        return int(row[0]) if row and row[0] else None

    def close(self, session: _RSSession) -> None:
        session.close()

    def ping(
        self,
        timeout_s: float = 5.0,
        config: Optional[SourceConfig] = None,
    ) -> PingResult:
        """Credential-free reachability probe: TCP-connect to host:port.

        Runs outside bwrap without vault secrets, so it verifies the cluster
        endpoint is reachable but does NOT authenticate or run SELECT 1.
        """
        raw = config.raw if config is not None else {}
        host = raw.get("host", "")
        port = raw.get("port", 5439)
        return tcp_reachability_ping(host, port, timeout_s)


__all__ = ["RedshiftAdapter", "PSYCOPG2_AVAILABLE"]
