"""SQL statement shapes vs prose for the delegation prefilter (review R2-SC-11)."""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "corvin_operator" / "bridges" / "shared"))
from delegation_policy import _DB_RE  # noqa: E402

SQL = [
    'UPDATE "orders" SET "status" = 1', "UPDATE orders o SET o.status = 2",
    "update low_priority t set x=1", "UPDATE t SET (a,b) = (1,2)", "UPDATE ONLY t SET a = 1",
    "INSERT INTO t DEFAULT VALUES", "insert ignore into t values (1)",
    'INSERT INTO "t" ("a") VALUES (1)', "insert or replace into t(a) values(1)",
    "DELETE FROM t WHERE id NOT IN (1,2)", "DELETE FROM t WHERE lower(name) = 'x'",
    'delete from "t" where "a" = 1', "DELETE FROM t AS x WHERE x.a=1",
]
PROSE = [
    "update my calendar and set a reminder",
    "delete from the draft the paragraph where we talk",
    "insert into the deck a chart and select colors",
    "please update the doc set it straight",
    "delete from my list where possible",
]


@pytest.mark.parametrize("q", SQL)
def test_statement_is_recognised(q):
    assert _DB_RE.search(q)


@pytest.mark.parametrize("q", PROSE)
def test_prose_is_not(q):
    assert not _DB_RE.search(q)


@pytest.mark.parametrize("blob", [
    "update " + "a " * 20000, "delete from " + "x " * 20000 + " where",
    "update a set " + "(" * 20000, "delete from t where " + "f(" * 20000,
])
def test_linear_on_hostile_input(blob):
    t = time.monotonic()
    _DB_RE.search(blob)
    assert time.monotonic() - t < 1.0
