"""ADR-0278 — CEL Decision Record: audit-complete + PII-safe.

Verifies the two-layer trail:
  * Layer A is CONTENT-FREE — no task/brief/text ever reaches the record, and
    assert_content_free fails LOUD on a leak (forbidden key OR long string).
  * ALL conceptual stages appear — inactive ones as `not_run` (completeness).
  * per-source {id, score} causal reason is preserved (not collapsed to a tier).
  * brief_sha256 in Layer A == sha256(brief) AND Layer B sidecar is that file.
  * Layer A is written through the REAL hash-chained writer (carries `hash`,
    `prev_hash`) and contains NO brief text.
  * emit never raises into the turn.

Run: python3 operator/context_engineering/tests/test_decision_record_adr0278.py
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "operator" / "forge"))


def _load_dr():
    ce = _REPO / "operator" / "context_engineering"
    spec = importlib.util.spec_from_file_location(
        "context_engineering", str(ce / "__init__.py"),
        submodule_search_locations=[str(ce)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["context_engineering"] = mod
    spec.loader.exec_module(mod)
    # __init__ ran `from .decision_record import …`, registering the submodule.
    return sys.modules["context_engineering.decision_record"]


_TRACE = {
    "task_preview": "erklär postgres partial indexes",  # NB: present in the TRACE…
    "stages": [
        {"stage": "memory", "status": "ok", "confidence_tier": "high",
         "duration_ms": 118,
         "sources": [{"id": "pg-indexes.md", "score": 0.82},
                     {"id": "query-planner.md", "score": 0.55}]},
        {"stage": "graph", "status": "ok", "confidence_tier": "medium",
         "sources": [{"id": "ADR-0269", "score": 0.4}]},
        {"stage": "skill", "status": "failed", "error": "skill store unreachable"},
    ],
}
_BRIEF = "## Context brief\nRelevant past memory:\n  - Postgres indexes\n"


class DecisionRecordTests(unittest.TestCase):
    def setUp(self):
        self.dr = _load_dr()
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        os.environ["CORVIN_HOME"] = self.td.name
        self.addCleanup(lambda: os.environ.pop("CORVIN_HOME", None))
        self.workdir = Path(self.td.name) / "sess"
        self.workdir.mkdir(parents=True)

    def test_all_conceptual_stages_present(self):
        rec = self.dr.build_record(_TRACE, _BRIEF, turn_id="turn-1")
        names = [s["stage"] for s in rec["stages"]]
        self.assertEqual(names,
                         ["memory", "graph", "skill", "approach_synthesis", "blocker_id"])
        # In this trace only memory/graph/skill are present, so the two later
        # stages are recorded as not_run — but with reason "not_reached" now that
        # they actually run in the live pipeline (ADR-0275), not "stage_inactive".
        inactive = {s["stage"]: s for s in rec["stages"]
                    if s["status"] == "not_run"}
        self.assertEqual(set(inactive), {"approach_synthesis", "blocker_id"})
        self.assertEqual(inactive["approach_synthesis"]["reason"], "not_reached")

    def test_per_source_score_preserved(self):
        rec = self.dr.build_record(_TRACE, _BRIEF, turn_id="turn-1")
        mem = next(s for s in rec["stages"] if s["stage"] == "memory")
        self.assertEqual(mem["sources"][0], {"id": "pg-indexes.md", "score": 0.82})
        self.assertEqual(rec["top_score"], 0.82)
        self.assertEqual(rec["stages_ok"], 2)  # memory + graph ok; skill failed

    def test_content_free_assertion_catches_leaks(self):
        with self.assertRaises(ValueError):
            self.dr.assert_content_free({"task": "some user text"})
        with self.assertRaises(ValueError):
            self.dr.assert_content_free({"note": "x" * 200})
        # a clean record passes
        self.dr.assert_content_free(self.dr.build_record(_TRACE, _BRIEF, turn_id="t"))

    def test_brief_hash_binds_layers(self):
        rec = self.dr.emit(_TRACE, _BRIEF, turn_id="turn-1", tenant_id="_default",
                           workdir=self.workdir, session_id="sess")
        self.assertIsNotNone(rec)
        want = hashlib.sha256(_BRIEF.encode("utf-8")).hexdigest()
        self.assertEqual(rec["brief_sha256"], want)
        sidecar = self.workdir / "cel-briefs" / f"{want}.txt"
        self.assertTrue(sidecar.exists(), "Layer B sidecar keyed by the hash")
        self.assertEqual(sidecar.read_text(encoding="utf-8"), _BRIEF)

    def test_layer_a_is_content_free_and_hash_chained(self):
        self.dr.emit(_TRACE, _BRIEF, turn_id="turn-1", tenant_id="_default",
                     workdir=self.workdir, session_id="sess")
        from forge.paths import tenant_global_dir
        chain = Path(tenant_global_dir("_default")) / "forge" / "audit.jsonl"
        self.assertTrue(chain.exists(), "Layer A written to the hash-chained log")
        events = [json.loads(ln) for ln in chain.read_text().splitlines() if ln.strip()]
        cel = [e for e in events if e.get("event_type") == "cel.decision"
               or e.get("type") == "cel.decision"]
        self.assertEqual(len(cel), 1)
        blob = json.dumps(cel[0])
        # tamper-evidence: a chain hash is present…
        self.assertTrue("hash" in cel[0] or "prev_hash" in blob or "hash" in blob)
        # …and the brief text is NOT in the immutable record (content-free).
        self.assertNotIn("partial indexes", blob)
        self.assertNotIn("Relevant past memory", blob)

    def test_layer_a_write_failure_is_surfaced_not_silent(self):
        # P-0 (ADR-0278 durability): a hash-chain write failure is LOGGED, not
        # silently dropped, and the turn still runs (emit degrades to None).
        import forge.security_events as se
        with patch.object(se, "write_event", side_effect=OSError("chain locked")):
            with self.assertLogs("context_engineering.decision_record",
                                 level="ERROR") as cm:
                rec = self.dr.emit(_TRACE, _BRIEF, turn_id="turn-x",
                                   tenant_id="_default", workdir=self.workdir,
                                   session_id="sess")
        self.assertIsNone(rec, "emit degrades (turn runs), does not raise")
        self.assertTrue(any("AUDIT-WRITE FAILED" in m for m in cm.output),
                        "the audit-write failure must be surfaced to the log")

    def test_emit_never_raises(self):
        # a bogus tenant path / workdir must degrade to None, never raise
        rec = self.dr.emit(_TRACE, _BRIEF, turn_id="t", tenant_id="_default",
                           workdir="/nonexistent/\0bad", session_id="s")
        self.assertTrue(rec is None or isinstance(rec, dict))

    def test_forge_stage_source_ids_are_hashed(self):
        # review R2 C1: an egress/forge stage's source ids are task-derived (an LLM
        # names a tool/skill from the task) → they must be HASHED in Layer A, never
        # stored raw. The conceptual stages keep their (system) ids.
        trace = {"task_preview": "x", "stages": [
            {"stage": "memory", "status": "ok", "sources": [{"id": "adr-0222.md", "score": 1.0}]},
            {"stage": "toolforge", "status": "ok",
             "sources": [{"id": "mcp__forge__summarize_klaus_mueller", "score": 1.0}]},
        ]}
        rec = self.dr.build_record(trace, "b", turn_id="t1")
        self.dr.assert_content_free(rec)  # must not raise
        blob = json.dumps(rec)
        self.assertNotIn("klaus_mueller", blob, "forged (task-derived) name not raw in chain")
        self.assertIn("adr-0222.md", blob, "conceptual (system) id kept for causality")

    def test_content_free_rejects_short_pii(self):
        # review R2 C2: length-independent PII shapes must fail loud even ≤80 chars.
        for bad_id in ("john.doe@example.com", "DE89370400440532013000"):
            rec = self.dr.build_record(
                {"stages": [{"stage": "memory", "status": "ok",
                             "sources": [{"id": bad_id, "score": 1.0}]}]},
                "b", turn_id="t")
            with self.assertRaises(ValueError):
                self.dr.assert_content_free(rec)

    def test_numeric_session_id_does_not_drop_record(self):
        # review R3 C1 (regression the R2 PII scan introduced): a 19-digit Discord
        # snowflake session_id matches the \d{9,} PII shape — it must NOT trip
        # assert_content_free and void the whole audit record.
        rec = self.dr.build_record(
            {"stages": [{"stage": "memory", "status": "ok", "sources": []}]},
            "brief", turn_id="turn-msn0", session_id="1501540900529246251",
            tenant_id="_default")
        self.dr.assert_content_free(rec)  # must NOT raise
        self.assertEqual(rec["session_id"], "1501540900529246251")

    def test_raw_exception_not_in_record(self):
        # review R3 C3: a stage's raw `error` string is never persisted (only the
        # slug-shaped reason), so a prompt/task fragment in an exception can't leak.
        trace = {"stages": [{"stage": "llm_synthesis", "status": "failed",
                             "reason": "parse_error",
                             "error": "boom on user secret pw=hunter2 line 4",
                             "sources": []}]}
        rec = self.dr.build_record(trace, "b", turn_id="t")
        self.dr.assert_content_free(rec)
        self.assertNotIn("hunter2", json.dumps(rec))
        self.assertIn("parse_error", json.dumps(rec))

    def test_forged_rollback_is_counts_not_names(self):
        # review R2 C1: forged_rolled_back carries COUNTS, never the names.
        trace = {"stages": [{"stage": "memory", "status": "ok", "sources": []}],
                 "gate2_denied": "[house-rules] not permitted (rule 'x')",
                 "forged_rolled_back": {"tools": ["evil_tool"], "skills": ["evil_skill"]}}
        rec = self.dr.build_record(trace, "b", turn_id="t")
        self.dr.assert_content_free(rec)
        self.assertEqual(rec["forged_rolled_back"], {"tools": 1, "skills": 1})
        self.assertNotIn("evil_tool", json.dumps(rec))


if __name__ == "__main__":
    unittest.main()
