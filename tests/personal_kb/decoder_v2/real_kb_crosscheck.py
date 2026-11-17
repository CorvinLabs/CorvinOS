"""Manual cross-check (not part of the test suite): the generic detectors of ``consistency.py`` against ``kb check``, an
independent implementation, on the REAL Corvin-Knowledge graph. Run from the repo root:

    python3 /home/shumway/projects/Corvin-Knowledge/scripts/kb.py check --json /tmp/kbcheck.json
    python -m tests.personal_kb.decoder_v2.real_kb_crosscheck [/tmp/kbcheck.json]

It reads the graph files ``kb/graph/*.jsonl`` and the findings file, writes ``real_kb_crosscheck_results.json``.
"""
from __future__ import annotations

import collections
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from tests.personal_kb.decoder_v2 import consistency as C  # noqa: E402

KB = Path("/home/shumway/projects/Corvin-Knowledge")


def main(check_json: str = "/tmp/kbcheck.json") -> dict:
    ents = [json.loads(line) for line in (KB / "kb/graph/entities.jsonl").read_text().splitlines()]
    rels = [json.loads(line) for line in (KB / "kb/graph/relations.jsonl").read_text().splitlines()]
    issues = json.loads(Path(check_json).read_text())["issues"]
    known = {x for e in ents for x in (e.get("uid"), e.get("id")) if x}
    src_id = {e["uid"]: e["id"] for e in ents if e.get("uid")}
    mine = {(src_id.get(r["src"], r["src"]), r["rel"], r["dst"]) for r in rels if not r.get("external") and r["dst"] not in known}
    theirs = set()
    for x in issues["E14_link_dangling"]:
        rel, _, dst = x["detail"].partition(" -> ")
        theirs.add((x["id"], rel, dst))
    cnt = collections.Counter(e["id"] for e in ents if e.get("id"))
    dup_mine = {i for i, c in cnt.items() if c > 1}
    dup_theirs = {x["id"] for x in issues["E06_id_duplicate"]}
    cands = [e for e in ents if e["type"] in ("idea", "concept") and e.get("uid")]
    G = {"entities": {e["uid"]: {"uid": e["uid"], "type": "idea", "status": "inbox", "title": e["title"], "body": "", "links": [],
                                 "labels": [], "id": e["id"]} for e in cands}, "meta": {}}
    for r in rels:
        if r["rel"] in C.ACKNOWLEDGING_RELS or r["rel"] == "search":
            if r["src"] in G["entities"]:
                G["entities"][r["src"]]["links"].append({"rel": "related", "to": r["dst"]})
    ack = C._acknowledged(G)
    pairs, capped = C._duplicates(list(G["entities"].values()))
    near_mine = {frozenset((G["entities"][a]["id"], G["entities"][b]["id"])) for a, b in pairs if frozenset((a, b)) not in ack}
    near_theirs = set()
    for x in issues["G0_near_duplicate"]:
        m = re.search(r"near ([A-Z]+-\d+)", x["detail"])
        if m:
            near_theirs.add(frozenset((x["id"], m.group(1))))
    out = {"entities": len(ents), "relations": len(rels),
           "dangling_links": {"mine": len(mine), "kb_check": len(theirs), "both": len(mine & theirs), "only_mine": sorted(mine - theirs)},
           "duplicate_ids": {"mine": len(dup_mine), "kb_check": len(dup_theirs), "both": len(dup_mine & dup_theirs)},
           "near_duplicates_ideas_and_concepts": {"mine": len(near_mine), "kb_check": len(near_theirs), "both": len(near_mine & near_theirs),
                                                  "only_kb_check": [sorted(p) for p in near_theirs - near_mine], "capped": capped}}
    Path(__file__).with_name("real_kb_crosscheck_results.json").write_text(json.dumps(out, indent=1, sort_keys=True))
    return out


if __name__ == "__main__":
    print(json.dumps(main(*sys.argv[1:2]), indent=1, sort_keys=True))
