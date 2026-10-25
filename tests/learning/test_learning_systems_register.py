"""Checks the learning-system register (core/learning/learning_systems.yaml).

ADR-2281 §2 (PLAN-0964). The register is the list of every learning system with
its production callers. These tests check that the register is well-formed and
that each recorded caller still exists at the recorded line. They do not check
that a caller really runs: that is the job of the E2E tests named in `tests`.

Per state: every A has a caller; every B has a caller and an `intake` (the
condition under which its output may act); every D names the decision that
retires it (`retired_by`, an ADR id that must resolve in Corvin-Knowledge) and
still has at least one of its `modules` on disk (the entry leaves the register
with its last module). The core/vibe_engineering classification (ADR-2281 §6)
is checked against the reachability baseline.
"""

import json
import os
import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
REGISTER = REPO / "core" / "learning" / "learning_systems.yaml"
REACH_BASELINE = REPO / "scripts" / "learning_reachability_baseline.json"
STATES = {"A", "B", "D"}
ADR_ID = re.compile(r"^ADR-\d{4}$")


def _decisions_dir() -> Path | None:
    """The Corvin-Knowledge decisions directory, or None when no checkout is
    reachable: CORVIN_KB_REPO, a ``Corvin-Knowledge`` sibling of the repo or of
    any parent (a worktree lives below the main checkout), then the
    ``corvin_decisions`` submodule."""
    cands = []
    if os.environ.get("CORVIN_KB_REPO"):
        cands.append(Path(os.environ["CORVIN_KB_REPO"]) / "decisions")
    cands += [p / "Corvin-Knowledge" / "decisions" for p in REPO.parents]
    cands.append(REPO / "corvin_decisions" / "decisions")
    for c in cands:
        if c.is_dir() and any(c.glob("ADR-*.md")):
            return c
    return None


def _load() -> list[dict]:
    data = yaml.safe_load(REGISTER.read_text(encoding="utf-8"))
    return data["systems"]


def _callers() -> list[tuple[str, str, str]]:
    out = []
    for system in _load():
        for ref in system.get("callers", []):
            path, _, line = ref.rpartition(":")
            out.append((system["id"], path, line))
    return out


def test_register_exists_and_parses():
    assert REGISTER.is_file()
    assert _load(), "register has no systems"


def test_ids_are_unique_and_states_valid():
    systems = _load()
    ids = [s["id"] for s in systems]
    assert len(ids) == len(set(ids)), "duplicate system id"
    for s in systems:
        assert s["state"] in STATES, f"{s['id']}: unknown state {s['state']!r}"
        assert s.get("name"), f"{s['id']}: missing name"


def test_live_systems_have_a_caller():
    for s in _load():
        if s["state"] == "A":
            assert s.get("callers"), f"{s['id']} is A but lists no caller"


@pytest.mark.parametrize(("sys_id", "path", "line"), _callers())
def test_caller_exists_at_recorded_line(sys_id, path, line):
    target = REPO / path
    assert target.is_file(), f"{sys_id}: caller file missing: {path}"
    assert line.isdigit(), f"{sys_id}: caller line not a number: {line!r}"
    n_lines = len(target.read_text(encoding="utf-8", errors="replace").splitlines())
    assert 1 <= int(line) <= n_lines, (
        f"{sys_id}: {path}:{line} is past the end of the file ({n_lines} lines)"
    )


@pytest.mark.parametrize(
    ("sys_id", "test_path"),
    [(s["id"], t) for s in _load() for t in s.get("tests", [])],
)
def test_listed_test_file_exists(sys_id, test_path):
    assert (REPO / test_path).is_file(), f"{sys_id}: test file missing: {test_path}"


def test_shadow_systems_state_their_intake():
    for s in _load():
        if s["state"] == "B":
            assert s.get("callers"), f"{s['id']} is B but lists no caller"
            assert str(s.get("intake") or "").strip(), (
                f"{s['id']} is B but has no intake: state when its output may act")


def test_dead_systems_name_their_retiring_decision():
    for s in _load():
        if s["state"] != "D":
            continue
        assert ADR_ID.match(str(s.get("retired_by") or "")), (
            f"{s['id']} is D but names no retiring ADR id (retired_by)")
        assert s.get("modules"), f"{s['id']} is D but lists no modules"


def _matches(pattern: str) -> list[Path]:
    if any(ch in pattern for ch in "*?["):
        if pattern.endswith("**"):   # "dir/**" yields directories only on 3.11
            pattern += "/*"
        return [p for p in REPO.glob(pattern) if p.is_file()]
    p = REPO / pattern
    return [p] if p.is_file() else []


@pytest.mark.parametrize("sys_id", [s["id"] for s in _load() if s["state"] == "D"])
def test_dead_entry_still_has_a_module(sys_id):
    s = next(x for x in _load() if x["id"] == sys_id)
    left = [m for m in s["modules"] if _matches(m)]
    assert left, (f"{sys_id}: every module is gone; remove the D entry in the same "
                  f"change that deleted its last module ({s['retired_by']})")


def test_retiring_decisions_resolve_in_corvin_knowledge():
    ids = sorted({s["retired_by"] for s in _load() if s["state"] == "D"})
    if not ids:
        pytest.skip("no D entry")
    d = _decisions_dir()
    if d is None:
        pytest.skip("no Corvin-Knowledge checkout reachable (CORVIN_KB_REPO, a sibling "
                    "Corvin-Knowledge/, or the corvin_decisions submodule)")
    for adr in ids:
        assert list(d.glob(f"{adr}-*.md")), f"{adr} does not resolve in {d}"


def test_vibe_engineering_unreachable_modules_are_classified():
    """ADR-2281 §6: each unreachable core/vibe_engineering module is classified
    as learning (a register entry lists it) or not-learning (with a reason)."""
    data = yaml.safe_load(REGISTER.read_text(encoding="utf-8"))
    not_learning = {c["path"]: c for c in data.get("classified_not_learning", [])}
    for c in not_learning.values():
        assert str(c.get("reason") or "").strip(), f"{c['path']}: no reason"
        assert (REPO / c["path"]).is_file(), (
            f"{c['path']} is gone; drop its classification")
    in_register = {str(p.relative_to(REPO)) for s in data["systems"]
                   for m in s.get("modules", []) for p in _matches(m)}
    baseline = json.loads(REACH_BASELINE.read_text(encoding="utf-8"))["unreachable"]
    ve = [b for b in baseline if b.startswith("core/vibe_engineering/")]
    assert ve, "positive control: the baseline lists no core/vibe_engineering module"
    for path in ve:
        assert path in not_learning or path in in_register, (
            f"{path} is unreachable and not classified (learning or not-learning)")
