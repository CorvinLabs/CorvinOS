"""LDD measurement for the personal decoder v2. Run:  python -m tests.personal_kb.decoder_v2.run_eval

Each mechanism is scored by a loss in [0, 1] that comes from outside the mechanism (ground truth of seeded
faults, replayed outcome streams). ``results.json`` holds the numbers of the current design; the iteration
protocol with every earlier number is kept in the knowledge base (see the design note it cites).
"""
from __future__ import annotations

import copy
import json
import random
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tests.personal_kb.decoder_v2 import consistency as C  # noqa: E402
from tests.personal_kb.decoder_v2 import estimators, learning  # noqa: E402
from tests.personal_kb.decoder_v2.baseline_v1 import measure_v1_baseline  # noqa: E402
from tests.personal_kb.decoder_v2.corpus import FAULTS, NOW, clean_graph, legit_edits  # noqa: E402

INSTANCES = 25
LATENCY_BUDGET_MS = 200.0
PERF_IDEAS = 500
OWNER_FIELDS = ("title", "body", "labels")


def _owner_content(e: dict):
    return (tuple(e.get(f) if not isinstance(e.get(f), list) else tuple(e[f]) for f in OWNER_FIELDS),
            (e.get("task") or {}).get("dod"))


def _heal_problems(G: dict) -> list[str]:
    """Why a heal of G would be unsafe. Empty = safe."""
    problems: list[str] = []
    H, applied = C.heal(G, NOW)
    for uid, e in G["entities"].items():
        if e["origin"] in ("user", "confirmed"):
            if uid not in H["entities"]:
                problems.append("owner entity deleted")
                continue
        elif uid not in H["entities"]:
            if not C.untouched(e):
                problems.append("touched draft deleted")
            continue
        h = H["entities"][uid]
        if e["origin"] in ("user", "confirmed") and _owner_content(e) != _owner_content(h):
            problems.append("owner content changed")
        if e["type"] == "idea" and e["status"] != h["status"]:
            problems.append("idea status written")
        keep = [ln for ln in e.get("links") or [] if ln.get("rel") in C.KEEP_LINKS or not C.is_local(ln.get("to"))]
        if any(ln not in (h.get("links") or []) for ln in keep):
            problems.append("a kept or external link was removed")
        if e.get("origin") == "derived" and any(ln.get("rel") == "derived_from" for ln in e.get("links") or []) \
                and not any(ln.get("rel") == "derived_from" for ln in h.get("links") or []):
            problems.append("derived entry lost its derived_from link")
    if [v for v in C.sweep(H, NOW) if v.fix == "auto"]:
        problems.append("auto violation left after heal")
    H2, _ = C.heal(H, NOW)
    if H2 != H:
        problems.append("heal not idempotent")
    return problems


