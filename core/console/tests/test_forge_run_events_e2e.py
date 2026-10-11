"""The per-run event log and the run envelope (ADR-2253 D1/D2; T-0147).

Real generate routes with the scripted engine; the log is read back from the file a host writes. Nothing here
mocks the writer."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_forge_creator_e2e import PLUGIN_PLAN, ScriptedEngine, console, poll, start  # noqa: E402

from corvin_console import forge_runs  # noqa: E402

REQUEST = "count the words in a text, the secret project name is Bluebird"


def _log(client, run_id: str) -> list[dict]:
    p = client.home / "tenants" / "_default" / "global" / "forge_runs" / run_id / "events.jsonl"
    assert p.is_file(), f"no event log at {p}"
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def test_a_tool_run_leaves_an_ordered_log_of_what_it_did_and_never_its_prompt(tmp_path):
    with console(tmp_path, ScriptedEngine()) as client:
        run_id = start(client, "tool", REQUEST)
        body = poll(client, run_id)
        assert body["status"] == "success", body
        ev = _log(client, run_id)
        assert [e["seq"] for e in ev] == list(range(1, len(ev) + 1)), "seq strictly increases from 1"
        types = {e["type"] for e in ev}
        assert {"progress", "test_result"} <= types, types
        assert types <= forge_runs.EVENT_TYPES
        tr = next(e for e in ev if e["type"] == "test_result")
        assert tr["data"] == {"passed": 2, "total": 2}
        raw = json.dumps(ev)
        assert "Bluebird" not in raw and "count the words in a text" not in raw, "the request text is never logged"
        # the final progress event says 100
        assert ev[-1]["type"] == "progress" and ev[-1]["data"]["progress"] == 100


def test_a_plugin_run_logs_each_file_it_wrote(tmp_path):
    with console(tmp_path, ScriptedEngine()) as client:
        run_id = start(client, "plugin", "a weather digest plugin for operators")
        body = poll(client, run_id)
        assert body["status"] == "success", body
        files = [e["data"]["path"] for e in _log(client, run_id) if e["type"] == "file_written"]
        assert "plugin.py" in files and len(files) == len(set(files)) >= 3, files


def test_a_failed_run_logs_an_error_event(tmp_path):
    from test_forge_creator_e2e import BROKEN_IMPL, tool_plan
    engine = ScriptedEngine(tool_plan=tool_plan(BROKEN_IMPL), tool_fix=tool_plan(BROKEN_IMPL))
    with console(tmp_path, engine) as client:
        run_id = start(client, "tool", "count the words in a text")
        assert poll(client, run_id)["status"] == "failed"
        ev = _log(client, run_id)
        last = ev[-1]
        assert last["type"] == "error" and last["level"] == "error"
        # a failed run still shows what it DID: the sandbox tests that ran (and failed), before the error
        tr = next(e for e in ev if e["type"] == "test_result")
        assert tr["data"]["passed"] < tr["data"]["total"], tr
        assert tr["seq"] < last["seq"]


@pytest.mark.parametrize("kind,request_text", [("tool", "count the words in a text"), ("plugin", "a weather digest plugin")])
def test_the_envelope_is_complete_for_tool_and_plugin_runs(tmp_path, kind, request_text):
    with console(tmp_path, ScriptedEngine()) as client:
        run_id = start(client, kind, request_text)
        body = poll(client, run_id)
        assert body["status"] == "success"
        rec = json.loads((client.home / "tenants/_default/global/forge_runs" / f"{run_id}.json").read_text())
        for field in ("started_at", "ended_at", "engine", "model", "artifact_ref", "request_digest"):
            assert rec.get(field) not in (None, ""), (field, rec)
        assert rec["model"] == "scripted-model", "the model the engine REPORTED, not a guess"
        assert rec["artifact_ref"]["kind"] == kind and rec["artifact_ref"]["id"]
        assert len(rec["request_digest"]) == 16 and request_text not in json.dumps(rec["request_digest"])


def test_only_the_owner_appends_and_a_pii_shaped_message_is_withheld(tmp_path):
    with console(tmp_path, ScriptedEngine()) as client:
        rid = forge_runs.new_run(tenant_id="_default", kind="tool", phases=("planning",), sid_fingerprint=None,
                                 resume={"request": "x" * 12})
        assert forge_runs.emit_event(rid, "progress", "contact jane.doe@example.org about it") == 1
        assert forge_runs.emit_event(rid, "progress", "call +49 170 1234567 now") == 2
        ev = _log(client, rid)
        assert all("jane.doe" not in e["message"] and "1234567" not in e["message"] for e in ev), ev
        assert ev[0]["message"].startswith("[withheld")
        # not the owner: a record owned by another process is never appended to
        forge_runs.runs[rid]["boot_id"] = "somebody-else"
        assert forge_runs.emit_event(rid, "progress", "must not be written") is None
        assert len(_log(client, rid)) == 2


def test_the_cap_counts_what_it_leaves_out_and_says_so_with_a_truncated_event(tmp_path, monkeypatch):
    monkeypatch.setattr(forge_runs, "MAX_EVENTS_PER_RUN", 5)
    with console(tmp_path, ScriptedEngine()) as client:
        rid = forge_runs.new_run(tenant_id="_default", kind="tool", phases=("planning",), sid_fingerprint=None,
                                 resume={"request": "x" * 12})
        written = [forge_runs.emit_event(rid, "progress", f"step {i}") for i in range(12)]
        assert written[:5] == [1, 2, 3, 4, 5] and all(w is None for w in written[5:])
        forge_runs.finalize_events(rid)
        forge_runs.finalize_events(rid)  # idempotent: one marker only
        ev = _log(client, rid)
        assert len(ev) == 6 and ev[-1]["type"] == "truncated"
        assert ev[-1]["data"]["dropped"] == 7 and ev[-1]["level"] == "info"
        assert [e["type"] for e in ev].count("error") == 0, "a truncation is never an error"


def test_the_byte_cap_also_bounds_a_run(tmp_path, monkeypatch):
    monkeypatch.setattr(forge_runs, "MAX_EVENT_BYTES_PER_RUN", 600)
    with console(tmp_path, ScriptedEngine()) as client:
        rid = forge_runs.new_run(tenant_id="_default", kind="tool", phases=("planning",), sid_fingerprint=None,
                                 resume={"request": "x" * 12})
        for i in range(50):
            forge_runs.emit_event(rid, "progress", "y" * 100)
        forge_runs.finalize_events(rid)
        ev = _log(client, rid)
        assert ev[-1]["type"] == "truncated" and ev[-1]["data"]["dropped"] > 0
        assert len(ev) < 50


def test_an_unknown_event_type_is_refused(tmp_path):
    with console(tmp_path, ScriptedEngine()):
        rid = forge_runs.new_run(tenant_id="_default", kind="tool", phases=("planning",), sid_fingerprint=None)
        with pytest.raises(ValueError):
            forge_runs.emit_event(rid, "prompt", "the whole prompt")
