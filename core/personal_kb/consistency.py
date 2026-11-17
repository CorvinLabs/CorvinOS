"""Consistency of the personal decoder: a model-free sweep and a derivable-only heal (ADR-2329).

The pure part (``sweep``, ``heal`` on a graph ``{"entities": {uid: entity}, "meta": {...}}``) is the executable
specification that ``tests/personal_kb/decoder_v2`` measured and three adversarial rounds shaped; its rules:

* The stage of an idea is a CURSOR. A revisit moves it back on purpose and leaves later layers standing; an owner may
  forget a layer note and keep the rest. The only violation is a cursor standing on a layer that has no live entity (an
  entity is written only after its gate passed). A repair only ever LOWERS the cursor, to the highest live layer below.
* ``decoder.children`` are the POINTERS of the current layer set, found through them and never through labels.
* A heal never writes an idea's ``status``, never removes ``derived_from``/``promoted_to`` or an external reference
  (``kb:``, ``legacy:``), never deletes a draft the owner touched, never changes a word of the owner.
* A stray untouched draft is removed only after a grace period; a step writes its drafts first and the idea last.
* Judgement calls are reported (``fix == "review"``), never applied; ``STUCK_REALIZATION`` is dismissible.

``run_sweep`` applies the same rules to a live :class:`PersonalStore`: it skips an idea whose decoder step is running in
this process, writes every change against the version it read (a concurrent writer wins, the next sweep retries), and
records counts only in the audit chain.
"""
from __future__ import annotations

import copy
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

ACTIVE_IDEA = ("inbox", "exploring")
TERMINAL_TASK = ("done", "cancelled")
STAGES = ("seed", "shape", "choices", "plan", "done")
DUPLICATE_THRESHOLD = 0.6
MAX_HEAL_PASSES = 8
MAX_DUPLICATE_WORK = 150_000       # posting-list steps for the filtered path; hitting it is reported, never silent
COMMON_TOKEN_SHARE = 0.02          # beyond EXACT_PAIRS_UP_TO ideas, a word in more than 2 percent of them (and in more than 40) is not used to find candidates
COMMON_TOKEN_FLOOR = 40
KEEP_LINKS = ("derived_from", "promoted_to")          # a heal never removes these
ACKNOWLEDGING_RELS = ("related", "derived_from", "supersedes", "promoted_to")

_UNSEGMENTED = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af\u0e00-\u0eff\u1000-\u109f\u1780-\u17ff]")
_STOP = frozenset("the and for with that this from into über eine einer einen und für mit dass oder auch "
                  "nicht sein sind wird werden have has will would should could le la les de des du et un une "
                  "el los las y en in of to a an".split())


_ASCII_WORD = re.compile(r"[a-z0-9]+")


def _runs(text: str):
    """Words as runs of letters, digits and combining marks (Thai vowels are marks), split where the script class changes
    between an unsegmented script (CJK, Thai, Myanmar, Khmer) and the rest."""
    cur, cur_u = [], None
    for ch in unicodedata.normalize("NFC", text or "").lower():
        word = unicodedata.category(ch)[0] in "LNM"
        u = bool(_UNSEGMENTED.match(ch)) if word else None
        if word and (cur_u is None or u == cur_u):
            cur.append(ch)
            cur_u = u
            continue
        if cur:
            yield "".join(cur), bool(cur_u)
        cur, cur_u = ([ch], u) if word else ([], None)
    if cur:
        yield "".join(cur), bool(cur_u)


def sig(text: str) -> frozenset[str]:
    """Word set: accents kept, digits kept, unsegmented scripts as character pairs (a lone character stays a word)."""
    out: set[str] = set()
    if (text or "").isascii():            # the common case, 20 times cheaper than the character walk
        return frozenset(w for w in _ASCII_WORD.findall(text.lower()) if w not in _STOP and (len(w) >= 2 or w.isdigit()))
    for w, unseg in _runs(text):
        if unseg:
            out.update(w[i:i + 2] for i in range(max(1, len(w) - 1)))
        elif w not in _STOP and (len(w) >= 2 or w.isdigit()):
            out.add(w)
    return frozenset(out)


