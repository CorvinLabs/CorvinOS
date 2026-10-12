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


def test_a_document_reaches_the_skill_and_plugin_forge_redacted_and_survives_a_skill_resume(tmp_path, monkeypatch):
    from corvin_console import forge_runs
    doc = "# Runbook\nPage anna.schmidt@example.org when the nightly job fails; see the checklist below.\n"
    engine = ScriptedEngine()
    with console(tmp_path, engine) as client:
        rep = _up(client, "runbook.md", doc, "text/markdown").json()
        assert rep["verdict"] == "admitted_redacted" and rep["fmt"] == "text" and "email" in rep["pii_kinds"]
        spawned = []
        monkeypatch.setattr(forge_runs, "spawn", lambda **kw: spawned.append(kw))
        s = client.post("/v1/console/skill-creator/generate", json={"user_request": "a skill that follows the attached runbook",
                                                                     "attachment_ids": [rep["item_id"]]})
        assert s.status_code == 202, s.text
        rec = json.loads((client.home / "tenants/_default/global/forge_runs" / f"{s.json()['run_id']}.json").read_text())
        pack = rec["resume"]["attachment_pack"]
        assert "anna.schmidt" not in json.dumps(pack) and "Runbook" in json.dumps(pack), "the resume payload carries the REDACTED pack"
        # a resumed run builds its engine request from that stored pack
        from corvin_console.routes import skill_creator_api as api
        seen = []

        class Stub:
            engine_id = "stub"
            client = None

            def __init__(self, **kw): pass

            async def create_skill(self, request, base=None):
                seen.append(request)
                raise RuntimeError("stop")
        monkeypatch.setattr(api, "SkillCreatorOrchestrator", Stub)
        resumed = forge_runs._resumers["skill"](s.json()["run_id"], rec)
        try:
            resumed["work"](lambda *a, **k: None)
        except RuntimeError:
            pass
        assert seen and "Attached data" in seen[0] and "Runbook" in seen[0] and "anna.schmidt" not in seen[0]
        p = client.post("/v1/console/forge-creator/plugin/generate",
                        json={"user_request": "a plugin that follows the attached runbook", "attachment_ids": [rep["item_id"]]})
        assert p.status_code == 202, p.text


def test_every_generator_path_rejects_an_unknown_field_with_422_and_starts_nothing(tmp_path):
    with console(tmp_path, ScriptedEngine()) as client:
        junk = {"attachment_ids": ["att-0123456789abcdef"], "bogus": 1}
        for path, base in (("/v1/console/forge-creator/tool/generate", {"user_request": "count the words in a text"}),
                           ("/v1/console/forge-creator/plugin/generate", {"user_request": "a weather digest plugin"}),
                           ("/v1/console/skill-creator/generate", {"user_request": "a skill that validates json"}),
                           ("/v1/console/layer-forge/plan", {"layer_id": "L34", "intent": "guard the boundary"})):
            r = client.post(path, json={**base, "bogus": 1})
            assert r.status_code == 422, (path, r.status_code, r.text[:200])
        # the skill and layer paths do not consume attachments: the id is an unknown field there, not silently dropped
        assert client.post("/v1/console/skill-creator/generate",
                           json={"user_request": "a skill that validates json", **junk}).status_code == 422
        # the layer PLAN takes attachment ids now (slice 5); an unknown field is still refused, and a foreign id is a 404
        assert client.post("/v1/console/layer-forge/plan",
                           json={"layer_id": "L34", "intent": "guard the boundary", "attachment_ids": ["att-0123456789abcdef"]}
                           ).status_code == 404


def test_uploads_age_out_unused_after_24h_and_used_after_14d_and_foreign_names_are_left_alone(tmp_path):
    import os
    from corvin_console.routes import forge_intake_api as api
    with console(tmp_path, ScriptedEngine()) as client:
        a = _up(client, "a.csv", CSV).json()["item_id"]
        b = _up(client, "b.csv", CSV).json()["item_id"]
        root = client.home / "tenants/_default/global/forge_intake/uploads"
        meta = root / b / "meta.json"
        m = json.loads(meta.read_text()); m["used"] = True; meta.write_text(json.dumps(m))
        stranger = root / "notes"; stranger.mkdir()
        for d, age in ((root / a, 2 * 86400), (root / b, 2 * 86400), (stranger, 40 * 86400)):
            t = time.time() - age
            os.utime(d, (t, t))
        api._prune("_default")
        assert not (root / a).exists(), "unused and older than 24 h"
        assert (root / b).exists(), "used by a run: kept for 14 days"
        assert stranger.exists(), "only att-<hex> directories are ever touched"
        t = time.time() - 15 * 86400
        os.utime(root / b, (t, t))
        api._prune("_default")
        assert not (root / b).exists()


def test_the_layer_plan_receives_only_the_redacted_projection_of_an_attachment(tmp_path, monkeypatch):
    from core.orchestration.layer_forge import llm_client
    engine = ScriptedEngine()
    monkeypatch.setattr(llm_client, "get_client", lambda **kw: engine)
    with console(tmp_path, engine) as client:
        rep = _up(client, "policy.md", "Boundary rule: only the build may write to dist. Owner: lena.k@example.org\n", "text/markdown").json()
        assert rep["verdict"] == "admitted_redacted"
        r = client.post("/v1/console/layer-forge/plan", json={"layer_id": "L34", "intent": "enforce the attached boundary rule",
                                                              "attachment_ids": [rep["item_id"]]})
        assert r.status_code < 600 and engine.prompts, "the PLAN reached the engine"
        prompts = "\n".join(engine.prompts)
        assert "Boundary rule" in prompts and "lena.k@example.org" not in prompts
