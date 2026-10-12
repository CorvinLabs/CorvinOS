"""Attachments through the real console router: upload -> report -> generate with attachment_ids (ADR-2254, T-0155/T-0167)."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_forge_creator_e2e import GOOD_IMPL, ScriptedEngine, console, tool_plan  # noqa: E402

CSV = "email,product,qty\n" + "".join(f"user{i}@example.org,widget{i},{i}\n" for i in range(30))
GEN = "/v1/console/forge-creator/tool/generate"


def _up(client, name, data, ctype="text/csv"):
    return client.post("/v1/console/forge-intake/attachments", files={"file": (name, data, ctype)})


def _wait(client, run_id):
    for _ in range(100):
        b = client.get(f"/v1/console/forge-creator/status/{run_id}").json()
        if b["status"] != "running":
            return b
        time.sleep(0.2)
    raise AssertionError("run did not finish")


def test_upload_reports_redaction_without_name_or_value_and_generate_hands_the_engine_only_the_projection(tmp_path):
    engine = ScriptedEngine(plan=tool_plan(GOOD_IMPL))
    with console(tmp_path, engine) as client:
        r = _up(client, "secret-customers.csv", CSV)
        assert r.status_code == 201, r.text
        rep = r.json()
        assert rep["verdict"] == "admitted_redacted" and rep["pii_kinds"].get("email")
        assert "secret-customers" not in r.text and "user1@example.org" not in r.text
        g = client.post(GEN, json={"user_request": "dedupe the rows of the attached table", "attachment_ids": [rep["item_id"]]})
        assert g.status_code == 202, g.text
        _wait(client, g.json()["run_id"])
        prompts = "\n".join(engine.prompts)
        assert "Attached data" in prompts and "widget1" in prompts
        assert "user1@example.org" not in prompts, "the raw e-mail address must never reach the engine"


def test_a_secret_file_is_refused_at_upload_and_the_whole_generate_is_a_4xx(tmp_path):
    with console(tmp_path, ScriptedEngine(plan=tool_plan(GOOD_IMPL))) as client:
        bad = _up(client, "k.csv", "a,b\n1,AKIAIOSFODNN7EXAMPLE\n").json()
        assert bad["verdict"] == "refused" and bad["reason_code"] == "contains_secret"
        g = client.post(GEN, json={"user_request": "use the attached file please", "attachment_ids": [bad["item_id"]]})
        assert g.status_code == 422


def test_unknown_foreign_deleted_and_altered_attachments_fail_the_request(tmp_path):
    with console(tmp_path, ScriptedEngine(plan=tool_plan(GOOD_IMPL))) as client:
        body = lambda ids: {"user_request": "use the attached file please", "attachment_ids": ids}  # noqa: E731
        assert client.post(GEN, json=body(["att-0123456789abcdef"])).status_code == 404
        assert client.post(GEN, json=body(["../etc/passwd"])).status_code == 404
        rep = _up(client, "a.csv", CSV).json()
        f = next((client.home / "tenants/_default/global/forge_intake/uploads" / rep["item_id"]).glob("data.csv"))
        f.write_text(CSV + "tampered,row,1\n")
        assert client.post(GEN, json=body([rep["item_id"]])).status_code == 409
        rep2 = _up(client, "b.csv", CSV).json()
        assert client.delete(f"/v1/console/forge-intake/attachments/{rep2['item_id']}").status_code == 200
        assert client.post(GEN, json=body([rep2["item_id"]])).status_code == 404


def test_attachments_on_a_plugin_or_a_skill_request_are_422(tmp_path):
    with console(tmp_path, ScriptedEngine()) as client:
        rep = _up(client, "a.csv", CSV).json()
        r = client.post("/v1/console/forge-creator/plugin/generate",
                        json={"user_request": "a plugin with the attached file", "attachment_ids": [rep["item_id"]]})
        assert r.status_code == 422
