"""LocalFileAdapter — reads CSV / JSON / Parquet files (ADR-0026 Section D).

supports_streaming=True (line-by-line for CSV)
supports_pushdown=False (client-side FilterExpr)
supports_schema_discovery=True
supports_incremental=False
"""
from __future__ import annotations

import csv
import io
import json
import time
from pathlib import Path
from typing import Any, Iterator, Optional

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
    PingResult,
    SecretEnv,
    SourceConfig,
    SourceQuery,
    SourceSchema,
    SourceSession,
)


class _LocalSession(SourceSession):
    def __init__(self, path: Path) -> None:
        self.path = path

    def close(self) -> None:
        pass


class LocalFileAdapter(BaseDataSourceAdapter):
    """Reads local CSV / JSON / Parquet files."""

    adapter_name = "local_file"
    display_name = "Local File"
    description = "Read CSV, JSON, or Parquet files from the local filesystem."
    supported_formats = frozenset({"csv", "json", "parquet"})
    locality = "local"
    network_egress = "none"
    config_schema = {
        "type": "object",
        "properties": {
                    "path":   {"type": "string", "description": "Absolute path to the file"},
            "format": {"type": "string", "enum": ["csv", "json", "parquet"], "default": "csv"},
        },
    }

    supports_streaming: bool = True
    supports_pushdown: bool = False
    supports_schema_discovery: bool = True
    supports_incremental: bool = False

    # ------------------------------------------------------------------
    def connect(self, config: SourceConfig, secret_env: SecretEnv) -> _LocalSession:
        path = Path(config.raw.get("path", ""))
        if not path.exists():
            raise FileNotFoundError(f"LocalFileAdapter: file not found: {path}")
        return _LocalSession(path)

    # ------------------------------------------------------------------
    def discover_schema(
        self, session: _LocalSession, config: SourceConfig
    ) -> SourceSchema:
        path = session.path
        fmt = _detect_format(path)

        if fmt == "parquet":
            return _schema_parquet(path)
        if fmt == "json":
            return _schema_json(path)
        return _schema_csv(path)

    # ------------------------------------------------------------------
    def create_cursor(
        self,
        session: _LocalSession,
        config: SourceConfig,
        query: SourceQuery,
    ) -> DataCursor:
        path = session.path
        fmt = _detect_format(path)

        if fmt == "parquet":
            rows = _read_parquet(path, query)
        elif fmt == "json":
            rows = _read_json(path, query)
        else:
            rows = _read_csv(path, query)

        # Client-side filter application (no pushdown)
        for row in rows:
            if _passes_filters(row, query.filters):
                yield row

    # ------------------------------------------------------------------
    def estimate_rows(
        self,
        session: _LocalSession,
        config: SourceConfig,
        query: SourceQuery,
    ) -> Optional[int]:
        path = session.path
        if _detect_format(path) == "parquet" and PYARROW_AVAILABLE:
            meta = pq.read_metadata(path)
            return meta.num_rows
        return None

    def close(self, session: _LocalSession) -> None:
        session.close()

    # ------------------------------------------------------------------
    def ping(
        self,
        timeout_s: float = 5.0,
        config: Optional[SourceConfig] = None,
    ) -> PingResult:
        """Real reachability test: the configured path must exist and be a
        readable regular file. No secrets, no network — pure filesystem stat.
        """
        raw = config.raw if config is not None else {}
        path_str = raw.get("path", "")
        if not path_str:
            return PingResult(ok=False, latency_ms=0.0, detail="no path configured")
        path = Path(path_str)
        t0 = time.monotonic()
        try:
            if not path.exists():
                return PingResult(ok=False, latency_ms=0.0, detail="file not found")
            if not path.is_file():
                return PingResult(ok=False, latency_ms=0.0, detail="path is not a regular file")
            # Confirm the file is actually openable for reading.
            with path.open("rb") as fh:
                fh.read(1)
        except PermissionError:
            return PingResult(ok=False, latency_ms=0.0, detail="permission denied")
        except OSError:
            return PingResult(ok=False, latency_ms=0.0, detail="file not readable")
        latency = (time.monotonic() - t0) * 1000
        return PingResult(ok=True, latency_ms=latency, detail="file readable")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _detect_format(path: Path) -> str:
    suf = path.suffix.lower()
    if suf in (".parquet", ".pq"):
        return "parquet"
    if suf in (".json", ".jsonl", ".ndjson"):
        return "json"
    return "csv"


def _schema_csv(path: Path) -> SourceSchema:
    columns: list[ColumnInfo] = []
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows_sample: list[dict] = []
        for i, row in enumerate(reader):
            if i >= 100:
                break
            rows_sample.append(row)
        if reader.fieldnames:
            for col_name in reader.fieldnames:
                sample_vals = [r.get(col_name) for r in rows_sample if r.get(col_name)]
                dtype = _infer_dtype(sample_vals)
                columns.append(ColumnInfo(name=col_name, dtype=dtype))
    return SourceSchema(columns=columns, source_format="csv")


def _schema_json(path: Path) -> SourceSchema:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            if i >= 100:
                break
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    if not rows:
        return SourceSchema(columns=[], source_format="json")
    all_keys: list[str] = list({k for row in rows for k in row})
    columns = [
        ColumnInfo(
            name=k,
            dtype=_infer_dtype([r.get(k) for r in rows if r.get(k) is not None]),
        )
        for k in all_keys
    ]
    return SourceSchema(columns=columns, source_format="json")


def _schema_parquet(path: Path) -> SourceSchema:
    if not PYARROW_AVAILABLE:
        return SourceSchema(columns=[], source_format="parquet")
    schema = pq.read_schema(path)
    columns = [
        ColumnInfo(name=field.name, dtype=str(field.type))
        for field in schema
    ]
    meta = pq.read_metadata(path)
    return SourceSchema(
        columns=columns,
        estimated_row_count=meta.num_rows,
        source_format="parquet",
    )


def _read_csv(path: Path, query: SourceQuery) -> Iterator[dict]:
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        count = 0
        for row in reader:
            if query.columns:
                row = {k: row[k] for k in query.columns if k in row}
            yield row
            count += 1
            if query.limit and count >= query.limit:
                break


def _read_json(path: Path, query: SourceQuery) -> Iterator[dict]:
    count = 0
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if query.columns:
                row = {k: row[k] for k in query.columns if k in row}
            yield row
            count += 1
            if query.limit and count >= query.limit:
                break


def _read_parquet(path: Path, query: SourceQuery) -> Iterator[dict]:
    if not PYARROW_AVAILABLE:
        return
    cols = query.columns or None
    table = pq.read_table(path, columns=cols)
    batch = table.to_pydict()
    keys = list(batch.keys())
    n_rows = len(batch[keys[0]]) if keys else 0
    limit = query.limit or n_rows
    for i in range(min(n_rows, limit)):
        yield {k: batch[k][i] for k in keys}


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
        if f.op == "like" and not (isinstance(val, str) and f.value.replace("%", "") in val):
            return False
        if f.op == "in" and val not in f.value:
            return False
        if f.op == "not_in" and val in f.value:
            return False
    return True


def _infer_dtype(vals: list) -> str:
    if not vals:
        return "string"
    v = vals[0]
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "float"
    # Try numeric coercion
    try:
        int(str(v))
        return "integer"
    except (ValueError, TypeError):
        pass
    try:
        float(str(v))
        return "float"
    except (ValueError, TypeError):
        pass
    return "string"


__all__ = ["LocalFileAdapter"]
