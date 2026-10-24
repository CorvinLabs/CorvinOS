"""Deterministic SYNTHETIC chart dataset for the release demo (WP-04 / W-2, W-7).

Decision Q18 (synthetic for every public artefact): nothing here reads the Spotify
archive. Every name is composed from the word lists next to this file. The output has
the table/view/index layout of the reference ``spotify_charts.db`` (see
``schema_columns.tsv``), but every row is invented. See README.md for the file layout,
the NULL convention and the notes for loading the CSVs into PostgreSQL and MySQL.

Run ``python3 -I generator.py --out DIR`` to write:
    synthetic_charts.db        SQLite, same tables/views/indexes as the reference schema
    tables/<table>.csv         full export of every table, reference column order, NULL = \\N
    top_tracks.csv             top tracks per country (top_tracks_by_country logic)
    daily_streams.csv          every chart row of DAILY_CSV_COUNTRIES
    truth.json                 ground truth for the demo assertions (W-7), variant "synthetic"

The data is shaped, not uniform: five countries with their own market size, one planted
"anthem" per country, one headline artist with several strong tracks, one label that
signs more tracks than the others, and six tracks released inside the window that ramp
up and decay. The planted levels are chosen so that every published fact wins against
its runner-up by at least MIN_MARGIN; a test enforces it.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path

SEED = 20261024
WINDOW_START = date(2024, 1, 1)  # a Monday: ISO week 2024-W01
WINDOW_DAYS = 91  # 13 full ISO weeks, last day Sunday 2024-03-31
COUNTRY_SCALE = {"DE": 1.0, "SE": 0.25, "FR": 0.8, "GB": 1.1, "US": 3.0}
COUNTRIES = tuple(COUNTRY_SCALE)
DAILY_CSV_COUNTRIES = ("DE", "SE")
N_ARTISTS = 40
N_TRACKS = 220
CHART_SIZE = 40
TOP_N_CSV = 10
FEATURE_SHARE = 0.2
STREAM_UNIT = 1_000_000

ANTHEM_BASE = 1.6
HEADLINER_BASE = 1.2
HEADLINER_TRACKS = 5
RELEASE_OFFSETS = (8, 21, 33, 47, 60, 74)  # days after WINDOW_START; #1 peaks on a Friday (see track_peak_day)
RELEASE_BASES = (2.4, 2.0, 1.8, 1.6, 1.5, 1.4)
RELEASE_RAMP = (0.35, 0.55, 0.75, 0.9, 1.0)  # day 0..4 after release, peak on day 4
RELEASE_DECAY = 0.92  # per day after the peak
PLANTED_LABEL_WEIGHT = 3
PEAK_DAY_COUNTRY = "GB"
MIN_MARGIN = 0.05

# Market multiplier per day: a weekday effect (Monday..Sunday) and a gentle per-country
# trend per day. It scales every track of a country-day alike, so ranks are unaffected,
# but weekly country totals are not flat.
WEEKDAY_FACTOR = (0.96, 0.97, 0.98, 1.0, 1.07, 1.05, 0.97)
COUNTRY_TREND = {"DE": 0.0015, "SE": -0.001, "FR": 0.0005, "GB": 0.001, "US": 0.002}

NULL = "\\N"  # NULL marker in every CSV (PostgreSQL COPY / MySQL LOAD DATA default)

WORDLIST_DIR = Path(__file__).resolve().parent / "wordlists"

# Verbatim DDL of the reference spotify_charts.db (schema only). Order: tables, indexes, views.
DDL = (
    "CREATE TABLE albums(album_uri TEXT PRIMARY KEY, album_name TEXT, artist_names TEXT, "
    "label TEXT, release_date TEXT)",
    "CREATE TABLE artists(artist_uri TEXT PRIMARY KEY, artist_name TEXT)",
    "CREATE TABLE charts_songs_daily(chart_date TEXT, country TEXT, rank INTEGER, track_uri TEXT, "
    "artist_names TEXT, track_name TEXT,\n  label TEXT, peak_rank INTEGER, previous_rank INTEGER, "
    "days_on_chart INTEGER, streams INTEGER, entry_status TEXT)",
    "CREATE TABLE songs(track_uri TEXT PRIMARY KEY, track_name TEXT, artist_names TEXT, "
    "label TEXT, release_date TEXT)",
    "CREATE INDEX ix_c_country_date ON charts_songs_daily(country, chart_date)",
    "CREATE INDEX ix_c_track ON charts_songs_daily(track_uri)",
    "CREATE VIEW artist_summary AS\n"
    "  SELECT artist_names, COUNT(DISTINCT track_uri) AS tracks, COUNT(DISTINCT country) AS countries, "
    "SUM(streams) AS total_streams, MIN(rank) AS best_rank\n"
    "  FROM charts_songs_daily GROUP BY artist_names",
    "CREATE VIEW top_tracks_by_country AS\n"
    "  SELECT country, track_name, artist_names, SUM(streams) AS total_streams, MIN(rank) AS best_rank, "
    "COUNT(*) AS days_charted\n"
    "  FROM charts_songs_daily GROUP BY country, track_uri ORDER BY country, total_streams DESC",
)

# PostgreSQL (verified on 17.4): the SQLite view groups by track_uri only and selects
# track_name/artist_names, which PostgreSQL rejects ("must appear in the GROUP BY clause").
# Both are functionally dependent on track_uri, so grouping by them too changes no result.
DDL_POSTGRES = DDL[:7] + (
    "CREATE VIEW top_tracks_by_country AS\n"
    "  SELECT country, track_name, artist_names, SUM(streams) AS total_streams, MIN(rank) AS best_rank, "
    "COUNT(*) AS days_charted\n"
    "  FROM charts_songs_daily GROUP BY country, track_uri, track_name, artist_names "
    "ORDER BY country, total_streams DESC",
)

# MySQL (verified on 8.4): `rank` is reserved; a TEXT column cannot be a key or an index
# column without a prefix length (errors 1170/1071), so keys and indexed columns are VARCHAR;
# ONLY_FULL_GROUP_BY (default) needs the same view fix as PostgreSQL (error 1055).
DDL_MYSQL = (
    "CREATE TABLE albums(album_uri VARCHAR(64) PRIMARY KEY, album_name TEXT, artist_names TEXT, "
    "label TEXT, release_date TEXT)",
    "CREATE TABLE artists(artist_uri VARCHAR(64) PRIMARY KEY, artist_name TEXT)",
    "CREATE TABLE charts_songs_daily(chart_date VARCHAR(10), country VARCHAR(2), `rank` INTEGER, "
    "track_uri VARCHAR(64), artist_names TEXT, track_name TEXT, label TEXT, peak_rank INTEGER, "
    "previous_rank INTEGER, days_on_chart INTEGER, streams INTEGER, entry_status TEXT)",
    "CREATE TABLE songs(track_uri VARCHAR(64) PRIMARY KEY, track_name TEXT, artist_names TEXT, "
    "label TEXT, release_date TEXT)",
    "CREATE INDEX ix_c_country_date ON charts_songs_daily(country, chart_date)",
    "CREATE INDEX ix_c_track ON charts_songs_daily(track_uri)",
    "CREATE VIEW artist_summary AS SELECT artist_names, COUNT(DISTINCT track_uri) AS tracks, "
    "COUNT(DISTINCT country) AS countries, SUM(streams) AS total_streams, MIN(`rank`) AS best_rank "
    "FROM charts_songs_daily GROUP BY artist_names",
    "CREATE VIEW top_tracks_by_country AS SELECT country, track_name, artist_names, "
    "SUM(streams) AS total_streams, MIN(`rank`) AS best_rank, COUNT(*) AS days_charted "
    "FROM charts_songs_daily GROUP BY country, track_uri, track_name, artist_names "
    "ORDER BY country, total_streams DESC",
)

# Load commands for the per-table CSVs ({table}, {path} filled in). PostgreSQL: a psql
# meta-command (client-side file). MySQL: server-side file, needs secure_file_priv to allow
# the directory. Both read \N as NULL and skip the header row.
LOAD_POSTGRES = "\\copy {table} FROM '{path}' WITH (FORMAT csv, HEADER true, NULL '\\N')"
LOAD_MYSQL = (
    "LOAD DATA INFILE '{path}' INTO TABLE {table} CHARACTER SET utf8mb4 "
    "FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '\"' LINES TERMINATED BY '\\n' IGNORE 1 LINES"
)

# One portable query per fact kind (ANSI quoting; for MySQL replace "rank" with `rank`).
# Each returns two rows: (answer key, value) and (runner-up key, value).
FACT_SQL = {
    "top_track_by_streams": (
        "SELECT track_uri, SUM(streams) AS v FROM charts_songs_daily WHERE country = {country} "
        "GROUP BY track_uri ORDER BY v DESC, track_uri LIMIT 2"
    ),
    "most_rank1_days": (
        "SELECT track_uri, COUNT(*) AS v FROM charts_songs_daily WHERE country = {country} AND \"rank\" = 1 "
        "GROUP BY track_uri ORDER BY v DESC, track_uri LIMIT 2"
    ),
    "top_artist_by_streams": (
        "SELECT artist_names, total_streams AS v FROM artist_summary ORDER BY v DESC, artist_names LIMIT 2"
    ),
    "label_track_count": "SELECT label, COUNT(*) AS v FROM songs GROUP BY label ORDER BY v DESC, label LIMIT 2",
    "track_peak_day": (
        "SELECT chart_date, streams AS v FROM charts_songs_daily WHERE country = {country} "
        "AND track_uri = {track_uri} ORDER BY v DESC, chart_date LIMIT 2"
    ),
    "biggest_new_release": (
        "SELECT c.track_uri, SUM(c.streams) AS v FROM charts_songs_daily c JOIN songs s "
        "ON s.track_uri = c.track_uri WHERE s.release_date BETWEEN {start} AND {end} "
        "GROUP BY c.track_uri ORDER BY v DESC, c.track_uri LIMIT 2"
    ),
    "largest_market": (
        "SELECT country, SUM(streams) AS v FROM charts_songs_daily GROUP BY country ORDER BY v DESC, country LIMIT 2"
    ),
}
FACT_SOURCE = {
    "top_track_by_streams": "charts_songs_daily: SUM(streams) per track_uri within one country",
    "most_rank1_days": "charts_songs_daily: COUNT(*) of rank = 1 rows per track_uri within one country",
    "top_artist_by_streams": "artist_summary: groups by the exact artist_names string, so a track with a "
    "featured artist forms its own group",
    "label_track_count": "songs: COUNT(*) per label (catalogue tracks, charted or not)",
    "track_peak_day": "charts_songs_daily: the track's rows in one country, by streams",
    "biggest_new_release": "charts_songs_daily JOIN songs: SUM(streams) of tracks whose songs.release_date "
    "is inside the window",
    "largest_market": "charts_songs_daily: SUM(streams) per country",
}
DOC_CORPUS = (
    "one document per songs row (track_name, artist_names, label), per artists row (artist_name) and "
    "per albums row (album_name, artist_names, label); a document matches a fact when its lower-case "
    "word tokens contain every token of each of the fact's query_terms"
)

TABLES = ("albums", "artists", "charts_songs_daily", "songs")
DUMP_ORDER = {
    "albums": "album_uri",
    "artists": "artist_uri",
    "songs": "track_uri",
    "charts_songs_daily": "chart_date, country, rank",
}


def window_end() -> date:
    return WINDOW_START + timedelta(days=WINDOW_DAYS - 1)


def _words(name: str) -> list[str]:
    lines = (WORDLIST_DIR / name).read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]


# Only rng.random() is used anywhere, so the sequence is stable across Python versions.
def _pick(rng: random.Random, items: list) -> object:
    return items[int(rng.random() * len(items))]


def _shuffled(rng: random.Random, items: list) -> list:
    out = list(items)
    for i in range(len(out) - 1, 0, -1):
        j = int(rng.random() * (i + 1))
        out[i], out[j] = out[j], out[i]
    return out


def _release_shape(days_since_release: int) -> float:
    if days_since_release < 0:
        return 0.0
    if days_since_release < len(RELEASE_RAMP):
        return RELEASE_RAMP[days_since_release]
    return RELEASE_DECAY ** (days_since_release - len(RELEASE_RAMP) + 1)


def build_catalogue(rng: random.Random) -> dict:
    """Artists, tracks, albums and labels, all composed from the word lists, plus the plan."""
    prefixes = _shuffled(rng, _words("artist_prefixes.txt"))
    suffixes = _shuffled(rng, _words("artist_suffixes.txt"))
    artists = [
        {"artist_uri": f"syn:artist:{i + 1:05d}", "artist_name": f"{prefixes[i]} {suffixes[i]}"}
        for i in range(N_ARTISTS)
    ]

    labels = _shuffled(rng, [f"{w} Records" for w in _words("label_words.txt")])
    planted_label = labels[0]
    label_pool = [planted_label] * PLANTED_LABEL_WEIGHT + labels[1:]

    first = _shuffled(rng, _words("track_first_words.txt"))
    second = _shuffled(rng, _words("track_second_words.txt"))
    n_planted = len(COUNTRIES) + HEADLINER_TRACKS + len(RELEASE_OFFSETS)
    titles = [f"{first[i]} {second[i]}" for i in range(n_planted)]  # planted: unique words
    seen = set(titles)
    while len(titles) < N_TRACKS:  # the rest never reuses a planted word
        title = f"{_pick(rng, first[n_planted:])} {_pick(rng, second[n_planted:])}"
        if title not in seen:
            seen.add(title)
            titles.append(title)

    album_first = _words("album_first_words.txt")
    album_second = _words("album_second_words.txt")
    album_names: list[str] = []
    album_seen: set[str] = set()
    while len(album_names) < N_TRACKS:
        name = f"{_pick(rng, album_first)} {_pick(rng, album_second)}"
        if name not in album_seen:
            album_seen.add(name)
            album_names.append(name)

    regular_artists = artists[len(COUNTRIES) + 1 + len(RELEASE_OFFSETS):]
    tracks: list[dict] = []
    for i in range(N_TRACKS):
        guest = None
        if i < len(COUNTRIES):
            role, role_index, lead = "anthem", i, artists[i]
        elif i < len(COUNTRIES) + HEADLINER_TRACKS:
            role, role_index, lead = "headliner", i - len(COUNTRIES), artists[len(COUNTRIES)]
        elif i < n_planted:
            j = i - len(COUNTRIES) - HEADLINER_TRACKS
            role, role_index, lead = "release", j, artists[len(COUNTRIES) + 1 + j]
        else:
            role, role_index = "regular", None
            lead = _pick(rng, regular_artists)
            if rng.random() < FEATURE_SHARE:
                other = _pick(rng, regular_artists)
                if other is not lead:
                    guest = other
        if role == "release":
            released = WINDOW_START + timedelta(days=RELEASE_OFFSETS[role_index])
        else:
            released = WINDOW_START - timedelta(days=30 + int(rng.random() * 3000))
        names = [lead["artist_name"]] + ([guest["artist_name"]] if guest else [])
        tracks.append(
            {
                "track_uri": f"syn:track:{i + 1:05d}",
                "track_name": titles[i],
                "artist_names": ", ".join(names),
                "label": _pick(rng, label_pool),
                "release_date": released.isoformat(),
                "album_uri": f"syn:album:{i + 1:05d}",
                "album_name": album_names[i],
                "role": role,
                "role_index": role_index,
            }
        )
    return {
        "artists": artists,
        "tracks": tracks,
        "planted_label": planted_label,
        "headliner": artists[len(COUNTRIES)]["artist_name"],
    }


def simulate_charts(rng: random.Random, tracks: list[dict]) -> list[dict]:
    """Daily top-CHART_SIZE rows for every country in the window (deterministic)."""
    base: dict[str, list[float]] = {}
    for c_idx, country in enumerate(COUNTRIES):
        row = []
        for t in tracks:
            regular = 0.05 + rng.random() ** 2
            if t["role"] == "anthem":
                row.append(ANTHEM_BASE if t["role_index"] == c_idx else regular)
            elif t["role"] == "headliner":
                row.append(HEADLINER_BASE)
            elif t["role"] == "release":
                row.append(RELEASE_BASES[t["role_index"]])
            else:
                row.append(regular)
        base[country] = row

    rows: list[dict] = []
    prev_rank: dict[str, dict[str, int]] = {c: {} for c in COUNTRIES}
    count: dict[str, dict[str, int]] = {c: {} for c in COUNTRIES}
    peak: dict[str, dict[str, int]] = {c: {} for c in COUNTRIES}
    for offset in range(WINDOW_DAYS):
        current = WINDOW_START + timedelta(days=offset)
        day = current.isoformat()
        for country in COUNTRIES:
            market = WEEKDAY_FACTOR[current.weekday()] * (1 + COUNTRY_TREND[country] * offset)
            streams = []
            for t_idx, t in enumerate(tracks):
                draw = rng.random()
                if t["role"] == "release":
                    shape = _release_shape(offset - RELEASE_OFFSETS[t["role_index"]])
                    noise = 0.99 + 0.02 * draw  # low noise keeps the peak day distinct
                else:
                    shape = 1.0
                    noise = 0.8 + 0.4 * draw
                streams.append(
                    int(COUNTRY_SCALE[country] * market * STREAM_UNIT * base[country][t_idx] * shape * noise)
                )
            eligible = [t for t in range(len(tracks)) if streams[t] > 0]
            order = sorted(eligible, key=lambda t: (-streams[t], tracks[t]["track_uri"]))
            today: dict[str, int] = {}
            for rank, t in enumerate(order[:CHART_SIZE], start=1):
                uri = tracks[t]["track_uri"]
                today[uri] = rank
                count[country][uri] = count[country].get(uri, 0) + 1
                previous = prev_rank[country].get(uri)
                if previous is None:
                    status = "RE-ENTRY" if uri in peak[country] else "NEW"
                elif rank < previous:
                    status = "UP"
                elif rank > previous:
                    status = "DOWN"
                else:
                    status = "SAME"
                peak[country][uri] = min(peak[country].get(uri, rank), rank)
                rows.append(
                    {
                        "chart_date": day,
                        "country": country,
                        "rank": rank,
                        "track_uri": uri,
                        "artist_names": tracks[t]["artist_names"],
                        "track_name": tracks[t]["track_name"],
                        "label": tracks[t]["label"],
                        "peak_rank": peak[country][uri],
                        "previous_rank": previous,
                        "days_on_chart": count[country][uri],
                        "streams": streams[t],
                        "entry_status": status,
                    }
                )
            prev_rank[country] = today
    rows.sort(key=lambda r: (r["chart_date"], r["country"], r["rank"]))
    return rows


# ---------------------------------------------------------------- truth (W-7)


def _top2(values: dict) -> tuple[tuple, tuple]:
    """Winner and runner-up of {key: value}; ties broken by key (and caught by the margin test)."""
    ranked = sorted(values.items(), key=lambda kv: (-kv[1], kv[0]))
    return ranked[0], ranked[1]


def _margin(value: int, runner_up: int) -> float:
    return round((value - runner_up) / runner_up, 4)


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def document_tokens(catalogue: dict) -> list[set[str]]:
    """Token sets of the documents described by DOC_CORPUS."""
    docs = [_tokens(" ".join((t["track_name"], t["artist_names"], t["label"]))) for t in catalogue["tracks"]]
    docs += [_tokens(a["artist_name"]) for a in catalogue["artists"]]
    docs += [_tokens(" ".join((t["album_name"], t["artist_names"], t["label"]))) for t in catalogue["tracks"]]
    return docs


def count_doc_matches(docs: list[set[str]], query_terms: list[str]) -> int:
    wanted = set().union(*(_tokens(q) for q in query_terms))
    return sum(1 for d in docs if wanted <= d)


def _sql_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def render_fact_sql(kind: str, params: dict) -> str:
    """The fact's portable query with literal values (ANSI quoting)."""
    return FACT_SQL[kind].format(**{k: _sql_literal(v) for k, v in params.items() if v is not None})


