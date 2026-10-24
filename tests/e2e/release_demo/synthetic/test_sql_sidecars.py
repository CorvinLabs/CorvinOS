"""Opt-in: load the synthetic per-table CSVs into throwaway PostgreSQL and MySQL servers.

Set CORVIN_DB_BIN_DIR to a directory holding initdb/pg_ctl/psql and/or
mysqld/mysql/mysqladmin. Each engine is skipped when its binaries are absent. A server
runs only for this module: data directory under tmp_path, a free port on 127.0.0.1, no
unix socket for PostgreSQL, a socket under tmp_path for MySQL. It is stopped afterwards.
The DDL (generator.DDL_POSTGRES / DDL_MYSQL) and the load commands (LOAD_POSTGRES /
LOAD_MYSQL) are the ones README.md documents; the 10 fact queries come from truth.json.
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest

from tests.e2e.release_demo.synthetic import generator as gen

BIN_ENV = "CORVIN_DB_BIN_DIR"


def _binaries(*names: str) -> list[Path]:
    root = os.environ.get(BIN_ENV)
    if not root:
        pytest.skip(f"{BIN_ENV} unset (opt-in test)")
    paths = [Path(root) / n for n in names]
    missing = [p.name for p in paths if not p.is_file()]
    if missing:
        pytest.skip(f"{', '.join(missing)} not in {BIN_ENV}")
    return paths


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run(cmd: list, env: dict, check: bool = True):
    done = subprocess.run([str(c) for c in cmd], env=env, capture_output=True, text=True, timeout=180)
    if not check:
        return done.returncode, done.stderr
    assert done.returncode == 0, f"{cmd[0]} exit {done.returncode}: {done.stderr[-2000:]}"
    return done.stdout


@pytest.fixture(scope="module")
def dataset(tmp_path_factory) -> dict[str, Path]:
    return gen.generate(tmp_path_factory.mktemp("sidecar_data"))


@pytest.fixture(scope="module")
def truth(dataset) -> dict:
    return json.loads(dataset["truth"].read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def postgres(tmp_path_factory, dataset):
    initdb, pg_ctl, psql = _binaries("initdb", "pg_ctl", "psql")
    root = tmp_path_factory.mktemp("pg")
    data, port = root / "data", _free_port()
    env = dict(os.environ, LC_ALL="C", LANG="C", PGTZ="UTC")
    _run([initdb, "-D", data, "-U", "postgres", "-A", "trust", "-E", "UTF8", "--locale=C"], env)
    _run([pg_ctl, "-D", data, "-l", root / "server.log", "-w", "-t", "60",
          "-o", f"-p {port} -c listen_addresses=127.0.0.1 -c unix_socket_directories=''", "start"], env)
    try:
        def sql(statement: str, check: bool = True):
            return _run([psql, "-X", "-h", "127.0.0.1", "-p", port, "-U", "postgres", "-d", "postgres",
                         "-v", "ON_ERROR_STOP=1", "-At", "-F", "\t", "-c", statement], env, check)

        for stmt in gen.DDL_POSTGRES:
            sql(stmt)
        for table in gen.TABLES:
            sql(gen.LOAD_POSTGRES.format(table=table, path=dataset[f"table_{table}"]))
        yield sql
    finally:
        subprocess.run([str(pg_ctl), "-D", str(data), "-m", "immediate", "-w", "stop"],
                       env=env, capture_output=True, timeout=60)


@pytest.fixture(scope="module")
def mysql(tmp_path_factory, dataset):
    mysqld, client, admin = _binaries("mysqld", "mysql", "mysqladmin")
    root = tmp_path_factory.mktemp("my")
    data, port, basedir = root / "data", _free_port(), mysqld.parent.parent
    tables_dir = dataset["table_songs"].parent
    env = dict(os.environ, LC_ALL="C", LANG="C", TZ="UTC")
    _run([mysqld, "--no-defaults", f"--basedir={basedir}", f"--datadir={data}",
          f"--log-error={root / 'init.err'}", "--initialize-insecure"], env)
    proc = subprocess.Popen(
        [str(mysqld), "--no-defaults", f"--basedir={basedir}", f"--datadir={data}", f"--port={port}",
         "--bind-address=127.0.0.1", f"--socket={root / 'mysqld.sock'}", "--mysqlx=OFF",
         f"--secure-file-priv={tables_dir}", f"--pid-file={root / 'mysqld.pid'}",
         f"--log-error={root / 'server.err'}", "--skip-log-bin"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = [client, "--no-defaults", "-h127.0.0.1", f"-P{port}", "-uroot", "-N", "-B"]
    try:
        deadline = time.monotonic() + 90
        while subprocess.run([str(admin), "--no-defaults", "-h127.0.0.1", f"-P{port}", "-uroot", "ping"],
                             env=env, capture_output=True).returncode != 0:
            assert proc.poll() is None, (root / "server.err").read_text(errors="replace")[-2000:]
            assert time.monotonic() < deadline, "mysqld did not come up"
            time.sleep(0.5)
        _run(base + ["-e", "CREATE DATABASE charts CHARACTER SET utf8mb4"], env)

        def sql(statement: str, check: bool = True, database: str = "charts"):
            return _run(base + [database, "-e", statement], env, check)

        for stmt in gen.DDL_MYSQL:
            sql(stmt)
        for table in gen.TABLES:
            sql(gen.LOAD_MYSQL.format(table=table, path=dataset[f"table_{table}"]))
        yield sql
    finally:
        subprocess.run([str(admin), "--no-defaults", "-h127.0.0.1", f"-P{port}", "-uroot", "shutdown"],
                       env=env, capture_output=True, timeout=60)
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()


def _rows(output: str) -> list[list[str]]:
    return [line.split("\t") for line in output.splitlines() if line]


def _sqlite_scalar(dataset, statement: str) -> str:
    con = sqlite3.connect(f"file:{dataset['db']}?mode=ro", uri=True)
    try:
        return str(con.execute(statement).fetchone()[0])
    finally:
        con.close()


CHECKS = [
    "SELECT COUNT(*) FROM charts_songs_daily",
    "SELECT COUNT(*) FROM songs",
    "SELECT COUNT(*) FROM albums",
    "SELECT COUNT(*) FROM artists",
    "SELECT COUNT(*) FROM charts_songs_daily WHERE previous_rank IS NULL",
    "SELECT COUNT(*) FROM songs WHERE label LIKE '%''%'",
    "SELECT COUNT(*) FROM artists WHERE artist_name LIKE '%''%'",
    "SELECT COUNT(*) FROM songs WHERE artist_names LIKE '%, %'",
    "SELECT COUNT(*) FROM top_tracks_by_country",
    "SELECT COUNT(*) FROM artist_summary",
    "SELECT SUM(total_streams) FROM top_tracks_by_country",
]


def _check_engine(sql, dataset, truth, mysql_quoting: bool) -> None:
    for statement in CHECKS:
        assert _rows(sql(statement)) == [[_sqlite_scalar(dataset, statement)]], statement
    assert len(truth["facts"]) == 10
    for fact in truth["facts"]:
        statement = fact["sql"].replace('"rank"', "`rank`") if mysql_quoting else fact["sql"]
        expected = [[fact["answer_key"], str(fact["value"])],
                    [fact["runner_up"]["key"], str(fact["runner_up"]["value"])]]
        assert _rows(sql(statement)) == expected, fact["id"]


def test_postgres_loads_the_csvs_and_answers_every_fact(postgres, dataset, truth):
    _check_engine(postgres, dataset, truth, mysql_quoting=False)


def test_mysql_loads_the_csvs_and_answers_every_fact(mysql, dataset, truth):
    _check_engine(mysql, dataset, truth, mysql_quoting=True)


# ---------------------------------------------------------------- controls: the verbatim DDL fails


def test_postgres_rejects_the_verbatim_sqlite_view(postgres):
    postgres("CREATE SCHEMA verbatim")
    results = [postgres(f"SET search_path TO verbatim; {stmt}", check=False) for stmt in gen.DDL]
    assert [code for code, _ in results[:7]] == [0] * 7
    code, err = results[7]
    assert code != 0 and "must appear in the GROUP BY clause" in err, err


def test_mysql_rejects_the_verbatim_sqlite_ddl(mysql):
    mysql("CREATE DATABASE verbatim", database="charts")
    run = lambda stmt: mysql(stmt, check=False, database="verbatim")  # noqa: E731
    # Only `rank` quoted; everything else as in the SQLite DDL.
    quoted = [stmt.replace(" rank ", " `rank` ").replace("(rank)", "(`rank`)") for stmt in gen.DDL]
    code, err = run(gen.DDL[2])
    assert code != 0, "unquoted rank should be a syntax error"
    tables = [run(stmt) for stmt in quoted[:4]]  # albums, artists, charts_songs_daily, songs
    assert tables[2][0] == 0, tables[2]  # with `rank` quoted the chart table is accepted
    for code, err in (tables[0], tables[1], tables[3]):  # TEXT PRIMARY KEY is rejected
        assert code != 0 and "1170" in err, err
    index_results = [run(stmt) for stmt in quoted[4:6]]
    assert all(c != 0 and "1170" in e for c, e in index_results), index_results
    view_code, view_err = run(quoted[7])
    query_code, query_err = run("SELECT COUNT(*) FROM top_tracks_by_country") if view_code == 0 else (view_code, view_err)
    assert query_code != 0 and "1055" in query_err, query_err