def measure_consistency() -> dict:
    rng = random.Random(11)
    base = clean_graph()
    control = C.sweep(base, NOW)
    legit = legit_edits(copy.deepcopy(base), random.Random(3))
    legit_v = C.sweep(legit, NOW)
    per_class: dict[str, dict] = {}
    unexpected_total = unsafe = instances = 0
    for name, fn in FAULTS.items():
        hit = fp = 0
        for k in range(INSTANCES):
            G = clean_graph(seed=100 + k)
            case = fn(G, rng)
            vs = C.sweep(case.graph, NOW)
            got = {(v.code, v.uid) for v in vs}
            ok = all(any(c == code and (u == "*" or u == uid) for code, uid in got) for c, u in case.expect)
            hit += int(ok)
            fp += sum(1 for v in vs if v.uid not in case.affected and v.uid != "meta")
            unsafe += int(bool(_heal_problems(case.graph)))
            instances += 1
        unexpected_total += fp
        per_class[name] = {"recall": round(hit / INSTANCES, 3), "unexpected": fp}
    big = clean_graph(n_ideas=PERF_IDEAS, seed=5)
    hostile = {"entities": {f"H{i}": {"uid": f"H{i}", "type": "idea", "title": f"u{i}a u{i}b garden balcony plants", "body": "",
                                      "status": "inbox", "origin": "user", "links": [], "labels": [], "version": 1, "decoder": None}
                            for i in range(2300)}, "meta": {}}
    chain = {"entities": {f"T{i}": {"uid": f"T{i}", "type": "task", "title": "t", "body": "", "status": "open", "origin": "user",
                                    "links": [{"rel": "blocks", "to": f"T{i + 1}"}] if i < 1199 else [], "labels": [], "version": 1,
                                    "decoder": None} for i in range(1200)}, "meta": {}}
    # round 2, finding 3: 2300 ideas of five words, each word in about 115 of them, plus one identical pair at the end
    import itertools as _it
    words = [f"w{k}" for k in range(100)]
    rr = random.Random(2)
    near_cut = {"entities": {}, "meta": {}}
    for i in range(2298):
        near_cut["entities"][f"N{i:05d}"] = {"uid": f"N{i:05d}", "type": "idea", "title": " ".join(rr.sample(words, 5)), "body": "",
                                             "status": "inbox", "origin": "user", "links": [], "labels": [], "version": 1, "decoder": None}
    for k in (1, 2):
        near_cut["entities"][f"Z{k}"] = {**near_cut["entities"]["N00000"], "uid": f"Z{k}", "title": "Restore grandfather vintage motorcycle engine"}
    pair_found = any(v.code == "DUPLICATE_IDEAS" for v in C.sweep(near_cut, NOW))
    flood = {"entities": {f"F{i:05d}": {"uid": f"F{i:05d}", "type": "idea", "title": "Learn to bake bread", "body": "", "status": "inbox",
                                      "origin": "user", "links": [], "labels": [], "version": 1, "decoder": None} for i in range(2300)}, "meta": {}}
    pool = [f"p{k}q" for k in range(1500)]
    rl = random.Random(4)
    longt = {"entities": {}, "meta": {}}
    for i in range(2296):
        longt["entities"][f"L{i:05d}"] = {"uid": f"L{i:05d}", "type": "idea", "title": " ".join(rl.sample(pool, 28)), "body": "", "status": "inbox",
                                          "origin": "user", "links": [], "labels": [], "version": 1, "decoder": None}
    base_title = longt["entities"]["L00007"]["title"].split()
    base_title[3] = "swapped"
    for k, title in (("Z1", longt["entities"]["L00007"]["title"]), ("Z2", " ".join(base_title))):   # the newest ideas
        longt["entities"][f"{k}"] = {**longt["entities"]["L00007"], "uid": f"{k}", "title": title}
    newest_found = any(v.code == "DUPLICATE_IDEAS" and {v.uid, v.detail} <= {"L00007", "Z1", "Z2"} for v in C.sweep(longt, NOW))
    # what the filtered path (more than EXACT_PAIRS_UP_TO ideas) loses on natural-looking titles: 1500 ideas of 4 to 7 words
    # from a 600-word vocabulary, 60 of them copies of another title with one word replaced
    vocab = [f"v{k}x" for k in range(600)]
    rn = random.Random(9)
    nat = {"entities": {}, "meta": {}}
    titles = []
    for i in range(1500):
        title = " ".join(rn.sample(vocab, rn.randint(4, 7)))
        titles.append(title)
        nat["entities"][f"A{i:05d}"] = {"uid": f"A{i:05d}", "type": "idea", "title": title, "body": "", "status": "inbox",
                                        "origin": "user", "links": [], "labels": [], "version": 1, "decoder": None}
    injected = set()
    for i in range(60):
        src = titles[i * 20].split()
        src[rn.randrange(len(src))] = "swapped"
        uid = f"B{i:05d}"
        nat["entities"][uid] = {**nat["entities"][f"A{i * 20:05d}"], "uid": uid, "title": " ".join(src)}
        injected.add(frozenset((f"A{i * 20:05d}", uid)))
    got = {frozenset((v.uid, v.detail)) for v in C.sweep(nat) if v.code == "DUPLICATE_IDEAS"}
    filtered_recall = len(injected & got) / len(injected)
    ms = 0.0
    for g in (big, hostile, chain, near_cut, flood, longt):
        t0 = time.process_time()      # CPU time of this process: a busy host (load 40 on this machine) must not move the metric
        for _ in range(3):
            C.sweep(g, NOW)
        ms = max(ms, (time.process_time() - t0) / 3 * 1000)
    recall = sum(v["recall"] for v in per_class.values()) / len(per_class)
    fp_rate = min(1.0, (unexpected_total + len(control) + len(legit_v)) / instances)
    unsafe_rate = unsafe / instances
    hostile_miss = 0.0 if pair_found and newest_found else 1.0
    # Wall or CPU time is NOT part of the loss: on a loaded host (load 47 here) the same sweep measured 200 ms idle and
    # 330 to 590 ms busy. It is reported, and the test bound is generous; the design's cost bound is the work budget
    # MAX_DUPLICATE_WORK (steps, not seconds).
    loss = round((1 - recall + fp_rate + unsafe_rate + hostile_miss) / 4, 4)
    return {"recall_mean": round(recall, 4), "per_class": per_class, "clean_violations": len(control),
            "legit_violations": {c: sum(1 for v in legit_v if v.code == c) for c in sorted({v.code for v in legit_v})}, "unexpected_on_faults": unexpected_total,
            "unsafe_heal_share": round(unsafe_rate, 4), "sweep_cpu_ms_worst_of_6_corpora_informational": round(ms, 1),
            "entities_at_perf": [len(big["entities"]), len(hostile["entities"]), len(chain["entities"]), len(near_cut["entities"]), len(flood["entities"]), len(longt["entities"])],
            "hostile_identical_pair_found_at_2300_ideas": pair_found,
            "newest_pair_found_among_2298_long_titles": newest_found,
            "filtered_path_recall_on_1560_natural_titles": round(filtered_recall, 3), "loss": loss}


