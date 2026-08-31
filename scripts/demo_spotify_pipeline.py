#!/usr/bin/env python3
"""
demo_spotify_pipeline.py — Create a complete Spotify chart analysis pipeline fixture on disk.

Generates realistic-looking data for testing the awpkg export feature in the
Compute Dashboard UI (ADR-0090).

Usage:
    python3 scripts/demo_spotify_pipeline.py
"""

from __future__ import annotations

import csv
import json
import os
import random
from datetime import date, timedelta
from pathlib import Path


# ---------------------------------------------------------------------------
# Constants / fixture data
# ---------------------------------------------------------------------------

PIPELINE_ID = "pipe_spotify_chart_pred_demo"
MARKETS = ["DE", "FR", "GB", "NL", "SE"]
NUM_WEEKS = 10
NUM_TRACKS = 50
CHART_SIZE = 200  # ranks per market per week
START_DATE = date(2026, 3, 1)

TRACKS: list[tuple[str, str]] = [
    ("Midnight Rain", "Taylor Swift"),
    ("As It Was", "Harry Styles"),
    ("Anti-Hero", "Taylor Swift"),
    ("Flowers", "Miley Cyrus"),
    ("Kill Bill", "SZA"),
    ("Unholy", "Sam Smith"),
    ("Calm Down", "Rema & Selena Gomez"),
    ("Bad Habit", "Steve Lacy"),
    ("About Damn Time", "Lizzo"),
    ("Hold Me Closer", "Elton John & Britney Spears"),
    ("Running Up That Hill", "Kate Bush"),
    ("Left and Right", "Charlie Puth"),
    ("Golden Hour", "JVKE"),
    ("Super Freaky Girl", "Nicki Minaj"),
    ("Die For You", "The Weeknd"),
    ("Creepin", "Metro Boomin"),
    ("More Life", "Drake"),
    ("Escapism", "RAYE"),
    ("Lift Me Up", "Rihanna"),
    ("Quevedo Bzrp Session", "Bizarrap"),
    ("Titi Me Pregunto", "Bad Bunny"),
    ("Ojitos Lindos", "Bad Bunny"),
    ("Moscow Mule", "Bad Bunny"),
    ("Shakira: Bzrp Session", "Bizarrap"),
    ("La Bachata", "Manuel Turizo"),
    ("Te Felicito", "Shakira"),
    ("Monumento", "Residente"),
    ("Actrices", "Nathy Peluso"),
    ("Beso", "Rosalia"),
    ("Watati", "Burna Boy"),
    ("Last Last", "Burna Boy"),
    ("Peru", "Fireboy DML"),
    ("Essence", "WizKid"),
    ("Ye", "Burna Boy"),
    ("Finesse", "Pheelz"),
    ("Overdue", "Benson Boone"),
    ("Beautiful Things", "Benson Boone"),
    ("Stargazing", "Kygo"),
    ("Blinding Lights", "The Weeknd"),
    ("Save Your Tears", "The Weeknd"),
    ("Stay", "Justin Bieber"),
    ("Ghost", "Justin Bieber"),
    ("Peaches", "Justin Bieber"),
    ("Levitating", "Dua Lipa"),
    ("Physical", "Dua Lipa"),
    ("Shivers", "Ed Sheeran"),
    ("Overpass Graffiti", "Ed Sheeran"),
    ("Collide", "Justine Skye"),
    ("Watermelon Sugar", "Harry Styles"),
    ("Daylight", "Harry Styles"),
]

STAGE_IDS = [
    "stage_ingest",
    "stage_features",
    "stage_train",
    "stage_evaluate",
    "stage_predict",
]

