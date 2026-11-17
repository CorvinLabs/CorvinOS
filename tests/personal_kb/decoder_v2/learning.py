"""Mechanism 2: tuning the decoder with a MEASURED effect (design v2) against the shipped ratchet.

Two controllers face the same simulated owner. The simulator knows the true quality of every setting; the
controllers see only what the product sees: per run, did the owner accept the decoder's output as it was
(``accepted``), and what did the run cost.

* ``Ratchet`` is ``core/personal_kb/evolve.py::record_decoder_error`` as shipped: three failures of one class tighten
  one knob one step. It never loosens and never checks that the tightening helped.
* ``Measured`` proposes one change at a time, runs it for a window, and keeps it only if the owner's acceptance
  rate is credibly better (tighten) or credibly not worse while cheaper (loosen); otherwise it rolls back. A rolled-
  back direction rests for a cool-down; two rollbacks in a row freeze tuning (the brake).
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

TEMPLATES = (0, 1, 2)        # 0 light, 1 required fields, 2 strict
ROUNDS = (0, 1, 2, 3)        # adversarial passes
WINDOW = 40                  # items per arm (one run yields ITEMS_PER_RUN items: tasks and notes the owner keeps or edits)
ITEMS_PER_RUN = 5
RUNS = 160                   # about six months at the decoder's real pace (a few stage runs a week, one owner's data only)
START = (1, 1)               # (template, rounds): the middle of the allowlist
TARGET_Q = 0.80
NONINFERIORITY = 0.05
P_ADOPT = 0.90               # an improvement must be credible before it is kept
P_SAVE = 0.90                # same bar as an improvement: a wrong saving costs quality the owner never sees
COOLDOWN_WINDOWS = 3
BRAKE_AFTER = 2


def cost(theta: tuple[int, int]) -> float:
    t, r = theta
    return 1.0 + 0.5 * r + 0.2 * t


@dataclass
class Scenario:
    name: str
    q: callable            # (theta, run_index) -> true acceptance probability
    note: str = ""


def _base(t: int, r: int) -> float:
    return 0.50 + 0.08 * t + 0.05 * min(r, 2)


SCENARIOS = [
    Scenario("tighten_helps", lambda th, i: _base(*th), "stricter template and 2 rounds help; the 3rd round adds nothing"),
    Scenario("tighten_harms", lambda th, i: 0.65 - 0.07 * th[0] + 0.03 * th[1], "a strict template makes the owner edit more"),
    Scenario("rounds_useless", lambda th, i: 0.60 + 0.03 * th[0], "extra critic rounds change nothing but cost"),
    Scenario("flat_loosen_ok", lambda th, i: 0.70 if th[1] >= 1 else 0.55, "one round is enough; two more are waste"),
    Scenario("drift", lambda th, i: _base(*th) if i < RUNS // 2 else 0.65 - 0.07 * th[0] + 0.03 * th[1],
             "the owner's taste flips halfway"),
]


# Added after the independent audit: scenarios fixed BEFORE looking at any controller's result, including ones where
# tightening is the right answer and ones where the starting point is already best.
SCENARIOS += [
    Scenario("monotone_all_help", lambda th, i: 0.45 + 0.10 * th[0] + 0.08 * th[1], "every tightening helps"),
    Scenario("big_tighten_needed", lambda th, i: 0.30 + 0.20 * th[0] + 0.10 * th[1], "the start is poor, strictness fixes it"),
    Scenario("start_is_optimal", lambda th, i: 0.82 - 0.06 * abs(th[0] - 1) - 0.06 * abs(th[1] - 1), "(1,1) is the best setting"),
    Scenario("stationary_flat", lambda th, i: 0.70, "nothing matters, only the cost does"),
]


def grid():
    return [(t, r) for t in TEMPLATES for r in ROUNDS]


def best_of(sc: Scenario, i: int) -> tuple[tuple[int, int], float]:
    """Highest true quality; among settings within 0.02 of it, the cheapest."""
    qs = {th: sc.q(th, i) for th in grid()}
    top = max(qs.values())
    cand = [th for th, q in qs.items() if q >= top - 0.02]
    th = min(cand, key=cost)
    return th, qs[th]


class Ratchet:
    """The shipped outer loop: tighten after the same failure in three different ideas; never loosen, never measure."""

    def __init__(self) -> None:
        self.theta = START
        self.fail_ideas: set[int] = set()
        self.changes = 0
        self.events: list = []      # (run, old theta, new theta) for every change that is KEPT

    def observe(self, i: int, accepted: bool) -> None:
        if not accepted:
            self.fail_ideas.add(i)
        if len(self.fail_ideas) >= 3:
            t, r = self.theta
            old = self.theta
            if t < 2:
                self.theta = (t + 1, r)
            elif r < 3:
                self.theta = (t, r + 1)
            self.fail_ideas.clear()
            if self.theta != old:
                self.changes += 1
                self.events.append((i, old, self.theta))


HARM_MARGIN = 0.03
P_HARM = 0.75


class Guarded:
    """The shipped ratchet plus a veto: every tightening is kept UNLESS the next window is credibly worse than the one
    before it (then it is rolled back and that direction rests). No improvement has to be proven, only harm ruled out."""

    def __init__(self) -> None:
        self.theta = START
        self.events: list = []
        self._fail_ideas: set[int] = set()
        self._items: list[bool] = []
        self._prev: tuple[int, int] | None = None
        self._pending: tuple[tuple[int, int], tuple[int, int]] | None = None     # (old theta, stats before)
        self._rest_until = -1
        self._run = 0

    def observe_run(self, i: int, items: list[bool]) -> None:
        self._run = i
        for it in items:
            self._items.append(bool(it))
            if len(self._items) >= WINDOW:
                self._window(i, sum(self._items), len(self._items))
                self._items = []
        if sum(items) < len(items) - 1:
            self._fail_ideas.add(i // 3)
        if self._pending is None and len(self._fail_ideas) >= 3 and i > self._rest_until:
            t, r = self.theta
            new = (t + 1, r) if t < 2 else ((t, r + 1) if r < 3 else None)
            self._fail_ideas.clear()
            if new is not None and self._prev is not None:
                self._pending = (self.theta, self._prev)
                self.theta = new
                self._items = []

    def _window(self, i: int, a: int, n: int) -> None:
        if self._pending is not None:
            old, (a0, n0) = self._pending
            self._pending = None
            if p_better(a0, n0, a, n, margin=-HARM_MARGIN) >= P_HARM:      # the old setting was credibly better
                self.theta = old
                self._rest_until = i + 24
                return
            self.events.append((i, old, self.theta))
        self._prev = (a, n)

    def observe(self, i: int, accepted: bool) -> None:   # not used: the guarded loop needs the whole run
        pass


class Static:
    """Never changes anything: the baseline every learning claim has to beat."""

    def __init__(self) -> None:
        self.theta = START
        self.events: list = []

    def observe(self, i: int, accepted: bool) -> None:
        pass


def _phi(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def p_better(a_new: int, n_new: int, a_old: int, n_old: int, margin: float = 0.0) -> float:
    """P(q_new > q_old - margin), normal approximation with a +1/+2 shrink so small windows stay honest."""
    p1, p0 = (a_new + 1) / (n_new + 2), (a_old + 1) / (n_old + 2)
    var = p1 * (1 - p1) / (n_new + 2) + p0 * (1 - p0) / (n_old + 2)
    return _phi((p1 - p0 + margin) / math.sqrt(var))


MIN_EFFECT = 0.05            # an improvement below this is not worth a change (and cannot be seen in a window)
DRIFT_DROP = 0.10
P_DRIFT = 0.90
MAX_REST = 24


@dataclass
class Measured:
    theta: tuple[int, int] = START
    frozen_until: int = -1
    _hist: list = field(default_factory=list)          # accepted flags of the current arm
    _arm: str = "baseline"                             # "baseline" | "trial"
    _old: tuple[int, int] = START
    _old_stats: tuple[int, int] = (0, 0)
    _ref: tuple[int, int] | None = None                # acceptance stats of the settled state, for drift detection
    _anchor: tuple[int, int] | None = None             # stats at the last IMPROVEMENT: loosening may not drift below it
    _kind: str = ""
    _test: str = ""                                    # "improve" | "save"
    _rest: dict = field(default_factory=dict)          # direction -> window index until which it rests
    _strikes: dict = field(default_factory=dict)       # direction -> consecutive rollbacks (back-off exponent)
    _consec_rollbacks: int = 0
    window_index: int = 0
    rollbacks: int = 0
    events: list = field(default_factory=list)         # (run, old, new) for every KEPT change

    def observe(self, i: int, accepted: bool) -> None:
        self._hist.append(bool(accepted))
        if len(self._hist) < WINDOW:
            return
        a, n = sum(self._hist), len(self._hist)
        self._hist = []
        self.window_index += 1
        if self._arm == "trial":
            self._decide(i, a, n)
            return
        if self._ref is None:
            self._ref = self._anchor = (a, n)
        elif p_better(*self._ref, a, n, margin=-DRIFT_DROP) >= P_DRIFT:
            # the owner's taste moved: what was learnt about the directions no longer holds
            self._rest.clear()
            self._strikes.clear()
            self.frozen_until = -1
            self._consec_rollbacks = 0
            self._ref = self._anchor = (a, n)
        self._old_stats = (a, n)
        self._maybe_propose(a / n)

    def _candidates(self, q: float) -> list[tuple[tuple[int, int], str, str]]:
        t, r = self.theta
        up = [((t + 1, r), "tighten:template", "improve"), ((t, r + 1), "tighten:rounds", "improve")]
        down = [((t, r - 1), "loosen:rounds", "save"), ((t - 1, r), "loosen:template", "save")]
        if q < TARGET_Q:     # quality is short of the goal: tighten first; when that rests, look the other way too
            return up + down
        return down

    def _maybe_propose(self, q: float) -> None:
        if self.window_index <= self.frozen_until:
            return
        for th, kind, test in self._candidates(q):
            if th[0] in TEMPLATES and th[1] in ROUNDS and self._rest.get(kind, -1) < self.window_index:
                self._old, self.theta, self._kind, self._test, self._arm = self.theta, th, kind, test, "trial"
                return

    def _decide(self, i: int, a: int, n: int) -> None:
        a0, n0 = self._old_stats
        if self._test == "improve":
            ok = p_better(a, n, a0, n0, margin=-MIN_EFFECT) >= P_ADOPT and cost(self.theta) <= 1.5 * cost(self._old)
        else:
            ok = (p_better(a, n, a0, n0, margin=NONINFERIORITY) >= P_SAVE and cost(self.theta) < cost(self._old)
                  and p_better(a, n, *self._anchor, margin=NONINFERIORITY) >= P_SAVE)
        self._arm = "baseline"
        if ok:
            self.events.append((i, self._old, self.theta))
            self._consec_rollbacks = 0
            self._strikes[self._kind] = 0
            self._ref = (a, n)
            if self._test == "improve":
                self._anchor = (a, n)
            return
        self.theta = self._old
        self.rollbacks += 1
        self._consec_rollbacks += 1
        k = self._strikes.get(self._kind, 0)
        self._strikes[self._kind] = k + 1
        self._rest[self._kind] = self.window_index + min(MAX_REST, COOLDOWN_WINDOWS * 2 ** k)
        if self._consec_rollbacks >= BRAKE_AFTER:
            self.frozen_until = self.window_index + 2 * COOLDOWN_WINDOWS
            self._consec_rollbacks = 0


def simulate(sc: Scenario, controller_cls, seed: int) -> dict:
    rng = random.Random(seed)
    c = controller_cls()
    qs, costs, bests = [], [], []
    start_q = []
    for i in range(RUNS):
        th = c.theta
        q = sc.q(th, i)
        qs.append(q)
        costs.append(cost(th))
        bests.append(best_of(sc, i))
        start_q.append(sc.q(START, i))
        items = [rng.random() < q for _ in range(ITEMS_PER_RUN)]
        if hasattr(c, "observe_run"):
            c.observe_run(i, items)
        elif controller_cls is Ratchet:      # the shipped loop sees one verdict per run and counts ideas (3 stages each)
            c.observe(i // 3, sum(items) >= ITEMS_PER_RUN - 1)
        else:
            for k, it in enumerate(items):
                c.observe(i, it)
    wrong = 0
    for i, old, new in c.events:    # a KEPT change is wrong if, at that moment, it was worse or only dearer
        if sc.q(new, i) < sc.q(old, i) - 0.02 or (sc.q(new, i) <= sc.q(old, i) + 0.02 and cost(new) > cost(old)):
            wrong += 1
    third = RUNS // 3
    qbest = sum(b[1] for b in bests[-third:]) / third
    cbest = sum(cost(b[0]) for b in bests[-third:]) / third
    worst = min(sc.q(th, RUNS - 1) for th in grid())
    rng_q = max(1e-9, qbest - worst)
    return {"regret": max(0.0, (qbest - sum(qs[-third:]) / third) / rng_q),
            "cost_excess": max(0.0, (sum(costs[-third:]) / third) / cbest - 1),
            "harm": sum(max(0.0, s - q) for s, q in zip(start_q, qs)) / RUNS / rng_q,   # time spent below the starting quality
            "wrong_changes": wrong, "changes": len(c.events)}


def _components(rs: list[dict]) -> dict:
    n = len(rs)
    changes = sum(r["changes"] for r in rs)
    return {"regret": sum(min(1.0, r["regret"]) for r in rs) / n, "cost_excess": sum(min(1.0, r["cost_excess"]) for r in rs) / n,
            "harm": sum(min(1.0, r["harm"]) for r in rs) / n,
            "wrong_change_rate": (sum(r["wrong_changes"] for r in rs) / changes) if changes else 0.0,
            "changes_per_owner": changes / n}


WEIGHTINGS = {"equal(regret,cost,harm)": (1, 1, 1), "regret_only": (1, 0, 0), "regret_x3": (3, 1, 1)}


def measure(seeds: int = 40) -> dict:
    out: dict = {}
    for name, cls in (("do_nothing", Static), ("ratchet_v1", Ratchet), ("guarded_ratchet", Guarded), ("measured_v2", Measured)):
        per = {}
        for sc in SCENARIOS:
            rs = [simulate(sc, cls, 1000 + s) for s in range(seeds)]
            per[sc.name] = {k: round(v, 4) for k, v in _components(rs).items()}
        out[name] = {"per_scenario": per}
        for wn, (wr, wc, wh) in WEIGHTINGS.items():
            vals = [(wr * v["regret"] + wc * v["cost_excess"] + wh * v["harm"]) / (wr + wc + wh) for v in per.values()]
            out[name].setdefault("loss", {})[wn] = round(sum(vals) / len(vals), 4)
    # an oracle that knows the best fixed setting in hindsight bounds what ANY controller could gain
    orc = {}
    for sc in SCENARIOS:
        best = max(grid(), key=lambda th: (sc.q(th, 0) + sc.q(th, RUNS - 1)) / 2 - 0.02 * cost(th))
        orc[sc.name] = {"best_fixed": best, "q": round((sc.q(best, 0) + sc.q(best, RUNS - 1)) / 2, 3),
                        "q_at_start": round((sc.q(START, 0) + sc.q(START, RUNS - 1)) / 2, 3)}
    out["oracle_best_fixed_setting"] = orc
    return out
