"""Does anything actually invoke an extension point? (ADR-0251)

`test_extension_points.py` proves the bus works.  `test_structural_guards.py`
proves it cannot be abused across tenants.  Neither proves anything *calls* it,
and as of 2026-07-27 nothing did: every `invoke()` in the tree lived in this
directory.  A plugin could register a hook on `workflow.workflow_gate`, see it
accepted, see it in `describe()`, and it would never run.

That is the third occurrence of one defect class in this ADR series — ADR-0233
named it in the prototype it retired, then found it twice in its own
implementation.  So this module mirrors `test_surface_map.py`'s call-site half
for the extension-point axis:

* every point either has a production call site or is recorded in
  :data:`_UNWIRED_POINTS` as knowingly inert;
* every point in that set is re-checked, so wiring one up turns this suite red
  and forces the record to be corrected in the same commit.  A test that breaks
  on GOOD news is the only version that stays true without anyone remembering;
* the search itself has a positive control, because a broken pattern would make
  every assertion here pass by finding nothing.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from corvin_plugins.extension_points import KNOWN_EXTENSION_POINTS

_REPO = Path(__file__).resolve().parents[3]


#: Paths that describe the bus rather than use it.  A hit inside the plugins
#: package, its tests, or a template is not evidence of a call site.
_NOT_A_CALL_SITE = (
    "core/plugins/corvin_plugins/",
    "core/plugins/tests/",
    "core/plugins/templates/",
    "tests/",
)

#: Points with no production caller, recorded rather than incidental.
#:
#: ADR-0251 wires all four.  Emptying this set is the deliverable — do not add
#: to it to make a red test green.  A point JOINING this set means a call site
#: was deleted and an extension point went dark, which is a regression wearing
#: the same red as an improvement.
_UNWIRED_POINTS: frozenset[str] = frozenset()

#: Wired, with the module that calls each one. Not decoration: it is what makes
#: a DELETED call site distinguishable from a point that was never wired, since
#: both look the same to the grep above.
#:
#: All four landed 2026-07-27 (ADR-0251 D1):
#:
#: * ``engine.engine_selection`` — ``shared/delegation_policy.py``
#:   (``resolve_worker_engine``). A hook may confirm the bundled route or
#:   de-escalate to ``native``; never escalate.
#: * ``delegation.route_selection_policy`` — ``shared/delegation_policy.py``
#:   (``resolve_delegation_route``). A hook may suppress delegation; never cause
#:   it.
#: * ``engine.model_selection`` — ``shared/model_selector.py``
#:   (``resolve_step_model``). A hook may name any model in the engine's
#:   registry and nothing else.
#: * ``workflow.workflow_gate`` — ``corvin_console/routes/workflows.py``
#:   (``_stream_run``, before the ``dry_run`` branch). Conjunction with the core
#:   gate: a hook may only ever be more restrictive.
_WIRED_POINTS: frozenset[str] = frozenset(KNOWN_EXTENSION_POINTS)


def _grep_repo(pattern: str, *, exclude: tuple[str, ...]) -> list[str]:
    """Search tracked .py files, returning 'path:line' hits.

    git grep rather than a walk, so the search matches what is committed rather
    than whatever stray files exist in the worktree — same reasoning as
    ``test_surface_map._grep_repo``.
    """
    try:
        out = subprocess.run(
            ["git", "grep", "-n", "-E", pattern, "--", "*.py"],
            cwd=_REPO,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover
        pytest.skip(f"git grep unavailable: {exc}")
    return [
        line
        for line in out.stdout.splitlines()
        if not any(frag in line for frag in exclude)
    ]


def _call_site_pattern(point: str) -> str:
    """Regex matching a real invocation of ``point``.

    The point name is quoted at the call site — ``invoke("workflow.workflow_gate",
    ...)`` — so the literal string is the reliable anchor.  Dots are escaped
    because an unescaped ``.`` would let ``engine.model_selection`` match
    ``engineXmodel_selection`` and, more importantly, would keep matching after
    a rename.
    """
    literal = point.replace(".", r"\.")
    return rf"""["']{literal}["']"""


def test_grep_finds_a_known_reference():
    """Positive control for the search itself.

    Without it, a broken pattern makes every "is still unwired" assertion pass by
    finding nothing, and this module reports health it never checked.  The
    control has to point at something that is true *today* and stays true: the
    bus declares each point's spec in ``extension_points.py``, so searching with
    the exclusions lifted must find it there.
    """
    for point in sorted(KNOWN_EXTENSION_POINTS):
        hits = _grep_repo(_call_site_pattern(point), exclude=())
        assert any("extension_points.py" in h for h in hits), (
            f"the search found no reference to {point!r} even in the module that "
            f"declares it — the grep pattern is broken, so every assertion in "
            f"this file is vacuous. hits={hits}"
        )


@pytest.mark.parametrize("point", sorted(KNOWN_EXTENSION_POINTS))
def test_point_is_wired_or_recorded_as_unwired(point):
    """Every declared point either has a caller or is knowingly inert.

    This is the assertion that would have caught the original defect: four
    points shipped, tested, and documented, with `invoke()` appearing nowhere
    outside this directory.
    """
    hits = _grep_repo(_call_site_pattern(point), exclude=_NOT_A_CALL_SITE)
    if point in _UNWIRED_POINTS:
        pytest.skip(f"{point} is recorded as unwired; see the reverse assertion")
    assert hits, (
        f"{point} has no production call site and is not recorded in "
        f"_UNWIRED_POINTS. A registered hook on it would never run, and nothing "
        f"would say so. Either wire it or record it deliberately."
    )


@pytest.mark.parametrize("point", sorted(_UNWIRED_POINTS))
def test_unwired_point_is_still_unwired(point):
    """Fails when a point gains a caller without the record being corrected.

    Deliberately a test that breaks on an improvement — wiring these four is
    exactly what ADR-0251 asks for.  When it happens, remove the point from
    :data:`_UNWIRED_POINTS` in the same commit as the call site.
    """
    hits = _grep_repo(_call_site_pattern(point), exclude=_NOT_A_CALL_SITE)
    assert not hits, (
        f"{point} is recorded as unwired but these look like call sites now — "
        f"remove it from _UNWIRED_POINTS:\n  " + "\n  ".join(hits[:5])
    )


def test_the_unwired_set_names_only_real_points():
    """A stale entry here would silence the wiring assertion for a live point."""
    unknown = _UNWIRED_POINTS - KNOWN_EXTENSION_POINTS
    assert not unknown, (
        f"_UNWIRED_POINTS names points that do not exist: {sorted(unknown)}. "
        f"A renamed point leaves its old name here, where it protects nothing "
        f"and hides the new name's missing call site."
    )


def test_the_two_sets_partition_the_known_points():
    """Every point is recorded as exactly one of wired / unwired.

    Without this, removing a name from `_UNWIRED_POINTS` and forgetting to add
    it to `_WIRED_POINTS` leaves it in neither — and the only assertion left
    covering it is the generic "has a call site" one, which is precisely what a
    deleted call site would fail silently in the other direction.
    """
    both = _WIRED_POINTS & _UNWIRED_POINTS
    assert not both, f"points recorded as both wired and unwired: {sorted(both)}"
    missing = KNOWN_EXTENSION_POINTS - _WIRED_POINTS - _UNWIRED_POINTS
    assert not missing, (
        f"points in neither record: {sorted(missing)}. Wiring one means moving "
        f"its name from _UNWIRED_POINTS to _WIRED_POINTS in the same commit as "
        f"the call site."
    )


@pytest.mark.parametrize("point", sorted(_WIRED_POINTS))
def test_a_wired_point_still_has_its_call_site(point):
    """Fails when a call site is DELETED and the record is not corrected.

    The reverse of `test_unwired_point_is_still_unwired`, and the reason both
    directions are needed: a point silently losing its caller is a regression
    that looks exactly like a point that was never wired.
    """
    hits = _grep_repo(_call_site_pattern(point), exclude=_NOT_A_CALL_SITE)
    assert hits, (
        f"{point} is recorded as wired but has no production call site any "
        f"more. A registered hook on it would never run, and nothing else "
        f"would say so."
    )