def title_sig(idea: dict) -> frozenset[str]:
    """A title of fewer than two words says too little ("Garage", "Birthday"): then the body decides."""
    s = sig(idea["title"])
    return s if len(s) >= 2 else sig(idea["title"] + " " + (idea.get("body") or ""))


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


@dataclass(frozen=True)
class Violation:
    code: str
    uid: str          # the entity the violation is about
    fix: str          # "auto" (derivable) | "review" (judgement)
    detail: str = ""


def is_local(ref: object) -> bool:
    """A link target inside this store is a bare uid; ``kb:...``, ``legacy:...`` and URLs are external references."""
    return isinstance(ref, str) and ":" not in ref and bool(ref)


def untouched(e: dict) -> bool:
    """The rule decoder._drop_replaced_children uses: a draft nobody has changed since the decoder wrote it."""
    default = {"task": "open", "note": "active", "idea": "inbox"}.get(e["type"], "")
    return int(e.get("version", 0)) <= 1 and e.get("origin") == "derived" and e.get("status") == default


def _pointers(idea: dict) -> dict[str, list[str]]:
    ch = (idea.get("decoder") or {}).get("children") or {}
    return {k: [u for u in (v if isinstance(v, list) else []) if isinstance(u, str)] for k, v in ch.items()}


def _alive(G: dict, idea: dict, stage: str) -> bool:
    return any(u in G["entities"] for u in _pointers(idea).get(stage, []))


def lowered_cursor(G: dict, idea: dict) -> str:
    """The highest layer BELOW the current cursor that still has a live entity. A repair never raises a cursor: the
    owner's "redo from here" must not be reversed. A layer's entities are written only after its gate passed, so a live
    entity is the evidence that the layer was attested; no separate attestation record is needed."""
    cur = (idea.get("decoder") or {}).get("stage", "seed")
    below = {"shape": (), "choices": ("shape",), "plan": ("choices", "shape"), "done": ("plan", "choices", "shape")}.get(cur, ())
    return next((s for s in below if _alive(G, idea, s)), "seed")


def closing_rule(G: dict, idea: dict) -> bool:
    """The closing rule design v2 gives ``refresh_done``: every task of the CURRENT plan is terminal and one is done.
    (The shipped rule needs every task done, so one cancelled task keeps an idea open forever: measured in baseline_v1.)"""
    uids = [u for u in _pointers(idea).get("plan", []) if u in G["entities"]]
    tasks = [G["entities"][u] for u in uids]
    return bool(tasks) and all(t["status"] in TERMINAL_TASK for t in tasks) and any(t["status"] == "done" for t in tasks)


EXACT_PAIRS_UP_TO = 400


def _numbers(s: frozenset[str]) -> frozenset[str]:
    return frozenset(w for w in s if w.isdigit())          # a whole number; "mp3" or "h2o" is a word


def _alike(sa: frozenset[str], sb: frozenset[str]) -> bool:
    """Word sets of at least two words that reach the threshold. A CONFLICT of numbers distinguishes them ("tax return
    2025" is not "tax return 2026", "Kurs Python 2" is not "Kurs Python 3"); a number on one side only does not ("Learn
    Python 3" is "Learn Python"; "12 books in 2026" is "12 books in 2026 instead of 10").)"""
    if len(sa) < 2 or len(sb) < 2 or jaccard(sa, sb) < DUPLICATE_THRESHOLD:
        return False
    na, nb = _numbers(sa), _numbers(sb)
    return not (na - nb and nb - na)


