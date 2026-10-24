"""Tests for the synthetic chart dataset (WP-04 / W-2, W-7). Synthetic variant only (Q18).

Nothing here reads a real chart archive. A reference DB is consulted only when
CORVIN_REFERENCE_CHARTS_DB names one, and then only its schema (sqlite_master / PRAGMA).
"""

from __future__ import annotations

import csv
import json
import os
import re
import sqlite3
from datetime import date
from itertools import combinations
from pathlib import Path

import pytest

from tests.e2e.release_demo.synthetic import generator as gen

HERE = Path(__file__).resolve().parent
COMMITTED_SCHEMA = HERE / "schema_columns.tsv"
DENYLIST = HERE / "denylist_real_names.txt"
REFERENCE_ENV = "CORVIN_REFERENCE_CHARTS_DB"

# Pinned regression values for SEED. A change to the generator, a word list or the seed
# changes them; update them in the same commit, deliberately.
EXPECTED_DB_TABLE_DIGEST = "2fb89939a6e833bdf13f656ebf136790eafb90e2755ff0df59ea875d07d46fc2"
EXPECTED_TEXT_OUTPUTS_DIGEST = "2f1db4acd4ec55a776e2d3ef79cf235ddd5c0ce27bc072adeb391169ad020bcb"

WORDLISTS = {
    "artist": ("artist_prefixes.txt", "artist_suffixes.txt"),
    "track": ("track_first_words.txt", "track_second_words.txt"),
    "album": ("album_first_words.txt", "album_second_words.txt"),
    "label": ("label_words.txt",),
}


@pytest.fixture(scope="module")
def generated(tmp_path_factory) -> dict[str, Path]:
    return gen.generate(tmp_path_factory.mktemp("synthetic"))


@pytest.fixture(scope="module")
def truth(generated) -> dict:
    return json.loads(generated["truth"].read_text(encoding="utf-8"))