STAGE_META: dict[str, dict] = {
    "stage_ingest": {
        "tool_name": "code_spotify_ingest",
        "best_loss": 0.15,
        "best_params": {"chunk_size": 1000, "dedup": True},
        "run_id": "run_spotify_ingest_001",
    },
    "stage_features": {
        "tool_name": "code_spotify_features",
        "best_loss": 0.12,
        "best_params": {"window_weeks": 4, "lag_features": 3},
        "run_id": "run_spotify_features_001",
    },
    "stage_train": {
        "tool_name": "code_spotify_train",
        "best_loss": 0.082,
        "best_params": {"lr": 0.0032, "depth": 6, "n_estimators": 200},
        "run_id": "run_spotify_train_001",
    },
    "stage_evaluate": {
        "tool_name": "code_spotify_evaluate",
        "best_loss": 0.071,
        "best_params": {"threshold": 0.60, "metric": "ndcg@10"},
        "run_id": "run_spotify_eval_001",
    },
    "stage_predict": {
        "tool_name": "code_spotify_predict",
        "best_loss": 0.068,
        "best_params": {"top_k": 10, "min_confidence": 0.55},
        "run_id": "run_spotify_predict_001",
    },
}

TRAIN_LOSSES = [0.24, 0.18, 0.15, 0.13, 0.11, 0.10, 0.095, 0.088, 0.082, 0.082]

