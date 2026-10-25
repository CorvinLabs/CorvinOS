"""Checks the learning-system register (core/learning/learning_systems.yaml).

PLAN-0964, Welle 1. The register is the list of every learning system with its
production callers. These tests check that the register is well-formed and that
each recorded caller still exists at the recorded line. They do not check that
a caller really runs: that is the job of the E2E tests named in `tests`.
"""

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
REGISTER = REPO / "core" / "learning" / "learning_systems.yaml"
STATES = {"A", "B", "D"}


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