def _ro(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _combos(first: str, second: str) -> set[str]:
    return {f"{a} {b}" for a in gen._words(first) for b in gen._words(second)}


# ---------------------------------------------------------------- determinism


def test_generation_is_deterministic(tmp_path):
    first = gen.generate(tmp_path / "run1")
    second = gen.generate(tmp_path / "run2")
    assert gen.digest_db(first["db"]) == gen.digest_db(second["db"])
    assert gen.digest_text_outputs(tmp_path / "run1") == gen.digest_text_outputs(tmp_path / "run2")
    for key in first:
        if key != "db":
            assert gen.sha256_file(first[key]) == gen.sha256_file(second[key]), key


def test_generated_output_matches_pinned_digests(generated):
    assert gen.digest_db(generated["db"]) == EXPECTED_DB_TABLE_DIGEST
    assert gen.digest_text_outputs(generated["db"].parent) == EXPECTED_TEXT_OUTPUTS_DIGEST


# ---------------------------------------------------------------- schema


def _schema_tuples(con: sqlite3.Connection) -> list[tuple[str, ...]]:
    """Normalised schema description: (kind, object, cid-or-table, column-or-cols, type)."""
    out: list[tuple[str, ...]] = []
    for kind, name in con.execute(
        "SELECT type, name FROM sqlite_master WHERE type IN ('table','view') ORDER BY type, name"
    ).fetchall():
        for cid, col, ctype, *_ in con.execute(f'PRAGMA table_info("{name}")'):
            out.append((kind, name, str(cid), col, ctype or ""))
    for name, tbl in con.execute(
        "SELECT name, tbl_name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL ORDER BY name"
    ).fetchall():
        cols = [r[2] for r in con.execute(f'PRAGMA index_info("{name}")')]
        out.append(("index", name, tbl, ",".join(cols), ""))
    return out


def _committed_tuples() -> list[tuple[str, ...]]:
    rows = []
    for line in COMMITTED_SCHEMA.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        kind, obj, cid, col, ctype = line.split("\t")
        rows.append((kind, obj, cid, col, "" if ctype == "-" else ctype))
    return rows


def test_schema_matches_committed_column_list(generated):
    with _ro(generated["db"]) as con:
        assert _schema_tuples(con) == _committed_tuples()


def test_committed_column_list_matches_reference_schema():
    ref = os.environ.get(REFERENCE_ENV)
    if not ref:
        pytest.skip(f"{REFERENCE_ENV} unset; the committed column list is the check")
    ref_path = Path(ref)
    assert ref_path.is_file(), f"{REFERENCE_ENV} points at a missing file"
    # Schema only: sqlite_master and PRAGMA, never a SELECT on a data table.
    con = _ro(ref_path)
    try:
        reference = _schema_tuples(con)
    finally:
        con.close()
    assert _committed_tuples() == reference


def test_committed_files_carry_no_host_path():
    for path in [COMMITTED_SCHEMA, Path(gen.__file__), Path(__file__), HERE / "README.md"]:
        text = path.read_text(encoding="utf-8")
        for needle in ("/" + "home" + "/", "claude" + "-playground", "spotify-dsi" + "-test"):
            assert needle not in text, (path.name, needle)


# ---------------------------------------------------------------- shape


def test_window_is_full_iso_weeks(generated, truth):
    start, end = date.fromisoformat(truth["window"]["start"]), date.fromisoformat(truth["window"]["end"])
    assert start.isoweekday() == 1 and end.isoweekday() == 7
    assert gen.WINDOW_DAYS % 7 == 0 and (end - start).days + 1 == gen.WINDOW_DAYS == truth["window"]["days"]
    with _ro(generated["db"]) as con:
        lo, hi, n = con.execute(
            "SELECT MIN(chart_date), MAX(chart_date), COUNT(DISTINCT chart_date) FROM charts_songs_daily"
        ).fetchone()
    assert (lo, hi, n) == (start.isoformat(), end.isoformat(), gen.WINDOW_DAYS)


def test_countries_scale_and_size(generated):
    with _ro(generated["db"]) as con:
        per_country = dict(con.execute("SELECT country, SUM(streams) FROM charts_songs_daily GROUP BY country"))
        n_rows = con.execute("SELECT COUNT(*) FROM charts_songs_daily").fetchone()[0]
    assert set(per_country) == {"DE", "SE", "FR", "GB", "US"}
    assert per_country["US"] > per_country["GB"] > per_country["SE"]
    assert per_country["DE"] > per_country["SE"] * 2
    assert n_rows == gen.WINDOW_DAYS * len(gen.COUNTRIES) * gen.CHART_SIZE
    assert 15_000 <= n_rows <= 25_000


def test_in_window_releases_ramp_up_and_decay(generated, truth):
    with _ro(generated["db"]) as con:
        releases = con.execute(
            "SELECT track_uri, release_date FROM songs WHERE release_date BETWEEN ? AND ?",
            (truth["window"]["start"], truth["window"]["end"]),
        ).fetchall()
        assert len(releases) >= 4
        shaped = 0
        for uri, released in releases:
            first = con.execute(
                "SELECT MIN(chart_date) FROM charts_songs_daily WHERE track_uri = ?", (uri,)
            ).fetchone()[0]
            assert first is None or first >= released, uri  # never on a chart before release
            series = [
                s for (s,) in con.execute(
                    "SELECT streams FROM charts_songs_daily WHERE track_uri = ? AND country = 'US' "
                    "ORDER BY chart_date", (uri,)
                )
            ]
            if len(series) >= 6:
                peak = series.index(max(series))
                if 0 < peak < len(series) - 1 and series[0] < series[peak] > series[-1]:
                    shaped += 1
        assert shaped >= 3


def test_weekly_country_totals_are_not_flat(generated):
    with _ro(generated["db"]) as con:
        rows = con.execute(
            "SELECT country, chart_date, SUM(streams) FROM charts_songs_daily GROUP BY country, chart_date"
        ).fetchall()
    weeks: dict[str, dict[int, int]] = {}
    weekday: dict[str, dict[int, list[int]]] = {}
    for country, day, total in rows:
        d = date.fromisoformat(day)
        week = weeks.setdefault(country, {})
        week[d.isocalendar()[1]] = week.get(d.isocalendar()[1], 0) + total
        weekday.setdefault(country, {}).setdefault(d.weekday(), []).append(total)
    for country, totals in weeks.items():
        assert len(totals) == 13, country
        assert max(totals.values()) / min(totals.values()) > 1.02, country
        fri = sum(weekday[country][4]) / len(weekday[country][4])
        mon = sum(weekday[country][0]) / len(weekday[country][0])
        assert fri > mon, country


def test_some_names_contain_an_apostrophe(generated):
    with _ro(generated["db"]) as con:
        names = [r[0] for r in con.execute(
            "SELECT artist_name FROM artists UNION ALL SELECT label FROM songs UNION ALL SELECT track_name FROM songs"
        )]
    assert any("'" in n for n in names)
    _, rows = _read_csv(generated["table_songs"])
    assert any("'" in cell for row in rows for cell in row)


# ---------------------------------------------------------------- truth (W-7)


def _top2(con, sql: str, params=()) -> list[tuple]:
    rows = con.execute(sql, params).fetchall()
    assert len(rows) == 2
    return rows


def _margin(value: int, runner: int) -> float:
    return round((value - runner) / runner, 4)


def _recompute_fact(con: sqlite3.Connection, fact: dict, window: dict) -> dict:
    """Recompute one published fact with SQL. Returns {answer, value, runner_up}."""
    kind, country = fact["kind"], fact["country"]
    if kind == "top_track_by_streams":
        (u, n, a, v), (ru, rn, _, rv) = _top2(
            con,
            "SELECT track_uri, track_name, artist_names, SUM(streams) AS s FROM charts_songs_daily "
            "WHERE country = ? GROUP BY track_uri ORDER BY s DESC, track_uri LIMIT 2",
            (country,),
        )
        return {"answer": {"track_uri": u, "track_name": n, "artist_names": a}, "value": v,
                "runner_up": {"key": ru, "name": rn, "value": rv}}
    if kind == "most_rank1_days":
        (u, n, a, v), (ru, rn, _, rv) = _top2(
            con,
            "SELECT track_uri, track_name, artist_names, COUNT(*) AS d FROM charts_songs_daily "
            "WHERE country = ? AND rank = 1 GROUP BY track_uri ORDER BY d DESC, track_uri LIMIT 2",
            (country,),
        )
        return {"answer": {"track_uri": u, "track_name": n, "artist_names": a}, "value": v,
                "runner_up": {"key": ru, "name": rn, "value": rv}}
    if kind == "top_artist_by_streams":
        (a, v), (ra, rv) = _top2(
            con, "SELECT artist_names, total_streams FROM artist_summary ORDER BY total_streams DESC, artist_names LIMIT 2"
        )
        return {"answer": {"artist_names": a}, "value": v, "runner_up": {"key": ra, "name": ra, "value": rv}}
    if kind == "label_track_count":
        (lb, v), (rl, rv) = _top2(
            con, "SELECT label, COUNT(*) AS n FROM songs GROUP BY label ORDER BY n DESC, label LIMIT 2"
        )
        return {"answer": {"label": lb}, "value": v, "runner_up": {"key": rl, "name": rl, "value": rv}}
    if kind == "track_peak_day":
        uri = fact["answer"]["track_uri"]
        released = con.execute("SELECT release_date FROM songs WHERE track_uri = ?", (uri,)).fetchone()[0]
        assert window["start"] <= released <= window["end"]
        (d, r, v), (rd, _, rv) = _top2(
            con,
            "SELECT chart_date, rank, streams FROM charts_songs_daily WHERE country = ? AND track_uri = ? "
            "ORDER BY streams DESC, chart_date LIMIT 2",
            (country, uri),
        )
        best = con.execute(
            "SELECT MIN(rank) FROM charts_songs_daily WHERE country = ? AND track_uri = ?", (country, uri)
        ).fetchone()[0]
        best_first = con.execute(
            "SELECT MIN(chart_date) FROM charts_songs_daily WHERE country = ? AND track_uri = ? AND rank = ?",
            (country, uri, best),
        ).fetchone()[0]
        name, artists = con.execute("SELECT track_name, artist_names FROM songs WHERE track_uri = ?", (uri,)).fetchone()
        return {
            "answer": {"track_uri": uri, "track_name": name, "artist_names": artists, "date": d,
                       "rank_on_date": r, "peak_rank": best, "peak_rank_first_date": best_first},
            "value": v,
            "runner_up": {"key": rd, "name": rd, "value": rv},
        }
    if kind == "biggest_new_release":
        (u, n, a, rel, v), (ru, rn, _, _, rv) = _top2(
            con,
            "SELECT c.track_uri, s.track_name, s.artist_names, s.release_date, SUM(c.streams) AS t "
            "FROM charts_songs_daily c JOIN songs s ON s.track_uri = c.track_uri "
            "WHERE s.release_date BETWEEN ? AND ? GROUP BY c.track_uri ORDER BY t DESC, c.track_uri LIMIT 2",
            (window["start"], window["end"]),
        )
        return {"answer": {"track_uri": u, "track_name": n, "artist_names": a, "release_date": rel}, "value": v,
                "runner_up": {"key": ru, "name": rn, "value": rv}}
    if kind == "largest_market":
        (c, v), (rc, rv) = _top2(
            con, "SELECT country, SUM(streams) AS t FROM charts_songs_daily GROUP BY country ORDER BY t DESC, country LIMIT 2"
        )
        assert c == country
        return {"answer": {"country": c}, "value": v, "runner_up": {"key": rc, "name": rc, "value": rv}}
    raise AssertionError(f"unknown fact kind {kind}")


def assert_truth_facts(db_path: Path, truth_path: Path) -> None:
    """Recompute every truth entry from the DB with SQL and compare it to the truth file.

    Expected values come from truth.json, never from constants in this module.
    """
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    con = _ro(db_path)
    try:
        for country in truth["countries"]:
            t = truth["top_track_by_country"][country]
            got = _recompute_fact(con, {"kind": "top_track_by_streams", "country": country}, truth["window"])
            assert got["answer"] == {k: t[k] for k in ("track_uri", "track_name", "artist_names")}, country
            assert (got["value"], got["runner_up"]) == (t["total_streams"], t["runner_up"]), country
            assert t["margin"] == _margin(t["total_streams"], t["runner_up"]["value"]), country
            best, days = con.execute(
                "SELECT MIN(rank), COUNT(*) FROM charts_songs_daily WHERE country = ? AND track_uri = ?",
                (country, t["track_uri"]),
            ).fetchone()
            assert (best, days) == (t["best_rank"], t["days_charted"]), country
            rank1 = dict(con.execute(
                "SELECT chart_date, track_uri FROM charts_songs_daily WHERE country = ? AND rank = 1", (country,)
            ).fetchall())
            assert rank1 == truth["rank1_by_day"][country], country

        for fact in truth["facts"]:
            got = _recompute_fact(con, fact, truth["window"])
            assert got["answer"] == fact["answer"], fact["id"]
            assert got["value"] == fact["value"], fact["id"]
            assert got["runner_up"] == fact["runner_up"], fact["id"]
            assert fact["margin"] == _margin(fact["value"], fact["runner_up"]["value"]), fact["id"]
    finally:
        con.close()


def test_truth_file_matches_recomputed_rows(generated):
    assert_truth_facts(generated["db"], generated["truth"])


@pytest.mark.parametrize("path", [("facts", 0, "value"), ("facts", 6, "value"), ("top_track_by_country", "SE", "total_streams")])
def test_truth_helper_rejects_a_tampered_truth_file(generated, truth, tmp_path, path):
    # Positive control: the helper must fail when the truth file disagrees with the rows.
    tampered = json.loads(json.dumps(truth))
    node = tampered
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] += 1
    out = tmp_path / "truth.json"
    out.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(AssertionError):
        assert_truth_facts(generated["db"], out)