REAL_STATS_INGEST = {
    "total_rows": 5000,
    "output_rows": 5000,
    "unique_countries": 5,
    "iso_weeks": 10,
    "file_size_mb": 0.32,
    "watermark_date": "2026-05-08",
    "date_range_start": "2026-03-01",
    "date_range_end": "2026-05-08",
    "pii_detected": False,
    "zone": "EU",
    "top_tracks": [
        {"track_name": "Midnight Rain", "artist": "Taylor Swift",
         "total_streams": 48200000, "peak_rank": 1, "days_on_chart": 42},
        {"track_name": "As It Was", "artist": "Harry Styles",
         "total_streams": 41500000, "peak_rank": 2, "days_on_chart": 38},
        {"track_name": "Anti-Hero", "artist": "Taylor Swift",
         "total_streams": 39800000, "peak_rank": 1, "days_on_chart": 35},
        {"track_name": "Flowers", "artist": "Miley Cyrus",
         "total_streams": 37200000, "peak_rank": 3, "days_on_chart": 28},
        {"track_name": "Kill Bill", "artist": "SZA",
         "total_streams": 35100000, "peak_rank": 4, "days_on_chart": 31},
    ],
    "column_stats": {
        "streams_p50": {"min": 1200000, "max": 48200000, "p50": 8500000, "p95": 35000000},
        "peak_rank": {"min": 1, "max": 200, "p50": 45},
    },
    "schema": [
        {"name": "week", "type": "VARCHAR", "nullable": False},
        {"name": "country", "type": "VARCHAR", "nullable": False},
        {"name": "track_id", "type": "VARCHAR", "nullable": False},
        {"name": "track_name", "type": "VARCHAR", "nullable": False},
        {"name": "artist", "type": "VARCHAR", "nullable": False},
        {"name": "streams_p50", "type": "BIGINT", "nullable": False},
        {"name": "peak_rank", "type": "INTEGER", "nullable": False},
        {"name": "days_on_chart", "type": "INTEGER", "nullable": False},
    ],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def corvin_home() -> Path:
    raw = os.environ.get("CORVIN_HOME", "~/.corvin")
    return Path(raw).expanduser()


def pipeline_root(home: Path) -> Path:
    return home / "tenants" / "_default" / "compute" / "pipelines" / PIPELINE_ID


def iso_week_str(d: date) -> str:
    return d.strftime("%Y-W%V")


def gen_track_id(track_name: str, artist: str) -> str:
    # Deterministic fake Spotify track ID from name bytes
    h = 0
    for ch in (track_name + artist):
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return f"T{h:08X}"


def generate_csv_rows() -> list[dict]:
    rng = random.Random(42)
    rows: list[dict] = []
    track_ids = [(gen_track_id(n, a), n, a) for n, a in TRACKS]

    for week_idx in range(NUM_WEEKS):
        week_date = START_DATE + timedelta(weeks=week_idx)
        week_str = iso_week_str(week_date)
        for country in MARKETS:
            # Shuffle track list per market/week (different chart ordering per market)
            market_tracks = rng.sample(track_ids, min(NUM_TRACKS, CHART_SIZE))
            # Fill remaining slots with repeats if needed
            while len(market_tracks) < CHART_SIZE:
                market_tracks.extend(rng.sample(track_ids, min(len(track_ids), CHART_SIZE - len(market_tracks))))
            market_tracks = market_tracks[:CHART_SIZE]

            for rank, (tid, tname, artist) in enumerate(market_tracks, start=1):
                # Stream count decreases with rank, with noise
                base_streams = max(1_200_000, int(50_000_000 / (rank ** 0.6)))
                streams = int(base_streams * rng.uniform(0.8, 1.2))
                days_on_chart = rng.randint(7, 70)
                rows.append({
                    "week": week_str,
                    "country": country,
                    "track_id": tid,
                    "track_name": tname,
                    "artist": artist,
                    "streams_p50": streams,
                    "peak_rank": rank,
                    "days_on_chart": days_on_chart,
                })

    # Trim/pad to exactly 5000 rows
    return rows[:5000]


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------

def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def create_pipeline(home: Path, rows: list[dict]) -> None:
    root = pipeline_root(home)
    root.mkdir(parents=True, exist_ok=True)

    # --- manifest.json ---
    manifest = {
        "pipeline_id": PIPELINE_ID,
        "version": "1.0",
        "description": "Spotify Charts top-50 chart-position prediction — EU markets",
        "created_at": "2026-05-08T09:00:00Z",
        "stages": [
            {
                "stage_id": sid,
                "tool_name": STAGE_META[sid]["tool_name"],
                "run_id": STAGE_META[sid]["run_id"],
                "depends_on": [STAGE_IDS[i - 1]] if i > 0 else [],
            }
            for i, sid in enumerate(STAGE_IDS)
        ],
        "datasources": ["spotify-charts-s3"],
        "output_sinks": ["spotify-results-s3"],
        "rag_providers": ["spotify-charts-elastic"],
        "tags": ["spotify", "charts", "xgboost", "eu", "demo"],
    }
    write_json(root / "manifest.json", manifest)

    # --- pipeline_summary.json ---
    summary = {
        "state": "complete",
        "best_losses": {sid: STAGE_META[sid]["best_loss"] for sid in STAGE_IDS},
        "completed_stages": STAGE_IDS,
        "run_ids": {sid: STAGE_META[sid]["run_id"] for sid in STAGE_IDS},
        "pipeline_id": PIPELINE_ID,
        "finished_at": "2026-05-08T14:37:22Z",
    }
    write_json(root / "pipeline_summary.json", summary)

    # --- per-stage directories ---
    for stage_id in STAGE_IDS:
        meta = STAGE_META[stage_id]
        stage_dir = root / "stages" / stage_id

        stage_summary: dict = {
            "state": "complete",
            "stage_id": stage_id,
            "best_loss": meta["best_loss"],
            "best_params": meta["best_params"],
            "tool_name": meta["tool_name"],
            "run_id": meta["run_id"],
        }

        if stage_id == "stage_ingest":
            stage_summary["real_stats"] = REAL_STATS_INGEST
            # Write actual CSV artifact
            csv_path = stage_dir / "artifacts" / "weekly_chart_aggregates.csv"
            write_csv(csv_path, rows)

        write_json(stage_dir / "stage_summary.json", stage_summary)

    # --- stage_train iterations ---
    iter_dir = root / "stages" / "stage_train" / "iterations"
    iter_dir.mkdir(parents=True, exist_ok=True)
    train_meta = STAGE_META["stage_train"]
    for i, loss in enumerate(TRAIN_LOSSES):
        # Params evolve toward champion values
        frac = i / max(len(TRAIN_LOSSES) - 1, 1)
        iter_data = {
            "iter": i,
            "loss": loss,
            "params": {
                "lr": round(0.01 - frac * (0.01 - train_meta["best_params"]["lr"]), 6),
                "depth": int(4 + round(frac * 2)),
                "n_estimators": int(50 + round(frac * 150)),
            },
            "improved": loss < (TRAIN_LOSSES[i - 1] if i > 0 else 999),
        }
        write_json(iter_dir / f"iter_{i:04d}.json", iter_data)


def create_rag_manifest(home: Path) -> None:
    rag_dir = home / "tenants" / "_default" / "global" / "rag"
    rag_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "provider_id": "spotify-charts-elastic",
        "type": "elasticsearch",
        "description": "Spotify Charts weekly aggregates — EU markets (DE/FR/GB/NL/SE)",
        "index_pattern": "spotify-charts-*",
        "endpoint_env": "ELASTIC_SPOTIFY_URL",
        "api_key_env": "ELASTIC_SPOTIFY_API_KEY",
        "zone": "EU",
        "fields": ["week", "country", "track_id", "track_name", "artist",
                   "streams_p50", "peak_rank", "days_on_chart"],
        "chunk_size": 512,
        "overlap": 64,
        "embedding_model": "text-embedding-3-small",
        "created_at": "2026-05-08T08:00:00Z",
    }
    (rag_dir / "spotify-charts-elastic.yaml").write_text(
        "# RAG provider manifest — generated by demo_spotify_pipeline.py\n"
        + json.dumps(manifest, indent=2).replace("{", "").replace("}", "").replace('"', "").replace(",", ""),
        encoding="utf-8",
    )