def truth_from(catalogue: dict, rows: list[dict]) -> dict:
    """Ground truth computed in Python from the rows. The tests recompute it with SQL."""
    by_uri = {t["track_uri"]: t for t in catalogue["tracks"]}
    sums = {c: {} for c in COUNTRIES}
    best_rank = {c: {} for c in COUNTRIES}
    days = {c: {} for c in COUNTRIES}
    rank1_days = {c: {} for c in COUNTRIES}
    rank1_by_day = {c: {} for c in COUNTRIES}
    artist_totals: dict[str, int] = {}
    release_totals: dict[str, int] = {}
    country_totals: dict[str, int] = {c: 0 for c in COUNTRIES}
    window = (WINDOW_START.isoformat(), window_end().isoformat())
    for r in rows:
        c, uri = r["country"], r["track_uri"]
        sums[c][uri] = sums[c].get(uri, 0) + r["streams"]
        best_rank[c][uri] = min(best_rank[c].get(uri, r["rank"]), r["rank"])
        days[c][uri] = days[c].get(uri, 0) + 1
        if r["rank"] == 1:
            rank1_days[c][uri] = rank1_days[c].get(uri, 0) + 1
            rank1_by_day[c][r["chart_date"]] = uri
        artist_totals[r["artist_names"]] = artist_totals.get(r["artist_names"], 0) + r["streams"]
        if window[0] <= by_uri[uri]["release_date"] <= window[1]:
            release_totals[uri] = release_totals.get(uri, 0) + r["streams"]
        country_totals[c] += r["streams"]

    def track_answer(uri: str) -> dict:
        t = by_uri[uri]
        return {"track_uri": uri, "track_name": t["track_name"], "artist_names": t["artist_names"]}

    def runner(uri: str, value: int) -> dict:
        return {"key": uri, "name": by_uri[uri]["track_name"], "value": value}

    top_track = {}
    for c in COUNTRIES:
        (uri, value), (r_uri, r_value) = _top2(sums[c])
        top_track[c] = dict(
            track_answer(uri),
            total_streams=value,
            best_rank=best_rank[c][uri],
            days_charted=days[c][uri],
            runner_up=runner(r_uri, r_value),
            margin=_margin(value, r_value),
        )

    span = f"between {window[0]} and {window[1]}"
    docs = document_tokens(catalogue)
    facts: list[dict] = []

    def add(kind: str, country, question: str, query_terms: list[str], answer: dict, answer_key: str,
            metric: str, value: int, runner_up: dict) -> None:
        params = {"country": country, "track_uri": answer.get("track_uri"), "start": window[0], "end": window[1]}
        facts.append(
            {
                "id": f"fact_{len(facts) + 1:02d}",
                "kind": kind,
                "country": country,
                "question": question,
                "query_terms": query_terms,
                "expected_doc_matches": count_doc_matches(docs, query_terms),
                "source": FACT_SOURCE[kind],
                "sql": render_fact_sql(kind, params),
                "answer": answer,
                "answer_key": answer_key,
                "metric": metric,
                "value": value,
                "runner_up": runner_up,
                "margin": _margin(value, runner_up["value"]),
            }
        )

    for c in ("DE", "SE", "GB", "US"):
        tt = top_track[c]
        add(
            "top_track_by_streams", c,
            f"Which track has the most streams in {c} {span}?",
            [tt["track_name"]],
            track_answer(tt["track_uri"]), tt["track_uri"], "total_streams", tt["total_streams"], tt["runner_up"],
        )

    (uri, value), (r_uri, r_value) = _top2(rank1_days["FR"])
    add(
        "most_rank1_days", "FR",
        f"Which track was number one in FR on the most days {span}, and on how many?",
        [by_uri[uri]["track_name"]],
        track_answer(uri), uri, "rank1_days", value, runner(r_uri, r_value),
    )

    (artist, value), (r_artist, r_value) = _top2(artist_totals)
    add(
        "top_artist_by_streams", None,
        f"Which artist has the most streams across all countries {span}?",
        [artist],
        {"artist_names": artist}, artist, "total_streams", value,
        {"key": r_artist, "name": r_artist, "value": r_value},
    )

    label_counts: dict[str, int] = {}
    for t in catalogue["tracks"]:
        label_counts[t["label"]] = label_counts.get(t["label"], 0) + 1
    (label, value), (r_label, r_value) = _top2(label_counts)
    add(
        "label_track_count", None,
        f"How many tracks has {label} released, and is it the label with the most tracks?",
        [label.removesuffix(" Records")],
        {"label": label}, label, "tracks", value, {"key": r_label, "name": r_label, "value": r_value},
    )

    peak_track = next(t for t in catalogue["tracks"] if t["role"] == "release" and t["role_index"] == 1)
    track_rows = [r for r in rows if r["country"] == PEAK_DAY_COUNTRY and r["track_uri"] == peak_track["track_uri"]]
    ranked_days = sorted(track_rows, key=lambda r: (-r["streams"], r["chart_date"]))
    top_day, second_day = ranked_days[0], ranked_days[1]
    best = min(r["rank"] for r in track_rows)
    add(
        "track_peak_day", PEAK_DAY_COUNTRY,
        f"On which date did {peak_track['track_name']} have its most streams in {PEAK_DAY_COUNTRY}, "
        "at what rank, and what was its peak rank?",
        [peak_track["track_name"]],
        dict(
            track_answer(peak_track["track_uri"]),
            date=top_day["chart_date"],
            rank_on_date=top_day["rank"],
            peak_rank=best,
            peak_rank_first_date=min(r["chart_date"] for r in track_rows if r["rank"] == best),
        ),
        top_day["chart_date"], "streams_on_date", top_day["streams"],
        {"key": second_day["chart_date"], "name": second_day["chart_date"], "value": second_day["streams"]},
    )

    (uri, value), (r_uri, r_value) = _top2(release_totals)
    add(
        "biggest_new_release", None,
        f"Which track released {span} has the most streams across all countries?",
        [by_uri[uri]["track_name"]],
        dict(track_answer(uri), release_date=by_uri[uri]["release_date"]), uri,
        "total_streams", value, runner(r_uri, r_value),
    )

    (country, value), (r_country, r_value) = _top2(country_totals)
    add(
        "largest_market", country,
        f"Which country has the most chart streams {span}?",
        [country],
        {"country": country}, country, "total_streams", value,
        {"key": r_country, "name": r_country, "value": r_value},
    )

    return {
        "variant": "synthetic",
        "seed": SEED,
        "window": {"start": window[0], "end": window[1], "days": WINDOW_DAYS},
        "countries": list(COUNTRIES),
        "min_margin": MIN_MARGIN,
        "doc_corpus": DOC_CORPUS,
        "top_track_by_country": top_track,
        "rank1_by_day": {c: dict(sorted(rank1_by_day[c].items())) for c in COUNTRIES},
        "facts": facts,
    }


