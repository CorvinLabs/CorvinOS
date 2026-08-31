"""Snapshot generator — schema + stats + sample, never raw data.

Generates a compact statistical projection of a file:

  * **schema**: per-column name + type-inference + (later) PII class
  * **stats**:  per-column nulls + quantile-bracketed extremes + distinct
  * **sample**: small head+random+tail row blend

The snapshot is the LLM-FACING artefact; the bytes themselves stay
sandbox-side. ADR-0012 §A.

Cost contract:
  * Pure stdlib (csv, json, statistics, hashlib, secrets, random).
  * No pandas / polars / pyarrow.
  * Parquet requires duckdb installed in the operator's env; an
    ImportError with a clear hint is the right outcome when missing.
"""
from __future__ import annotations

import csv
import json
import random
import secrets
import statistics
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterator

from .format_sniffer import Format, sniff_format


# ---------------------------------------------------------------------------
# Data classes — the LLM-visible Snapshot shape
# ---------------------------------------------------------------------------

@dataclass
class FileMeta:
    """File-level metadata. Always populated; never carries raw bytes."""

    path:     str
    format:   Format
    size_b:   int
    rowcount: int
    encoding: str = "utf-8"
    # Whether the rowcount is exact (small file streamed end-to-end) or
    # noised (large file; noise is ±5 when rowcount < 100, see SnapshotOptions).
    rowcount_exact: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ColumnSchema:
    """Per-column type + (later) PII classification.

    ``pii_class`` is None in Phase 12.1; Phase 12.2's pii_detector
    fills it in.  ``cardinality`` is the approximate distinct count
    (None when the column is not enumerated, e.g. a free-text column
    explicitly skipped by policy).
    """

    name:        str
    type:        str            # "string" | "int" | "float" | "bool" | "date" | "datetime"
    pii_class:   str | None = None
    cardinality: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ColumnStats:
    """Per-column statistics. None for the slots that don't apply
    (e.g. ``p05`` is None for string columns)."""

    nulls:    int = 0
    p05:      float | int | None = None
    p50:      float | int | None = None
    p95:      float | int | None = None
    distinct: int | None = None
    top:      list[str] | None = None   # populated only when distinct < TOP_THRESHOLD
    # When True, distinct + top reflect a sample, not the full file.
    approximate: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Snapshot:
    """The complete LLM-facing projection of a registered dataset."""

    file:   FileMeta
    schema: list[ColumnSchema]
    sample: list[dict[str, Any]]
    stats:  dict[str, ColumnStats]

    def to_dict(self) -> dict[str, Any]:
        return {
            "file":   self.file.to_dict(),
            "schema": [c.to_dict() for c in self.schema],
            "sample": self.sample,
            "stats":  {k: v.to_dict() for k, v in self.stats.items()},
        }


# ---------------------------------------------------------------------------
# Snapshot-options + errors
# ---------------------------------------------------------------------------

@dataclass
class SnapshotOptions:
    """Operator/agent-tunable knobs for snapshot generation."""

    # Sample size + composition
    rows:                  int = 20
    rows_strategy:         str = "head+random+tail"   # "head" | "tail" | "random" | "head+random+tail"

    # Stats inclusion
    include_quantiles:     bool = True
    include_distinct:      bool = True
    include_top:           bool = True

    # Cardinality / quantile caps (memory bound)
    distinct_cap:          int = 10_000   # set() size before we mark approximate
    top_threshold:         int = 50       # emit top-values list iff distinct < this
    quantile_value_cap:    int = 100_000  # per-column numeric value cap for quantile calc

    # Type-inference look-ahead
    type_inference_rows:   int = 1_000

    # Noising
    rowcount_jitter:       int = 5        # applied when rowcount < 100
    rowcount_jitter_threshold: int = 100

    # PRNG seed for reservoir / random-sample reproducibility. None ⇒
    # use a per-call urandom seed (production); int ⇒ deterministic
    # (testing).
    seed:                  int | None = None


