"""GCSParquetAdapter — reads Parquet files from Google Cloud Storage (ADR-0026 D).

Only imports google.cloud.storage — NOT google.cloud.aiplatform.
connect() MUST only be called inside bwrap.
"""
from __future__ import annotations

import io
from typing import Any, Iterator, Optional

try:
    import google.cloud.storage  # type: ignore[import]
    import google  # noqa: F811 — re-import to have module-level name for mocking
    GCS_AVAILABLE = True
except ImportError:
    google = None  # type: ignore[assignment]
    GCS_AVAILABLE = False

try:
    import pyarrow.parquet as pq  # type: ignore[import]
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


class _GCSSession(SourceSession):
    def __init__(self, client: Any, bucket_name: str, prefix: str) -> None:
        self.client = client
        self.bucket_name = bucket_name
        self.prefix = prefix

    def close(self) -> None:
        pass


class GCSParquetAdapter(BaseDataSourceAdapter):
    """Reads Parquet files from Google Cloud Storage."""

    adapter_name = "gcs_parquet"
    display_name = "Google Cloud Storage (Parquet)"
    description = "Read Parquet files from Google Cloud Storage."
    supported_formats = frozenset({"parquet"})
    locality = "any"
    network_egress = "any"
    config_schema = {
        "type": "object",
        "properties": {
                    "bucket": {"type": "string"},
            "prefix": {"type": "string", "default": ""},
            "project": {"type": ["string", "null"], "default": None},
        },
    }

    supports_streaming: bool = True
    supports_pushdown: bool = True  # prefix + predicate pushdown
    supports_schema_discovery: bool = True
    supports_incremental: bool = True

    def connect(self, config: SourceConfig, secret_env: SecretEnv) -> _GCSSession:
        if not GCS_AVAILABLE:
            raise ImportError("google-cloud-storage is not installed.")
        import os
        creds_path = secret_env.require("GOOGLE_APPLICATION_CREDENTIALS")
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = creds_path
        # Use module-level google reference so tests can patch it
        client = google.cloud.storage.Client()
        bucket_name = config.raw.get("bucket", "")
        prefix = config.raw.get("prefix", "")
        return _GCSSession(client, bucket_name, prefix)

    def _list_blobs(self, session: _GCSSession) -> list[str]:
        bucket = session.client.bucket(session.bucket_name)
        blobs = bucket.list_blobs(prefix=session.prefix)
        return sorted(
            b.name for b in blobs
            if b.name.endswith(".parquet") or b.name.endswith(".pq")
        )

    def discover_schema(
        self, session: _GCSSession, config: SourceConfig
    ) -> SourceSchema:
        if not PYARROW_AVAILABLE:
            return SourceSchema(columns=[], source_format="gcs_parquet")
        blobs = self._list_blobs(session)
        if not blobs:
            return SourceSchema(columns=[], source_format="gcs_parquet")
        bucket = session.client.bucket(session.bucket_name)
        blob = bucket.blob(blobs[0])
        buf = io.BytesIO(blob.download_as_bytes())
        schema = pq.read_schema(buf)
        columns = [
            ColumnInfo(name=field.name, dtype=str(field.type))
            for field in schema
        ]
        return SourceSchema(columns=columns, source_format="gcs_parquet")

    def create_cursor(
        self,
        session: _GCSSession,
        config: SourceConfig,
        query: SourceQuery,
    ) -> DataCursor:
        blobs = self._list_blobs(session)

        # Shard by object list split
        if query.n_shards > 1 and blobs:
            chunk_size = max(1, len(blobs) // query.n_shards)
            start = query.shard_index * chunk_size
            end = start + chunk_size if query.shard_index < query.n_shards - 1 else len(blobs)
            blobs = blobs[start:end]

        bucket = session.client.bucket(session.bucket_name)
        count = 0
        for blob_name in blobs:
            blob = bucket.blob(blob_name)
            buf = io.BytesIO(blob.download_as_bytes())
            if PYARROW_AVAILABLE:
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
        session: _GCSSession,
        config: SourceConfig,
        query: SourceQuery,
    ) -> Optional[int]:
        return None

    def close(self, session: _GCSSession) -> None:
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


__all__ = ["GCSParquetAdapter", "GCS_AVAILABLE", "PYARROW_AVAILABLE"]
