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


# ── second refutation round (2026-10-08): R4-R-1/2/3 ─────────────────────────

import importlib  # noqa: E402
import sys  # noqa: E402

from core.pii.jwt_scan import LinearJwtHeader  # noqa: E402

TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJub3Jkd2luZCJ9.c2lnbmF0dXJlLW5vcmR3aW5k"


@pytest.mark.parametrize("pos,endpos", [(-5, None), (0, -3), (-1, -1), (3, 2), (10_000, None), (0, 10_000)])
@pytest.mark.parametrize("make", [
    lambda: (LinearJwtPattern(10, 10, 10), _regex(10, 10, 10, True, True)),
    lambda: (LinearJwtPattern(1, 1, 1, start_boundary=False, end_boundary=False, ignore_case=True),
             re.compile(r"eyJ[a-zA-Z0-9_\-]+\.[a-zA-Z0-9_\-]+\.[a-zA-Z0-9_\-]+", re.IGNORECASE)),
])
def test_R4_R3_negative_and_oversized_pos_endpos_clamp_like_re(make, pos, endpos):
    lin, rx = make()
    text = f"x {TOKEN} y {TOKEN.upper().replace('EYJ', 'eyJ')}"
    kw = {"endpos": endpos} if endpos is not None else {}
    want = [m.span() for m in rx.finditer(text, pos, **kw)] if endpos is not None else [m.span() for m in rx.finditer(text, pos)]
    got = [m.span() for m in lin.finditer(text, pos, **kw)] if endpos is not None else [m.span() for m in lin.finditer(text, pos)]
    assert got == want


def test_R4_R2_ignore_case_matches_the_IGNORECASE_original():
    rx = re.compile(r"eyJ[a-zA-Z0-9_\-]+\.[a-zA-Z0-9_\-]+\.[a-zA-Z0-9_\-]+", re.IGNORECASE)
    lin = LinearJwtPattern(1, 1, 1, start_boundary=False, end_boundary=False, ignore_case=True)
    rnd = random.Random(11)
    hits = 0
    for _ in range(30_000):
        parts = [rnd.choice(["eyJ", "EYJ", "eyj", "EyJ", "a", "-", ".", "Z", "9", " ", "ä"]) * rnd.choice((1, 1, 2, 5)) for _ in range(rnd.randint(0, 30))]
        text = "".join(parts)
        want = [m.span() for m in rx.finditer(text)]
        assert [m.span() for m in lin.finditer(text)] == want, text
        hits += bool(want)
    assert hits > 250   # the generator really produces matches


def test_R4_R2_header_matcher_equals_the_htrace_telemetry_alternative():
    rx = re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.")
    lin = LinearJwtHeader(6)
    rnd = random.Random(12)
    hits = 0
    for _ in range(40_000):
        parts = [rnd.choice(["eyJ", "-", ".", "a", "Z", "9", "_", " ", "ä", "eyJabcdefg"]) * rnd.choice((1, 1, 2, 4)) for _ in range(rnd.randint(0, 30))]
        text = "".join(parts)
        m = rx.search(text)
        g = lin.search(text)
        assert (m.span() if m else None) == (g.span() if g else None), text
        hits += bool(m)
    assert hits > 250   # the generator really produces header matches


def _orig_alternation(rest_pattern: str) -> "re.Pattern[str]":
    """The module's regex BEFORE the split: the JWT-header alternative put back in."""
    return re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.|" + rest_pattern)