def test_truth_header(truth):
    assert truth["variant"] == "synthetic"
    assert truth["doc_corpus"]
    assert truth["seed"] == gen.SEED
    assert truth["countries"] == list(gen.COUNTRIES)


def test_every_published_fact_wins_by_the_minimum_margin(truth):
    assert truth["min_margin"] >= 0.05
    entries = [(f["id"], f["value"], f["runner_up"]["value"], f["margin"]) for f in truth["facts"]]
    entries += [
        (f"top_track_{c}", t["total_streams"], t["runner_up"]["value"], t["margin"])
        for c, t in truth["top_track_by_country"].items()
    ]
    for ident, value, runner, margin in entries:
        assert value != runner, f"{ident} is a tie"
        assert runner > 0 and (value - runner) / runner >= truth["min_margin"], ident
        assert margin >= truth["min_margin"], ident


def test_facts_have_neutral_ids_and_enough_coverage(truth):
    facts = truth["facts"]
    assert len(facts) >= 10
    assert [f["id"] for f in facts] == [f"fact_{i:02d}" for i in range(1, len(facts) + 1)]
    for f in facts:
        assert "country" in f and f["question"] and f["query_terms"]
        assert not re.search(r"\b(DE|SE|FR|GB|US)\b", f["id"])
    kinds = {f["kind"] for f in facts}
    assert {"top_artist_by_streams", "label_track_count", "track_peak_day"} <= kinds


