"""Metadata injection into a learning export — adversarial vector 20, learning surface.

Attack shape (from the archived probe script): free-form strings that reach an
export are attacker-controlled metadata. The original probe targeted the PDF
compliance report; the same class exists on the learning surface, where
``POST /v1/console/learning/metrics/export`` copies ``skill_id`` and ``lom``
straight out of the event store into JSONL and CSV.

Payloads: NUL and C0 control bytes, a CRLF row break, an embedded quote and
comma, and the spreadsheet formula prefixes (``=``, ``+``, ``-``, ``@``) that
turn a CSV cell into code in Excel/LibreOffice/Sheets.

Driven through the REAL router with a REAL session — the export body IS the
response, so what these assertions read is exactly what an operator downloads.

Run:  .venv/bin/python -m pytest core/console/tests/test_learning_export_metadata_injection.py
"""
from __future__ import annotations

import csv
import io
import json
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
for _p in (str(_REPO), str(_REPO / "core" / "console"), str(_REPO / "core" / "plugins")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

TENANT = "_default"
_PURGED = ("corvin_console", "corvin_gateway", "forge")

#: One event per payload. Every one of these is a string an attacker who can
#: write a learning event controls end-to-end.
PAYLOADS = {
    "nul": "evil\x00<injected>",
    "control": "evil\x01\x02\x1f end",
    "crlf": "evil\r\nfake_id,fake_type,fake_skill,2026-01-01T00:00:00Z,ref,lom",
    "quote_comma": 'evil","injected","x',
    "formula_eq": "=cmd|'/c calc'!A1",
    "formula_plus": "+1+1",
    "formula_minus": "-2+3",
    "formula_at": "@SUM(1:9)",
}


def _reset_modules(restore: dict | None = None) -> None:
    for key in list(sys.modules):
        if key.startswith(_PURGED):
            del sys.modules[key]
    if restore:
        sys.modules.update(restore)


@contextmanager
def _console(tmp_path: Path):
    home = tmp_path / "corvin_home"
    (home / "tenants" / TENANT / "global" / "auth").mkdir(parents=True)
    (home / "tenants" / TENANT / "global" / "console" / "sessions").mkdir(parents=True)

    keys = ("CORVIN_HOME", "CORVIN_TENANT_ID", "VOICE_AUDIT_PATH")
    prev = {k: os.environ.get(k) for k in keys}
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["CORVIN_TENANT_ID"] = TENANT
    os.environ["VOICE_AUDIT_PATH"] = str(home / "audit.jsonl")
    preloaded = {k: v for k, v in sys.modules.items() if k.startswith(_PURGED)}
    try:
        _reset_modules()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from corvin_console import auth as _auth
        from corvin_console.app import router

        rec = _auth.create_session(tenant_id=TENANT, token_fingerprint="inject-fp")
        csrf = _auth.derive_csrf_token(rec.csrf_secret, rec.sid)

        app = FastAPI()
        app.include_router(router, prefix="/v1/console")
        client = TestClient(app, raise_server_exceptions=False)
        client.cookies.set("corvin_console_sid", rec.sid)
        client.headers.update({"X-CSRF-Token": csrf})
        yield client, home
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_modules(restore=preloaded)


def _seed_poisoned_events(home: Path) -> int:
    """One learning event per payload, written to the real store's JSONL file."""
    from core.learning.learning_events import EventType, LearningEvent

    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%S") + "Z"
    events_dir = home / "tenants" / TENANT / "learning" / "events"
    events_dir.mkdir(parents=True, exist_ok=True)

    lines = []
    for name, payload in PAYLOADS.items():
        event = LearningEvent.create(
            event_type=EventType.METRIC,
            skill_id=payload,
            tenant_id=TENANT,
            signal={"case": name},
            lom=payload,
        )
        record = event.to_dict()
        record["timestamp"] = stamp
        record["audit_ref"] = payload
        lines.append(json.dumps(record, separators=(",", ":")))

    (events_dir / (now.strftime("%Y-%m-%d") + ".jsonl")).write_text("\n".join(lines) + "\n")
    return len(lines)


def _export(client, fmt: str):
    return client.post(
        "/v1/console/learning/metrics/export",
        json={"format": fmt, "window": "24h"},
    )


def test_json_export_round_trips_every_payload_without_corruption(tmp_path):
    """JSONL must survive NUL, control bytes and CRLF: one line per event."""
    with _console(tmp_path) as (client, home):
        expected = _seed_poisoned_events(home)

        res = _export(client, "json")
        assert res.status_code == 200, res.text

        lines = [l for l in res.text.splitlines() if l.strip()]
        assert len(lines) == expected, (
            f"expected {expected} JSONL rows, got {len(lines)} — an embedded "
            "newline broke the record framing"
        )
        recovered = {json.loads(l)["skill_id"] for l in lines}
        assert recovered == set(PAYLOADS.values()), "payloads must round-trip exactly"


def test_csv_export_cannot_be_row_injected(tmp_path):
    """An embedded CRLF must stay INSIDE one quoted cell, not create a row."""
    with _console(tmp_path) as (client, home):
        expected = _seed_poisoned_events(home)

        res = _export(client, "csv")
        assert res.status_code == 200, res.text

        from corvin_console.routes.learning_metrics import csv_safe

        rows = list(csv.DictReader(io.StringIO(res.text)))
        assert len(rows) == expected, (
            f"expected {expected} CSV rows, got {len(rows)} — CRLF in a field "
            "created extra rows (row injection)"
        )
        # Values survive intact apart from the deliberate formula neutralisation.
        assert {r["skill_id"] for r in rows} == {csv_safe(v) for v in PAYLOADS.values()}
        for row in rows:
            assert row["skill_id"].lstrip("'") in set(PAYLOADS.values())


def test_csv_export_does_not_hand_a_spreadsheet_a_formula(tmp_path):
    """`=`, `+`, `-`, `@` leading a cell is executable in Excel/Sheets.

    If this fails, the export is a CSV-injection vector and the fix belongs in
    ``routes/learning_metrics.py`` (prefix such cells with a single quote or a
    tab before writing).
    """
    with _console(tmp_path) as (client, home):
        _seed_poisoned_events(home)

        res = _export(client, "csv")
        assert res.status_code == 200, res.text

        offenders = []
        for row in csv.DictReader(io.StringIO(res.text)):
            for field, value in row.items():
                if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
                    offenders.append((field, value))

        assert not offenders, (
            "CSV cells start with a spreadsheet formula character: "
            f"{offenders}"
        )
