"""Read-only SQL over a registered DSI v1 connection (chat tool ``datasource_query``).

The caller (forge MCP server) has already applied the licence, residency and
classification gates and resolved the driver environment (credentials from the
secret vault). This module only runs ONE read-only statement and returns a
bounded result - it never sees a vault, a tenant or an LLM.

Guards (defence in depth - none of them is the only line):

1. ``validate_select`` - one statement, ``SELECT``/``WITH`` only, no
   write/DDL/file keywords, no comment tricks.
2. The session itself is read-only (``SET TRANSACTION READ ONLY`` /
   ``set_session(readonly=True)``), so a statement that slips past 1 still cannot
   write.
3. Statement timeout, row cap (``fetchmany``), cell-size cap, whole-result byte cap.
4. The connection's role should be SELECT-only (documented in the setup hint).

Adapters: ``postgresql`` (psycopg2), ``mysql``/``mariadb`` (pymysql) and
``local_file`` for a CSV (loaded into an in-memory SQLite table named ``data``).
"""
from __future__ import annotations

import csv
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

MAX_ROWS_CAP = 200
DEFAULT_ROWS = 50
MAX_CELL_CHARS = 500
MAX_RESULT_BYTES = 64 * 1024
MAX_SQL_CHARS = 4000
MAX_CSV_BYTES = 50 * 1024 * 1024


class QueryRefused(ValueError):
    """The statement is not an acceptable read-only SELECT."""


_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|merge|upsert|drop|alter|create|truncate|rename|grant|revoke|"
    r"copy|vacuum|attach|detach|pragma|into|outfile|dumpfile|load_file|pg_read_file|"
    r"pg_read_binary_file|pg_ls_dir|pg_stat_file|lo_import|lo_export|dblink|pg_sleep|"
    r"sleep|benchmark|nextval|setval)\b",
    re.IGNORECASE,
)


def validate_select(sql: str) -> str:
    """Return the statement without a trailing ``;`` or raise :class:`QueryRefused`."""
    if not isinstance(sql, str) or not sql.strip():
        raise QueryRefused("empty statement")
    if len(sql) > MAX_SQL_CHARS:
        raise QueryRefused(f"statement longer than {MAX_SQL_CHARS} characters")
    text = sql.strip()
    while text.endswith(";"):
        text = text[:-1].rstrip()
    if "\x00" in text:
        raise QueryRefused("invalid character")
    # Look at the statement with string literals and quoted identifiers blanked
    # out, so 'update' or 'C#' inside a value does not trip the guard.
    scrubbed = re.sub(r"'(?:[^']|'')*'", "''", text)
    scrubbed = re.sub(r'"(?:[^"]|"")*"', '""', scrubbed)
    scrubbed = re.sub(r"`[^`]*`", "``", scrubbed)
    if ";" in scrubbed:
        raise QueryRefused("exactly one statement is allowed")
    if "--" in scrubbed or "/*" in scrubbed or "#" in scrubbed:
        raise QueryRefused("comments are not allowed in a query")
    first = re.match(r"\s*(\w+)", scrubbed)
    if not first or first.group(1).lower() not in ("select", "with"):
        raise QueryRefused("only SELECT (or WITH ... SELECT) statements are allowed")
    hit = _FORBIDDEN.search(scrubbed)
    if hit:
        raise QueryRefused(f"keyword {hit.group(1).upper()!r} is not allowed in a read-only query")
    return text


