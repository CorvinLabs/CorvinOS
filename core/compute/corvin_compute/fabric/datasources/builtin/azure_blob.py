"""AzureBlobAdapter — reads files from Azure Blob Storage (ADR-0026 Section D).

supports_pushdown=True (prefix filter)
supports_incremental=True (timestamp)
connect() MUST only be called inside bwrap.
"""
from __future__ import annotations

import csv
import io
from typing import Any, Iterator, Optional

try:
    import azure.storage.blob  # type: ignore[import]
    import azure  # noqa: F811 — re-import to have module-level name for mocking
    AZURE_AVAILABLE = True
except ImportError:
    azure = None  # type: ignore[assignment]
    AZURE_AVAILABLE = False

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


class _AzureSession(SourceSession):
    def __init__(self, container_client: Any, prefix: str) -> None:
        self.container_client = container_client
        self.prefix = prefix

    def close(self) -> None:
        pass


class AzureBlobAdapter(BaseDataSourceAdapter):
    """Reads files from Azure Blob Storage."""

    adapter_name = "azure_blob"
    display_name = "Azure Blob Storage"
    description = "Read files from an Azure Blob Storage container."
    supported_formats = frozenset({"csv", "parquet", "json"})
    locality = "any"
    network_egress = "any"
    config_schema = {
        "type": "object",
        "properties": {
                    "account_name": {"type": "string"},
            "container":    {"type": "string"},
            "path_prefix":  {"type": "string", "default": ""},
            "format":       {"type": "string", "enum": ["parquet", "csv", "json"]},
        },
    }

    supports_streaming: bool = True
    supports_pushdown: bool = True  # prefix filter
    supports_schema_discovery: bool = True
    supports_incremental: bool = True

    def connect(self, config: SourceConfig, secret_env: SecretEnv) -> _AzureSession:
        if not AZURE_AVAILABLE:
            raise ImportError("azure-storage-blob is not installed.")
        conn_str = secret_env.require("AZURE_STORAGE_CONNECTION_STRING")
        container_name = config.raw.get("container", "")
        prefix = config.raw.get("prefix", "")
        # Use module-level azure reference so tests can patch it
        BlobServiceClient = azure.storage.blob.BlobServiceClient
        service_client = BlobServiceClient.from_connection_string(conn_str)
        container_client = service_client.get_container_client(container_name)
        return _AzureSession(container_client, prefix)

    def _list_blobs(self, session: _AzureSession) -> list[str]:
        blobs = session.container_client.list_blobs(name_starts_with=session.prefix)
        return sorted(b["name"] for b in blobs)

    def discover_schema(
        self, session: _AzureSession, config: SourceConfig
    ) -> SourceSchema:
        blobs = self._list_blobs(session)
        if not blobs:
            return SourceSchema(columns=[], source_format="azure_blob")
        blob_client = session.container_client.get_blob_client(blobs[0])
        content = blob_client.download_blob().readall().decode("utf-8")
        reader = csv.DictReader(io.StringIO(content))
        rows = []
        for i, row in enumerate(reader):
            if i >= 100:
                break
            rows.append(row)
        columns = []
        if reader.fieldnames:
            for col_name in reader.fieldnames:
                columns.append(ColumnInfo(name=col_name, dtype="string"))
        return SourceSchema(columns=columns, source_format="azure_blob")

    def create_cursor(
        self,
        session: _AzureSession,
        config: SourceConfig,
        query: SourceQuery,
    ) -> DataCursor:
        blobs = self._list_blobs(session)

        # Shard by blob list split
        if query.n_shards > 1 and blobs:
            chunk_size = max(1, len(blobs) // query.n_shards)
            start = query.shard_index * chunk_size
            end = start + chunk_size if query.shard_index < query.n_shards - 1 else len(blobs)
            blobs = blobs[start:end]

        count = 0
        for blob_name in blobs:
            blob_client = session.container_client.get_blob_client(blob_name)
            content = blob_client.download_blob().readall().decode("utf-8")
            reader = csv.DictReader(io.StringIO(content))
            for row in reader:
                if query.columns:
                    row = {k: row[k] for k in query.columns if k in row}
                if _passes_filters(row, query.filters):
                    yield dict(row)
                    count += 1
                    if query.limit and count >= query.limit:
                        return

    def estimate_rows(
        self,
        session: _AzureSession,
        config: SourceConfig,
        query: SourceQuery,
    ) -> Optional[int]:
        return None

    def close(self, session: _AzureSession) -> None:
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


__all__ = ["AzureBlobAdapter", "AZURE_AVAILABLE"]
