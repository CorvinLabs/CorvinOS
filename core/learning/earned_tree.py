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
from pathlib import Path

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


def _store(tenant_id: str) -> dict:
    """Read the CEL stage-grade store (the self-earned confidence source). Empty on any
    error — the tree then shows the neutral 0.5 prior, never crashes."""
    try:
        from forge.paths import tenant_global_dir  # noqa: PLC0415
        p = Path(tenant_global_dir(tenant_id)) / "ce_stage_grades.json"
        return json.loads(p.read_text("utf-8")) if p.is_file() else {}
    except Exception:  # noqa: BLE001
        return {}


def build_earned_tree(tenant_id: str) -> list[dict]:
    """A Framework→Method tree whose confidence is EARNED from the CEL grade store.
    Each stage node carries its evidence split (auto-earned from the outcome loop vs.
    explicit operator grades). Returns node dicts in the dashboard's TreeNode shape."""
    store = _store(tenant_id)
    methods: list[dict] = []
    confs: list[float] = []
    total_calls = 0
    for sid, label, when in _STAGES:
        grades = (store.get(sid) or {}).get("grades") or []
        scores = [g.get("score") for g in grades
                  if isinstance(g.get("score"), (int, float))]
        n = len(scores)
        conf = round(sum(scores) / n, 3) if n else 0.5  # 0.5 = no evidence yet
        auto = sum(1 for g in grades if g.get("grader") == "__loop__")
        op = sum(1 for g in grades if g.get("grader") in _PROMOTING)
        op_notes = [["", g.get("grader", "operator"), g.get("notes", "")]
                    for g in grades if g.get("grader") in _PROMOTING and g.get("notes")]
        methods.append({
            "id": f"stage-{sid}", "level": "method", "name": label,
            "confidence": conf, "calls_in_production": n,
            "when": [when], "anti_when": [], "children": [],
            "operator_notes": op_notes, "adr_link": "ADR-0285",
            "evidence": {"auto_earned": auto, "operator": op},
        })
        confs.append(conf)
        total_calls += n
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
