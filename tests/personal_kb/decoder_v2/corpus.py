"""Corpora for the consistency measurement: a clean personal graph, legitimate owner actions, seeded faults.

Entities have the shape ``core/personal_kb/model.py`` gives them and the decoder writes them the way
``decoder._write_stage`` does (a shape note, a choices note, tasks; ``derived_from`` links; label ``decoder``; the idea
carries ``decoder.stage``, ``decoder.gates`` and ``decoder.children``). The clean graph already contains the states the
first red-team round showed the product really produces: a revisited idea (cursor behind the newest layer), a promoted
idea (``promoted_to`` points at a ``kb:`` reference) and notes migrated from the legacy user model.
"""
from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field
from typing import Callable

_TOPICS = ["herb garden", "spanish trip", "flat shopping list", "marathon training", "family photo book",
           "home server backup", "sourdough starter", "bike repair", "tax paperwork", "reading club",
           "piano practice", "balcony solar", "guitar pedals", "language exchange", "budget review",
           "woodworking bench", "podcast launch", "garage cleanup", "recipe archive", "trail map",
           "chess openings", "plant watering", "photo scanning", "board game night", "winter tyres",
           "gift planning", "sleep routine", "cold brew coffee", "dance class", "volunteer shifts"]
_NAMES = [a + b + c for a in "kmrsvz" for b in ("ao", "ei", "ui") for c in ("n", "l", "t", "d")][:72]   # unique per idea
NOW = 10_000.0     # the clock of the measurement; drafts created at 0 are old, drafts created at NOW-5 are in flight
_VERBS = ["Start", "Plan", "Finish", "Rethink", "Organise", "Learn", "Build", "Sort"]


def _ent(n: int, etype: str, title: str, **kw) -> dict:
    e = {"schema": 1, "uid": f"U{n:05d}", "type": etype, "title": title, "body": kw.pop("body", ""),
         "status": kw.pop("status", {"idea": "inbox", "task": "open", "note": "active"}.get(etype, "active")),
         "origin": kw.pop("origin", "user"), "review": "none", "key": None, "links": kw.pop("links", []),
         "labels": kw.pop("labels", []), "version": kw.pop("version", 1), "created": kw.pop("created", 0.0), "task": None, "decoder": None,
         "promoted_to": None}
    if etype == "task":
        e["task"] = {"dod": kw.pop("dod", "checked"), "blocked_reason": None, "cancel_reason": None}
    e.update(kw)
    return e


def _attest(upto: str) -> dict:
    order = ("shape", "choices", "plan")
    return {s: {"ok": True, "failures": []} for s in order[:order.index(upto) + 1]} if upto in order else {}


