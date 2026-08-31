"""Earned-confidence projection for the TreeOfThoughts view (Weg A).

TreeOfThoughts should show confidence CorvinOS EARNS ITSELF from real turns — not a tree a
human hand-grades into existence. That self-managing source already exists: the CEL
stage-grade store, auto-filled by the outcome-feedback loop (ADR-0269 Phase-4b / G4) after
every context-engineered turn, and refined by explicit operator-override grades
(ADR-0285 / G3). This module projects that store into the Framework→Method node shape the
learning dashboard renders, so the tree is real and *earned*, with zero new hand-entry.

Read-only: it never writes. Operator overrides go straight to the CEL grade endpoint
(`POST /vibe-engineering/grades/{stage}`), so a correction lands in the same store the
confidence is computed from — closing the loop instead of forking a second grade store.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

_MAX_GRADES = 5000  # bound the per-stage read so a huge store can't slow /nodes (L3)

# The CEL pipeline stages, in pipeline order, with human labels + a "when" hint.
_STAGES: list[tuple[str, str, str]] = [
    ("memory", "Memory retrieval", "Recall what's known about the user/project"),
    ("graph", "ADR graph traversal", "Pull in linked decisions"),
    ("skill", "Skill injection", "Bind relevant learned skills"),
    ("approach_synthesis", "Approach synthesis", "Sketch the solution path"),
    ("blocker_id", "Blocker identification", "Name likely failure modes"),
    ("llm_synthesis", "LLM synthesis", "Condense via an LLM (opt-in)"),
    ("toolforge", "Tool forging", "Build a tool for the task (opt-in)"),
    ("skillforge", "Skill forging", "Learn a reusable skill (opt-in)"),
]

_PROMOTING = {"operator"}  # the only grader that moves default-eligibility (ADR-0285)


def _clamp_score(raw: object) -> "float | None":
    """A grade's score as a clean [0,1] float, or None if it isn't a usable number.
    Rejects bools (bool ⊂ int), non-numbers, NaN/Inf, AND an over-range int whose float
    conversion overflows (a 310+-digit literal in a hand-edited store) — the last of which
    would otherwise raise OverflowError inside math.isfinite and blank the whole tree."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    try:
        f = float(raw)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return max(0.0, min(1.0, f))


def _store(tenant_id: str) -> dict:
    """Read the CEL stage-grade store (the self-earned confidence source). Empty on any
    error — the tree then shows the neutral 0.5 prior, never crashes."""
    try:
        from forge.paths import tenant_global_dir  # noqa: PLC0415
        p = Path(tenant_global_dir(tenant_id)) / "ce_stage_grades.json"
        data = json.loads(p.read_text("utf-8")) if p.is_file() else {}
        return data if isinstance(data, dict) else {}  # non-dict store → neutral (L1)
    except Exception:  # noqa: BLE001
        return {}


def _is_operator(grader: object) -> bool:
    """True iff grader is the promoting operator. The ``isinstance(str)`` guard runs FIRST,
    so the set-membership only ever sees a hashable str — an unhashable grader (a dict/list
    in a corrupt store) returns False instead of raising ``TypeError: unhashable type`` (R4)."""
    return isinstance(grader, str) and grader in _PROMOTING


def _neutral_method(sid: str, label: str, when: str) -> dict:
    """A no-evidence stage node — the safe fallback when a stage's store slice is too
    corrupt to read, so ONE bad stage degrades to neutral instead of blanking all eight."""
    return {
        "id": f"stage-{sid}", "level": "method", "name": label,
        "confidence": 0.5, "calls_in_production": 0,
        "when": [when], "anti_when": [], "children": [],
        "operator_notes": [], "adr_link": "ADR-0285",
        "evidence": {"auto_earned": 0, "operator": 0},
    }


def _method_node(sid: str, label: str, when: str, store: dict) -> dict:
    """Project one CEL stage's grades into a method node with earned confidence."""
    entry = store.get(sid)
    raw = entry.get("grades") if isinstance(entry, dict) else None      # non-dict entry → none
    raw = raw[-_MAX_GRADES:] if isinstance(raw, list) else []           # bound the read (L3)
    grades = [g for g in raw if isinstance(g, dict)]                    # drop non-dict leaves
    # _clamp_score rejects out-of-range / NaN / Inf / bool / over-range-int without raising.
    scores = [s for g in grades if (s := _clamp_score(g.get("score"))) is not None]
    n_grades = len(grades)
    conf = round(sum(scores) / len(scores), 3) if scores else 0.5  # 0.5 = no evidence yet
    operator = sum(1 for g in grades if _is_operator(g.get("grader")))
    op_notes = [["", g.get("grader", "operator"), g.get("notes", "")]
                for g in grades if _is_operator(g.get("grader")) and g.get("notes")]
    return {
        "id": f"stage-{sid}", "level": "method", "name": label,
        "confidence": conf, "calls_in_production": n_grades,
        "when": [when], "anti_when": [], "children": [],
        "operator_notes": op_notes, "adr_link": "ADR-0285",
        # Evidence sums to n_grades (M1): operator = explicit human grades, auto_earned =
        # everything else the SYSTEM produced (outcome loop + bootstrap seed).
        "evidence": {"auto_earned": n_grades - operator, "operator": operator},
    }


def build_earned_tree(tenant_id: str) -> list[dict]:
    """A Framework→Method tree whose confidence is EARNED from the CEL grade store.
    Each stage node carries its evidence split (auto-earned from the outcome loop vs.
    explicit operator grades). Returns node dicts in the dashboard's TreeNode shape.

    Per-stage failures degrade that ONE stage to neutral (try/except around each) — a
    corrupt store can NEVER blank the whole tree, the invariant every prior review round
    chipped at one field at a time (score, grader, …). This closes the class structurally."""
    store = _store(tenant_id)
    if not isinstance(store, dict):  # defence in depth — _store already guards this
        store = {}
    methods: list[dict] = []
    for sid, label, when in _STAGES:
        try:
            methods.append(_method_node(sid, label, when, store))
        except Exception:  # noqa: BLE001 — one bad stage must never sink the tree
            methods.append(_neutral_method(sid, label, when))
    confs = [m["confidence"] for m in methods]
    total_calls = sum(m["calls_in_production"] for m in methods)
    framework = {
        "id": "framework-cel", "level": "framework",
        "name": "Context Engineering — self-earned confidence",
        "confidence": round(sum(confs) / len(confs), 3) if confs else 0.5,
        "calls_in_production": total_calls,
        "when": ["Every context-engineered turn"], "anti_when": [],
        "children": [m["id"] for m in methods],
        "operator_notes": [], "adr_link": "ADR-0269",
        "evidence": {"auto_earned": sum(m["evidence"]["auto_earned"] for m in methods),
                     "operator": sum(m["evidence"]["operator"] for m in methods)},
    }
    return [framework, *methods]