def test_fact_query_terms_are_keyword_distinct(truth):
    for a, b in combinations(truth["facts"], 2):
        ta = set().union(*(_tokens(t) for t in a["query_terms"]))
        tb = set().union(*(_tokens(t) for t in b["query_terms"]))
        assert not ta & tb, (a["id"], b["id"], ta & tb)


def test_fact_artists_share_no_prefix_or_suffix(truth):
    prefixes = gen._words("artist_prefixes.txt")
    names = set()
    for f in truth["facts"]:
        if "artist_names" in f["answer"]:
            names.update(f["answer"]["artist_names"].split(", "))
    assert len(names) >= 5
    split = []
    for name in names:
        p = next(p for p in prefixes if name.startswith(p + " "))
        split.append((p, name[len(p) + 1:]))
    assert len({p for p, _ in split}) == len(split)
    assert len({s for _, s in split}) == len(split)


def test_fact_sql_answers_on_sqlite(generated, truth):
    with _ro(generated["db"]) as con:
        for fact in truth["facts"]:
            got = [list(r) for r in con.execute(fact["sql"]).fetchall()]
            expected = [[fact["answer_key"], fact["value"]], [fact["runner_up"]["key"], fact["runner_up"]["value"]]]
            assert got == expected, fact["id"]


def test_facts_name_their_source(truth):
    for fact in truth["facts"]:
        assert fact["source"] == gen.FACT_SOURCE[fact["kind"]]
        assert fact["source"].split(":")[0].split(" ")[0] in {"charts_songs_daily", "songs", "artist_summary"}
    by_kind = {f["kind"]: f for f in truth["facts"]}
    assert by_kind["label_track_count"]["source"].startswith("songs")
    assert "exact artist_names" in by_kind["top_artist_by_streams"]["source"]
    label_term = by_kind["label_track_count"]["query_terms"]
    assert label_term == [by_kind["label_track_count"]["answer"]["label"].removesuffix(" Records")]
    assert "records" not in _tokens(label_term[0])


