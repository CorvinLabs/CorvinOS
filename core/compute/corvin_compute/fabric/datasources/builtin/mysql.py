"""MySQLAdapter — PyMySQL-backed SQL source (ADR-0026 Section D).

FilterExpr values are NEVER interpolated — parameterized queries only.
connect() MUST only be called inside bwrap.
"""
from __future__ import annotations

from typing import Any, Iterator, Optional

try:
    import pymysql  # type: ignore[import]
    import pymysql.cursors  # type: ignore[import]
    PYMYSQL_AVAILABLE = True
except ImportError:
    pymysql = None  # type: ignore[assignment]
    PYMYSQL_AVAILABLE = False

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


class _MySQLSession(SourceSession):
    def __init__(self, conn: Any) -> None:
        self.conn = conn

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass


class MySQLAdapter(BaseDataSourceAdapter):
    """MySQL DataSource adapter using PyMySQL."""

    adapter_name = "mysql"
    display_name = "MySQL"
    description = "Query tables in a MySQL or MariaDB database."
    supported_formats = frozenset({"mysql"})
    locality = "any"
    network_egress = "any"
    config_schema = {
        "type": "object",
        "properties": {
                    "host":     {"type": "string"},
            "port":     {"type": "integer", "default": 3306},
            "database": {"type": "string"},
            "ssl":      {"type": "boolean", "default": True},
        },
    }

    supports_streaming: bool = True
    supports_pushdown: bool = True
    supports_schema_discovery: bool = True
    supports_incremental: bool = True

    def connect(self, config: SourceConfig, secret_env: SecretEnv) -> _MySQLSession:
        if not PYMYSQL_AVAILABLE:
            raise ImportError("pymysql is not installed. Install pymysql.")
        conn = pymysql.connect(
            host=config.raw.get("host", secret_env.get("MYSQL_HOST", "localhost")),
            port=int(config.raw.get("port", secret_env.get("MYSQL_PORT", "3306"))),
            database=config.raw.get("database", secret_env.get("MYSQL_DATABASE", "")),
            user=secret_env.require("MYSQL_USER"),
            password=secret_env.require("MYSQL_PASSWORD"),
            cursorclass=pymysql.cursors.DictCursor,
        )
        return _MySQLSession(conn)

    def discover_schema(
        self, session: _MySQLSession, config: SourceConfig
    ) -> SourceSchema:
        table = config.raw.get("table", "")
        database = config.raw.get("database", "")
        cur = session.conn.cursor()
        # Parameterized — no interpolation
        cur.execute(
            """
            SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
            ORDER BY ORDINAL_POSITION
            """,
            (database, table),
        )
        columns = [
            ColumnInfo(
                name=row["COLUMN_NAME"],
                dtype=row["DATA_TYPE"],
                nullable=(row["IS_NULLABLE"] == "YES"),
            )
            for row in cur.fetchall()
        ]
        # Row count estimate
        cur.execute(
            "SELECT TABLE_ROWS FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = %s",
            (table,),
        )
        row = cur.fetchone()
        estimated_rows = int(row["TABLE_ROWS"]) if row and row["TABLE_ROWS"] else None
        cur.close()
        return SourceSchema(
            columns=columns,
            estimated_row_count=estimated_rows,
            source_format="mysql",
        )

    def create_cursor(
        self,
        session: _MySQLSession,
        config: SourceConfig,
        query: SourceQuery,
    ) -> DataCursor:
        table = config.raw.get("table", "")
        sql, params = safe_to_sql(query, table, "mysql")

        # Shard pushdown
        if query.n_shards > 1:
            shard_clause = "MOD(id, %s) = %s"
            if "WHERE" in sql:
                sql = sql + f" AND {shard_clause}"
            else:
                sql = sql + f" WHERE {shard_clause}"
            params = list(params) + [query.n_shards, query.shard_index]

        cur = session.conn.cursor()
        cur.execute(sql, params)
        for row in cur.fetchall():
            yield dict(row)
        cur.close()

    def estimate_rows(
        self,
        session: _MySQLSession,
        config: SourceConfig,
        query: SourceQuery,
    ) -> Optional[int]:
        table = config.raw.get("table", "")
        cur = session.conn.cursor()
        cur.execute(
            "SELECT TABLE_ROWS FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = %s",
            (table,),
        )
        row = cur.fetchone()
        cur.close()
        return int(row["TABLE_ROWS"]) if row and row.get("TABLE_ROWS") else None

    def close(self, session: _MySQLSession) -> None:
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
        port = raw.get("port", 3306)
        return tcp_reachability_ping(host, port, timeout_s)


__all__ = ["MySQLAdapter", "PYMYSQL_AVAILABLE"]
