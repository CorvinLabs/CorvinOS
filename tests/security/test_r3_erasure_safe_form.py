"""R3-SEC-6: the folded directory form of a subject id is not an identity.

``session_state.safe_chat_key`` folds every non-alphanumeric character to
``_``: ``john.doe`` and ``john_doe`` share ``john_doe``. Round 2 restricted the
comparison of that form to ``chat_key`` / ``session_key`` - and under exactly
those keys an erasure of ``john.doe`` still deleted ``{"chat_key": "john_doe"}``,
which may be the record of the person whose id IS ``john_doe``.

A folded-form match now needs the raw id in the same record. A record that has
only the folded form stays, and the layer reports ``not_erasable``.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SHARED = REPO / "corvin_operator" / "bridges" / "shared"
FORGE = REPO / "corvin_operator" / "forge"

for _p in (SHARED, FORGE, REPO):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def test_folded_form_alone_does_not_match_under_safe_form_keys():
    import erasure_handlers as eh

    for key in sorted(eh._SAFE_FORM_KEYS):
        eh.reset_ambiguous_safe_form_count()
        assert not eh._mentions_subject({key: "john_doe"}, "john.doe"), key
        assert not eh._mentions_subject({"rows": [{key: "john_doe"}]}, "john.doe"), key
        assert eh.ambiguous_safe_form_count() == 2
    jid = "4915112345678@s.whatsapp.net"
    eh.reset_ambiguous_safe_form_count()
    assert not eh._mentions_subject({"chat_key": "4915112345678_s_whatsapp_net"}, jid)
    assert eh.ambiguous_safe_form_count() == 1


def test_folded_form_with_the_raw_id_in_the_same_record_matches():
    import erasure_handlers as eh

    jid = "4915112345678@s.whatsapp.net"
    safe = "4915112345678_s_whatsapp_net"
    eh.reset_ambiguous_safe_form_count()
    assert eh._mentions_subject({"chat_key": safe, "jid": jid}, jid)
    assert eh._mentions_subject({"chat_key": safe, "meta": {"raw_chat_id": jid}}, jid)
    assert eh._mentions_subject({"turns": [{"session_key": safe}], "from": jid}, jid)
    assert eh.ambiguous_safe_form_count() == 0
    # The raw id of someone ELSE in the record does not vouch for the folded form.
    assert not eh._mentions_subject({"chat_key": "john_doe", "from": "john_doe"}, "john.doe")


def test_exact_match_is_unchanged_and_never_counted_ambiguous():
    import erasure_handlers as eh

    eh.reset_ambiguous_safe_form_count()
    assert eh._mentions_subject({"chat_key": "john.doe"}, "john.doe")
    assert eh._mentions_subject({"a": [{"b": {"user": "john.doe"}}]}, "john.doe")
    assert not eh._mentions_subject({"user": "john_doe"}, "john.doe")
    assert not eh._mentions_subject({"note": "john.doe"}, "john.doe")
    # An id with one form only cannot be ambiguous.
    assert not eh._mentions_subject({"chat_key": "user43"}, "user42")
    assert eh.ambiguous_safe_form_count() == 0


def _run(code: str, tmp_path: Path) -> dict:
    home = tmp_path / "home"
    key = tmp_path / "anchor.key"
    key.write_bytes(os.urandom(32))
    key.chmod(0o600)
    env = {k: v for k, v in os.environ.items()
           if k not in ("FORGE_ROOT", "VOICE_AUDIT_PATH", "CORVIN_TENANT_ID")}
    env.update({
        "CORVIN_HOME": str(home), "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
        "CORVIN_AUDIT_ANCHOR_KEY": str(key), "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": os.pathsep.join([str(REPO), str(REPO / "corvin_operator"),
                                       str(FORGE), str(SHARED)]),
    })
    proc = subprocess.run([sys.executable, "-c", textwrap.dedent(code)], env=env,
                          capture_output=True, text=True, timeout=180, cwd=str(REPO))
    assert proc.returncode == 0, proc.stderr[-3000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


_E2E = """
    import json
    from pathlib import Path
    import erasure_orchestrator as eo, erasure_handlers as eh
    import paths
    home = Path(paths.tenant_home("_default"))
    side = home / "learning" / "decisions.jsonl"
    side.parent.mkdir(parents=True, exist_ok=True)
    side.write_text("".join(json.dumps(r) + "\\n" for r in RECORDS))
    chain = Path(paths.tenant_audit_chain("_default"))
    chain.parent.mkdir(parents=True, exist_ok=True)
    orch = eo.ErasureOrchestrator(tenant_id="_default", trail_dir=home / "global" / "erasure",
                                  audit_writer=eo.make_forge_audit_writer(chain))
    orch.register_handler(eh.LearningEventHandler(tenant_id="_default"))
    res = orch.execute(eo.ErasureRequest(subject_id="john.doe", requester="dpo"))
    lr = res.per_layer[0]
    events = [json.loads(l) for l in chain.read_text().splitlines()]
    layer_events = [[e["event_type"], e["details"].get("code"), e["details"].get("count")]
                    for e in events if e["event_type"] in ("erasure.applied", "erasure.skipped")]
    print(json.dumps({"left": [json.loads(l) for l in side.read_text().splitlines()],
                      "status": lr.status.value, "code": lr.code, "count": lr.count,
                      "reason": lr.reason, "unattributable": res.unattributable_layers,
                      "overall": res.overall_status.value, "events": layer_events,
                      "trail": json.loads(next((home / "global" / "erasure").glob("*.json")).read_text())}))
"""


def test_layer_with_only_folded_form_records_reports_not_erasable(tmp_path):
    other = {"chat_key": "john_doe", "text": "belongs to john_doe"}
    out = _run(f"RECORDS = {[other]!r}\n" + textwrap.dedent(_E2E), tmp_path)
    assert out["left"] == [other], "another subject's record was deleted"
    assert (out["status"], out["code"]) == ("skipped", "not_erasable")
    assert "folded directory form" in out["reason"]
    assert out["events"] == [["erasure.skipped", "not_erasable", 0]]


def test_layer_that_applied_still_reports_the_records_it_left(tmp_path):
    other = {"chat_key": "john_doe", "text": "belongs to john_doe"}
    mine = {"user": "john.doe", "text": "x"}
    mine_folded = {"chat_key": "john_doe", "raw_chat_id": "john.doe", "text": "y"}
    out = _run(f"RECORDS = {[other, mine, mine_folded]!r}\n" + textwrap.dedent(_E2E), tmp_path)
    assert out["left"] == [other]
    assert out["status"] == "applied" and out["count"] == 2
    assert out["unattributable"] == ["L-learning"]
    assert out["trail"]["unattributable_layers"] == ["L-learning"]
    assert ["erasure.applied", "deleted", 2] in out["events"]
    assert ["erasure.skipped", "not_erasable", 1] in out["events"]


def test_clean_layer_is_not_flagged(tmp_path):
    mine = {"user": "john.doe", "text": "x"}
    unrelated = {"chat_key": "alice", "text": "z"}
    out = _run(f"RECORDS = {[mine, unrelated]!r}\n" + textwrap.dedent(_E2E), tmp_path)
    assert out["left"] == [unrelated]
    assert out["status"] == "applied" and out["count"] == 1
    assert out["unattributable"] == []
    assert out["events"] == [["erasure.applied", "deleted", 1]]