# ---------------------------------------------------------------- writers


def _write_db(path: Path, catalogue: dict, rows: list[dict]) -> None:
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    try:
        for stmt in DDL:
            con.execute(stmt)
        con.executemany("INSERT INTO artists VALUES (:artist_uri, :artist_name)", catalogue["artists"])
        con.executemany(
            "INSERT INTO songs VALUES (:track_uri, :track_name, :artist_names, :label, :release_date)",
            catalogue["tracks"],
        )
        con.executemany(
            "INSERT INTO albums VALUES (:album_uri, :album_name, :artist_names, :label, :release_date)",
            catalogue["tracks"],
        )
        con.executemany(
            "INSERT INTO charts_songs_daily VALUES (:chart_date, :country, :rank, :track_uri, "
            ":artist_names, :track_name, :label, :peak_rank, :previous_rank, :days_on_chart, "
            ":streams, :entry_status)",
            rows,
        )
        con.commit()
    finally:
        con.close()


def _cell(value) -> str:
    return NULL if value is None else str(value)


def _write_csv(path: Path, header: list[str], lines) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(header)
        writer.writerows([_cell(v) for v in line] for line in lines)


def _export_tables(db_path: Path, out_dir: Path) -> dict[str, Path]:
    """One CSV per table: header row, reference column order, NULL written as \\N."""
    paths = {}
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        for table in TABLES:
            cols = [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]
            lines = con.execute(f'SELECT * FROM "{table}" ORDER BY {DUMP_ORDER[table]}').fetchall()
            path = out_dir / "tables" / f"{table}.csv"
            _write_csv(path, cols, lines)
            paths[f"table_{table}"] = path
    finally:
        con.close()
    return paths