def clean_graph(n_ideas: int = 60, seed: int = 7) -> dict:
    rng = random.Random(seed)
    ents: dict[str, dict] = {}
    n = [0]

    def nid() -> int:
        n[0] += 1
        return n[0]

    topics = _TOPICS[:]
    rng.shuffle(topics)
    for i in range(n_ideas):
        topic = topics[i % len(topics)]
        title = f"{_VERBS[(i // len(topics)) % len(_VERBS)]} the {topic} project {_NAMES[i % len(_NAMES)]}"
        idea = _ent(nid(), "idea", title, status="inbox", origin="user")
        stage = rng.choice(["seed", "shape", "choices", "plan", "plan", "done"])
        newest = stage
        if stage == "plan" and rng.random() < 0.25:
            stage = "choices"            # a revisit: the cursor went back, the later layers stand
        st = {"stage": stage, "iterations": 0, "loss": 0.0, "gates": _attest(newest if newest != "done" else "plan"),
              "children": {"shape": [], "choices": [], "plan": []}}
        link = [{"rel": "derived_from", "to": idea["uid"]}]
        if newest != "seed":
            idea["status"] = "exploring"
            note = _ent(nid(), "note", f"Shape: {title}", origin="derived", links=list(link), labels=["shape", "decoder"])
            ents[note["uid"]] = note
            st["children"]["shape"] = [note["uid"]]
        if newest in ("choices", "plan", "done"):
            note = _ent(nid(), "note", f"Choices: {title}", origin="derived", links=list(link), labels=["choices", "decoder"])
            ents[note["uid"]] = note
            st["children"]["choices"] = [note["uid"]]
            choices_uid = note["uid"]
        if newest in ("plan", "done"):
            tasks = []
            for k in range(rng.randint(3, 6)):
                t = _ent(nid(), "task", f"Task {k} for {topic} {i}", origin="derived", labels=["decoder"],
                         links=list(link) + [{"rel": "related", "to": choices_uid}])
                if tasks:   # design v2 persists the plan's depends_on as blocks links
                    tasks[-1]["links"].append({"rel": "blocks", "to": t["uid"]})
                tasks.append(t)
            if newest == "done":
                for t in tasks:
                    t["status"] = rng.choice(["done", "done", "cancelled"])
                tasks[0]["status"] = "done"
                idea["status"] = "realized"
                st["stage"] = "done"
            else:
                for t in tasks:
                    t["status"] = rng.choice(["open", "in_progress", "blocked", "done"])
                tasks[-1]["status"] = rng.choice(["open", "in_progress"])
            for t in tasks:
                t["version"] = 1 if t["status"] == "open" else 2
                ents[t["uid"]] = t
            st["children"]["plan"] = [t["uid"] for t in tasks]
        idea["decoder"] = st
        ents[idea["uid"]] = idea
    for i in range(3):    # promoted ideas: the link points at a project-KB reference, not a local uid
        e = _ent(nid(), "idea", f"Promoted thought number {i} about {_NAMES[40 + i]}", status="promoted", origin="user",
                 links=[{"rel": "promoted_to", "to": f"kb:01JPROMOTED{i:012d}"}])
        ents[e["uid"]] = e
    for i in range(3):    # notes migrated from the legacy user model
        e = _ent(nid(), "note", f"Legacy fact {i} {_NAMES[50 + i]}", origin="derived",
                 links=[{"rel": "derived_from", "to": "legacy:user_model"}])
        ents[e["uid"]] = e
    for i in range(10):   # the owner's own notes, some linked to real entries
        tgt = rng.choice([u for u in ents if ents[u]["type"] in ("idea", "task", "note")])
        e = _ent(nid(), "note", f"Owner note {i}", origin="user", links=[{"rel": "related", "to": tgt}])
        ents[e["uid"]] = e
    ideas = [u for u, e in ents.items() if e["type"] == "idea"]
    return {"entities": ents, "meta": {"decoder_errors": [{"u": rng.choice(ideas), "c": "gate:shape"} for _ in range(4)]}}


@dataclass
class Case:
    name: str
    graph: dict
    expect: list[tuple[str, str]]          # (violation code, uid) that MUST be reported ("*" = any uid)
    affected: set[str] = field(default_factory=set)   # every uid the case may legitimately touch


def _pick(G: dict, rng: random.Random, pred: Callable[[dict], bool]) -> dict:
    cands = sorted((e for e in G["entities"].values() if pred(e)), key=lambda e: e["uid"])
    return rng.choice(cands)


def _pointed(G: dict, idea: dict) -> list[dict]:
    ch = (idea.get("decoder") or {}).get("children") or {}
    return [G["entities"][u] for us in ch.values() for u in us if u in G["entities"]]


def _is_cursor(idea: dict, stage: str) -> bool:
    return idea["type"] == "idea" and (idea.get("decoder") or {}).get("stage") == stage and idea["status"] == "exploring"


# ---- seeded faults ------------------------------------------------------------------------------------------------

def f_child_forgotten(G: dict, rng: random.Random) -> Case:
    idea = _pick(G, rng, lambda e: _is_cursor(e, "plan") and (e["decoder"]["children"]["plan"]))
    kids = _pointed(G, idea)
    victim = next(k for k in kids if k["type"] == "note" and "choices" in k["labels"])
    del G["entities"][victim["uid"]]
    referrers = {e["uid"] for e in G["entities"].values() if any(ln.get("to") == victim["uid"] for ln in e.get("links") or [])}
    return Case("child_forgotten", G, [("CHILD_MISSING", idea["uid"])], {idea["uid"], *referrers})


def f_cursor_ahead(G: dict, rng: random.Random) -> Case:
    idea = _pick(G, rng, lambda e: _is_cursor(e, "shape"))
    idea["decoder"]["stage"] = "plan"
    return Case("cursor_ahead", G, [("STAGE_CURSOR", idea["uid"])], {idea["uid"]})