def critic_probe_summary() -> dict:
    """Summary of the stored twin probe (``critic_probe_twins.py``): 8 synthetic twin pairs, real critic, blind judge."""
    p = Path(__file__).with_name("critic_probe_twins_results.json")
    if not p.exists():
        return {"available": False}
    d = json.loads(p.read_text())
    flawed = [r for r in d["runs"] if r["kind"] == "flawed"]
    sound = [r for r in d["runs"] if r["kind"] == "sound"]
    by_rank = {k: {"real_defect": 0, "nitpick": 0, "wrong": 0} for k in (1, 2, 3)}
    for r in d["runs"]:
        for i, rating in enumerate(r["judge"]["ratings"][:3]):
            by_rank[i + 1][rating] += 1
    return {"available": True, "model": d["model"], "flawed_runs": len(flawed), "sound_runs": len(sound),
            "objections_per_run": sorted({len(r["objections"]) for r in d["runs"]}),
            "defect_present_in_flawed_by_judge": sum(r["judge"]["defect_present"] for r in flawed),
            "defect_present_in_sound_by_judge": sum(r["judge"]["defect_present"] for r in sound),
            "seeded_defect_addressed": sum(1 for r in flawed if r["judge"]["addresses_defect"]),
            "seeded_defect_ranked_first": sum(1 for r in flawed if r["judge"]["addresses_defect"] == 1),
            "absent_defect_claimed_on_sound": sum(1 for r in sound if r["judge"]["addresses_defect"]),
            "ratings_by_rank": by_rank}


def main(label: str = "current") -> dict:
    out = {"label": label, "consistency": measure_consistency(), "learning_measured_controller": learning.measure(),
           "learning_dense_signals": estimators.measure(), "critic_probe": critic_probe_summary(),
           "shipped_decoder_v1_observed": measure_v1_baseline()}
    path = Path(__file__).with_name("results.json")
    path.write_text(json.dumps(out, indent=1, sort_keys=True))
    return out


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1] if len(sys.argv) > 1 else "current"), indent=1, sort_keys=True))
