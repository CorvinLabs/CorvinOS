"""A fixed replay corpus for the personal decoder's loss (LDD).

Each idea carries, per stage, the model's FIRST answer and its answer when the prompt contains the
gate failures as feedback ("FIX THESE PROBLEMS"). The first answers reproduce failure modes seen
when a model shapes an idea: no boundary, no measurable success, a decision with one alternative,
tasks without a definition of done, more tasks than allowed, dependencies pointing forward. The
corpus is deterministic, so a change to the decoder is measured, not guessed.
"""
from __future__ import annotations

GOOD_SHAPE = {"goal": "g", "boundaries": ["b1"], "why_now": "w", "success": "s"}
GOOD_CHOICES = {"meaning": "m", "reach": "r", "doing": "d",
                "decisions": [{"question": "q", "alternatives": ["a", "b"], "chosen": "a", "why": "y"}]}
GOOD_PLAN = {"tasks": [{"title": "t1", "dod": "d1", "depends_on": []}, {"title": "t2", "dod": "d2", "depends_on": [0]}]}


def _idea(title, shape=None, choices=None, plan=None, objections=()):
    return {"title": title,
            "shape": {"first": shape or GOOD_SHAPE, "fixed": GOOD_SHAPE},
            "choices": {"first": choices or GOOD_CHOICES, "fixed": GOOD_CHOICES},
            "plan": {"first": plan or GOOD_PLAN, "fixed": GOOD_PLAN},
            "objections": list(objections)}


CORPUS = [
    _idea("Balcony herb garden"),
    _idea("Learn Spanish for the trip", shape={"goal": "speak basics", "boundaries": [], "why_now": "trip", "success": ""}),
    _idea("Weekly running plan", shape={"goal": "run", "boundaries": ["no gym"], "why_now": "", "success": "5 km"}),
    _idea("Family photo archive", choices={"meaning": "m", "reach": "r", "doing": "d",
                                           "decisions": [{"question": "where", "alternatives": ["nas"], "chosen": "nas"}]}),
    _idea("Home office tidy-up", plan={"tasks": [{"title": "sort cables", "dod": "", "depends_on": []},
                                                 {"title": "buy shelf", "dod": "shelf mounted", "depends_on": []}]}),
    _idea("Newsletter for the club", plan={"tasks": [{"title": f"step {i}", "dod": "x", "depends_on": []} for i in range(10)]}),
    _idea("Budget spreadsheet", plan={"tasks": [{"title": "a", "dod": "x", "depends_on": [1]}, {"title": "b", "dod": "y", "depends_on": []}]}),
    _idea("Read one book a month", objections=[{"category": "risk", "text": "busy months"}]),
    _idea("Bike repair course", shape={"goal": "", "boundaries": ["weekends"], "why_now": "spring", "success": "fix a flat"}),
    _idea("Plant-based cooking week", choices={"meaning": "", "reach": "", "doing": "cook",
                                               "decisions": []}),
    _idea("Digital detox Sundays", objections=[{"category": "vague", "text": "what counts as digital"}]),
    _idea("Garage sale in May", plan={"tasks": []}),
]