def create_datasource_connections(home: Path) -> None:
    ds_dir = home / "tenants" / "_default" / "datasource_connections"
    ds_dir.mkdir(parents=True, exist_ok=True)

    input_conn = {
        "connection_id": "spotify-charts-s3",
        "type": "s3",
        "direction": "input",
        "description": "Spotify Charts weekly exports — EU region S3 bucket",
        "bucket_env": "SPOTIFY_CHARTS_S3_BUCKET",
        "prefix": "charts/eu/weekly/",
        "region": "eu-central-1",
        "format": "csv",
        "credentials_env": "AWS_SPOTIFY_ACCESS_KEY",
        "zone": "EU",
        "created_at": "2026-05-08T08:00:00Z",
    }
    write_json(ds_dir / "spotify-charts-s3.json", input_conn)

    output_conn = {
        "connection_id": "spotify-results-s3",
        "type": "s3",
        "direction": "output",
        "description": "Spotify prediction results — EU region S3 output sink",
        "bucket_env": "SPOTIFY_RESULTS_S3_BUCKET",
        "prefix": "predictions/eu/",
        "region": "eu-central-1",
        "format": "json",
        "credentials_env": "AWS_SPOTIFY_ACCESS_KEY",
        "zone": "EU",
        "created_at": "2026-05-08T08:00:00Z",
    }
    write_json(ds_dir / "spotify-results-s3.json", output_conn)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    home = corvin_home()
    rows = generate_csv_rows()

    create_pipeline(home, rows)
    create_rag_manifest(home)
    create_datasource_connections(home)

    root = pipeline_root(home)
    home_display = str(root).replace(str(Path.home()), "~")

    print(f"✓ Created Spotify demo pipeline: {PIPELINE_ID}")
    print(f"✓ Generated {len(rows)} chart rows ({len(MARKETS)} markets × {NUM_WEEKS} weeks × {NUM_TRACKS} tracks)")
    print(f"✓ Created {len(STAGE_IDS)} pipeline stages with champion params")
    train_meta = STAGE_META["stage_train"]
    print(f"✓ Stage train: {len(TRAIN_LOSSES)} iterations, best_loss={train_meta['best_loss']}")
    print("✓ RAG manifest: spotify-charts-elastic")
    print("✓ Datasource: spotify-charts-s3 (input), spotify-results-s3 (output)")
    print()
    print(f"Pipeline directory: {home_display}/")
    print("Ready to export: open the Compute page → Pipelines tab → Export awpkg")
    print()
    print("Demo run IDs created:")
    run_ids = [STAGE_META[sid]["run_id"] for sid in STAGE_IDS]
    print(f"  {run_ids[0]}, {run_ids[1]}, {run_ids[2]}")
    print(f"  {run_ids[3]}, {run_ids[4]}")


if __name__ == "__main__":
    main()