def _clip(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<{len(bytes(value))} bytes>"
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    text = value if isinstance(value, str) else str(value)
    return text if len(text) <= MAX_CELL_CHARS else text[:MAX_CELL_CHARS] + "..."


def _package(columns: list[str], rows_iter: Any, max_rows: int, started: float) -> dict[str, Any]:
    out_rows: list[list[Any]] = []
    truncated = False
    size = 0
    for row in rows_iter:
        if len(out_rows) >= max_rows:
            truncated = True
            break
        clipped = [_clip(c) for c in row]
        size += sum(len(str(c)) for c in clipped) + 2
        if size > MAX_RESULT_BYTES:
            truncated = True
            break
        out_rows.append(clipped)
    return {
        "columns": columns,
        "rows": out_rows,
        "row_count": len(out_rows),
        "truncated": truncated,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }


def _run_postgres(raw: dict, env: dict, sql: str, max_rows: int, timeout_s: int) -> dict[str, Any]:
    try:
        import psycopg2  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError("psycopg2 is not installed (install the [compute] extra)") from exc
    cfg = raw.get("config") or {}
    started = time.monotonic()
    conn = psycopg2.connect(
        host=env.get("PGHOST") or cfg.get("host") or "localhost",
        port=int(env.get("PGPORT") or cfg.get("port") or 5432),
        dbname=env.get("PGDATABASE") or cfg.get("dbname") or cfg.get("database") or "",
        user=env["PGUSER"],
        password=env["PGPASSWORD"],
        connect_timeout=min(timeout_s, 10),
        options=f"-c statement_timeout={timeout_s * 1000} -c default_transaction_read_only=on",
    )
    try:
        conn.set_session(readonly=True, autocommit=False)
        with conn.cursor() as cur:
            cur.execute(sql)
            columns = [d[0] for d in (cur.description or [])]
            return _package(columns, _iter_cursor(cur, max_rows), max_rows, started)
    finally:
        try:
            conn.rollback()
        finally:
            conn.close()


def _run_mysql(raw: dict, env: dict, sql: str, max_rows: int, timeout_s: int) -> dict[str, Any]:
    try:
        import pymysql  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError("pymysql is not installed (install the [compute] extra)") from exc
    cfg = raw.get("config") or {}
    started = time.monotonic()
    conn = pymysql.connect(
        host=env.get("MYSQL_HOST") or cfg.get("host") or "localhost",
        port=int(env.get("MYSQL_PORT") or env.get("MYSQL_TCP_PORT") or cfg.get("port") or 3306),
        database=env.get("MYSQL_DATABASE") or cfg.get("database") or cfg.get("dbname") or "",
        user=env["MYSQL_USER"],
        password=env["MYSQL_PASSWORD"],
        connect_timeout=min(timeout_s, 10),
        read_timeout=timeout_s,
        autocommit=False,
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SET SESSION TRANSACTION READ ONLY")
            for stmt in (f"SET SESSION MAX_EXECUTION_TIME={timeout_s * 1000}",  # MySQL
                         f"SET SESSION max_statement_time={timeout_s}"):        # MariaDB
                try:
                    cur.execute(stmt)
                except Exception:  # noqa: BLE001 - the other server flavour's variable
                    pass
            cur.execute("START TRANSACTION READ ONLY")
            cur.execute(sql)
            columns = [d[0] for d in (cur.description or [])]
            result = _package(columns, _iter_cursor(cur, max_rows), max_rows, started)
        conn.rollback()
        return result
    finally:
        conn.close()


def _iter_cursor(cur: Any, max_rows: int):
    """Fetch at most ``max_rows + 1`` rows (the extra one proves truncation)."""
    for row in cur.fetchmany(max_rows + 1):
        yield tuple(row.values()) if isinstance(row, dict) else row


def _run_csv(raw: dict, sql: str, max_rows: int) -> dict[str, Any]:
    cfg = raw.get("config") or {}
    path = Path(str(cfg.get("path") or "")).expanduser()
    if not path.is_file() or path.suffix.lower() not in (".csv", ".tsv"):
        raise RuntimeError("the connection's file is missing or is not a .csv/.tsv file")
    if path.stat().st_size > MAX_CSV_BYTES:
        raise RuntimeError("the file is larger than the 50 MB query limit")
    delimiter = "\t" if path.suffix.lower() == ".tsv" else str(cfg.get("delimiter") or ",")
    started = time.monotonic()
    with path.open(newline="", encoding=str(cfg.get("encoding") or "utf-8")) as fh:
        reader = csv.reader(fh, delimiter=delimiter)
        header = next(reader, None)
        if not header:
            raise RuntimeError("the file has no header row")
        names = [re.sub(r"\W+", "_", h.strip()).strip("_") or f"col{i}" for i, h in enumerate(header)]
        db = sqlite3.connect(":memory:")
        try:
            cols = ", ".join(f'"{n}"' for n in names)
            db.execute(f"CREATE TABLE data ({cols})")
            marks = ", ".join("?" for _ in names)
            db.executemany(
                f"INSERT INTO data VALUES ({marks})",
                (row[: len(names)] + [""] * (len(names) - len(row)) for row in reader),
            )
            # Numeric-looking columns compare numerically: store them as numbers.
            for n in names:
                db.execute(
                    f'UPDATE data SET "{n}" = CAST("{n}" AS INTEGER) '
                    f'WHERE "{n}" GLOB \'[0-9]*\' AND "{n}" NOT GLOB \'*[^0-9]*\''
                )
                db.execute(
                    f'UPDATE data SET "{n}" = CAST("{n}" AS REAL) '
                    f'WHERE typeof("{n}") = \'text\' AND "{n}" GLOB \'[0-9]*.[0-9]*\' '
                    f'AND "{n}" NOT GLOB \'*[^0-9.]*\''
                )
            db.set_authorizer(_sqlite_read_only)
            cur = db.execute(sql)
            columns = [d[0] for d in (cur.description or [])]
            return _package(columns, cur.fetchmany(max_rows + 1), max_rows, started)
        finally:
            db.close()


def _sqlite_read_only(action: int, *_args: Any) -> int:
    allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}
    return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY


def run_readonly_query(
    raw_manifest: dict[str, Any],
    env: dict[str, str],
    sql: str,
    *,
    max_rows: int = DEFAULT_ROWS,
    timeout_s: int = 15,
) -> dict[str, Any]:
    """Run one validated SELECT against the connection described by *raw_manifest*."""
    statement = validate_select(sql)
    max_rows = max(1, min(int(max_rows), MAX_ROWS_CAP))
    timeout_s = max(1, min(int(timeout_s), 60))
    adapter = str(raw_manifest.get("adapter", "")).lower()
    if adapter in ("postgresql", "postgres"):
        return _run_postgres(raw_manifest, env, statement, max_rows, timeout_s)
    if adapter in ("mysql", "mariadb"):
        return _run_mysql(raw_manifest, env, statement, max_rows, timeout_s)
    if adapter == "local_file":
        return _run_csv(raw_manifest, statement, max_rows)
    raise RuntimeError(f"adapter {adapter!r} cannot be queried from chat in this build")


__all__ = ["QueryRefused", "validate_select", "run_readonly_query", "MAX_ROWS_CAP"]
