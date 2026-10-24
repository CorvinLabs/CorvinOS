# Synthetic chart dataset (release demo, W-2 / W-7)

Every artefact the public demo shows is built from this **synthetic** dataset (decision Q18).
All names are composed from `wordlists/`; no row comes from any real chart archive. The
schema matches the reference `spotify_charts.db`: the committed column list is in
`schema_columns.tsv`, and the DDL is in `generator.DDL`.

```bash
python3 -I generator.py --out /tmp/demo-data      # deterministic, seed in generator.SEED
```

| Output | Content |
|---|---|
| `synthetic_charts.db` | SQLite: `artists`, `songs`, `albums`, `charts_songs_daily`, views `artist_summary`, `top_tracks_by_country`, two indexes |
| `tables/<table>.csv` | full export of each table, header row, reference column order |
| `top_tracks.csv` | top 10 tracks per country by summed streams |
| `daily_streams.csv` | every chart row for DE and SE |
| `truth.json` | `"variant": "synthetic"`; top track per country, rank 1 per day, and the published `facts`: neutral ids `fact_NN`, `country` as a field, `query_terms`, `expected_doc_matches`, `source`, a portable `sql`, `answer_key`, `runner_up`, `margin` |

The window is 2024-01-01 .. 2024-03-31, which is 13 full ISO weeks. Five countries are
included, each with its own market scale, a weekday effect (Friday highest) and a gentle
per-country trend. Each day has a top 40 per country, about 18k chart rows in total. Every
published fact beats its runner-up by at least 5 %, and `test_synthetic_dataset.py`
enforces this.

Names are built from `wordlists/`. After stripping a plural `s`, no 4-character substring
is shared between the artist, track, album and label lists, and no two artist entries share
one. Some names contain an apostrophe, so the loaders' quoting is exercised.

## Ground truth for the Knowledge and Databases checks

Each fact in `truth.json` carries:

- `sql`: one portable query that returns two rows, (`answer_key`, `value`) and (runner-up key, value).
  It uses ANSI quoting (`"rank"`); for MySQL, replace `"rank"` with `` `rank` ``.
- `source`: the table or view it is computed from. Two notes:
  - the label fact counts catalogue rows in `songs`;
  - the artist fact groups `artist_summary` by the exact `artist_names` string, so a track
    with a featured artist forms its own group.
- `query_terms` and `expected_doc_matches`: how many documents contain every token of the
  query terms when one document is built per `songs` row (track, artists, label), per
  `artists` row and per `albums` row (album, artists, label). `truth.json` states the same
  rule under `doc_corpus`.

## NULL convention

Every CSV writes SQL NULL as `\N`, unquoted. That is the default NULL marker of MySQL
`LOAD DATA` and is spelled out for PostgreSQL `COPY`. An empty field always means an empty
string, never NULL. Today NULL appears only in `charts_songs_daily.previous_rank` (a
track that was not on the chart the day before).

## Loading into the sidecars (W-3)

Use the dialect DDL from `generator.py`, not the SQLite `DDL`:

| Engine | DDL | Load command (per table) | Why the SQLite DDL fails |
|---|---|---|---|
| PostgreSQL | `DDL_POSTGRES` | `LOAD_POSTGRES` (psql `\copy … WITH (FORMAT csv, HEADER true, NULL '\N')`) | `CREATE VIEW top_tracks_by_country`: "track_name must appear in the GROUP BY clause" |
| MySQL | `DDL_MYSQL` | `LOAD_MYSQL` (`LOAD DATA INFILE … OPTIONALLY ENCLOSED BY '"' … IGNORE 1 LINES`; the CSV directory must be allowed by `secure_file_priv`) | `rank` is reserved; `TEXT PRIMARY KEY` and the two indexes on TEXT columns fail with error 1170; the view fails under ONLY_FULL_GROUP_BY with error 1055 |

Both dialects group `top_tracks_by_country` by `country, track_uri, track_name,
artist_names`. Track name and artist names are fixed per `track_uri`, so the result is the
same as the SQLite view. MySQL uses `VARCHAR(64)` for the URI keys, `VARCHAR(10)` for
`chart_date` and `VARCHAR(2)` for `country`. The `artist_names` field contains `, ` for
featured artists; those fields are quoted in the CSV.

**Verified** by `test_sql_sidecars.py` against PostgreSQL 17.4 and MySQL 8.4.0. The test is
opt-in: set `CORVIN_DB_BIN_DIR` to a directory with `initdb`/`pg_ctl`/`psql` and/or
`mysqld`/`mysql`/`mysqladmin`. Each engine is skipped when its binaries are missing. The
test starts a throwaway server on a free port with its data directory under the pytest
temp dir. It then:

- applies the dialect DDL and loads the four CSVs with the commands above;
- compares row counts, NULL counts, apostrophe rows and both views with SQLite;
- runs the 10 fact queries;
- shows that the verbatim SQLite DDL fails with the errors listed above.

## Tests

```bash
PYTHONPATH=<repo> .venv/bin/python -m pytest tests/e2e/release_demo/synthetic -q
CORVIN_DB_BIN_DIR=<dir with postgres+mysql binaries> PYTHONPATH=<repo> .venv/bin/python -m pytest tests/e2e/release_demo/synthetic -q
```

The reference-schema comparison runs only when `CORVIN_REFERENCE_CHARTS_DB` points at a
reference database. It reads only `sqlite_master` and `PRAGMA` output, never rows. Without
the variable, the committed `schema_columns.tsv` is the check.