def _duplicates(ideas: list[dict]) -> tuple[list[tuple[str, str]], bool]:
    """(pairs, capped). Identical word sets are found by hashing, whatever the word frequencies. Up to
    ``EXACT_PAIRS_UP_TO`` ideas every pair is compared. Beyond that, candidates come from prefix filtering on rare words
    (common words ignored) with a hard cap; hitting the cap is REPORTED, never silent."""
    sigs = {i["uid"]: title_sig(i) for i in ideas}
    uids = sorted(sigs)
    out: set[tuple[str, str]] = set()
    groups: dict[tuple, list[str]] = {}
    for u in uids:
        if len(sigs[u]) >= 2:
            groups.setdefault(tuple(sorted(sigs[u])), []).append(u)
    for g in groups.values():            # a star, not all pairs: a flood of identical ideas costs O(n), never O(n^2)
        out.update((g[0], g[i]) for i in range(1, len(g)))
    small: dict[tuple, list[str]] = {}   # a title of fewer than two words is a duplicate only with the same body as well
    for i in ideas:
        s = sig(i["title"])
        if len(s) < 2 and s:
            small.setdefault((tuple(s), (i.get("body") or "").strip().lower()), []).append(i["uid"])
    for g in small.values():
        g.sort()
        out.update((g[0], g[k]) for k in range(1, len(g)))
    if len(uids) <= EXACT_PAIRS_UP_TO:
        out.update((a, b) for i, a in enumerate(uids) for b in uids[i + 1:] if _alike(sigs[a], sigs[b]))
        return sorted(out), False
    df: dict[str, int] = {}
    for s in sigs.values():
        for w in s:
            df[w] = df.get(w, 0) + 1
    common = {w for w, c in df.items() if c > max(COMMON_TOKEN_FLOOR, COMMON_TOKEN_SHARE * len(uids))}
    postings: dict[str, list[str]] = {}
    for u in uids:                       # ascending, so every posting list is sorted
        for w in sigs[u] - common:
            postings.setdefault(w, []).append(u)
    work, capped = 0, False
    for u in reversed(uids):             # newest first (uids are time-ordered): the likeliest new duplicates are compared
        shared: dict[str, int] = {}      # first, so a budget that runs out starves the OLDEST ideas, never the newest
        for w in sigs[u] - common:
            for v in postings[w]:
                if v >= u:
                    break
                shared[v] = shared.get(v, 0) + 1
                work += 1
        out.update((v, u) for v, c in shared.items() if c >= 2 and _alike(sigs[v], sigs[u]))
        if work > MAX_DUPLICATE_WORK:
            capped = True
            break
    return sorted(out), capped


def _acknowledged(G: dict) -> set[frozenset[str]]:
    out: set[frozenset[str]] = set()
    for e in G["entities"].values():
        for ln in e.get("links") or []:
            if ln.get("rel") in ACKNOWLEDGING_RELS:
                out.add(frozenset((e["uid"], ln.get("to"))))
    return out


GRACE_S = 600.0   # a decoder step writes its drafts, then the idea; a sweep inside that window must not touch them