@pytest.mark.parametrize("module,attrs", [
    ("corvin_core.aco.htrace", ("_PII", "_PII_NO_LONGHEX")),
    ("corvin_core.aco.telemetry", ("_LEAK",)),
])
def test_R4_R2_htrace_and_telemetry_scanners_judge_exactly_as_before(module, attrs):
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2] / "core" / "console"))
    mod = importlib.import_module(module)
    assert mod._JWT_HDR is not None, "the linear header scan is not active"
    rnd = random.Random(13)
    atoms = ["eyJ", "eyJabcdefgh", "-", ".", "a", "9", " ", "@", "/home/", "AKIA-", "sk_", "ä", "10.0.0.1", "_"]
    for attr in attrs:
        rest = getattr(mod, attr)
        orig = _orig_alternation(rest.pattern)
        hits = 0
        for _ in range(25_000):
            text = "".join(rnd.choice(atoms) * rnd.choice((1, 1, 3)) for _ in range(rnd.randint(0, 25)))
            old = bool(orig.search(text))
            new = bool(mod._JWT_HDR.search(text) or rest.search(text))
            assert new == old, (module, attr, text)
            hits += old
        assert hits > 500


def test_R4_R1_validate_rejects_a_jwt_glued_to_a_non_ascii_letter():
    """The old ASCII-boundary check rejected these; a Unicode \\b did not (511 of 1M fuzz cases)."""
    from core.forge_bundle import validate

    for glue in ("é", "²", "ß", "٣", "ǅ"):
        with pytest.raises(validate.BundleRejected) as exc:
            validate._scan_text(f"token={glue}{TOKEN}", "tool x")
        assert exc.value.stage == "secrets"
    validate._scan_text("harmless text without credentials", "tool x")   # positive control


def test_R4_I9_every_migrated_call_site_still_redacts_or_flags_a_jwt():
    """A unit test of the matcher proves the matcher; this proves each production
    function still uses it (the 35 failing tool_ranking tests cannot say so — they
    fail on a missing pytest-asyncio before reaching it)."""
    from core.learning import tool_ranking
    from core.learning.outcome_feedback import OutcomeFeedbackStore  # noqa: F401
    from core.pii.sensitive import detect_sensitive_types

    assert "REDACTED" in tool_ranking._scrub_text(f"failed with {TOKEN}")
    assert TOKEN not in tool_ranking._scrub_text(f"failed with {TOKEN}")
    assert "jwt" in detect_sensitive_types(f"Authorization: {TOKEN}")
    from core.skills import skill_registry_phase1 as reg
    assert reg._PII_PATTERNS["jwt"].search(TOKEN)

    sys.path[:0] = [str(__import__("pathlib").Path(__file__).resolve().parents[2] / p)
                    for p in ("core/console", "core/observability", "corvin_operator/bridges/shared")]
    from corvin_core.aco import error_signature
    from corvin_logging import scrubber
    import debug_logging
    assert TOKEN not in error_signature.scrub(f"boom {TOKEN}")
    assert TOKEN not in scrubber.scrub_text(f"boom {TOKEN}")[0]
    assert TOKEN not in debug_logging.redact(f"boom {TOKEN}")

    from core.skills.os_skills.data_hub.security.scanner import SecurityScanner
    _, issues = SecurityScanner().scan_text(f"key {TOKEN.upper().replace('EYJ', 'eyJ')}")
    assert any(i.pattern_name == "jwt_token" for i in issues)
    from core.learning.outcome_feedback import OutcomeRecorder
    assert OutcomeRecorder._contains_potential_secret(None, TOKEN.upper().replace("EYJ", "eyJ"))
    assert not OutcomeRecorder._contains_potential_secret(None, "all good, thanks")


def test_R4_R2_the_migrated_scanners_are_linear_on_eyJ_dash_runs():
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2] / "core" / "console"))
    from corvin_core.aco import htrace, telemetry
    x = "eyJ-" * (1 << 17)                        # 512 KB: the original takes ~12 s here
    t = time.perf_counter()
    assert htrace._JWT_HDR.search(x) is None and htrace._PII.search(x) is None
    assert htrace._PII_NO_LONGHEX.search(x) is None
    assert telemetry._JWT_HDR.search(x) is None and telemetry._LEAK.search(x) is None
    assert time.perf_counter() - t < 1.5