def generate(out_dir: Path) -> dict[str, Path]:
    """Write the dataset into out_dir (created if missing). Same output for the same SEED."""
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)
    catalogue = build_catalogue(rng)
    rows = simulate_charts(rng, catalogue["tracks"])
    truth = truth_from(catalogue, rows)

    db_path = out_dir / "synthetic_charts.db"
    _write_db(db_path, catalogue, rows)
    paths = {"db": db_path}
    paths.update(_export_tables(db_path, out_dir))

    top_lines = []
    for c in COUNTRIES:
        agg: dict[str, dict] = {}
        for r in rows:
            if r["country"] != c:
                continue
            a = agg.setdefault(
                r["track_uri"],
                {"track_name": r["track_name"], "artist_names": r["artist_names"], "total": 0, "best": r["rank"], "days": 0},
            )
            a["total"] += r["streams"]
            a["best"] = min(a["best"], r["rank"])
            a["days"] += 1
        ordered = sorted(agg.items(), key=lambda kv: (-kv[1]["total"], kv[0]))[:TOP_N_CSV]
        for pos, (uri, a) in enumerate(ordered, start=1):
            top_lines.append([c, pos, uri, a["track_name"], a["artist_names"], a["total"], a["best"], a["days"]])
    paths["top_tracks"] = out_dir / "top_tracks.csv"
    _write_csv(
        paths["top_tracks"],
        ["country", "position", "track_uri", "track_name", "artist_names", "total_streams", "best_rank", "days_charted"],
        top_lines,
    )

    paths["daily_streams"] = out_dir / "daily_streams.csv"
    _write_csv(
        paths["daily_streams"],
        ["chart_date", "country", "rank", "track_uri", "track_name", "artist_names", "streams"],
        [
            [r["chart_date"], r["country"], r["rank"], r["track_uri"], r["track_name"], r["artist_names"], r["streams"]]
            for r in rows
            if r["country"] in DAILY_CSV_COUNTRIES
        ],
    )

    paths["truth"] = out_dir / "truth.json"
    paths["truth"].write_text(json.dumps(truth, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return paths


# ---------------------------------------------------------------- digests


def digest_db(db_path: Path) -> str:
    """sha256 over the sorted content of every table and view (not over file bytes)."""
    h = hashlib.sha256()
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        for table in TABLES:
            cols = [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]
            h.update(f"## {table} {','.join(cols)}\n".encode())
            for row in con.execute(f'SELECT * FROM "{table}" ORDER BY {DUMP_ORDER[table]}'):
                h.update(repr(tuple(row)).encode() + b"\n")
        for row in con.execute("SELECT * FROM top_tracks_by_country ORDER BY 1, 2, 3, 4, 5, 6"):
            h.update(repr(tuple(row)).encode() + b"\n")
        for row in con.execute("SELECT * FROM artist_summary ORDER BY 1, 2, 3, 4, 5"):
            h.update(repr(tuple(row)).encode() + b"\n")
    finally:
        con.close()
    return h.hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest_text_outputs(out_dir: Path) -> str:
    """sha256 over (relative path, sha256) of every CSV/JSON output, sorted by path."""
    h = hashlib.sha256()
    for path in sorted(p for p in out_dir.rglob("*") if p.suffix in {".csv", ".json"}):
        h.update(f"{path.relative_to(out_dir).as_posix()} {sha256_file(path)}\n".encode())
    return h.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    for name, path in generate(args.out).items():
        print(f"{name}\t{path}")


if __name__ == "__main__":
    main()
