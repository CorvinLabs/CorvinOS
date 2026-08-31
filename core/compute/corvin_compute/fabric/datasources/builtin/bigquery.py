"""BigQueryAdapter — parameterized BQ queries (ADR-0026 Section D).

Shard: WHERE MOD(FARM_FINGERPRINT(CAST(pk AS STRING)), n_shards) = shard_index
Uses BQ named/positional params — NOT string interpolation.
Only imports google.cloud.bigquery — NOT google.cloud.aiplatform.
connect() MUST only be called inside bwrap.
"""
from __future__ import annotations

from typing import Any, Iterator, Optional

try:
    from google.cloud import bigquery  # type: ignore[import]
    BQ_AVAILABLE = True
except ImportError:
    bigquery = None  # type: ignore[assignment]
    BQ_AVAILABLE = False

from ..protocol import (
    BaseDataSourceAdapter,
    ColumnInfo,
    DataCursor,
    SecretEnv,
    SourceConfig,
    SourceQuery,
    SourceSchema,
    SourceSession,
)
from ..query import _validate_identifier, _validate_order_by, safe_to_sql


class _BQSession(SourceSession):
    def __init__(self, client: Any, dataset: str) -> None:
        self.client = client
        self.dataset = dataset

    def close(self) -> None:
        pass


class BigQueryAdapter(BaseDataSourceAdapter):
    """BigQuery DataSource adapter."""

    adapter_name = "bigquery"
    display_name = "Google BigQuery"
    description = "Query tables in Google BigQuery."
    supported_formats = frozenset({"bigquery"})
    locality = "any"
    network_egress = "any"
    config_schema = {
        "type": "object",
        "properties": {
                    "project": {"type": "string"},
            "dataset": {"type": "string"},
            "table":   {"type": "string"},
            "location": {"type": "string", "default": "US"},
        },
    }

    supports_streaming: bool = True
    supports_pushdown: bool = True
    supports_schema_discovery: bool = True
    supports_incremental: bool = True

    def connect(self, config: SourceConfig, secret_env: SecretEnv) -> _BQSession:
        if not BQ_AVAILABLE:
            raise ImportError("google-cloud-bigquery is not installed.")
        import os
        creds_path = secret_env.require("GOOGLE_APPLICATION_CREDENTIALS")
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = creds_path
        project = config.raw.get("project", "")
        dataset = config.raw.get("dataset", "")
        client = bigquery.Client(project=project)
        return _BQSession(client, dataset)

    def discover_schema(
        self, session: _BQSession, config: SourceConfig
    ) -> SourceSchema:
        table_name = config.raw.get("table", "")
        project = config.raw.get("project", "")
        dataset = session.dataset
        # BQ API — no SQL interpolation here
        table_ref = session.client.get_table(f"{project}.{dataset}.{table_name}")
        columns = [
            ColumnInfo(
                name=f.name,
                dtype=f.field_type,
                nullable=(f.mode != "REQUIRED"),
            )
            for f in table_ref.schema
        ]
        return SourceSchema(
            columns=columns,
            estimated_row_count=table_ref.num_rows,
            source_format="bigquery",
        )

    def create_cursor(
        self,
        session: _BQSession,
        config: SourceConfig,
        query: SourceQuery,
    ) -> DataCursor:
        # Identifiers are structural and cannot be parameterized — validate each
        # component (project/dataset/table, columns, filter cols, order_by,
        # primary_key) before it is interpolated into the SQL string.
        table_name = _validate_identifier(config.raw.get("table", ""), kind="table")
        project = _validate_identifier(config.raw.get("project", ""), kind="project")
        dataset = _validate_identifier(session.dataset, kind="dataset")
        full_table = f"`{project}.{dataset}.{table_name}`"

        col_list = (
            ", ".join(_validate_identifier(c, kind="column") for c in query.columns)
            if query.columns
            else "*"
        )
        parts = [f"SELECT {col_list} FROM {full_table}"]
        job_params: list[Any] = []

        # Filters — parameterized using BQ QueryJobConfig parameters
        where_clauses: list[str] = []
        for i, fexpr in enumerate(query.filters):
            param_name = f"p{i}"
            col = _validate_identifier(fexpr.col, kind="filter column")
            if fexpr.op == "=":
                where_clauses.append(f"{col} = @{param_name}")
            elif fexpr.op == "!=":
                where_clauses.append(f"{col} != @{param_name}")
            elif fexpr.op == ">":
                where_clauses.append(f"{col} > @{param_name}")
            elif fexpr.op == ">=":
                where_clauses.append(f"{col} >= @{param_name}")
            elif fexpr.op == "<":
                where_clauses.append(f"{col} < @{param_name}")
            elif fexpr.op == "<=":
                where_clauses.append(f"{col} <= @{param_name}")
            elif fexpr.op == "is_null":
                where_clauses.append(f"{col} IS NULL")
                continue  # no parameter
            else:
                where_clauses.append(f"{col} = @{param_name}")
            job_params.append(
                bigquery.ScalarQueryParameter(param_name, "STRING", str(fexpr.value))
            )

        # Shard pushdown — parameterized
        primary_key = _validate_identifier(
            config.raw.get("primary_key", "id"), kind="primary_key"
        )
        if query.n_shards > 1:
            shard_param_n = f"shard_n_{query.shard_index}"
            shard_param_s = f"shard_s_{query.shard_index}"
            where_clauses.append(
                f"MOD(FARM_FINGERPRINT(CAST({primary_key} AS STRING)), @{shard_param_n}) = @{shard_param_s}"
            )
            job_params.append(bigquery.ScalarQueryParameter(shard_param_n, "INT64", query.n_shards))
            job_params.append(bigquery.ScalarQueryParameter(shard_param_s, "INT64", query.shard_index))

        if where_clauses:
            parts.append("WHERE " + " AND ".join(where_clauses))

        if query.order_by:
            parts.append(f"ORDER BY {_validate_order_by(query.order_by)}")

        if query.limit is not None:
            parts.append(f"LIMIT {query.limit}")

        sql = " ".join(parts)
        job_config = bigquery.QueryJobConfig(query_parameters=job_params)
        rows_iter = session.client.query(sql, job_config=job_config).result()
        for row in rows_iter:
            yield dict(row)

    def estimate_rows(
        self,
        session: _BQSession,
        config: SourceConfig,
        query: SourceQuery,
    ) -> Optional[int]:
        table_name = config.raw.get("table", "")
        project = config.raw.get("project", "")
        table_ref = session.client.get_table(f"{project}.{session.dataset}.{table_name}")
        return table_ref.num_rows

    def close(self, session: _BQSession) -> None:
        session.close()


__all__ = ["BigQueryAdapter", "BQ_AVAILABLE"]
