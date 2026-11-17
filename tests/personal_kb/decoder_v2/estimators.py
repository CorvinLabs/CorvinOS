"""Mechanism 2b: learning from DENSE signals by closed-form estimation (design v2).

An owner produces few decoder runs but many small verdicts per run: which tasks they keep, which objections they fix
or dismiss. Those verdicts estimate two quantities directly, with a prior and bounds, no experiment needed:

* the plan size the owner actually finishes (``decoder_task_cap``), with CENSORED observations: keeping every
  planned task only says "at least that many";
* the precision of the critic per objection category (``fix`` = useful, ``dismiss`` = noise), which decides which
  categories are shown as objections and which are demoted to quiet advisories.

The shipped decoder tightens ``decoder_task_cap`` by 2 after a ``too_big`` revisit in three ideas and never loosens it;
its critic shows the first three objections whatever their category.
"""
from __future__ import annotations

import math
import random

DEFAULT_CAP = 7                 # core/personal_kb/policy.py: decoder_task_cap default 7, bounds 3..15
CAP_MIN, CAP_MAX = 3, 15
PRIOR_N = 3
PRIOR_MEAN = 6.0
HEADROOM = 1.25


class CapRatchet:
    """core/personal_kb/evolve.py: 'too_big' in three different ideas lowers the cap by 2 (floor 3); never raises it."""

    def __init__(self) -> None:
        self.cap = DEFAULT_CAP
        self.too_big_ideas: set[int] = set()

    def observe(self, idea: int, planned: int, kept: int) -> None:
        if kept < planned * 0.6:                      # the owner cut the plan down: the shipped 'too_big' revisit
            self.too_big_ideas.add(idea)
        if len(self.too_big_ideas) >= 3:
            self.cap = max(CAP_MIN, self.cap - 2)
            self.too_big_ideas.clear()


class CapEstimator:
    """Posterior mean of the finished plan size; a plan the owner kept whole counts as 'at least one more'."""

    def __init__(self) -> None:
        self.cap = DEFAULT_CAP
        self.n = 0
        self.total = 0.0

    def observe(self, idea: int, planned: int, kept: int) -> None:
        self.n += 1
        self.total += kept + (1 if kept >= planned else 0)       # censored: kept everything -> a lower bound, nudge up
        m = (PRIOR_N * PRIOR_MEAN + self.total) / (PRIOR_N + self.n)
        self.cap = max(CAP_MIN, min(CAP_MAX, math.ceil(HEADROOM * m)))


def cap_trial(true_size: int, controller, plans: int, rng: random.Random) -> list[int]:
    """The decoder plans ``cap`` tasks; the owner keeps up to ``true_size`` of them (± noise); returns the cap path."""
    path = []
    for i in range(plans):
        planned = controller.cap
        wanted = max(1, round(true_size + rng.gauss(0, 1.0)))
        controller.observe(i, planned, min(planned, wanted))
        path.append(controller.cap)
    return path


# ---- the critic: what to SHOW of its three ranked objections ------------------------------------------------------
# MEASURED (critic_probe_twins_results.json, blind-judged, 32 runs): the share of objections that are a real defect by the
# position the critic gave them. Rank 1 carries the signal; rank 3 is almost always a nitpick.
REAL_DEFECT_BY_RANK = (22 / 32, 9 / 32, 1 / 32)


def critic_display() -> dict:
    """Expected objections per run under two display policies, from the measured rates (no simulation, no assumption)."""
    r = REAL_DEFECT_BY_RANK
    show3 = {"loud_useful": sum(r), "loud_noise": 3 - sum(r), "quiet_useful": 0.0}
    top1 = {"loud_useful": r[0], "loud_noise": 1 - r[0], "quiet_useful": r[1] + r[2]}
    return {"show_three_loud_v1": {k: round(v, 3) for k, v in show3.items()},
            "first_loud_rest_quiet": {k: round(v, 3) for k, v in top1.items()}}


def measure(seeds: int = 60, plans: int = 24, runs: int = 40) -> dict:
    out: dict = {"cap": {}}
    controllers = (("static_7", None), ("ratchet_v1", CapRatchet), ("estimator_v2", CapEstimator))
    for label, sizes in (("small_plans_3to6", (3, 4, 5, 6)), ("mid_plans_6to9", (6, 7, 8, 9)), ("large_plans_10to14", (10, 12, 14))):
        out["cap"][label] = {}
        for name, cls in controllers:
            short = excess = err = 0.0
            n = 0
            for s in range(seeds):
                for size in sizes:
                    rng = random.Random(500 + s * 7 + size)
                    if cls is None:
                        path = [DEFAULT_CAP] * plans
                    else:
                        path = cap_trial(size, cls(), plans, rng)
                    tail = path[plans // 2:]
                    short += sum(max(0, size - c) for c in tail) / len(tail)          # tasks the owner would finish that the cap forbids
                    excess += sum(max(0, c - math.ceil(1.5 * size)) for c in tail) / len(tail)   # plans clearly larger than needed
                    err += sum(abs(c - size) for c in tail) / len(tail)
                    n += 1
            out["cap"][label][name] = {"shortfall": round(short / n, 3), "excess": round(excess / n, 3), "abs_error_to_size": round(err / n, 3)}
    out["critic"] = critic_display()
    return out