class SnapshotError(RuntimeError):
    """Raised on unreadable files / corrupt data / oversized in-RAM
    operations that cannot be streamed cleanly."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_snapshot(
    path: str | Path,
    *,
    format_hint: str | None = None,
    options:     SnapshotOptions | None = None,
) -> Snapshot:
    """Produce a Snapshot for *path*.

    Dispatches by detected format to the format-specific generator.
    The format generators are streaming where possible (CSV/TSV/JSONL)
    and bounded-RAM where not (JSON loads the whole file; refuses
    files > 100 MB to surface the problem early instead of OOMing
    silently).
    """
    p = Path(path)
    if not p.exists():
        raise SnapshotError(f"path does not exist: {path}")
    if not p.is_file():
        raise SnapshotError(f"path is not a regular file: {path}")

    opts = options or SnapshotOptions()
    fmt = sniff_format(p, format_hint=format_hint)
    size_b = p.stat().st_size

    if fmt == "csv":
        return _snapshot_delimited(p, fmt, size_b, opts, delimiter=",")
    if fmt == "tsv":
        return _snapshot_delimited(p, fmt, size_b, opts, delimiter="\t")
    if fmt == "json":
        return _snapshot_json(p, size_b, opts)
    if fmt == "jsonl":
        return _snapshot_jsonl(p, size_b, opts)
    if fmt == "parquet":
        return _snapshot_parquet(p, size_b, opts)
    raise SnapshotError(f"unhandled format: {fmt!r}")


# ---------------------------------------------------------------------------
# Format-specific generators
# ---------------------------------------------------------------------------

def _snapshot_delimited(
    path:      Path,
    fmt:       Format,
    size_b:    int,
    opts:      SnapshotOptions,
    *,
    delimiter: str,
) -> Snapshot:
    """Streaming CSV/TSV snapshot.

    Two-pass when feasible (rowcount + sample + stats); single-pass
    using reservoir sampling so we still work on a streaming source if
    we ever swap the second pass for tail-buffering.

    For Phase 12.1 we use a SINGLE pass: stream the file once,
    maintaining (a) head buffer, (b) tail circular buffer, (c)
    reservoir for random middle, (d) per-column type ballot, (e)
    per-column null counter, (f) per-column distinct set, (g)
    per-column numeric reservoir for quantiles.
    """
    rng = random.Random(opts.seed) if opts.seed is not None else random.Random()

    with path.open("r", encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.reader(fh, delimiter=delimiter)
        try:
            header = next(reader)
        except StopIteration:
            raise SnapshotError(f"file {path} is empty (no header row)")

        # Sanitize header: empty → "col_<i>"; trim whitespace.
        header = [
            (h.strip() if h.strip() else f"col_{i}")
            for i, h in enumerate(header)
        ]

        # Per-column accumulators
        col_state = {h: _ColumnAccumulator(opts) for h in header}

        # Sample buffers
        sample_state = _SampleState(opts, rng)

        rowcount = 0
        for row in reader:
            # Pad / trim rows to header length
            if len(row) < len(header):
                row = row + [""] * (len(header) - len(row))
            elif len(row) > len(header):
                row = row[:len(header)]

            record = dict(zip(header, row))
            rowcount += 1

            for h, raw_value in zip(header, row):
                col_state[h].observe(raw_value)

            sample_state.observe(record)

        schema, stats = _finalise_columns(header, col_state)
        sample = sample_state.materialise(rowcount)

    rowcount_exact, rowcount_published = _maybe_jitter_rowcount(rowcount, opts, rng)

    return Snapshot(
        file=FileMeta(
            path=str(path),
            format=fmt,
            size_b=size_b,
            rowcount=rowcount_published,
            rowcount_exact=rowcount_exact,
        ),
        schema=schema,
        sample=sample,
        stats=stats,
    )


def _snapshot_jsonl(path: Path, size_b: int, opts: SnapshotOptions) -> Snapshot:
    """Streaming JSONL snapshot. One object per line."""
    rng = random.Random(opts.seed) if opts.seed is not None else random.Random()

    # Two-pass strategy is incompatible with single-pass streaming over
    # a pipe; we do single-pass with header discovery on first row.
    col_state: dict[str, _ColumnAccumulator] = {}
    sample_state = _SampleState(opts, rng)
    header_order: list[str] = []
    rowcount = 0

    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SnapshotError(
                    f"{path}: JSONL parse failed at row {rowcount + 1}: {exc}"
                ) from exc
            if not isinstance(rec, dict):
                raise SnapshotError(
                    f"{path}: JSONL row {rowcount + 1} is not an object "
                    f"({type(rec).__name__})"
                )

            for k in rec.keys():
                if k not in col_state:
                    col_state[k] = _ColumnAccumulator(opts)
                    header_order.append(k)

            for h in header_order:
                v = rec.get(h, None)
                col_state[h].observe_python(v)

            sample_state.observe(rec)
            rowcount += 1

    schema, stats = _finalise_columns(header_order, col_state)
    sample = sample_state.materialise(rowcount)
    rowcount_exact, rowcount_published = _maybe_jitter_rowcount(rowcount, opts, rng)

    return Snapshot(
        file=FileMeta(
            path=str(path),
            format="jsonl",
            size_b=size_b,
            rowcount=rowcount_published,
            rowcount_exact=rowcount_exact,
        ),
        schema=schema,
        sample=sample,
        stats=stats,
    )


def _snapshot_json(path: Path, size_b: int, opts: SnapshotOptions) -> Snapshot:
    """JSON snapshot. Loads the whole file; refuses > 100 MB to fail
    loud instead of OOMing.

    Accepts:
      * a top-level array of objects   ``[{...}, {...}]``
      * a top-level object with one array field   ``{"records": [{...}, ...]}``
    """
    if size_b > 100 * 1024 * 1024:
        raise SnapshotError(
            f"{path}: JSON file size {size_b} bytes exceeds 100 MB cap. "
            f"Convert to JSONL (one object per line) for streamable processing."
        )

    rng = random.Random(opts.seed) if opts.seed is not None else random.Random()

    with path.open("r", encoding="utf-8", errors="replace") as fh:
        try:
            raw = json.load(fh)
        except json.JSONDecodeError as exc:
            raise SnapshotError(f"{path}: JSON parse failed: {exc}") from exc

    records = _extract_records_from_json(raw, path)
    if not records:
        raise SnapshotError(f"{path}: JSON contains no records")

    col_state: dict[str, _ColumnAccumulator] = {}
    sample_state = _SampleState(opts, rng)
    header_order: list[str] = []

    for rec in records:
        if not isinstance(rec, dict):
            raise SnapshotError(
                f"{path}: JSON record is not an object ({type(rec).__name__})"
            )
        for k in rec.keys():
            if k not in col_state:
                col_state[k] = _ColumnAccumulator(opts)
                header_order.append(k)

    for rec in records:
        for h in header_order:
            v = rec.get(h, None)
            col_state[h].observe_python(v)
        sample_state.observe(rec)

    rowcount = len(records)
    schema, stats = _finalise_columns(header_order, col_state)
    sample = sample_state.materialise(rowcount)
    rowcount_exact, rowcount_published = _maybe_jitter_rowcount(rowcount, opts, rng)

    return Snapshot(
        file=FileMeta(
            path=str(path),
            format="json",
            size_b=size_b,
            rowcount=rowcount_published,
            rowcount_exact=rowcount_exact,
        ),
        schema=schema,
        sample=sample,
        stats=stats,
    )


def _snapshot_parquet(path: Path, size_b: int, opts: SnapshotOptions) -> Snapshot:
    """Parquet snapshot via DuckDB (read-only).

    DuckDB is an OPTIONAL dependency. When missing, we raise ImportError
    with a clear operator-facing hint. We do NOT silently fall back to
    "treat as binary" — that would be a data-integrity failure.
    """
    try:
        import duckdb  # type: ignore[import-untyped]
    except ImportError as exc:
        raise ImportError(
            "Parquet snapshot generation requires duckdb. Install via "
            "`pip install duckdb` in the forge plugin's Python env, "
            "or convert the file to CSV/JSONL for stdlib processing."
        ) from exc

    conn = duckdb.connect(":memory:", read_only=True)
    try:
        path_lit = str(path).replace("'", "''")
        rel = conn.execute(f"SELECT * FROM read_parquet('{path_lit}')")
        column_names = [d[0] for d in rel.description]
        duckdb_types = [d[1] for d in rel.description]

        # Map duckdb's per-column type strings to our typed lattice.
        def _map_type(dt: str) -> str:
            dt = dt.upper()
            if dt in {"INTEGER", "BIGINT", "SMALLINT", "TINYINT", "HUGEINT", "UINTEGER", "UBIGINT", "USMALLINT", "UTINYINT"}:
                return "int"
            if dt in {"DOUBLE", "FLOAT", "DECIMAL", "REAL"}:
                return "float"
            if dt == "BOOLEAN":
                return "bool"
            if dt == "DATE":
                return "date"
            if dt.startswith("TIMESTAMP"):
                return "datetime"
            return "string"

        rng = random.Random(opts.seed) if opts.seed is not None else random.Random()
        col_state = {n: _ColumnAccumulator(opts) for n in column_names}
        sample_state = _SampleState(opts, rng)
        rowcount = 0

        # Iterate in chunks to bound RAM.
        while True:
            chunk = rel.fetchmany(10_000)
            if not chunk:
                break
            for row in chunk:
                rec = dict(zip(column_names, row))
                rowcount += 1
                for n, v in zip(column_names, row):
                    col_state[n].observe_python(v)
                sample_state.observe(rec)

        # DuckDB already knows the schema types; override the ballot
        # for parquet (we know the types are reliable).
        schema: list[ColumnSchema] = []
        stats: dict[str, ColumnStats] = {}
        for name, dt in zip(column_names, duckdb_types):
            acc = col_state[name]
            mapped = _map_type(dt)
            cs = acc.finalise(mapped)
            schema.append(ColumnSchema(name=name, type=mapped, cardinality=cs.distinct))
            stats[name] = cs

        sample = sample_state.materialise(rowcount)
        rowcount_exact, rowcount_published = _maybe_jitter_rowcount(rowcount, opts, rng)

        return Snapshot(
            file=FileMeta(
                path=str(path),
                format="parquet",
                size_b=size_b,
                rowcount=rowcount_published,
                rowcount_exact=rowcount_exact,
            ),
            schema=schema,
            sample=sample,
            stats=stats,
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Internal accumulators
# ---------------------------------------------------------------------------

class _ColumnAccumulator:
    """Per-column running state — type ballot, nulls, distinct, quantile reservoir."""

    def __init__(self, opts: SnapshotOptions) -> None:
        self.opts = opts
        self.nulls = 0
        self.observed = 0
        # Type ballot — counts of how many values fit each shape.
        self.votes = {"int": 0, "float": 0, "bool": 0, "date": 0, "datetime": 0, "string": 0}
        # Distinct set, capped at distinct_cap (then mark approximate).
        self._distinct: set[str] = set()
        self._distinct_capped = False
        # Numeric values for quantile computation (cap to bound RAM).
        self._numeric: list[float] = []
        self._numeric_capped = False
        # For top-values: count by value (lossy when cardinality >= top_threshold).
        self._counts: dict[str, int] = {}
        self._counts_capped = False

    # CSV path: observed values are always strings.
    def observe(self, raw: str) -> None:
        self.observed += 1
        if _is_null_string(raw):
            self.nulls += 1
            return
        self._vote_for_type(raw)
        self._track_distinct(raw)
        self._track_counts(raw)
        self._track_numeric(raw)

    # JSON path: observed values come typed.
    def observe_python(self, value: Any) -> None:
        self.observed += 1
        if value is None:
            self.nulls += 1
            return
        if isinstance(value, bool):
            self.votes["bool"] += 1
            self._track_distinct(str(value))
            self._track_counts(str(value))
            return
        if isinstance(value, int):
            self.votes["int"] += 1
            self._track_distinct(str(value))
            self._track_counts(str(value))
            self._track_numeric_value(float(value))
            return
        if isinstance(value, float):
            self.votes["float"] += 1
            self._track_distinct(repr(value))
            self._track_counts(repr(value))
            self._track_numeric_value(value)
            return
        if isinstance(value, str):
            self._vote_for_type(value)
            self._track_distinct(value)
            self._track_counts(value)
            self._track_numeric(value)
            return
        # Complex value — coerce to JSON-ish string
        s = json.dumps(value, sort_keys=True, default=str)
        self.votes["string"] += 1
        self._track_distinct(s)

    def _vote_for_type(self, raw: str) -> None:
        if self.observed > self.opts.type_inference_rows:
            return  # cap reached; existing ballot stays the source of truth
        # bool
        if raw.lower() in {"true", "false"}:
            self.votes["bool"] += 1
            return
        # int (no decimal point, optionally signed)
        if _looks_like_int(raw):
            self.votes["int"] += 1
            return
        # float
        if _looks_like_float(raw):
            self.votes["float"] += 1
            return
        # date / datetime via simple shape heuristic
        if _looks_like_datetime(raw):
            self.votes["datetime"] += 1
            return
        if _looks_like_date(raw):
            self.votes["date"] += 1
            return
        self.votes["string"] += 1

    def _track_distinct(self, value: str) -> None:
        if self._distinct_capped:
            return
        if len(self._distinct) >= self.opts.distinct_cap:
            self._distinct_capped = True
            return
        self._distinct.add(value)

    def _track_counts(self, value: str) -> None:
        if self._counts_capped:
            return
        if len(self._counts) >= self.opts.top_threshold * 4 and value not in self._counts:
            # We're well past the top-threshold cardinality; stop
            # growing the counter dict.
            self._counts_capped = True
            return
        self._counts[value] = self._counts.get(value, 0) + 1

    def _track_numeric(self, raw: str) -> None:
        if not self.opts.include_quantiles:
            return
        try:
            v = float(raw)
        except ValueError:
            return
        self._track_numeric_value(v)

    def _track_numeric_value(self, v: float) -> None:
        if self._numeric_capped:
            return
        if len(self._numeric) >= self.opts.quantile_value_cap:
            self._numeric_capped = True
            return
        self._numeric.append(v)

    def winning_type(self) -> str:
        """Pick the type with the most votes (excluding null observations).

        Ties resolved by preferring more-specific over less-specific:
        bool > int > float > date > datetime > string.
        """
        # Heuristic: if there are ANY non-string-typed votes that
        # cover ≥ 95% of non-null values, the winner is that type.
        # Otherwise fall back to string. This rule prevents one stray
        # malformed cell from demoting a column from int to string.
        total = sum(v for k, v in self.votes.items() if k != "string")
        total_with_string = sum(self.votes.values())
        if total == 0:
            return "string"
        # Find the best non-string candidate
        non_string = {k: v for k, v in self.votes.items() if k != "string"}
        best_kind, best_votes = max(non_string.items(), key=lambda kv: kv[1])
        if total_with_string > 0 and best_votes / total_with_string >= 0.95:
            return best_kind
        return "string"

    def finalise(self, type_override: str | None = None) -> ColumnStats:
        """Compute the final ColumnStats from accumulators."""
        cs = ColumnStats(nulls=self.nulls, approximate=self._distinct_capped)

        if self.opts.include_distinct:
            if self._distinct_capped:
                cs.distinct = self.opts.distinct_cap  # ≥ cap; mark approximate
            else:
                cs.distinct = len(self._distinct)

        if self.opts.include_top and not self._counts_capped:
            distinct_count = cs.distinct if cs.distinct is not None else len(self._counts)
            if distinct_count is not None and distinct_count < self.opts.top_threshold:
                # Top values sorted by frequency desc, then key asc.
                top = sorted(
                    self._counts.items(),
                    key=lambda kv: (-kv[1], kv[0]),
                )[:5]
                cs.top = [k for k, _v in top]

        if self.opts.include_quantiles and self._numeric:
            # Use statistics.quantiles for p05/p50/p95 — we want
            # bracketed extremes, never raw min/max.
            try:
                # n=20 → cuts at every 5% — index 0 ≈ p05, index 9 ≈ p50, index 18 ≈ p95.
                cuts = statistics.quantiles(self._numeric, n=20, method="inclusive")
                cs.p05 = cuts[0]
                cs.p50 = cuts[9]
                cs.p95 = cuts[18]
            except statistics.StatisticsError:
                # < 2 numeric values; quantiles undefined. Leave None.
                pass

        return cs


class _SampleState:
    """Maintains a head buffer, tail circular buffer, and a reservoir
    for the random-middle section. ``materialise(rowcount)`` produces
    the final sample list in row-order.
    """

    def __init__(self, opts: SnapshotOptions, rng: random.Random) -> None:
        self.opts = opts
        self.rng = rng
        # Partition the sample budget across head + random + tail (when
        # the strategy is the mixed mode). Each gets ~1/3.
        strategy = opts.rows_strategy
        if strategy == "head":
            self.head_n = opts.rows
            self.tail_n = 0
            self.random_n = 0
        elif strategy == "tail":
            self.head_n = 0
            self.tail_n = opts.rows
            self.random_n = 0
        elif strategy == "random":
            self.head_n = 0
            self.tail_n = 0
            self.random_n = opts.rows
        else:
            # "head+random+tail" (default)
            head_n = max(1, opts.rows // 3)
            tail_n = max(1, opts.rows // 3)
            random_n = opts.rows - head_n - tail_n
            self.head_n = head_n
            self.tail_n = tail_n
            self.random_n = max(0, random_n)

        self._head: list[tuple[int, dict[str, Any]]] = []
        self._tail: list[tuple[int, dict[str, Any]]] = []
        self._reservoir: list[tuple[int, dict[str, Any]]] = []
        self._row_idx = 0

    def observe(self, record: dict[str, Any]) -> None:
        idx = self._row_idx
        self._row_idx += 1

        if len(self._head) < self.head_n:
            self._head.append((idx, dict(record)))
        # Always maintain tail
        if self.tail_n > 0:
            self._tail.append((idx, dict(record)))
            if len(self._tail) > self.tail_n:
                self._tail.pop(0)

        # Reservoir sampling (Algorithm R) for the middle section.
        if self.random_n > 0:
            # Don't include rows that already landed in head or will
            # land in tail.
            if idx < self.head_n:
                return  # head territory
            if len(self._reservoir) < self.random_n:
                self._reservoir.append((idx, dict(record)))
            else:
                j = self.rng.randint(0, idx)
                if j < self.random_n:
                    self._reservoir[j] = (idx, dict(record))

    def materialise(self, rowcount: int) -> list[dict[str, Any]]:
        # Drop tail entries that overlap with head (small files).
        tail_filtered = [
            (i, r) for i, r in self._tail
            if i >= self.head_n and rowcount - i <= self.tail_n
        ]
        # Drop reservoir entries that overlap with tail.
        tail_start = rowcount - len(tail_filtered)
        reservoir_filtered = [
            (i, r) for i, r in self._reservoir
            if i < tail_start
        ]
        merged = self._head + reservoir_filtered + tail_filtered
        # De-duplicate by index while preserving order.
        seen: set[int] = set()
        out: list[dict[str, Any]] = []
        for idx, rec in merged:
            if idx in seen:
                continue
            seen.add(idx)
            out.append(rec)
        return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_null_string(raw: str) -> bool:
    """A CSV cell that should count as null."""
    if raw is None:
        return True
    if raw == "":
        return True
    if raw.strip().lower() in {"null", "none", "nan", "n/a", "na"}:
        return True
    return False


def _looks_like_int(raw: str) -> bool:
    s = raw.strip()
    if not s:
        return False
    if s[0] in ("+", "-"):
        s = s[1:]
    return s.isdigit()


def _looks_like_float(raw: str) -> bool:
    s = raw.strip()
    if not s:
        return False
    try:
        float(s)
        # Exclude pure integers — _looks_like_int handles those.
        return "." in s or "e" in s.lower()
    except ValueError:
        return False


def _looks_like_date(raw: str) -> bool:
    """Match common ISO-8601 date shapes: YYYY-MM-DD."""
    s = raw.strip()
    if len(s) != 10:
        return False
    if s[4] != "-" or s[7] != "-":
        return False
    return s[:4].isdigit() and s[5:7].isdigit() and s[8:10].isdigit()


def _looks_like_datetime(raw: str) -> bool:
    """Match ISO-8601 datetime shapes (date + 'T' + time)."""
    s = raw.strip()
    if len(s) < 16:
        return False
    if s[4] != "-" or s[7] != "-" or s[10] not in ("T", " "):
        return False
    return s[:4].isdigit() and s[5:7].isdigit() and s[8:10].isdigit()


def _finalise_columns(
    header: list[str],
    col_state: dict[str, _ColumnAccumulator],
) -> tuple[list[ColumnSchema], dict[str, ColumnStats]]:
    """Turn accumulator state into ColumnSchema + ColumnStats objects."""
    schema: list[ColumnSchema] = []
    stats: dict[str, ColumnStats] = {}
    for name in header:
        acc = col_state[name]
        typ = acc.winning_type()
        cs = acc.finalise()
        schema.append(ColumnSchema(name=name, type=typ, cardinality=cs.distinct))
        stats[name] = cs
    return schema, stats


def _maybe_jitter_rowcount(
    rowcount: int,
    opts:     SnapshotOptions,
    rng:      random.Random,
) -> tuple[bool, int]:
    """Apply rowcount jitter for small datasets to reduce re-identification
    via exact counts. Returns ``(exact, published)``.

    For rowcount ≥ threshold, the count is exact (publishing exact
    counts on 10M rows is no PII leak). For < threshold, we add
    [-jitter, +jitter] uniform noise.
    """
    if rowcount >= opts.rowcount_jitter_threshold:
        return True, rowcount
    if opts.rowcount_jitter <= 0:
        return True, rowcount
    delta = rng.randint(-opts.rowcount_jitter, opts.rowcount_jitter)
    return False, max(0, rowcount + delta)


def _extract_records_from_json(raw: Any, path: Path) -> list[Any]:
    """Pull a list of records out of a top-level JSON value.

    Accepts: an array, an object with exactly one array-valued key.
    """
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        array_keys = [k for k, v in raw.items() if isinstance(v, list)]
        if len(array_keys) == 1:
            return raw[array_keys[0]]
        raise SnapshotError(
            f"{path}: top-level JSON object has {len(array_keys)} array-valued "
            f"keys (need exactly 1); flatten to JSONL or restructure."
        )
    raise SnapshotError(
        f"{path}: top-level JSON value is {type(raw).__name__}; "
        f"expected array or object-with-array."
    )
