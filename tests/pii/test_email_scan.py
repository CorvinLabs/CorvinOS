"""core.pii.email_scan — same matches as the RFC5322 regex, linear time.

Checked differentially against the VERBATIM original regex
(``core.pii.patterns.EMAIL_RFC5322_SOURCE``) on random inputs biased toward
the hard cases: runs with a boundary at every other character, several ``@``,
Unicode word characters next to runs, dotted and dashed domains.
"""
from __future__ import annotations

import random
import re
import time

import pytest

from core.pii.email_scan import LinearEmailPattern
from core.pii.patterns import EMAIL_RFC5322, EMAIL_RFC5322_SOURCE, PII_PATTERNS

ORIGINAL = re.compile(EMAIL_RFC5322_SOURCE)


def test_the_registry_serves_the_linear_matcher():
    assert isinstance(EMAIL_RFC5322, LinearEmailPattern)
    assert PII_PATTERNS["email"].pattern is EMAIL_RFC5322


def _inputs(seed: int, count: int):
    rnd = random.Random(seed)
    local = "aZ9._-+'!~"
    dom = "aZ9-"
    noise = ["", " ", "-", ".", "ä", "@", "x", "\n", "_", "é", "@@"]

    def run(chars: str, lo: int, hi: int) -> str:
        return "".join(rnd.choice(chars) for _ in range(rnd.randint(lo, hi)))

    for _ in range(count):
        if rnd.random() < 0.35:
            yield "".join(rnd.choice(["a", "-", ".", "@", "ä", " ", "b.cc", "x@y.zz", "_"]) for _ in range(rnd.randint(0, 50)))
            continue
        parts = []
        for _ in range(rnd.randint(1, 3)):
            labels = ".".join(run(dom, 1, 6) for _ in range(rnd.randint(1, 3)))
            tld = run("abcXYZ", 0, 4)
            parts.append(rnd.choice(noise) + run(local, 0, 8) + "@" + labels + "." + tld + rnd.choice(noise))
        yield "".join(parts)


def test_same_spans_and_substitutions_as_the_original():
    hits = 0
    for text in _inputs(seed=2026_10_08, count=40_000):
        want = [m.span() for m in ORIGINAL.finditer(text)]
        got = [m.span() for m in EMAIL_RFC5322.finditer(text)]
        assert got == want, text
        assert EMAIL_RFC5322.sub("[EMAIL]", text) == ORIGINAL.sub("[EMAIL]", text), text
        hits += bool(want)
    assert hits > 5_000, f"only {hits} inputs contained an address — the comparison would be thin"


@pytest.mark.parametrize("unit", ["a-", "a.", "eyJ-", "a+"])
def test_linear_on_boundary_dense_runs(unit):
    x = unit * (1 << 18)                     # 512 KB-1 MiB; the regex needs minutes
    t = time.perf_counter()
    assert EMAIL_RFC5322.search(x) is None
    assert EMAIL_RFC5322.search(x + "@nordwind.de").group().endswith("@nordwind.de")
    assert time.perf_counter() - t < 1.5


def test_many_at_signs_and_long_domains_stay_linear():
    x = ("a@" + "b-" * 30 + "c.") * 20_000   # many '@', dashed labels, no TLD
    t = time.perf_counter()
    EMAIL_RFC5322.search(x)
    assert time.perf_counter() - t < 1.5


def test_real_addresses():
    for addr in ("ops@nordwind-logistik.de", "first.last+tag@sub.example.co.uk", "o'brien@x.io"):
        assert EMAIL_RFC5322.search(f"Kontakt: {addr}, danke").group() == addr
    assert EMAIL_RFC5322.search("user@localhost") is None
