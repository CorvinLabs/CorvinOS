"""core.pii.jwt_scan — exact equivalence with the regexes it replaces, in linear time.

The matcher replaced ten quadratic copies of the JWT regex (2026-10-08). Its
claim is "same spans as the regex"; this file checks that claim differentially
against Python's own ``re`` for every configuration used in the tree, on
random inputs biased toward the hard cases (dashes inside runs, dots, Unicode
word characters next to runs, repeated ``eyJ``).
"""
from __future__ import annotations

import random
import re
import time

import pytest

from core.pii.jwt_scan import LinearJwtPattern

# (a, b, c, start_boundary, end_boundary) — one row per call site.
CONFIGS = {
    "pii.sensitive / tool_ranking": (10, 10, 10, True, True),
    "skill_registry_phase1 / logging scrubber": (8, 8, 8, True, True),
    "aco.error_signature": (6, 6, 1, True, False),
    "bridges.debug_logging": (16, 16, 8, False, False),
    "forge_bundle.validate": (10, 10, 10, True, False),
}


def _regex(a, b, c, sb, eb):
    return re.compile((r"\b" if sb else "") + rf"eyJ[A-Za-z0-9_\-]{{{a},}}\.[A-Za-z0-9_\-]{{{b},}}\.[A-Za-z0-9_\-]{{{c},}}"
                      + (r"\b" if eb else ""))


def _inputs(seed: int, count: int):
    rnd = random.Random(seed)
    alphabet = ["eyJ", "eyJ", "-", "-", ".", "a", "Z", "9", "_", " ", "ä", "é", "\n", "x", "eyJhbGciOiJIUzI1NiJ9"]
    run_chars = "aZ9_-"
    noise = ["", "", "-", " ", "ä", "x", ".", "-eyJ", "é", "_"]

    def seg(lo: int) -> str:  # a run around the minimum length, sometimes ending in "-"
        return "".join(rnd.choice(run_chars) for _ in range(max(0, lo + rnd.randint(-3, 6))))

    for _ in range(count):
        if rnd.random() < 0.5:
            n = rnd.randint(0, 40)
            yield "".join(rnd.choice(alphabet) * rnd.choice((1, 1, 1, 3, 8, 12)) for _ in range(n))
        else:  # near-JWT shapes: three segments, minimums +/- a few, noise around and between
            lo = rnd.choice((1, 6, 8, 10, 16))
            body = rnd.choice(noise) + "eyJ" + seg(lo) + "." + seg(lo) + "." + seg(rnd.choice((1, 8, 10))) + rnd.choice(noise)
            yield rnd.choice(noise) + body * rnd.choice((1, 1, 2, 3))


@pytest.mark.parametrize("name", sorted(CONFIGS))
def test_same_spans_as_the_regex(name):
    a, b, c, sb, eb = CONFIGS[name]
    rx, lin = _regex(a, b, c, sb, eb), LinearJwtPattern(a, b, c, start_boundary=sb, end_boundary=eb)
    checked = hits = 0
    for text in _inputs(seed=hash(name) & 0xFFFF, count=20_000):
        want = [m.span() for m in rx.finditer(text)]
        got = [m.span() for m in lin.finditer(text)]
        assert got == want, (name, text)
        assert lin.sub("<jwt>", text) == rx.sub("<jwt>", text), (name, text)
        assert bool(lin.search(text)) == bool(rx.search(text))
        checked += 1
        hits += bool(want)
    # Positive control: the generator really produces matches, or the
    # comparison above would be vacuous.
    assert hits > 200, f"{name}: only {hits} of {checked} inputs contained a match"


def test_real_tokens_and_near_misses():
    lin = LinearJwtPattern(10, 10, 10)
    tok = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJub3Jkd2luZCJ9.c2lnbmF0dXJlLW5vcmR3aW5k"
    assert lin.search(f"Authorization: Bearer {tok}").group() == tok
    assert lin.search(f"x-{tok}") is not None          # after a dash: kept (regex \\b allows it)
    assert lin.search(f"x{tok}") is None               # glued to a word char: not a start
    assert lin.search(tok.replace(".", "", 1)) is None  # two segments only


def test_linear_on_the_pathological_input():
    """The regex needs ~0.73 s for 80 KB and grows x16 per x4; the matcher must
    stay linear: 1 MiB of 'eyJ-' well under a second."""
    lin = LinearJwtPattern(10, 10, 10)
    x = "eyJ-" * (1 << 18)
    t = time.perf_counter()
    assert lin.search(x) is None
    assert time.perf_counter() - t < 1.0
    y = "eyJ-" * (1 << 16) + "." + "a" * 20 + "." + "-" * (1 << 16)   # long third run, no boundary
    t = time.perf_counter()
    lin.search(y)
    assert time.perf_counter() - t < 1.0


def test_backrefs_in_replacements_are_refused_not_misapplied():
    with pytest.raises(ValueError):
        LinearJwtPattern(8, 8, 8).sub(r"\g<0>", "eyJaaaaaaaaaa.bbbbbbbbbb.cccccccccc")