def f_orphan_child(G: dict, rng: random.Random) -> Case:
    idea = _pick(G, rng, lambda e: e["type"] == "idea" and (e.get("decoder") or {}).get("stage") in ("shape", "choices")
                 and all(k["version"] <= 1 for k in _pointed(G, e)))
    kids = [k["uid"] for k in _pointed(G, idea)]
    del G["entities"][idea["uid"]]
    referrers = {e["uid"] for e in G["entities"].values() if any(ln.get("to") == idea["uid"] for ln in e.get("links") or [])}
    return Case("orphan_child", G, [("ORPHAN_CHILD", k) for k in kids], {*kids, *referrers})


def f_dangling_link(G: dict, rng: random.Random) -> Case:
    note = _pick(G, rng, lambda e: e["type"] == "note" and e["origin"] == "user")
    note["links"].append({"rel": "related", "to": "U99999"})
    return Case("dangling_link", G, [("DANGLING_LINK", note["uid"])], {note["uid"]})


def f_stuck_realization(G: dict, rng: random.Random) -> Case:
    idea = _pick(G, rng, lambda e: _is_cursor(e, "plan"))
    plan = [t for t in _pointed(G, idea) if t["type"] == "task"]
    for t in plan:
        t["status"] = "cancelled"
    plan[0]["status"] = "done"
    return Case("stuck_realization", G, [("STUCK_REALIZATION", idea["uid"])], {idea["uid"]})


def f_duplicate_ideas(G: dict, rng: random.Random) -> Case:
    idea = _pick(G, rng, lambda e: e["type"] == "idea" and e["status"] in ("inbox", "exploring"))
    dup = copy.deepcopy(idea)
    dup["uid"], dup["title"], dup["decoder"], dup["links"] = "U88888", idea["title"] + " again", None, []
    G["entities"][dup["uid"]] = dup
    return Case("duplicate_ideas", G, [("DUPLICATE_IDEAS", "*")], {idea["uid"], dup["uid"]})


def f_unicode_duplicate(G: dict, rng: random.Random) -> Case:
    title = rng.choice(["Пробежать марафон осенью", "学习中文语法", "Créer un café à Montréal", "เรียนภาษาอังกฤษให้เก่ง", "The garage"])
    a = _ent(88001, "idea", title)
    b = _ent(88002, "idea", title)
    G["entities"][a["uid"]], G["entities"][b["uid"]] = a, b
    return Case("unicode_duplicate", G, [("DUPLICATE_IDEAS", "*")], {a["uid"], b["uid"]})


def f_ledger_ghost(G: dict, rng: random.Random) -> Case:
    G["meta"]["decoder_errors"].append({"u": "U77777", "c": "gate:plan"})
    return Case("ledger_ghost", G, [("LEDGER_GHOST", "meta")], {"meta"})


def f_pointer_duplicates(G: dict, rng: random.Random) -> Case:
    idea = _pick(G, rng, lambda e: _is_cursor(e, "plan"))
    idea["decoder"]["children"]["plan"] = idea["decoder"]["children"]["plan"] * 2
    return Case("pointer_duplicates", G, [("CHILD_DUPLICATE", idea["uid"])], {idea["uid"]})


def f_decoder_state_lost(G: dict, rng: random.Random) -> Case:
    idea = _pick(G, rng, lambda e: _is_cursor(e, "plan"))
    idea["decoder"] = None
    return Case("decoder_state_lost", G, [("DECODER_STATE_LOST", idea["uid"])], {idea["uid"]})


def f_stray_draft(G: dict, rng: random.Random) -> Case:
    """A crashed step wrote tasks and never reached the idea: untouched derived drafts nobody points at."""
    idea = _pick(G, rng, lambda e: _is_cursor(e, "choices") and e["decoder"]["children"]["choices"])
    choices = idea["decoder"]["children"]["choices"][0]
    uids = []
    for k in range(2):
        t = _ent(87000 + k, "task", f"Crashed step task {k}", origin="derived", labels=["decoder"],
                 links=[{"rel": "derived_from", "to": idea["uid"]}, {"rel": "related", "to": choices}])
        G["entities"][t["uid"]] = t
        uids.append(t["uid"])
    return Case("stray_draft", G, [("STRAY_DRAFT", u) for u in uids], set(uids))


