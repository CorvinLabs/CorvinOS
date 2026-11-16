"""LDD measurement for the personal decoder: mean loss and gate pass rate over the replay corpus,
for the baseline (no repair round) and the current decoder (one repair round).

Run:  python -m tests.personal_kb.decoder_eval.run_eval   (writes results.json next to this file)
The test tests/personal_kb/test_decoder_ldd.py runs the same measurement and pins the result.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
for p in (str(REPO), str(REPO / "corvin_operator" / "forge"), str(REPO / "corvin_operator" / "bridges" / "shared")):
    if p not in sys.path:
        sys.path.insert(0, p)


def _gen_for(item):
    def g(stage, prompt):
        if stage == "adversarial":
            return {"objections": item["objections"] if "STAGE RESULT" in prompt and '"goal"' in prompt else []}
        key = "fixed" if "FIX THESE PROBLEMS" in prompt else "first"
        return json.loads(json.dumps(item[stage][key]))
    return g


def run(repair_rounds: int) -> dict:
    """Fresh sandbox, fresh owner, the whole corpus through shape -> choices -> plan."""
    from core.personal_kb import capabilities, crypto, decoder, limits, model, owner, session
    from core.personal_kb.store import PersonalStore
    from tests.personal_kb.decoder_eval.corpus import CORPUS

    with tempfile.TemporaryDirectory() as d:
        saved = {k: os.environ.get(k) for k in ("CORVIN_HOME", "CORVIN_AUDIT_ANCHOR_KEY", "FORGE_ROOT")}
        os.environ["CORVIN_HOME"] = str(Path(d) / "corvin")
        os.environ["CORVIN_AUDIT_ANCHOR_KEY"] = str(Path(d) / "anchor.key")
        os.environ.pop("FORGE_ROOT", None)
        kdf, rr = dict(crypto.KDF_PARAMS), decoder.REPAIR_ROUNDS
        crypto.KDF_PARAMS.update({"n": 2 ** 10})
        decoder.REPAIR_ROUNDS = repair_rounds
        limits._reset_for_tests(); session._reset_for_tests(); capabilities._set_for_tests(None)
        try:
            c = owner.create_owner("_default", "eval", "Lantern-Orchard-77-blue", consent=True, unlock_mode="strict")
            s = PersonalStore.open("_default", c.owner_id, c.keys)
            losses, passed, total, reached_plan = [], 0, 0, 0
            clock = 10_000.0
            for item in CORPUS:
                idea = s.put(model.new_entity("idea", item["title"], origin="user"))
                g = _gen_for(item)
                for stage in ("shape", "choices", "plan"):
                    clock += 86_401
                    r = decoder.unfold(s, idea["uid"], stage=stage, generate=g, now=clock)
                    total += 1
                    passed += int(r.gate_ok)
                    losses.append(r.loss)
                    if not r.gate_ok:
                        break
                    if stage == "plan":
                        reached_plan += 1
            return {"repair_rounds": repair_rounds, "stage_runs": total, "gate_pass_rate": round(passed / total, 4),
                    "mean_loss": round(sum(losses) / len(losses), 4), "ideas_with_plan": reached_plan,
                    "ideas": len(CORPUS)}
        finally:
            crypto.KDF_PARAMS.clear(); crypto.KDF_PARAMS.update(kdf)
            decoder.REPAIR_ROUNDS = rr
            for k, v in saved.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def main() -> None:
    before, after = run(0), run(1)
    out = {"before": before, "after": after}
    (Path(__file__).parent / "results.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
