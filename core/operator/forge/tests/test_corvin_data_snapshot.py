"""Phase 12.1 E2E — format sniffing + snapshot generation.

Covers:
  * format_sniffer: csv / tsv / json / jsonl / parquet-magic / extension /
    content-heuristic / unsupported
  * snapshot for CSV: header, type inference (int/float/date/string),
    nulls, distinct, top, quantiles, sample composition
  * snapshot for TSV: tab separator works
  * snapshot for JSON: top-level array + top-level-object-with-array;
    rejection of ambiguous shapes
  * snapshot for JSONL: streaming, varying schemas across rows
  * rowcount jitter behaviour for < 100-row and ≥ 100-row files
  * reservoir reproducibility under a fixed seed

Runs without external deps. Parquet is exercised via a magic-byte
sniff test only — actual parquet decode needs duckdb and is gated
inside the snapshot module itself (we test the ImportError path).
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from forge.corvin_data import (  # noqa: E402
    Snapshot,
    SnapshotError,
    SnapshotOptions,
    UnsupportedFormat,
    generate_snapshot,
    sniff_format,
)


PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


# ---------------------------------------------------------------------------
# format_sniffer
# ---------------------------------------------------------------------------

def test_sniff_csv_by_extension():
    print("\n[sniff: csv via extension]")
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as fh:
        fh.write("a,b,c\n1,2,3\n")
        p = Path(fh.name)
    try:
        t("csv extension", sniff_format(p) == "csv")
    finally:
        p.unlink()


def test_sniff_tsv_by_extension():
    print("\n[sniff: tsv via extension]")
    with tempfile.NamedTemporaryFile("w", suffix=".tsv", delete=False) as fh:
        fh.write("a\tb\tc\n1\t2\t3\n")
        p = Path(fh.name)
    try:
        t("tsv extension", sniff_format(p) == "tsv")
    finally:
        p.unlink()


def test_sniff_json_by_extension():
    print("\n[sniff: json via extension]")
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        fh.write('[{"a": 1}]')
        p = Path(fh.name)
    try:
        t("json extension", sniff_format(p) == "json")
    finally:
        p.unlink()


def test_sniff_jsonl_by_extension():
    print("\n[sniff: jsonl via extension]")
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
        fh.write('{"a": 1}\n{"a": 2}\n')
        p = Path(fh.name)
    try:
        t("jsonl extension", sniff_format(p) == "jsonl")
    finally:
        p.unlink()


def test_sniff_ndjson_alternative():
    print("\n[sniff: ndjson extension maps to jsonl]")
    with tempfile.NamedTemporaryFile("w", suffix=".ndjson", delete=False) as fh:
        fh.write('{"a": 1}\n')
        p = Path(fh.name)
    try:
        t("ndjson → jsonl", sniff_format(p) == "jsonl")
    finally:
        p.unlink()


def test_sniff_parquet_magic():
    print("\n[sniff: parquet via PAR1 magic]")
    with tempfile.NamedTemporaryFile("wb", suffix=".dat", delete=False) as fh:
        fh.write(b"PAR1\x00\x01\x02\x03")
        p = Path(fh.name)
    try:
        t("parquet magic", sniff_format(p) == "parquet")
    finally:
        p.unlink()


def test_sniff_content_csv_no_extension():
    print("\n[sniff: csv via content when no extension]")
    with tempfile.NamedTemporaryFile("w", suffix="", delete=False) as fh:
        fh.write("a,b,c\n1,2,3\n4,5,6\n")
        p = Path(fh.name)
    try:
        t("content-csv", sniff_format(p) == "csv")
    finally:
        p.unlink()


def test_sniff_format_hint_overrides():
    print("\n[sniff: explicit format_hint overrides]")
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
        fh.write("a,b,c\n1,2,3\n")
        p = Path(fh.name)
    try:
        t("hint=csv on .txt", sniff_format(p, format_hint="csv") == "csv")
    finally:
        p.unlink()


def test_sniff_invalid_hint_rejected():
    print("\n[sniff: invalid format_hint rejected]")
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as fh:
        fh.write("a,b\n1,2\n")
        p = Path(fh.name)
    try:
        try:
            sniff_format(p, format_hint="xml")
            t("rejects xml hint", False, detail="should have raised")
        except UnsupportedFormat as e:
            t("rejects xml hint", "xml" in str(e))
    finally:
        p.unlink()


def test_sniff_unsupported_format():
    print("\n[sniff: random binary raises UnsupportedFormat]")
    with tempfile.NamedTemporaryFile("wb", suffix=".bin", delete=False) as fh:
        fh.write(b"\x00\x01\x02\x03\x04\x05\x06\x07")  # no signal at all
        p = Path(fh.name)
    try:
        try:
            sniff_format(p)
            t("unsupported", False, detail="should have raised")
        except UnsupportedFormat:
            t("unsupported", True)
    finally:
        p.unlink()


def test_sniff_missing_file():
    print("\n[sniff: missing file raises UnsupportedFormat]")
    try:
        sniff_format("/nonexistent/path/file.csv")
        t("missing file", False, detail="should have raised")
    except UnsupportedFormat as e:
        t("missing file", "does not exist" in str(e))


# ---------------------------------------------------------------------------
# CSV snapshot
# ---------------------------------------------------------------------------

def _write_csv(content: str) -> Path:
    fh = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8")
    fh.write(content)
    fh.close()
    return Path(fh.name)


def test_csv_basic_header_and_rowcount():
    print("\n[csv: header + rowcount on small file]")
    p = _write_csv("a,b,c\n1,2,3\n4,5,6\n7,8,9\n")
    try:
        # Use seed=0 + jitter=0 to make rowcount exact-and-deterministic.
        snap = generate_snapshot(
            p,
            options=SnapshotOptions(seed=0, rowcount_jitter=0),
        )
        t("header names", [c.name for c in snap.schema] == ["a", "b", "c"])
        t("rowcount", snap.file.rowcount == 3)
        t("file format", snap.file.format == "csv")
    finally:
        p.unlink()


def test_csv_type_inference_int():
    print("\n[csv: int columns detected]")
    p = _write_csv("a,b\n1,10\n2,20\n3,30\n4,40\n")
    try:
        snap = generate_snapshot(p, options=SnapshotOptions(seed=0))
        a = next(c for c in snap.schema if c.name == "a")
        b = next(c for c in snap.schema if c.name == "b")
        t("col a int", a.type == "int", detail=a.type)
        t("col b int", b.type == "int", detail=b.type)
    finally:
        p.unlink()


def test_csv_type_inference_float():
    print("\n[csv: float column detected]")
    p = _write_csv("price\n1.5\n2.25\n3.75\n100.0\n")
    try:
        snap = generate_snapshot(p, options=SnapshotOptions(seed=0))
        col = snap.schema[0]
        t("float type", col.type == "float", detail=col.type)
    finally:
        p.unlink()


def test_csv_type_inference_date():
    print("\n[csv: date column detected]")
    p = _write_csv("dt\n2026-01-01\n2026-02-15\n2026-03-30\n")
    try:
        snap = generate_snapshot(p, options=SnapshotOptions(seed=0))
        col = snap.schema[0]
        t("date type", col.type == "date", detail=col.type)
    finally:
        p.unlink()


def test_csv_type_inference_string():
    print("\n[csv: mixed cells → string]")
    p = _write_csv("name\nalice\nbob\ncharlie\n")
    try:
        snap = generate_snapshot(p, options=SnapshotOptions(seed=0))
        col = snap.schema[0]
        t("string type", col.type == "string", detail=col.type)
    finally:
        p.unlink()


def test_csv_nulls_counted():
    print("\n[csv: empty / null / NaN values counted as nulls]")
    p = _write_csv("a\n1\n\nnull\nNULL\nNaN\n5\n")
    try:
        snap = generate_snapshot(p, options=SnapshotOptions(seed=0))
        st = snap.stats["a"]
        t("4 nulls", st.nulls == 4, detail=f"got {st.nulls}")
    finally:
        p.unlink()


def test_csv_distinct_small_cardinality_emits_top():
    print("\n[csv: small cardinality column emits top values]")
    p = _write_csv(
        "country\nDE\nAT\nDE\nCH\nDE\nAT\nDE\nCH\n"
    )
    try:
        snap = generate_snapshot(p, options=SnapshotOptions(seed=0))
        st = snap.stats["country"]
        t("distinct = 3", st.distinct == 3, detail=f"got {st.distinct}")
        t("top contains DE first", st.top is not None and st.top[0] == "DE",
          detail=str(st.top))
    finally:
        p.unlink()


def test_csv_quantiles_for_numeric():
    print("\n[csv: quantiles p05/p50/p95 for numeric column]")
    # 20 values 1..20 → p05 ≈ 1, p50 ≈ 10, p95 ≈ 19 (approx).
    rows = "\n".join(str(i) for i in range(1, 21))
    p = _write_csv(f"x\n{rows}\n")
    try:
        snap = generate_snapshot(p, options=SnapshotOptions(seed=0))
        st = snap.stats["x"]
        t("p05 set", st.p05 is not None)
        t("p50 in (8, 12)", st.p50 is not None and 8 <= st.p50 <= 12,
          detail=str(st.p50))
        t("p95 set", st.p95 is not None)
    finally:
        p.unlink()


def test_csv_sample_composition():
    print("\n[csv: sample is head+random+tail by default]")
    # 100 rows. With rows=20 default, expect 6 head + 7 random + 6 tail = ~19-20.
    body = "\n".join(f"r{i}" for i in range(100))
    p = _write_csv(f"name\n{body}\n")
    try:
        snap = generate_snapshot(p, options=SnapshotOptions(seed=42, rows=20))
        # First sample row is r0 (head).
        t("head starts at row 0", snap.sample[0]["name"] == "r0")
        # Last sample row is r99 (tail).
        t("tail ends at row 99", snap.sample[-1]["name"] == "r99")
        # Sample length close to 20.
        t("sample size ≈ 20", 15 <= len(snap.sample) <= 20,
          detail=f"got {len(snap.sample)}")
    finally:
        p.unlink()


def test_csv_reservoir_reproducible_with_seed():
    print("\n[csv: same seed → same sample]")
    body = "\n".join(f"r{i}" for i in range(100))
    p = _write_csv(f"name\n{body}\n")
    try:
        s1 = generate_snapshot(p, options=SnapshotOptions(seed=1234, rows=20))
        s2 = generate_snapshot(p, options=SnapshotOptions(seed=1234, rows=20))
        t("identical sample under same seed",
          [r["name"] for r in s1.sample] == [r["name"] for r in s2.sample])
    finally:
        p.unlink()


# ---------------------------------------------------------------------------
# TSV snapshot
# ---------------------------------------------------------------------------

def test_tsv_basic():
    print("\n[tsv: tab-separated parsed correctly]")
    fh = tempfile.NamedTemporaryFile("w", suffix=".tsv", delete=False, encoding="utf-8")
    fh.write("a\tb\n1\t2\n3\t4\n")
    fh.close()
    p = Path(fh.name)
    try:
        snap = generate_snapshot(p, options=SnapshotOptions(seed=0, rowcount_jitter=0))
        t("tsv header", [c.name for c in snap.schema] == ["a", "b"])
        t("tsv rowcount", snap.file.rowcount == 2)
        t("tsv format", snap.file.format == "tsv")
    finally:
        p.unlink()


# ---------------------------------------------------------------------------
# JSON snapshot
# ---------------------------------------------------------------------------

def test_json_array_of_objects():
    print("\n[json: top-level array]")
    fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump([{"a": 1, "b": "x"}, {"a": 2, "b": "y"}, {"a": 3, "b": "z"}], fh)
    fh.close()
    p = Path(fh.name)
    try:
        snap = generate_snapshot(p, options=SnapshotOptions(seed=0, rowcount_jitter=0))
        t("json rowcount", snap.file.rowcount == 3)
        t("json schema", {c.name for c in snap.schema} == {"a", "b"})
        cols = {c.name: c for c in snap.schema}
        t("json int detected", cols["a"].type == "int")
        t("json string detected", cols["b"].type == "string")
    finally:
        p.unlink()


def test_json_object_with_array():
    print("\n[json: top-level object with single array value]")
    fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump({"records": [{"x": 1}, {"x": 2}]}, fh)
    fh.close()
    p = Path(fh.name)
    try:
        snap = generate_snapshot(p, options=SnapshotOptions(seed=0, rowcount_jitter=0))
        t("flattened rowcount", snap.file.rowcount == 2)
    finally:
        p.unlink()


def test_json_ambiguous_object_rejected():
    print("\n[json: top-level object with multiple arrays rejected]")
    fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump({"a": [1], "b": [2]}, fh)
    fh.close()
    p = Path(fh.name)
    try:
        try:
            generate_snapshot(p, options=SnapshotOptions(seed=0))
            t("ambiguous rejected", False, detail="should have raised")
        except SnapshotError as e:
            t("ambiguous rejected", "array-valued keys" in str(e))
    finally:
        p.unlink()


# ---------------------------------------------------------------------------
# JSONL snapshot
# ---------------------------------------------------------------------------

def test_jsonl_basic():
    print("\n[jsonl: streaming line-by-line]")
    fh = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
    for i in range(5):
        fh.write(json.dumps({"x": i, "name": f"row_{i}"}) + "\n")
    fh.close()
    p = Path(fh.name)
    try:
        snap = generate_snapshot(p, options=SnapshotOptions(seed=0, rowcount_jitter=0))
        t("jsonl rowcount", snap.file.rowcount == 5)
        t("jsonl format", snap.file.format == "jsonl")
        cols = {c.name: c for c in snap.schema}
        t("jsonl int type", cols["x"].type == "int")
    finally:
        p.unlink()


def test_jsonl_varying_schema_unions():
    print("\n[jsonl: union of keys across rows]")
    fh = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
    fh.write(json.dumps({"a": 1}) + "\n")
    fh.write(json.dumps({"a": 2, "b": "extra"}) + "\n")
    fh.close()
    p = Path(fh.name)
    try:
        snap = generate_snapshot(p, options=SnapshotOptions(seed=0))
        names = {c.name for c in snap.schema}
        t("union of keys", names == {"a", "b"})
    finally:
        p.unlink()


def test_jsonl_malformed_line_raises():
    print("\n[jsonl: malformed line raises SnapshotError]")
    fh = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
    fh.write(json.dumps({"a": 1}) + "\n")
    fh.write("{this is not valid json\n")
    fh.close()
    p = Path(fh.name)
    try:
        try:
            generate_snapshot(p, options=SnapshotOptions(seed=0))
            t("malformed rejected", False, detail="should have raised")
        except SnapshotError as e:
            t("malformed rejected", "JSONL parse failed" in str(e))
    finally:
        p.unlink()


# ---------------------------------------------------------------------------
# Rowcount jitter behaviour
# ---------------------------------------------------------------------------

def test_rowcount_exact_for_large_file():
    print("\n[rowcount: exact when ≥ jitter_threshold]")
    body = "\n".join(str(i) for i in range(150))
    p = _write_csv(f"x\n{body}\n")
    try:
        snap = generate_snapshot(p, options=SnapshotOptions(seed=0))
        t("rowcount = 150", snap.file.rowcount == 150)
        t("rowcount_exact = True", snap.file.rowcount_exact is True)
    finally:
        p.unlink()


def test_rowcount_noised_for_small_file():
    print("\n[rowcount: jittered when < jitter_threshold]")
    p = _write_csv("x\n1\n2\n3\n4\n5\n")
    try:
        snap = generate_snapshot(p, options=SnapshotOptions(seed=0, rowcount_jitter=5))
        # With seed=0 and jitter=5, rowcount is some integer in [0, 10].
        t("rowcount_exact = False", snap.file.rowcount_exact is False)
        t("rowcount in [0, 10]", 0 <= snap.file.rowcount <= 10,
          detail=f"got {snap.file.rowcount}")
    finally:
        p.unlink()


# ---------------------------------------------------------------------------
# Parquet ImportError-when-duckdb-missing
# ---------------------------------------------------------------------------

def test_parquet_without_duckdb_raises_importerror():
    print("\n[parquet: requires duckdb; raises ImportError when absent]")
    # Write the parquet magic so the sniff lands on parquet.
    fh = tempfile.NamedTemporaryFile("wb", suffix=".parquet", delete=False)
    fh.write(b"PAR1" + b"\x00" * 32)
    fh.close()
    p = Path(fh.name)
    try:
        try:
            import duckdb  # noqa: F401  -- if installed, skip this case
            print("    SKIP (duckdb available on this host)")
            return
        except ImportError:
            pass
        try:
            generate_snapshot(p, options=SnapshotOptions(seed=0))
            t("parquet importerror", False, detail="should have raised")
        except ImportError as e:
            t("parquet importerror", "duckdb" in str(e).lower())
    finally:
        p.unlink()


# ---------------------------------------------------------------------------
# Empty / edge cases
# ---------------------------------------------------------------------------

def test_csv_empty_file_raises():
    print("\n[csv: empty file raises SnapshotError]")
    p = _write_csv("")
    try:
        try:
            generate_snapshot(p, options=SnapshotOptions(seed=0))
            t("empty rejected", False, detail="should have raised")
        except SnapshotError as e:
            t("empty rejected", "empty" in str(e).lower())
    finally:
        p.unlink()


def test_csv_header_only_zero_rows():
    print("\n[csv: header-only file has rowcount 0]")
    p = _write_csv("a,b,c\n")
    try:
        snap = generate_snapshot(p, options=SnapshotOptions(seed=0, rowcount_jitter=0))
        t("rowcount = 0", snap.file.rowcount == 0)
        t("schema preserved", [c.name for c in snap.schema] == ["a", "b", "c"])
    finally:
        p.unlink()


def test_snapshot_to_dict_serializable():
    print("\n[snapshot: to_dict produces JSON-serialisable structure]")
    p = _write_csv("a,b\n1,x\n2,y\n3,z\n")
    try:
        snap = generate_snapshot(p, options=SnapshotOptions(seed=0))
        d = snap.to_dict()
        # Round-trip through json
        s = json.dumps(d)
        d2 = json.loads(s)
        t("dict round-trips through json", d2 == d)
    finally:
        p.unlink()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    # format_sniffer
    test_sniff_csv_by_extension()
    test_sniff_tsv_by_extension()
    test_sniff_json_by_extension()
    test_sniff_jsonl_by_extension()
    test_sniff_ndjson_alternative()
    test_sniff_parquet_magic()
    test_sniff_content_csv_no_extension()
    test_sniff_format_hint_overrides()
    test_sniff_invalid_hint_rejected()
    test_sniff_unsupported_format()
    test_sniff_missing_file()
    # csv snapshot
    test_csv_basic_header_and_rowcount()
    test_csv_type_inference_int()
    test_csv_type_inference_float()
    test_csv_type_inference_date()
    test_csv_type_inference_string()
    test_csv_nulls_counted()
    test_csv_distinct_small_cardinality_emits_top()
    test_csv_quantiles_for_numeric()
    test_csv_sample_composition()
    test_csv_reservoir_reproducible_with_seed()
    # tsv
    test_tsv_basic()
    # json
    test_json_array_of_objects()
    test_json_object_with_array()
    test_json_ambiguous_object_rejected()
    # jsonl
    test_jsonl_basic()
    test_jsonl_varying_schema_unions()
    test_jsonl_malformed_line_raises()
    # rowcount jitter
    test_rowcount_exact_for_large_file()
    test_rowcount_noised_for_small_file()
    # parquet (gated)
    test_parquet_without_duckdb_raises_importerror()
    # edge cases
    test_csv_empty_file_raises()
    test_csv_header_only_zero_rows()
    test_snapshot_to_dict_serializable()

    print(f"\n{'=' * 50}")
    print(f"PASS={PASS}  FAIL={FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