def f_plan_forgotten(G: dict, rng: random.Random) -> Case:
    """The owner forgets every task of the plan: the idea would be a dead end at cursor 'plan'."""
    idea = _pick(G, rng, lambda e: _is_cursor(e, "plan") and e["decoder"]["children"]["plan"])
    gone = [u for u in idea["decoder"]["children"]["plan"]]
    for u in gone:
        G["entities"].pop(u, None)
    referrers = {e["uid"] for e in G["entities"].values() if any(ln.get("to") in gone for ln in e.get("links") or [])}
    return Case("plan_forgotten", G, [("CHILD_MISSING", idea["uid"]), ("STAGE_CURSOR", idea["uid"])], {idea["uid"], *referrers})


def f_number_mismatch_duplicate(G: dict, rng: random.Random) -> Case:
    a = _ent(88101, "idea", "Learn Python 3 basics")
    b = _ent(88102, "idea", "Learn Python basics")
    G["entities"][a["uid"]], G["entities"][b["uid"]] = a, b
    return Case("number_mismatch_duplicate", G, [("DUPLICATE_IDEAS", "*")], {a["uid"], b["uid"]})


def f_hostile_identical_pair(G: dict, rng: random.Random) -> Case:
    """Words that are common everywhere hide an exact duplicate from a frequency filter (round 2, finding 4)."""
    n0 = 88200
    for i in range(7):
        e = _ent(n0 + i, "idea", "Call grandma")
        G["entities"][e["uid"]] = e
    return Case("hostile_identical_pair", G, [("DUPLICATE_IDEAS", "*")], {f"U{n0 + i:05d}" for i in range(7)})


def f_mixed_script_duplicate(G: dict, rng: random.Random) -> Case:
    a, b = _ent(88301, "idea", "学习Python"), _ent(88302, "idea", "学习 Python")
    G["entities"][a["uid"]], G["entities"][b["uid"]] = a, b
    return Case("mixed_script_duplicate", G, [("DUPLICATE_IDEAS", "*")], {a["uid"], b["uid"]})


def f_one_word_same_body(G: dict, rng: random.Random) -> Case:
    a = _ent(88401, "idea", "Garage", body="Clear the garage so the car fits")
    b = _ent(88402, "idea", "Garage", body="Clear the garage so the car fits")
    G["entities"][a["uid"]], G["entities"][b["uid"]] = a, b
    return Case("one_word_same_body", G, [("DUPLICATE_IDEAS", "*")], {a["uid"], b["uid"]})


def f_extra_number_duplicate(G: dict, rng: random.Random) -> Case:
    a, b = _ent(88501, "idea", "Read 12 books in 2026"), _ent(88502, "idea", "Read 12 books in 2026 instead of 10")
    G["entities"][a["uid"]], G["entities"][b["uid"]] = a, b
    return Case("extra_number_duplicate", G, [("DUPLICATE_IDEAS", "*")], {a["uid"], b["uid"]})


FAULTS: dict[str, Callable[[dict, random.Random], Case]] = {
    "child_forgotten": f_child_forgotten, "cursor_ahead": f_cursor_ahead, "orphan_child": f_orphan_child,
    "dangling_link": f_dangling_link, "stuck_realization": f_stuck_realization, "duplicate_ideas": f_duplicate_ideas,
    "unicode_duplicate": f_unicode_duplicate, "ledger_ghost": f_ledger_ghost, "pointer_duplicates": f_pointer_duplicates,
    "decoder_state_lost": f_decoder_state_lost, "stray_draft": f_stray_draft, "plan_forgotten": f_plan_forgotten,
    "number_mismatch_duplicate": f_number_mismatch_duplicate, "hostile_identical_pair": f_hostile_identical_pair,
    "mixed_script_duplicate": f_mixed_script_duplicate, "one_word_same_body": f_one_word_same_body,
    "extra_number_duplicate": f_extra_number_duplicate,
}


# ---- legitimate owner actions (must NOT be reported as violations) -------------------------------------------------

