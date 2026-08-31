"""S3CSVAdapter — reads CSV files from S3 (ADR-0026 Section D).

supports_pushdown=True (prefix filter only, no predicate pushdown)
supports_incremental=True (timestamp-based)
connect() MUST only be called inside bwrap.
"""
from __future__ import annotations

import csv
import io
from typing import Any, Iterator, Optional

try:
    import boto3  # type: ignore[import]
    BOTO3_AVAILABLE = True
except ImportError:
    boto3 = None  # type: ignore[assignment]
    BOTO3_AVAILABLE = False

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


class _S3CSVSession(SourceSession):
    def __init__(self, s3_client: Any, bucket: str, prefix: str) -> None:
        self.s3 = s3_client
        self.bucket = bucket
        self.prefix = prefix

    def close(self) -> None:
        pass


class S3CSVAdapter(BaseDataSourceAdapter):
    """Reads CSV files from AWS S3. Prefix pushdown only."""

    adapter_name = "s3_csv"
    display_name = "Amazon S3 (CSV)"
    description = "Read CSV files stored in an Amazon S3 bucket."
    supported_formats = frozenset({"csv"})
    locality = "any"
    network_egress = "any"
    config_schema = {
        "type": "object",
        "properties": {
                    "bucket":    {"type": "string"},
            "prefix":    {"type": "string", "default": ""},
            "region":    {"type": "string"},
            "delimiter": {"type": "string", "default": ","},
        },
    }

    supports_streaming: bool = True
    supports_pushdown: bool = True  # prefix filter only
    supports_schema_discovery: bool = True
    supports_incremental: bool = True

    def connect(self, config: SourceConfig, secret_env: SecretEnv) -> _S3CSVSession:
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
        return _S3CSVSession(s3, bucket, prefix)

    def _list_objects(self, session: _S3CSVSession) -> list[str]:
        paginator = session.s3.get_paginator("list_objects_v2")
        keys: list[str] = []
        for page in paginator.paginate(Bucket=session.bucket, Prefix=session.prefix):
            for obj in page.get("Contents", []):
                if obj["Key"].endswith(".csv"):
                    keys.append(obj["Key"])
        return sorted(keys)

    def discover_schema(
        self, session: _S3CSVSession, config: SourceConfig
    ) -> SourceSchema:
        keys = self._list_objects(session)
        if not keys:
            return SourceSchema(columns=[], source_format="s3_csv")
        obj = session.s3.get_object(Bucket=session.bucket, Key=keys[0])
        content = obj["Body"].read().decode("utf-8")
        reader = csv.DictReader(io.StringIO(content))
        rows = []
        for i, row in enumerate(reader):
            if i >= 100:
                break
            rows.append(row)
        columns = []
        if reader.fieldnames:
            for col_name in reader.fieldnames:
                sample_vals = [r.get(col_name) for r in rows if r.get(col_name)]
                columns.append(ColumnInfo(name=col_name, dtype=_infer_dtype(sample_vals)))
        return SourceSchema(columns=columns, source_format="s3_csv")

    def create_cursor(
        self,
        session: _S3CSVSession,
        config: SourceConfig,
        query: SourceQuery,
    ) -> DataCursor:
        keys = self._list_objects(session)

        # Shard by splitting object list
        if query.n_shards > 1 and keys:
            chunk_size = max(1, len(keys) // query.n_shards)
            start = query.shard_index * chunk_size
            end = start + chunk_size if query.shard_index < query.n_shards - 1 else len(keys)
            keys = keys[start:end]

        count = 0
        for key in keys:
            obj = session.s3.get_object(Bucket=session.bucket, Key=key)
            content = obj["Body"].read().decode("utf-8")
            reader = csv.DictReader(io.StringIO(content))
            for row in reader:
                if query.columns:
                    row = {k: row[k] for k in query.columns if k in row}
                if _passes_filters(row, query.filters):
                    yield row
                    count += 1
                    if query.limit and count >= query.limit:
                        return

    def estimate_rows(
        self,
        session: _S3CSVSession,
        config: SourceConfig,
        query: SourceQuery,
    ) -> Optional[int]:
        return None

    def close(self, session: _S3CSVSession) -> None:
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


def _infer_dtype(vals: list) -> str:
    if not vals:
        return "string"
    try:
        int(str(vals[0]))
        return "integer"
    except (ValueError, TypeError):
        pass
    try:
        float(str(vals[0]))
        return "float"
    except (ValueError, TypeError):
        pass
    return "string"


__all__ = ["S3CSVAdapter", "BOTO3_AVAILABLE"]
