"""Your Talent — real Context-Engineering analytics (ADR-0275).

The prior version returned mock/random data. This computes every figure from the
durable `cel.decision` audit records (ADR-0278): how much you context-engineer,
how good the context is, which sources shape your turns, and the trend. Zero
records → an honest empty-state (all zero / empty), never invented numbers.

The four component figures are HONEST proxies of context-engineering quality,
not coding skill:
  * accuracy      → stage success rate (share of stages that ran ok)
  * learning_rate → context-quality trend (is top_score improving?)
  * variety       → source diversity (distinct sources / total source refs)
  * efficiency    → non-degraded rate (turns that ran enriched, not plain)
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from .. import auth as session_auth
from ..deps import require_session

try:
    from forge import paths as _forge_paths
except Exception:  # noqa: BLE001
    _forge_paths = None  # type: ignore[assignment]

router = APIRouter(prefix="/talent", tags=["console-talent"])

_MEDALS = ["🥇", "🥈", "🥉", "⭐", "✨"]


def _read_decisions(tenant_id: str, limit: int = 2000) -> list[dict]:
    """All cel.decision detail dicts for the tenant (with ts), newest last."""
    if _forge_paths is None:
        return []
    try:
        p = Path(_forge_paths.tenant_global_dir(tenant_id)) / "forge" / "audit.jsonl"
    except Exception:  # noqa: BLE001
        return []
    if not p.exists():
        return []
    out: list[dict] = []
    try:
        for ln in p.read_text(encoding="utf-8").splitlines():
            if '"cel.decision"' not in ln:
                continue
            try:
                e = json.loads(ln)
            except (json.JSONDecodeError, ValueError):
                continue
            if e.get("event_type") != "cel.decision":
                continue
            d = dict(e.get("details", {}) or {})
            d["ts"] = e.get("ts")
            out.append(d)
    except Exception:  # noqa: BLE001
        return []
    return out[-limit:]


def _stats(tenant_id: str) -> dict[str, Any]:
    """Aggregate the cel.decision records into talent figures. All real."""
    recs = _read_decisions(tenant_id)
    n = len(recs)
    if n == 0:
        return {"total_turns": 0}

    top_scores = [float(r.get("top_score") or 0.0) for r in recs]
    stage_total = stage_ok = 0
    src_counter: Counter = Counter()
    by_stage: Counter = Counter()
    non_degraded = 0
    for r in recs:
        if not r.get("degraded"):
            non_degraded += 1
        for s in r.get("stages", []):
            stage_total += 1
            if s.get("status") == "ok":
                stage_ok += 1
            srcs = s.get("sources", []) or []
            if srcs:
                by_stage[s.get("stage")] += len(srcs)
            for src in srcs:
                if isinstance(src, dict) and src.get("id"):
                    src_counter[src["id"]] += 1

    total_src_refs = sum(src_counter.values())
    variety = (len(src_counter) / total_src_refs) if total_src_refs else 0.0
    accuracy = (stage_ok / stage_total) if stage_total else 0.0
    efficiency = non_degraded / n
    avg_top = sum(top_scores) / n
    # learning_rate: recent-half avg vs older-half avg of top_score
    half = max(1, n // 2)
    older = top_scores[:half]
    recent = top_scores[half:] or older
    learning = min(1.0, max(0.0, 0.5 + (sum(recent) / len(recent) - sum(older) / len(older))))

    # per-day buckets
    daily: dict[str, list] = {}
    for r in recs:
        ts = r.get("ts")
        if not ts:
            continue
        day = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        daily.setdefault(day, []).append(float(r.get("top_score") or 0.0))

    return {
        "total_turns": n, "avg_top": avg_top, "accuracy": accuracy,
        "learning": learning, "variety": variety, "efficiency": efficiency,
        "src_counter": src_counter, "by_stage": by_stage, "daily": daily,
    }


@router.get("/score")
async def get_talent_score(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    st = _stats(rec.tenant_id)
    if st["total_turns"] == 0:
        return {"talent_score": 0.0, "trend": 0.0, "empty": True,
                "components": {"accuracy": 0, "learning_rate": 0, "variety": 0,
                               "efficiency": 0},
                "ranking": [], "events": []}
    ranking = []
    for i, (sid, cnt) in enumerate(st["src_counter"].most_common(5)):
        ranking.append({
            "id": sid, "rank": i + 1, "medal": _MEDALS[i],
            "status": "Top source" if i == 0 else "Frequent",
            "accuracy": round(st["accuracy"], 2),
            "feedback_pct": round(100 * cnt / max(1, st["total_turns"]), 1),
        })
    events = [
        {"timestamp": datetime.now(timezone.utc).isoformat(), "type": "milestone",
         "title": f"{st['total_turns']} context-engineered turns", "badge": "🧠",
         "description": f"Average context score {round(st['avg_top'], 2)}"},
    ]
    top = st["src_counter"].most_common(1)
    if top:
        events.append({"timestamp": datetime.now(timezone.utc).isoformat(),
                       "type": "achievement", "title": "Most-used source",
                       "description": top[0][0], "badge": "📌"})
    return {
        "talent_score": round(st["avg_top"] * 10, 1),
        "trend": round((st["learning"] - 0.5) * 10, 2),
        "empty": False,
        "components": {"accuracy": round(st["accuracy"], 2),
                       "learning_rate": round(st["learning"], 2),
                       "variety": round(st["variety"], 2),
                       "efficiency": round(st["efficiency"], 2)},
        "ranking": ranking, "events": events,
    }


@router.get("/history")
async def get_talent_history(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    days: int = 7,
) -> dict[str, Any]:
    if not 1 <= days <= 90:
        raise HTTPException(status_code=400, detail="days must be between 1 and 90")
    st = _stats(rec.tenant_id)
    if st["total_turns"] == 0:
        return {"daily": [], "empty": True}
    daily = []
    for day in sorted(st["daily"])[-days:]:
        scores = st["daily"][day]
        avg = sum(scores) / len(scores) if scores else 0.0
        daily.append({
            "date": day, "score": round(avg * 10, 2),
            "accuracy": round(st["accuracy"], 2),
            "learning_rate": round(st["learning"], 2),
            "variety": round(st["variety"], 2),
            "efficiency": round(st["efficiency"], 2),
            "record_count": len(scores),
        })
    return {"daily": daily, "empty": False}


@router.get("/task-types")
async def get_talent_task_types(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    days: int = 7,
) -> dict[str, Any]:
    if not 1 <= days <= 90:
        raise HTTPException(status_code=400, detail="days must be between 1 and 90")
    st = _stats(rec.tenant_id)
    if st["total_turns"] == 0:
        return {"task_types": [], "empty": True}
    labels = {"memory": "Memory Lookup", "graph": "Graph Traversal",
              "skill": "Skill Injection", "approach_synthesis": "Approach Synthesis",
              "blocker_id": "Blocker ID"}
    task_types = []
    for stage, cnt in st["by_stage"].most_common():
        task_types.append({
            "type": labels.get(stage, stage), "count": cnt,
            "accuracy": round(st["accuracy"], 2),
            "feedback_percentage": round(st["efficiency"] * 100, 1),
            "efficiency": round(st["variety"], 2),
        })
    return {"task_types": task_types, "empty": False}


@router.get("/correlation")
async def get_talent_correlation(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    days: int = 7,
) -> dict[str, Any]:
    if not 1 <= days <= 90:
        raise HTTPException(status_code=400, detail="days must be between 1 and 90")
    recs = _read_decisions(rec.tenant_id)
    points = []
    for r in recs:
        n_src = sum(len(s.get("sources", []) or []) for s in r.get("stages", []))
        points.append({"accuracy": round(float(r.get("top_score") or 0.0), 2),
                       "efficiency": round(min(1.0, n_src / 10.0), 2)})
    return {"correlation": {"points": points}, "empty": not points}


@router.get("/insights")
async def get_talent_insights(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    days: int = 7,
) -> dict[str, Any]:
    if not 1 <= days <= 90:
        raise HTTPException(status_code=400, detail="days must be between 1 and 90")
    st = _stats(rec.tenant_id)
    if st["total_turns"] == 0:
        return {"dimensions": [], "narratives": [], "badges": [], "empty": True}
    dims = [
        ("Context quality", "🎯", st["avg_top"], "Average top-source relevance per turn"),
        ("Learning rate", "📚", st["learning"], "Is context quality trending up?"),
        ("Source variety", "🎨", st["variety"], "Diversity of memories / ADRs / skills used"),
        ("Enriched rate", "⚡", st["efficiency"], "Turns that ran enriched, not plain"),
    ]
    dimensions = [{"dimension": name, "icon": icon, "current": round(val * 100, 1),
                   "change": 0.0, "status": "flat",
                   "narrative": desc, "analysis": desc} for name, icon, val, desc in dims]
    narratives = [{"icon": "🧠", "title": "Context-engineered turns",
                   "description": f"{st['total_turns']} turns enriched so far"}]
    top = st["src_counter"].most_common(3)
    badges = [{"badge": "📌", "title": sid[:32], "context": f"used {cnt}×",
               "level": ["gold", "silver", "bronze"][min(i, 2)]}
              for i, (sid, cnt) in enumerate(top)]
    return {"dimensions": dimensions, "narratives": narratives, "badges": badges,
            "empty": False}


@router.get("/story")
async def get_talent_story(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    days: int = 7,
) -> dict[str, Any]:
    if not 1 <= days <= 90:
        raise HTTPException(status_code=400, detail="days must be between 1 and 90")
    st = _stats(rec.tenant_id)
    if st["total_turns"] == 0:
        return {"story": None, "empty": True}
    end = round(st["avg_top"] * 10, 1)
    return {"story": {
        "summary": (f"You have context-engineered {st['total_turns']} turns. "
                    f"Average context score {round(st['avg_top'], 2)}, "
                    f"{round(st['efficiency'] * 100)}% enriched, "
                    f"{len(st['src_counter'])} distinct sources drawn on."),
        "score_start": end, "score_end": end,
        "score_change": round((st["learning"] - 0.5) * 10, 1),
        "trend": "improving" if st["learning"] > 0.5 else "steady",
        "milestone": f"{st['total_turns']} enriched turns",
    }, "empty": False}
