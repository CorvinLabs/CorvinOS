"""What the shipped decoder (``core/personal_kb/decoder.py``) does under the faults ADR-2329 targets.

Runs the real code against a real encrypted ``PersonalStore`` in a sandbox (same setup as ``decoder_eval``), with the
replay corpus of ``decoder_eval`` as generator. Every number is an observation of shipped behaviour, not a model.
Before ADR-2329 was implemented these numbers showed the gaps (no stored dependencies, an idea that never closed, stale
child lists, orphans, no sweep); a forget here is followed by ``consistency.run_sweep`` exactly as the console's and
the bridge's forget paths do.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
for p in (str(REPO), str(REPO / "corvin_operator" / "forge"), str(REPO / "corvin_operator" / "bridges" / "shared")):
    if p not in sys.path:
        sys.path.insert(0, p)


def measure_v1_baseline() -> dict:
    from core.personal_kb import capabilities, consistency, crypto, decoder, limits, model, owner, session
    from core.personal_kb.store import PersonalStore
    from tests.personal_kb.decoder_eval.corpus import CORPUS
    from tests.personal_kb.decoder_eval.run_eval import _gen_for

    with tempfile.TemporaryDirectory() as d:
        saved = {k: os.environ.get(k) for k in ("CORVIN_HOME", "CORVIN_AUDIT_ANCHOR_KEY", "FORGE_ROOT")}
        os.environ["CORVIN_HOME"] = str(Path(d) / "corvin")
        os.environ["CORVIN_AUDIT_ANCHOR_KEY"] = str(Path(d) / "anchor.key")
        os.environ.pop("FORGE_ROOT", None)
        kdf = dict(crypto.KDF_PARAMS)
        crypto.KDF_PARAMS.update({"n": 2 ** 10})
        limits._reset_for_tests(); session._reset_for_tests(); capabilities._set_for_tests(None)
        try:
            c = owner.create_owner("_default", "base", "Lantern-Orchard-77-blue", consent=True, unlock_mode="strict")
            s = PersonalStore.open("_default", c.owner_id, c.keys)
            clock = [10_000.0]

            def plan_idea(item) -> dict:
                idea = s.put(model.new_entity("idea", item["title"], origin="user"))
                for stage in ("shape", "choices", "plan"):
                    clock[0] += 86_401
                    decoder.unfold(s, idea["uid"], stage=stage, generate=_gen_for(item), now=clock[0])
                return s.get(idea["uid"])

            def kids(idea: dict) -> list[dict]:
                return [e for e in s.entries() if any(ln.get("rel") == "derived_from" and ln.get("to") == idea["uid"]
                                                      for ln in e.get("links") or [])]

            out: dict = {}
            # 1. the dependencies the plan names are not stored on the tasks
            idea = plan_idea(CORPUS[0])
            tasks = [k for k in kids(idea) if k["type"] == "task"]
            out["plan_tasks"] = len(tasks)
            out["tasks_with_a_blocks_link"] = sum(1 for t in tasks if any(ln["rel"] == "blocks" for ln in t["links"]))
            out["context_copy_of_stage_data_kept_on_the_idea"] = sorted((idea.get("decoder") or {}).get("context", {}))

            # 2. the owner cancels one task and finishes the others: the idea is never closed
            idea2 = plan_idea(CORPUS[1])
            ts = [e for e in kids(idea2) if e["type"] == "task"]
            first, rest = s.get(ts[0]["uid"]), [s.get(t["uid"]) for t in ts[1:]]
            first["status"] = "cancelled"
            first["task"] = dict(first["task"], cancel_reason="not needed")
            s.put(first, expected_version=first["version"])
            for t in rest:
                for st in ("in_progress", "done"):
                    t = s.get(t["uid"])
                    t["status"] = st
                    if st == "done":
                        t["task"] = dict(t["task"], dod=t["task"].get("dod") or "checked")
                    s.put(t, expected_version=t["version"])
            out["closed_after_one_cancelled_rest_done"] = bool(decoder.refresh_done(s, idea2["uid"]))
            out["idea_status_after_that"] = s.get(idea2["uid"])["status"]

            # 3. the owner forgets (hard delete) a plan task: the stored list keeps its uid, nothing notices
            idea3 = plan_idea(CORPUS[2])
            ts3 = [e for e in kids(idea3) if e["type"] == "task"]
            s.forget(ts3[0]["uid"])
            consistency.run_sweep(s)
            st3 = s.get(idea3["uid"])["decoder"]
            out["forgotten_task_still_listed_as_child"] = ts3[0]["uid"] in (st3["children"].get("plan") or [])
            out["stage_after_forgetting_a_task"] = st3["stage"]

            # 4. the owner forgets the idea: its derived notes and tasks stay behind
            idea4 = plan_idea(CORPUS[3])
            n_kids = len(kids(idea4))
            s.forget(idea4["uid"])
            consistency.run_sweep(s, now=clock[0] + 10_000)
            left = [e for e in s.entries() if any(ln.get("rel") == "derived_from" and ln.get("to") == idea4["uid"]
                                                  for ln in e.get("links") or [])]
            out["children_before_forgetting_the_idea"] = n_kids
            out["orphans_after_forgetting_the_idea"] = len(left)

            # 5. does any public decoder function report an inconsistency? (names that would)
            out["public_consistency_functions"] = sorted(n for n in ("sweep", "heal", "run_sweep") if callable(getattr(consistency, n, None)))
            return out
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
            crypto.KDF_PARAMS.update(kdf)
            limits._reset_for_tests(); session._reset_for_tests(); capabilities._set_for_tests(None)


if __name__ == "__main__":
    import json

    print(json.dumps(measure_v1_baseline(), indent=1, sort_keys=True))
