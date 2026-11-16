"""LLD measurement: the decoder's "find first" with and without the (uid, version) signature cache.

Before: every call recomputes the word signature of every entry. After: signatures are cached per
(uid, version), so a changed entry is recomputed and an unchanged one is not. The outputs must be
bit-identical; only the time differs.  Run:  python -m tests.personal_kb.decoder_eval.bench_find_first
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
for p in (str(REPO), str(REPO / "corvin_operator" / "forge"), str(REPO / "corvin_operator" / "bridges" / "shared")):
    if p not in sys.path:
        sys.path.insert(0, p)

WORDS = ("garden balcony herbs basil running plan spanish trip photo archive budget spreadsheet club newsletter "
         "bike repair cooking garage sale reading book detox sunday office shelf cable family weekly course").split()


def build_index(n: int) -> dict:
    import random
    from core.personal_kb import model
    rnd = random.Random(42)
    idx = {}
    for i in range(n):
        title = " ".join(rnd.choice(WORDS) for _ in range(6)) + f" {i}"
        e = model.new_entity(rnd.choice(["idea", "note"]), title, body=" ".join(rnd.choice(WORDS) for _ in range(20)))
        e["version"] = 1
        idx[e["uid"]] = e
    return idx


class _FakeStore:
    def __init__(self, index):
        self.index = index
        self._sig_cache = None

    def entries(self):
        return list(self.index.values())


def uncached_find_first(store, idea, limit=8):
    """The pre-optimisation algorithm, kept verbatim for the comparison."""
    from core.personal_kb import decoder as d
    me = d._sig(idea["title"] + " " + (idea.get("body") or ""))
    scored = []
    for e in store.index.values():
        if e["uid"] == idea["uid"] or e["type"] not in ("idea", "note") or "objection" in e.get("labels", []):
            continue
        if any(ln.get("to") == idea["uid"] for ln in e.get("links", [])):
            continue
        s = d._jaccard(me, d._sig(e["title"] + " " + (e.get("body") or "")))
        if s > 0:
            scored.append((s, e["uid"], e))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [e for _, _, e in scored[:limit]], [uid for s, uid, _ in scored if s >= d.DUPLICATE_THRESHOLD]


def run(n: int = 3000, calls: int = 20) -> dict:
    from core.personal_kb import decoder
    idx = build_index(n)
    store = _FakeStore(idx)
    probes = list(idx.values())[:calls]
    store._sig_cache = None  # cold cache
    t0 = time.perf_counter()
    before = [uncached_find_first(store, p) for p in probes]
    t_before = time.perf_counter() - t0
    t0 = time.perf_counter()
    after = [decoder.find_first(store, p) for p in probes]
    t_after = time.perf_counter() - t0
    same = all([e["uid"] for e in a[0]] == [e["uid"] for e in b[0]] and a[1] == b[1] for a, b in zip(after, before))
    return {"entries": n, "calls": calls, "seconds_before": round(t_before, 4), "seconds_after": round(t_after, 4),
            "speedup": round(t_before / t_after, 2) if t_after else None, "identical": same}


def main() -> None:
    with tempfile.TemporaryDirectory() as d:
        os.environ.setdefault("CORVIN_HOME", str(Path(d) / "corvin"))
        out = run()
    (Path(__file__).parent / "bench_results.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