def legit_edits(G: dict, rng: random.Random) -> dict:
    ents = G["entities"]
    ideas = sorted((e for e in ents.values() if e["type"] == "idea" and e["status"] in ("inbox", "exploring")), key=lambda e: e["uid"])
    plan_ideas = [i for i in ideas if (i.get("decoder") or {}).get("stage") == "plan"]
    # the owner rewrites a decoder note
    for e in rng.sample([e for e in ents.values() if e["type"] == "note" and e["origin"] == "derived" and "decoder" in e["labels"]], 5):
        e["body"], e["origin"], e["version"] = "My own wording.", "confirmed", 2
    # the owner adds a manual task to a plan (related link, and a derived_from link without the decoder label)
    for i in rng.sample(plan_ideas, min(3, len(plan_ideas))):
        t = _ent(90000 + len(ents), "task", "My own extra step", origin="user", links=[{"rel": "related", "to": i["uid"]}])
        ents[t["uid"]] = t
        t2 = _ent(93000 + len(ents), "task", "Another own step", origin="user", links=[{"rel": "derived_from", "to": i["uid"]}])
        ents[t2["uid"]] = t2
    # the owner cancels one task, with a reason, while others stay open
    for i in rng.sample(plan_ideas, min(3, len(plan_ideas))):
        t = next(k for k in _pointed(G, i) if k["type"] == "task" and k["status"] in ("open", "in_progress"))
        t["status"], t["task"]["cancel_reason"], t["version"] = "cancelled", "not needed", 3
    # the owner renames an idea, drops another one (its children stay)
    for i in rng.sample(ideas, 3):
        i["title"] = i["title"] + " (renamed)"
    ideas[-1]["status"] = "dropped"
    # titles that must not look alike
    for n, title in enumerate(["Reading list for the winter evenings", "Reading club evenings with friends",
                               "Write tax return 2025", "Write tax return 2026", "Купить велосипед", "Выучить гитару",
                               "学习中文语法", "学习日本料理", "Créer un café à Montréal", "Créer un jardin à Québec"]):
        ents[f"U{91000 + n}"] = _ent(91000 + n, "idea", title, origin="user")
    # a failed re-run of a stage: the last gate result is False, the layer was attested before (ever_ok)
    for i in rng.sample(plan_ideas, min(2, len(plan_ideas))):
        i["decoder"]["gates"]["plan"] = {"ok": False, "ever_ok": True, "failures": ["no task"]}
    # a decoder step that is still running: its drafts are fresh and not yet pointed at (swept with now=NOW)
    live = [i for i in ideas if (i.get("decoder") or {}).get("stage") == "choices" and i["decoder"]["children"]["choices"]]
    if live:
        for k in range(2):
            t = _ent(86000 + k, "task", f"Task being written {k}", origin="derived", labels=["decoder"], created=NOW - 5.0,
                     links=[{"rel": "derived_from", "to": live[0]["uid"]}])
            ents[t["uid"]] = t
    # one-word titles that are common and mean different things
    for n, body in enumerate(["Clear out the shed", "Collect the old paint"]):
        ents[f"U{91300 + n}"] = _ent(91300 + n, "idea", "Garage", body=body, origin="user")
    for n, body in enumerate(["party for Anna", "gift for dad"]):
        ents[f"U{91200 + n}"] = _ent(91200 + n, "idea", "Birthday", body=body, origin="user")
    # two near-identical ideas the owner related on purpose, and one marked distinct
    twin = ideas[0]
    t2 = _ent(91100, "idea", twin["title"] + " too", origin="user", links=[{"rel": "related", "to": twin["uid"]}])
    ents[t2["uid"]] = t2
    t3 = _ent(91101, "idea", twin["title"] + " also", origin="user", labels=["distinct"])
    ents[t3["uid"]] = t3
    # the owner adds a task to a realized idea, and reopens a realized idea and dismisses the nudge to close it again
    done = [e for e in ents.values() if e["type"] == "idea" and e["status"] == "realized"]
    if done:
        t = _ent(92000, "task", "Follow-up after the project", origin="user", links=[{"rel": "derived_from", "to": done[0]["uid"]}])
        ents[t["uid"]] = t
    if len(done) > 1:
        done[1]["status"] = "exploring"
        done[1]["decoder"]["dismissed"] = ["STUCK_REALIZATION"]        # the owner dismissed the nudge, like an objection
    # the owner patches an idea to realized while its tasks are open
    spare = [i for i in plan_ideas if i is not ideas[-1]]
    if spare:
        spare[0]["status"] = "realized"
    # the owner forgets an idea whose children they have all worked on: standalone work, not an orphan
    cand = [i for i in spare[1:] if i["status"] == "exploring"]
    if cand:
        victim = cand[-1]
        for k in _pointed(G, victim):
            k["version"] = max(2, k["version"])
            if k["type"] == "note":
                k["origin"] = "confirmed"
            elif k["status"] == "open":
                k["status"] = "in_progress"
        del ents[victim["uid"]]
    return G