def sweep(G: dict, now: float | None = None) -> list[Violation]:
    ents = G["entities"]
    found: list[Violation] = []
    derived_by_parent: dict[str, int] = {}
    for e in ents.values():
        if e.get("origin") == "derived":
            for ln in e.get("links") or []:
                if ln.get("rel") == "derived_from" and is_local(ln.get("to")):
                    derived_by_parent[ln["to"]] = derived_by_parent.get(ln["to"], 0) + 1
    ideas = [e for e in ents.values() if e["type"] == "idea"]
    for idea in ideas:
        st = idea.get("decoder")
        if not st:
            if derived_by_parent.get(idea["uid"]) and idea["status"] in ACTIVE_IDEA:
                found.append(Violation("DECODER_STATE_LOST", idea["uid"], "review", "derived children, no decoder state"))
            continue
        for stage, uids in _pointers(idea).items():
            for u in uids:
                if u not in ents:
                    found.append(Violation("CHILD_MISSING", idea["uid"], "auto", f"{stage}:{u}"))
            if len(set(uids)) != len(uids):
                found.append(Violation("CHILD_DUPLICATE", idea["uid"], "auto", stage))
        cursor = st.get("stage", "seed")
        need = {"shape": "shape", "choices": "choices", "plan": "plan", "done": "plan"}.get(cursor)
        closed = idea["status"] not in ACTIVE_IDEA            # a realized, dropped or promoted idea is history
        if cursor not in STAGES:
            found.append(Violation("STAGE_CURSOR", idea["uid"], "auto", f"unknown cursor {cursor!r}"))
        elif need and not closed:
            own = "plan" if need == "done" else need
            if not _alive(G, idea, own):     # behind the newest layer (a revisit) is fine; a cursor on a dead layer is not
                found.append(Violation("STAGE_CURSOR", idea["uid"], "auto", f"cursor {cursor!r}: its layer no longer exists"))
        if idea["status"] in ACTIVE_IDEA and cursor == "plan" and "STUCK_REALIZATION" not in (st.get("dismissed") or []) and closing_rule(G, idea):
            found.append(Violation("STUCK_REALIZATION", idea["uid"], "review", "every task of the plan is terminal"))
    for e in ents.values():
        for ln in e.get("links") or []:
            to = ln.get("to")
            if not is_local(to) or to in ents:
                continue
            if ln.get("rel") == "derived_from" and e.get("origin") == "derived":
                if untouched(e):
                    found.append(Violation("ORPHAN_CHILD", e["uid"], "auto", to))   # touched: standalone work, not a fault
            elif ln.get("rel") not in KEEP_LINKS:
                found.append(Violation("DANGLING_LINK", e["uid"], "auto", f"{ln.get('rel')}->{to}"))
    for e in ents.values():     # an untouched derived draft that no current layer set points at (a crashed step left it)
        if e.get("origin") == "derived" and untouched(e) and (now is None or now - float(e.get("created") or 0) >= GRACE_S):
            for ln in e.get("links") or []:
                parent = ents.get(ln.get("to")) if ln.get("rel") == "derived_from" and is_local(ln.get("to")) else None
                if parent is not None and parent["type"] == "idea" and parent.get("decoder") and e["uid"] not in {
                        u for us in _pointers(parent).values() for u in us}:
                    found.append(Violation("STRAY_DRAFT", e["uid"], "auto", parent["uid"]))
    active = [i for i in ideas if i["status"] in ACTIVE_IDEA]
    ack = _acknowledged(G)
    pairs, capped = _duplicates(active)
    if capped:
        found.append(Violation("DUPLICATE_CHECK_CAPPED", "meta", "review", "candidate cap reached; some pairs were not compared"))
    for a, b in pairs:
        if frozenset((a, b)) in ack or "distinct" in (ents[a].get("labels") or []) or "distinct" in (ents[b].get("labels") or []):
            continue
        found.append(Violation("DUPLICATE_IDEAS", a, "review", b))
    ghosts = [x for x in (G.get("meta") or {}).get("decoder_errors", []) if x.get("u") not in ents]
    if ghosts:
        found.append(Violation("LEDGER_GHOST", "meta", "auto", str(len(ghosts))))
    return found


def heal(G: dict, now: float | None = None) -> tuple[dict, list[Violation]]:
    """Apply the ``auto`` fixes to a COPY of the graph until none is left (a fix can create the next one: a deleted
    orphan leaves a dangling link). Each fix removes an entity, a pointer or a link, or lowers a cursor to what the gate
    record attests, so the loop ends; hitting the pass cap is reported by leaving an ``auto`` violation behind."""
    H = copy.deepcopy(G)
    applied: list[Violation] = []
    for _ in range(MAX_HEAL_PASSES):
        todo = [v for v in sweep(H, now) if v.fix == "auto"]
        if not todo:
            break
        for v in todo:
            _apply(H, v)
            applied.append(v)
    return H, applied


def _apply(H: dict, v: Violation) -> None:
    ents = H["entities"]
    if v.code in ("CHILD_MISSING", "CHILD_DUPLICATE"):
        idea = ents.get(v.uid)
        if idea is not None and idea.get("decoder"):
            ch = idea["decoder"].get("children") or {}
            for k, uids in list(ch.items()):
                seen: list[str] = []
                for u in uids if isinstance(uids, list) else []:
                    if u in ents and u not in seen:
                        seen.append(u)
                ch[k] = seen
    elif v.code == "STAGE_CURSOR":
        idea = ents.get(v.uid)
        if idea is not None and idea.get("decoder") is not None:
            idea["decoder"]["stage"] = lowered_cursor(H, idea)
    elif v.code in ("ORPHAN_CHILD", "STRAY_DRAFT"):
        ents.pop(v.uid, None)
    elif v.code == "DANGLING_LINK":
        e = ents.get(v.uid)
        if e is not None:
            e["links"] = [ln for ln in e["links"] if not is_local(ln.get("to")) or ln.get("to") in ents or ln.get("rel") in KEEP_LINKS]
    elif v.code == "LEDGER_GHOST":
        H["meta"]["decoder_errors"] = [x for x in H["meta"].get("decoder_errors", []) if x.get("u") in ents]


# ---- the live store ------------------------------------------------------------------------------------------------

