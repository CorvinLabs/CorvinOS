"""Review 2026-11-19 round 3: R3-NEW-28 (CSV query limits) and R3-NEW-29 (function gate)."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO / "core" / "compute") not in sys.path:
    sys.path.insert(0, str(_REPO / "core" / "compute"))

from corvin_compute.fabric.datasources import sql_query as sq  # noqa: E402

DENIED = [
    "select query_to_xml('select pg_read_file(''/etc/passwd'')', true, true, '')",
    "select pg_terminate_backend(pid) from pg_stat_activity",
    "select PG_CATALOG . pg_cancel_backend (1)",
    "select lo_get(16385)",
    "select GET_LOCK('x', 100)",
    "select set_config('log_statement', 'all', false)",
    "select pg_catalog.set_config('a','b',false)",
    "select LOAD_FILE('/etc/passwd')",
    "select dblink_connect('host=evil')",
    "select * from dblink('host=evil', 'select 1') as t(a int)",
    "select xpath('//a', xml)  from t",
    "select table_to_xml('t', true, true, '')",
    "select * from (select pg_ls_dir('.')) s",
    "with x as (select lo_export(1, '/tmp/x')) select * from x",
    "select 1 where exists (select pg_advisory_lock(1))",
    "select \"pg_read_file\"('/etc/passwd')",
    "select `load_file`('/etc/passwd')",
    "select $$'$$, pg_read_file('/etc/passwd'), $$'$$",
    "select $q$ x $q$",
    "select E'\\'', pg_read_file('/etc/passwd'), '\\''",
    "select E'abc'",
    "select 'a\\'', sleep(10) -- '",
    "select ts_stat('select pg_read_file(''x'')')",
    "select zeroblob(400000000)",
    "select nextval('s')",
    "select 'unterminated",
    "select pg_sleep  (10)",
    "select 'x' where 'select pg_read_file(1)' = 'y'",
]

ALLOWED = [
    "select count(*), max(price) from data",
    "select name from data where name in ('a', 'b') order by 1",
    "with t as (select a, sum(b) as s from data group by a) select * from t where s > 3",
    "select coalesce(a, 'x'), lower(b), substr(c, 1, 3) from data",
    "select \"my col\" from data where note = 'it''s an update'",
    "select * from information_schema.tables",
    "select * from pg_catalog.pg_tables",
]


@pytest.mark.parametrize("sql", DENIED)
def test_denied_statements(sql):
    with pytest.raises(sq.QueryRefused):
        sq.validate_select(sql)


@pytest.mark.parametrize("sql", ALLOWED)
def test_ordinary_selects_still_pass(sql):
    assert sq.validate_select(sql)


def _csv(tmp_path, rows=400):
    path = tmp_path / "d.csv"
    path.write_text("a,b\n" + "".join(f"{i},x{i}\n" for i in range(rows)))
    return {"adapter": "local_file", "config": {"path": str(path)}}


def test_csv_query_basic(tmp_path):
    out = sq.run_readonly_query(_csv(tmp_path), {}, "select count(*) as n, max(a) as m from data")
    assert out["rows"] == [[400, 399]]


def test_csv_cross_product_stops_at_the_timeout(tmp_path):
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="time limit"):
        sq.run_readonly_query(
            _csv(tmp_path), {},
            "select count(*) from data a, data b, data c, data d", timeout_s=1)
    assert time.monotonic() - started < 6


def test_csv_recursive_cte_is_refused_by_the_authorizer(tmp_path):
    import sqlite3

    with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
        sq.run_readonly_query(
            _csv(tmp_path), {},
            "with recursive r(n) as (select 1 union all select n + 1 from r) "
            "select max(n) from r", timeout_s=1)


def test_csv_value_size_is_capped(tmp_path):
    # zeroblob itself is refused by name; the length limit catches every other builder.
    with pytest.raises(sq.QueryRefused):
        sq.run_readonly_query(_csv(tmp_path), {}, "select length(zeroblob(400000000))")
    with pytest.raises(RuntimeError):
        sq.run_readonly_query(
            _csv(tmp_path), {},
            "select length(replace(printf('%0900000d', 1), '0', '0000'))")


def test_csv_authorizer_denies_functions_by_name(tmp_path):
    import sqlite3

    assert sq._sqlite_read_only(sqlite3.SQLITE_FUNCTION, None, "load_extension", None, None) \
        == sqlite3.SQLITE_DENY
    assert sq._sqlite_read_only(sqlite3.SQLITE_FUNCTION, None, "lower", None, None) \
        == sqlite3.SQLITE_OK


def test_csv_row_cap(tmp_path):
    out = sq.run_readonly_query(_csv(tmp_path), {}, "select * from data", max_rows=5)
    assert out["row_count"] == 5 and out["truncated"] is True
