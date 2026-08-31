"""DeltaLakeAdapter — reads Delta Lake tables (ADR-0026 Section D).

supports_pushdown=True (partition filter)
supports_incremental=True (delta log)
connect() MUST only be called inside bwrap.
"""
from __future__ import annotations

from typing import Any, Iterator, Optional

try:
    import deltalake  # type: ignore[import]
    from deltalake import DeltaTable  # type: ignore[import]
    DL_AVAILABLE = True
except ImportError:
    deltalake = None  # type: ignore[assignment]
    DeltaTable = None  # type: ignore[assignment,misc]
    DL_AVAILABLE = False

from ..protocol import (
    BaseDataSourceAdapter,
    ColumnInfo,
    DataCursor,
    FilterExpr,
    SecretEnv,
    SourceConfig,
    SourceQuery,
    SourceSchema,
    SourceSession,
)


class _DLSession(SourceSession):
    def __init__(self, table: Any) -> None:
        self.table = table

    def close(self) -> None:
        pass


class DeltaLakeAdapter(BaseDataSourceAdapter):
    """Reads Delta Lake tables using the deltalake Python library."""

    adapter_name = "delta_lake"
    display_name = "Delta Lake"
    description = "Read Delta Lake tables (local or cloud storage)."
    supported_formats = frozenset({"delta", "parquet"})
    locality = "any"
    network_egress = "any"
    config_schema = {
        "type": "object",
        "properties": {
                    "path":   {"type": "string", "description": "Delta table root path or URI"},
            "version": {"type": ["integer", "null"], "default": None},
        },
    }

    supports_streaming: bool = True
    supports_pushdown: bool = True   # partition filter
    supports_schema_discovery: bool = True
    supports_incremental: bool = True  # delta log based

    def connect(self, config: SourceConfig, secret_env: SecretEnv) -> _DLSession:
        if not DL_AVAILABLE:
            raise ImportError("deltalake is not installed.")
        table_path = config.raw.get("path", "")
        storage_options: dict[str, str] = {}
        # Inject S3/GCS/Azure credentials if provided
        for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
                    "GOOGLE_APPLICATION_CREDENTIALS",
                    "AZURE_STORAGE_CONNECTION_STRING"):
            val = secret_env.get(key)
            if val:
                storage_options[key] = val
        table = DeltaTable(table_path, storage_options=storage_options or None)
        return _DLSession(table)

    def discover_schema(
        self, session: _DLSession, config: SourceConfig
    ) -> SourceSchema:
        schema = session.table.schema()
        columns = [
            ColumnInfo(
                name=field.name,
                dtype=str(field.type),
                nullable=field.nullable,
            )
            for field in schema.fields
        ]
        return SourceSchema(
            columns=columns,
            estimated_row_count=None,
            source_format="delta_lake",
        )

    def create_cursor(
        self,
        session: _DLSession,
        config: SourceConfig,
        query: SourceQuery,
    ) -> DataCursor:
        # Build partition filter from FilterExpr (partition pushdown)
        partition_filters: list[tuple] = []
        client_filters: list[FilterExpr] = []
        partition_cols = set(config.raw.get("partition_columns", []))
        for fexpr in query.filters:
            if fexpr.col in partition_cols and fexpr.op == "=":
                partition_filters.append((fexpr.col, "=", str(fexpr.value)))
            else:
                client_filters.append(fexpr)

        # Convert to PyArrow table
        cols = query.columns or None
        try:
            if partition_filters:
                arrow_table = session.table.to_pyarrow(
                    filters=partition_filters,
                    columns=cols,
                )
            else:
                arrow_table = session.table.to_pyarrow(columns=cols)
        except Exception:
            return

        batch = arrow_table.to_pydict()
        col_keys = list(batch.keys())
        n = len(batch[col_keys[0]]) if col_keys else 0
        limit = query.limit or n
        count = 0
        for i in range(n):
            row = {k: batch[k][i] for k in col_keys}
            if _passes_filters(row, client_filters):
                yield row
                count += 1
                if count >= limit:
                    break

    def estimate_rows(
        self,
        session: _DLSession,
        config: SourceConfig,
        query: SourceQuery,
    ) -> Optional[int]:
        return None

    def close(self, session: _DLSession) -> None:
        session.close()


def _passes_filters(row: dict, filters: list[FilterExpr]) -> bool:
    for f in filters:
        val = row.get(f.col)
        if f.op == "=" and val != f.value:
            return False
        if f.op == "!=" and val == f.value:
            return False
        if f.op == "is_null" and val is not None:
            return False
    return True


__all__ = ["DeltaLakeAdapter", "DL_AVAILABLE"]