def _graph(store: Any) -> dict:
    return {"entities": {e["uid"]: e for e in store.entries()},
            "meta": {"decoder_errors": list(store.meta.get("decoder_errors", []))}}


def _running_ideas(store: Any) -> set[str]:
    from . import decoder  # noqa: PLC0415 - decoder calls back into this module after every step
    with decoder._RUNNING_LOCK:
        return {uid for (tid, oid, uid) in decoder._RUNNING if tid == store.tenant_id and oid == store.owner_id}


def _apply_live(store: Any, v: Violation, now: float | None) -> bool:
    """One auto fix on the live store, re-checked against the current entity and written against its version."""
    from .store import NotFound, VersionConflict  # noqa: PLC0415

    G = _graph(store)
    ents = G["entities"]
    try:
        if v.code in ("CHILD_MISSING", "CHILD_DUPLICATE", "STAGE_CURSOR"):
            idea = ents.get(v.uid)
            if idea is None or not idea.get("decoder"):
                return False
            st = copy.deepcopy(idea["decoder"])
            ch = st.get("children") or {}
            for k, uids in list(ch.items()):
                seen: list[str] = []
                for u in uids if isinstance(uids, list) else []:
                    if u in ents and u not in seen:
                        seen.append(u)
                ch[k] = seen
            st["children"] = ch
            probe = dict(idea, decoder=st)
            cur = st.get("stage", "seed")
            if cur not in STAGES:
                st["stage"] = "seed"
            elif cur != "seed" and idea["status"] in ACTIVE_IDEA and not _alive(G, probe, "plan" if cur == "done" else cur):
                st["stage"] = lowered_cursor(G, probe)       # only ever lower
            if st == idea["decoder"]:
                return False
            store.put(dict(idea, decoder=st), expected_version=idea["version"], now=now)
            return True
        if v.code in ("ORPHAN_CHILD", "STRAY_DRAFT"):
            e = ents.get(v.uid)
            if e is None or not untouched(e):
                return False
            fresh = [x for x in sweep(G, now) if x.code == v.code and x.uid == v.uid]
            if not fresh:
                return False
            store.forget(v.uid)
            return True
        if v.code == "DANGLING_LINK":
            e = ents.get(v.uid)
            if e is None:
                return False
            keep = [ln for ln in e.get("links") or [] if not is_local(ln.get("to")) or ln.get("to") in ents or ln.get("rel") in KEEP_LINKS]
            if keep == (e.get("links") or []):
                return False
            store.put(dict(e, links=keep), expected_version=e["version"], now=now)
            return True
        if v.code == "LEDGER_GHOST":
            live = set(ents)
            store.update_meta(lambda m: m.__setitem__(
                "decoder_errors", [x for x in m.get("decoder_errors", []) if x.get("u") in live]))
            return True
    except (VersionConflict, NotFound):
        return False          # a concurrent writer won; the next sweep looks again
    return False


def run_sweep(store: Any, *, now: float | None = None, apply: bool = True) -> dict[str, Any]:
    """Sweep the owner's store; with ``apply`` heal what is derivable, to a fixpoint. Returns a content-free report
    plus the review items (uids and codes only, for the owner's own session)."""
    import time as _time  # noqa: PLC0415
    from . import audit  # noqa: PLC0415

    ts = _time.time() if now is None else now
    store.reload_if_changed()
    fixed = 0
    for _ in range(MAX_HEAL_PASSES if apply else 1):
        busy = _running_ideas(store)
        found = sweep(_graph(store), ts)
        todo = [v for v in found if v.fix == "auto" and v.uid not in busy]
        if not apply or not todo:
            break
        progressed = False
        for v in todo:
            if _apply_live(store, v, ts):
                fixed += 1
                progressed = True
        if not progressed:
            break
    found = sweep(_graph(store), ts)
    review = [{"code": v.code, "uid": v.uid, "detail": v.detail if v.code in ("DUPLICATE_IDEAS",) else ""}
              for v in found if v.fix == "review"]
    auto_left = sum(1 for v in found if v.fix == "auto")
    if fixed or review or auto_left:
        audit.emit("personal_kb.decoder_swept", tenant_id=store.tenant_id, owner_hash=store.owner_hash,
                   healed=fixed, review=len(review), auto_left=auto_left)
    return {"healed": fixed, "review": review, "auto_left": auto_left}
