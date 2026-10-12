"""forge_intake.* audit events (ADR-2254): registered, allowlisted, on the tenant chain, and free of names and values."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_forge_creator_e2e import ScriptedEngine, console  # noqa: E402

from corvin_console import audit as console_audit  # noqa: E402

REPORT = {"item_id": "att-1", "verdict": "admitted_redacted", "reason_code": "", "fmt": "csv", "ext": ".csv", "size_bytes": 530,
          "sha256_prefix": "db29be3e9ddaea40", "data_class": "projection_cleared", "pii_kinds": {"email": 1, "address": 1},
          "redactions": {"redact": 2}, "columns": 3, "rows": 40}


def _records(client):
    chain = client.home / "tenants/_default/global/forge/audit.jsonl"
    return [json.loads(l) for l in chain.read_text().splitlines() if l.strip()]


def test_each_verdict_maps_to_its_event_with_only_ids_counts_and_codes(tmp_path):
    with console(tmp_path, ScriptedEngine()) as client:
        console_audit.forge_intake_item(tenant_id="_default", report=REPORT, run_id="run-aaaaaaaaaaaa")
        console_audit.forge_intake_item(tenant_id="_default", report={**REPORT, "verdict": "admitted", "pii_kinds": {}})
        console_audit.forge_intake_item(tenant_id="_default", report={
            "item_id": "att-2", "verdict": "refused", "reason_code": "contains_secret", "fmt": "csv", "size_bytes": 9, "sha256_prefix": "ab"})
        recs = {r["event_type"]: r for r in _records(client) if r["event_type"].startswith("forge_intake.")}
        assert set(recs) == {"forge_intake.item_redacted", "forge_intake.item_admitted", "forge_intake.item_refused"}
        d = recs["forge_intake.item_redacted"]["details"]
        assert d["pii_kinds_count"] == 2 and d["run_id"] == "run-aaaaaaaaaaaa" and d["format"] == "csv"
        assert "_dropped_fields" not in d, "every field is allowlisted: none was cut"
        assert recs["forge_intake.item_refused"]["severity"] == "WARNING"
        assert recs["forge_intake.item_refused"]["details"]["reason_code"] == "contains_secret"


def test_no_file_name_and_no_cell_value_can_reach_the_chain_even_if_a_caller_passes_them(tmp_path):
    with console(tmp_path, ScriptedEngine()) as client:
        hostile = {**REPORT, "name": "customers-2026.csv", "filename": "customers-2026.csv", "sample": [{"email": "a@b.org"}],
                   "pii_kinds": {"email": 1}}
        console_audit.forge_intake_item(tenant_id="_default", report=hostile)
        raw = (client.home / "tenants/_default/global/forge/audit.jsonl").read_text()
        assert "customers-2026" not in raw and "a@b.org" not in raw
