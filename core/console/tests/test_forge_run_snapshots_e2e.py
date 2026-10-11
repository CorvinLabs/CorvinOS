"""Snapshots and the diff between them (ADR-2253 D3; T-0150): real generate routes, the scripted engine, the real files."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_forge_creator_e2e import BROKEN_IMPL, GOOD_IMPL, ScriptedEngine, console, poll, start, tool_plan  # noqa: E402

from corvin_console import forge_runs  # noqa: E402

B = "/v1/console/forge"


def _tool_run(client, engine_kwargs=None):
    run_id = start(client, "tool", "count the words in a text")
    return run_id, poll(client, run_id)


def test_a_tool_that_fails_then_is_fixed_leaves_two_iterations_and_a_real_diff(tmp_path):
    engine = ScriptedEngine(tool_plan=tool_plan(BROKEN_IMPL), tool_fix=tool_plan(GOOD_IMPL))
    with console(tmp_path, engine) as client:
        run_id, body = _tool_run(client)
        assert body["status"] == "success", body
        ev = client.get(f"{B}/runs/{run_id}/events").json()["events"]
        its = [e for e in ev if e["type"] == "iteration"]
        assert [(e["data"]["k"], e["data"]["passed"] < e["data"]["total"]) for e in its] == [(1, True), (2, False)]
        snaps = client.get(f"{B}/runs/{run_id}/snapshots").json()["snapshots"]
        assert [s["label"] for s in snaps] == ["iteration 1", "iteration 2"]
        assert "assistant.word_count.py" in snaps[0]["files"]
        d = client.get(f"{B}/runs/{run_id}/diff").json()          # default: the last two
        assert d["from"]["label"] == "iteration 1" and d["to"]["label"] == "iteration 2"
        changed = {f["path"]: f for f in d["files"]}
        assert changed["assistant.word_count.py"]["status"] == "changed"
        assert "-print(json.dumps({'words': 42}))" in changed["assistant.word_count.py"]["diff"]
        assert "+print(json.dumps({'words': len(d['text'].split())}))" in changed["assistant.word_count.py"]["diff"]
        assert changed["input_schema.json"]["status"] == "same" and changed["input_schema.json"]["diff"] == ""


def test_diff_by_label_and_index_and_a_run_with_one_snapshot_has_nothing_to_compare(tmp_path):
    engine = ScriptedEngine(tool_plan=tool_plan(BROKEN_IMPL), tool_fix=tool_plan(GOOD_IMPL))
    with console(tmp_path, engine) as client:
        run_id, _ = _tool_run(client)
        by_label = client.get(f"{B}/runs/{run_id}/diff", params={"from": "iteration 1", "to": "iteration 2"}).json()
        by_index = client.get(f"{B}/runs/{run_id}/diff", params={"from": "1", "to": "2"}).json()
        assert by_label["files"] == by_index["files"]
        assert client.get(f"{B}/runs/{run_id}/diff", params={"from": "nope"}).status_code == 404
    with console(tmp_path / "second", ScriptedEngine()) as client:    # a tool that passes first time: one snapshot
        run_id, _ = _tool_run(client)
        assert len(client.get(f"{B}/runs/{run_id}/snapshots").json()["snapshots"]) == 1
        r = client.get(f"{B}/runs/{run_id}/diff")
        assert r.status_code == 404 and "no two snapshots" in r.json()["detail"]


def test_a_plugin_run_snapshots_its_staged_files(tmp_path):
    with console(tmp_path, ScriptedEngine()) as client:
        run_id = start(client, "plugin", "a weather digest plugin for operators")
        assert poll(client, run_id)["status"] == "success"
        snaps = client.get(f"{B}/runs/{run_id}/snapshots").json()["snapshots"]
        assert [s["label"] for s in snaps] == ["staged"] and "plugin.py" in snaps[0]["files"]


def test_snapshots_are_content_addressed_and_only_hashes_not_files_are_listed(tmp_path):
    with console(tmp_path, ScriptedEngine()) as client:
        rid = forge_runs.new_run(tenant_id="_default", kind="tool", phases=("planning",), sid_fingerprint=None, resume={"request": "x" * 12})
        assert forge_runs.snapshot(rid, "a", {"f.py": "print(1)\n", "g.py": "same\n"}) == 1
        assert forge_runs.snapshot(rid, "b", {"f.py": "print(2)\n", "g.py": "same\n"}) == 2
        blobs = list((client.home / "tenants/_default/global/forge_runs" / rid / "snapshots/blobs").iterdir())
        assert len(blobs) == 3, "g.py's identical content is stored once"
        listing = client.get(f"{B}/runs/{rid}/snapshots").text
        assert "print(2)" not in listing, "the snapshot list names files, it never inlines content"


def test_unsafe_paths_are_not_stored_and_oversized_files_are_cut_visibly(tmp_path, monkeypatch):
    monkeypatch.setattr(forge_runs, "MAX_SNAPSHOT_FILE_BYTES", 50)
    with console(tmp_path, ScriptedEngine()) as client:
        rid = forge_runs.new_run(tenant_id="_default", kind="tool", phases=("planning",), sid_fingerprint=None, resume={"request": "x" * 12})
        n = forge_runs.snapshot(rid, "s", {"../../etc/passwd": "root", "/abs/path": "x", "a/../b": "x", "ok/file.py": "y" * 500})
        snaps = forge_runs.read_snapshots("_default", rid)
        assert list(snaps[0]["files"]) == ["ok/file.py"], "traversal and absolute paths are refused"
        d = forge_runs.snapshot(rid, "t", {"ok/file.py": "z" * 500})
        diff = forge_runs.diff_snapshots("_default", rid)
        assert "[truncated" in diff["files"][0]["diff"], "a cut file says it was cut"
        assert n == 1 and d == 2


def test_the_snapshot_cap_skips_the_extra_one_and_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(forge_runs, "MAX_SNAPSHOTS_PER_RUN", 2)
    with console(tmp_path, ScriptedEngine()) as client:
        rid = forge_runs.new_run(tenant_id="_default", kind="tool", phases=("planning",), sid_fingerprint=None, resume={"request": "x" * 12})
        assert [forge_runs.snapshot(rid, str(i), {"f": str(i)}) for i in range(4)] == [1, 2, None, None]
        msgs = [e["message"] for e in client.get(f"{B}/runs/{rid}/events").json()["events"]]
        assert any("snapshot was skipped" in m for m in msgs)


def test_only_the_owner_snapshots_and_a_foreign_tenant_gets_404(tmp_path):
    from test_forge_runs_api_e2e import _second_tenant_client
    engine = ScriptedEngine(tool_plan=tool_plan(BROKEN_IMPL), tool_fix=tool_plan(GOOD_IMPL))
    with console(tmp_path, engine) as client:
        run_id, _ = _tool_run(client)
        other = _second_tenant_client(client)
        theirs = forge_runs.new_run(tenant_id="other_tenant", kind="tool", phases=("planning",), sid_fingerprint=None, resume={"request": "x" * 12})
        forge_runs.snapshot(theirs, "a", {"f": "1"})
        forge_runs.snapshot(theirs, "b", {"f": "2"})
        assert other.get(f"{B}/runs/{theirs}/diff").status_code == 200, "positive control: the owner tenant reads its own diff"
        for path in (f"{B}/runs/{run_id}/diff", f"{B}/runs/{run_id}/snapshots"):
            assert other.get(path).status_code == 404, path
        forge_runs.runs[theirs]["boot_id"] = "somebody-else"
        assert forge_runs.snapshot(theirs, "c", {"f": "3"}) is None
