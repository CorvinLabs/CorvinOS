"""S3ParquetAdapter — reads Parquet files from S3 (ADR-0026 Section D).

Shard pushdown: splits S3 object list into N equal segments.
Predicate pushdown via pyarrow.dataset when available.
connect() MUST only be called inside bwrap.
"""
from __future__ import annotations

from typing import Any, Iterator, List, Optional

try:
    import boto3  # type: ignore[import]
    BOTO3_AVAILABLE = True
except ImportError:
    boto3 = None  # type: ignore[assignment]
    BOTO3_AVAILABLE = False

try:
    import pyarrow.dataset as pad  # type: ignore[import]
    import pyarrow as pa  # type: ignore[import]
    PYARROW_AVAILABLE = True
except ImportError:
    PYARROW_AVAILABLE = False

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


class _S3Session(SourceSession):
    def __init__(self, s3_client: Any, bucket: str, prefix: str) -> None:
        self.s3 = s3_client
        self.bucket = bucket
        self.prefix = prefix

    def close(self) -> None:
        pass


class S3ParquetAdapter(BaseDataSourceAdapter):
    """Reads Parquet files from AWS S3."""

    adapter_name = "s3_parquet"
    display_name = "Amazon S3 (Parquet)"
    description = "Read Parquet files stored in an Amazon S3 bucket."
    supported_formats = frozenset({"parquet"})
    locality = "any"
    network_egress = "any"
    config_schema = {
        "type": "object",
        "required": ["bucket", "region"],
        "properties": {
            "bucket":       {"type": "string"},
            "prefix":       {"type": "string", "default": ""},
            "region":       {"type": "string"},
            "endpoint_url": {"type": ["string", "null"], "default": None},
        },
        "additionalProperties": False,
    }

    supports_streaming: bool = True
    supports_pushdown: bool = True
    supports_schema_discovery: bool = True
    supports_incremental: bool = True

    def connect(self, config: SourceConfig, secret_env: SecretEnv) -> _S3Session:
        if not BOTO3_AVAILABLE:
            raise ImportError("boto3 is not installed.")
        # Use module-level boto3 reference so tests can patch it
        s3 = boto3.client(
            "s3",
            aws_access_key_id=secret_env.require("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=secret_env.require("AWS_SECRET_ACCESS_KEY"),
            region_name=config.region,
        )
        bucket = config.raw.get("bucket", "")
        prefix = config.raw.get("prefix", "")
        return _S3Session(s3, bucket, prefix)

    def _list_objects(self, session: _S3Session) -> list[str]:
        """Return list of S3 object keys matching the prefix."""
        paginator = session.s3.get_paginator("list_objects_v2")
        keys: list[str] = []
        for page in paginator.paginate(Bucket=session.bucket, Prefix=session.prefix):
            for obj in page.get("Contents", []):
                if obj["Key"].endswith(".parquet") or obj["Key"].endswith(".pq"):
                    keys.append(obj["Key"])
        return sorted(keys)

    def discover_schema(
        self, session: _S3Session, config: SourceConfig
    ) -> SourceSchema:
        if not PYARROW_AVAILABLE:
            return SourceSchema(columns=[], source_format="s3_parquet")
        keys = self._list_objects(session)
        if not keys:
            return SourceSchema(columns=[], source_format="s3_parquet")
        # Read schema from first object
        obj = session.s3.get_object(Bucket=session.bucket, Key=keys[0])
        import io
        import pyarrow.parquet as pq
        buf = io.BytesIO(obj["Body"].read())
        schema = pq.read_schema(buf)
        columns = [
            ColumnInfo(name=field.name, dtype=str(field.type))
            for field in schema
        ]
        return SourceSchema(
            columns=columns,
            estimated_row_count=None,
            source_format="s3_parquet",
        )

    def create_cursor(
        self,
        session: _S3Session,
        config: SourceConfig,
        query: SourceQuery,
    ) -> DataCursor:
        keys = self._list_objects(session)

        # Shard pushdown: split object list into N equal segments
        if query.n_shards > 1 and keys:
            chunk_size = max(1, len(keys) // query.n_shards)
            start = query.shard_index * chunk_size
            end = start + chunk_size if query.shard_index < query.n_shards - 1 else len(keys)
            keys = keys[start:end]

        count = 0
        for key in keys:
            obj = session.s3.get_object(Bucket=session.bucket, Key=key)
            import io
            buf = io.BytesIO(obj["Body"].read())
            if PYARROW_AVAILABLE:
                import pyarrow.parquet as pq
                cols = query.columns or None
                table = pq.read_table(buf, columns=cols)
                batch = table.to_pydict()
                col_keys = list(batch.keys())
                n = len(batch[col_keys[0]]) if col_keys else 0
                for i in range(n):
                    row = {k: batch[k][i] for k in col_keys}
                    if _passes_filters(row, query.filters):
                        yield row
                        count += 1
                        if query.limit and count >= query.limit:
                            return

    def estimate_rows(
        self,
        session: _S3Session,
        config: SourceConfig,
        query: SourceQuery,
    ) -> Optional[int]:
        return None

    def close(self, session: _S3Session) -> None:
        session.close()


def _passes_filters(row: dict, filters: list[FilterExpr]) -> bool:
    for f in filters:
        val = row.get(f.col)
        if f.op == "=" and val != f.value:
            return False
        if f.op == "!=" and val == f.value:
            return False
        if f.op == ">" and not (val is not None and val > f.value):
            return False
        if f.op == ">=" and not (val is not None and val >= f.value):
            return False
        if f.op == "<" and not (val is not None and val < f.value):
            return False
        if f.op == "<=" and not (val is not None and val <= f.value):
            return False
        if f.op == "is_null" and val is not None:
            return False
        if f.op == "in" and val not in f.value:
            return False
        if f.op == "not_in" and val in f.value:
            return False
    return True


__all__ = ["S3ParquetAdapter", "BOTO3_AVAILABLE", "PYARROW_AVAILABLE"]