def test_expected_doc_matches_recomputed_from_tables(generated, truth):
    assert truth["doc_corpus"] == gen.DOC_CORPUS
    with _ro(generated["db"]) as con:
        docs = [_tokens(" ".join(r)) for r in con.execute("SELECT track_name, artist_names, label FROM songs")]
        docs += [_tokens(r[0]) for r in con.execute("SELECT artist_name FROM artists")]
        docs += [_tokens(" ".join(r)) for r in con.execute("SELECT album_name, artist_names, label FROM albums")]
        label_tracks = dict(con.execute("SELECT label, COUNT(*) FROM songs GROUP BY label").fetchall())
    for fact in truth["facts"]:
        wanted = set().union(*(_tokens(q) for q in fact["query_terms"]))
        assert sum(1 for d in docs if wanted <= d) == fact["expected_doc_matches"], fact["id"]
    by_kind = {f["kind"]: f for f in truth["facts"]}
    label_fact = by_kind["label_track_count"]
    # a label word matches one song and one album document per track of that label
    assert label_fact["expected_doc_matches"] == 2 * label_tracks[label_fact["answer"]["label"]]
    for f in truth["facts"]:
        if f["kind"] in {"top_track_by_streams", "most_rank1_days", "track_peak_day", "biggest_new_release"}:
            assert f["expected_doc_matches"] == 1, f["id"]


