"""SnowflakeAdapter — parameterized Snowflake SQL (ADR-0026 Section D).

Shard: WHERE MOD(SEQ8(), n_shards) = shard_index — parameterized.
connect() MUST only be called inside bwrap.
"""
from __future__ import annotations

from typing import Any, Iterator, Optional

try:
    import snowflake.connector  # type: ignore[import]
    import snowflake  # noqa: F811 — re-import to have module-level name for mocking
    SF_AVAILABLE = True
except ImportError:
    snowflake = None  # type: ignore[assignment]
    SF_AVAILABLE = False

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


class _SFSession(SourceSession):
    def __init__(self, conn: Any) -> None:
        self.conn = conn

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass


class SnowflakeAdapter(BaseDataSourceAdapter):
    """Snowflake DataSource adapter."""

    adapter_name = "snowflake"
    display_name = "Snowflake"
    description = "Query tables in a Snowflake data warehouse."
    supported_formats = frozenset({"snowflake"})
    locality = "any"
    network_egress = "any"
    config_schema = {
        "type": "object",
        "properties": {
                    "account":   {"type": "string"},
            "database":  {"type": "string"},
            "schema":    {"type": "string", "default": "PUBLIC"},
            "warehouse": {"type": "string"},
            "role":      {"type": ["string", "null"], "default": None},
        },
    }

    supports_streaming: bool = True
    supports_pushdown: bool = True
    supports_schema_discovery: bool = True
    supports_incremental: bool = True

    def connect(self, config: SourceConfig, secret_env: SecretEnv) -> _SFSession:
        if not SF_AVAILABLE:
            raise ImportError("snowflake-connector-python is not installed.")
        conn = snowflake.connector.connect(
            account=config.raw.get("account", secret_env.get("SNOWFLAKE_ACCOUNT", "")),
            warehouse=config.raw.get("warehouse", secret_env.get("SNOWFLAKE_WAREHOUSE", "")),
            database=config.raw.get("database", secret_env.get("SNOWFLAKE_DATABASE", "")),
            schema=config.raw.get("schema", secret_env.get("SNOWFLAKE_SCHEMA", "PUBLIC")),
            user=secret_env.require("SNOWFLAKE_USER"),
            password=secret_env.require("SNOWFLAKE_PASSWORD"),
        )
        return _SFSession(conn)

    def discover_schema(
        self, session: _SFSession, config: SourceConfig
    ) -> SourceSchema:
        table = config.raw.get("table", "")
        cur = session.conn.cursor()
        # Parameterized — no interpolation
        cur.execute("DESCRIBE TABLE IDENTIFIER(%s)", (table,))
        columns = [
            ColumnInfo(
                name=row[0],
                dtype=row[1],
                nullable=("Y" in str(row[3])),
            )
            for row in cur.fetchall()
        ]
        # Row count estimate
        cur.execute("SELECT COUNT(*) FROM IDENTIFIER(%s)", (table,))
        row = cur.fetchone()
        estimated_rows = int(row[0]) if row else None
        cur.close()
        return SourceSchema(
            columns=columns,
            estimated_row_count=estimated_rows,
            source_format="snowflake",
        )

    def create_cursor(
        self,
        session: _SFSession,
        config: SourceConfig,
        query: SourceQuery,
    ) -> DataCursor:
        table = config.raw.get("table", "")
        sql, params = safe_to_sql(query, table, "snowflake")

        # Shard pushdown: WHERE MOD(SEQ8(), n_shards) = shard_index (parameterized)
        if query.n_shards > 1:
            shard_clause = "MOD(SEQ8(), %s) = %s"
            if "WHERE" in sql:
                sql = sql + f" AND {shard_clause}"
            else:
                sql = sql + f" WHERE {shard_clause}"
            params = list(params) + [query.n_shards, query.shard_index]

        cur = session.conn.cursor(snowflake.connector.DictCursor)
        cur.execute(sql, params)
        for row in cur.fetchall():
            yield dict(row)
        cur.close()

    def estimate_rows(
        self,
        session: _SFSession,
        config: SourceConfig,
        query: SourceQuery,
    ) -> Optional[int]:
        table = config.raw.get("table", "")
        cur = session.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM IDENTIFIER(%s)", (table,))
        row = cur.fetchone()
        cur.close()
        return int(row[0]) if row else None

    def close(self, session: _SFSession) -> None:
        session.close()

    def ping(
        self,
        timeout_s: float = 5.0,
        config: Optional[SourceConfig] = None,
    ) -> PingResult:
        """Credential-free reachability probe: TCP-connect to the account host.

        Snowflake accounts resolve to ``<account>.snowflakecomputing.com:443``.
        Verifies the endpoint is reachable; does NOT authenticate. An explicit
        ``host`` override in config takes precedence (for private endpoints).
        """
        raw = config.raw if config is not None else {}
        host = raw.get("host", "")
        if not host:
            account = raw.get("account", "")
            if account:
                host = f"{account}.snowflakecomputing.com"
        port = raw.get("port", 443)
        return tcp_reachability_ping(host, port, timeout_s)


__all__ = ["SnowflakeAdapter", "SF_AVAILABLE"]