# ---------------------------------------------------------------- CSV outputs


def _read_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    with path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    return rows[0], rows[1:]


def test_top_tracks_csv_matches_view(generated, truth):
    header, rows = _read_csv(generated["top_tracks"])
    assert header[:2] == ["country", "position"]
    expected = []
    with _ro(generated["db"]) as con:
        for country in truth["countries"]:
            for pos, row in enumerate(con.execute(
                "SELECT track_name, artist_names, total_streams, best_rank, days_charted "
                "FROM top_tracks_by_country WHERE country = ? ORDER BY total_streams DESC LIMIT 10",
                (country,),
            ).fetchall(), start=1):
                expected.append([country, str(pos), *(str(x) for x in row)])
    assert [[r[0], r[1], *r[3:]] for r in rows] == expected


def test_daily_streams_csv_matches_chart_rows(generated):
    _, rows = _read_csv(generated["daily_streams"])
    with _ro(generated["db"]) as con:
        expected = [
            [str(v) for v in row]
            for row in con.execute(
                "SELECT chart_date, country, rank, track_uri, track_name, artist_names, streams "
                "FROM charts_songs_daily WHERE country IN ('DE', 'SE') ORDER BY chart_date, country, rank"
            ).fetchall()
        ]
    assert rows == expected
    assert {r[1] for r in rows} == set(gen.DAILY_CSV_COUNTRIES)


@pytest.mark.parametrize("table", gen.TABLES)
def test_table_csv_is_a_full_export_with_null_marker(generated, table):
    header, rows = _read_csv(generated[f"table_{table}"])
    committed = [col for kind, obj, _, col, _ in _committed_tuples() if kind == "table" and obj == table]
    assert header == committed
    with _ro(generated["db"]) as con:
        db_rows = con.execute(f'SELECT * FROM "{table}" ORDER BY {gen.DUMP_ORDER[table]}').fetchall()
    assert rows == [[gen.NULL if v is None else str(v) for v in row] for row in db_rows]
    flat = [cell for row in rows for cell in row]
    assert "" not in flat  # an empty field would be ambiguous with NULL
    if table == "charts_songs_daily":
        assert gen.NULL in flat  # previous_rank of a new entry


# ---------------------------------------------------------------- names


def _stem(token: str) -> str:
    return token[:-1] if token.endswith("s") and len(token) > 4 else token


def _grams(text: str) -> set[str]:
    """4-character substrings of every word token, after stripping a plural s."""
    out: set[str] = set()
    for tok in _tokens(text):
        tok = _stem(tok)
        out |= {tok[i:i + 4] for i in range(len(tok) - 3)}
    return out


def _category_grams() -> dict[str, set[str]]:
    grams = {kind: set().union(*(_grams(w) for f in files for w in gen._words(f))) for kind, files in WORDLISTS.items()}
    grams["label"] |= _grams("Records")  # every label name ends with it
    return grams


def _cross_category_collisions(grams: dict[str, set[str]]) -> list:
    return [(a, b, sorted(grams[a] & grams[b])) for a, b in combinations(grams, 2) if grams[a] & grams[b]]


def test_word_lists_share_no_4char_substring_across_categories():
    assert _cross_category_collisions(_category_grams()) == []


def test_artist_entries_share_no_4char_substring():
    entries = gen._words("artist_prefixes.txt") + gen._words("artist_suffixes.txt")
    seen: dict[str, str] = {}
    clashes = []
    for entry in entries:
        for g in _grams(entry):
            if g in seen and seen[g] != entry:
                clashes.append((seen[g], entry, g))
            seen.setdefault(g, entry)
    assert clashes == []


def test_word_list_rule_has_a_positive_control():
    # The pairs the review named must all count as collisions under this rule.
    for a, b in [("Comet", "Comets"), ("Meridian", "Meridians"), ("Lantern", "Lanternmen"),
                 ("Thimble", "Thimbleworks"), ("Orchard", "Orchardfolk"), ("Thicket", "Thicketfolk"),
                 ("Compassrose", "Compassmen")]:
        assert _cross_category_collisions({"x": _grams(a), "y": _grams(b)}), (a, b)
    assert not _cross_category_collisions({"x": _grams("Copperlight"), "y": _grams("Ledger")})


def test_every_generated_name_comes_from_the_word_lists(generated):
    artist_ok = _combos(*WORDLISTS["artist"])
    track_ok = _combos(*WORDLISTS["track"])
    album_ok = _combos(*WORDLISTS["album"])
    label_ok = {f"{w} Records" for w in gen._words("label_words.txt")}
    with _ro(generated["db"]) as con:
        artists = [r[0] for r in con.execute("SELECT artist_name FROM artists")]
        songs = con.execute("SELECT track_uri, track_name, artist_names, label FROM songs").fetchall()
        albums = con.execute("SELECT album_uri, album_name, artist_names, label FROM albums").fetchall()
        chart_names = con.execute("SELECT DISTINCT track_name, artist_names, label FROM charts_songs_daily").fetchall()
    assert artists and songs and albums and chart_names
    assert set(artists) <= artist_ok
    track_names = {name for _, name, _, _ in songs}
    for _, name, artist_names, label in songs + albums + [(None, *r) for r in chart_names]:
        assert name in track_ok or name in album_ok
        assert label in label_ok
        assert set(artist_names.split(", ")) <= artist_ok
    album_names = {name for _, name, _, _ in albums}
    assert album_names <= album_ok and not album_names & track_names


def _denylist_hits(names) -> list[tuple[str, str]]:
    entries = [ln.strip().lower() for ln in DENYLIST.read_text(encoding="utf-8").splitlines()
               if ln.strip() and not ln.startswith("#")]
    hits = []
    for name in names:
        low, toks = name.lower(), _tokens(name)
        for entry in entries:
            if " " in entry:
                hit = entry in low
            else:
                hit = entry in toks or (len(entry) >= 5 and any(entry in t for t in toks))
            if hit:
                hits.append((name, entry))
    return hits


def test_no_generated_name_matches_the_denylist(generated):
    with _ro(generated["db"]) as con:
        names = {r[0] for r in con.execute(
            "SELECT artist_name FROM artists UNION SELECT track_name FROM songs "
            "UNION SELECT album_name FROM albums UNION SELECT label FROM songs"
        )}
    assert len(names) > 400
    assert _denylist_hits(names) == []


def test_denylist_check_has_a_positive_control():
    # The matcher must catch a whole word, a word inside a token, and a multi-word name.
    assert _denylist_hits(["Oster Lanterns"]) and _denylist_hits(["Brindle Spotifyish"])
    assert _denylist_hits(["Kill Bill Sessions"])
    assert not _denylist_hits(["Brindle Tidewrights"])


def test_no_generated_output_is_committed():
    stray = [p for p in HERE.rglob("*") if p.is_file() and p.suffix in {".db", ".sqlite", ".csv", ".json"}]
    assert stray == []
