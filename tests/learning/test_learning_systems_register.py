"""Checks the learning-system register (core/learning/learning_systems.yaml).

ADR-2281 §2 (PLAN-0964). The register is the list of every learning system with
its production callers. These tests check that the register is well-formed and
that each recorded caller ("path:line:needle") still names its reference: the
needle must appear within 3 lines of the recorded line (review c120,
s5_completeness-3). They do not check that a caller really runs: that is the job
of the E2E tests named in `tests`.

Per state: every A has a caller; every B has a caller and an `intake` (the
condition under which its output may act); every D names the decision that
retires it (`retired_by`, an ADR id that must resolve in Corvin-Knowledge) and
still has at least one of its `modules` on disk (the entry leaves the register
with its last module). The core/vibe_engineering classification (ADR-2281 §6)
is checked against the reachability baseline, and the register table in
docs/claude-ref/learning-loop.md must name the same state per system.

ADR-2303: every B entry carries a ``signal_contract``; a proxy (``proxy_of``
not ``none``) never has a ``target``; ``consumer`` is ``none`` or a resolving
``path:line:needle``; ``validity`` is numbers or ``unmeasured``. Form only, not truth.
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


# "path:line:needle" - the needle is a token of the referenced call/import/registration.
_REF = re.compile(r"^(?P<path>[\w./-]+\.\w+):(?P<line>\d+):(?P<needle>\S.*)$")
NEEDLE_WINDOW = 3   # the needle must sit within +/-3 lines of the recorded line


def _callers() -> list[tuple[str, str]]:
    return [(system["id"], ref) for system in _load() for ref in system.get("callers", [])]


_FILE_CACHE: dict[Path, list[str]] = {}


def _lines(target: Path) -> list[str]:
    if target not in _FILE_CACHE:
        _FILE_CACHE[target] = target.read_text(encoding="utf-8", errors="replace").splitlines()
    return _FILE_CACHE[target]


def _ref_violation(ref: str) -> str | None:
    """Why ``ref`` ("path:line:needle") does not point at its reference, or None.

    The cited line, or one within NEEDLE_WINDOW lines of it, must contain the needle: a
    reference that drifted onto a blank line, a comment or another router's include fails,
    and so does one whose call was deleted while the file kept its length."""
    m = _REF.match(str(ref))
    if not m:
        return f"{ref!r} is not path:line:needle"
    target = REPO / m["path"]
    if not target.is_file():
        return f"{m['path']} does not exist"
    lines, line, needle = _lines(target), int(m["line"]), m["needle"].strip()
    if not 1 <= line <= len(lines):
        return f"{m['path']}:{line} is past the end of the file ({len(lines)} lines)"
    window = lines[max(0, line - 1 - NEEDLE_WINDOW): line + NEEDLE_WINDOW]
    if not any(needle in text for text in window):
        hits = [i + 1 for i, text in enumerate(lines) if needle in text][:5]
        return (f"{m['path']}:{line} has no {needle!r} within {NEEDLE_WINDOW} lines "
                f"(the needle is at lines {hits or 'nowhere'})")
    return None


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


@pytest.mark.parametrize(("sys_id", "ref"), _callers())
def test_caller_exists_at_recorded_line(sys_id, ref):
    assert _ref_violation(ref) is None, f"{sys_id}: {_ref_violation(ref)}"


def test_caller_check_fails_on_a_shifted_line_and_a_wrong_needle():
    """Negative control on a copy of a real reference (the register is not touched):
    the same needle cited 40 lines off, or a needle that is not at the line, fails."""
    sys_id, ref = next((s, r) for s, r in _callers() if s == "L05")
    m = _REF.match(ref)
    assert m and _ref_violation(ref) is None, "positive control: the real L05 reference resolves"
    shifted = f"{m['path']}:{int(m['line']) + 40}:{m['needle']}"
    assert "has no" in (_ref_violation(shifted) or ""), f"{sys_id}: a shifted line passed"
    wrong = f"{m['path']}:{m['line']}:no_such_symbol_anywhere_here"
    assert "has no" in (_ref_violation(wrong) or "")
    assert "is not path:line:needle" in (_ref_violation(f"{m['path']}:{m['line']}") or "")


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


CONTRACT_KEYS = ("signal", "proxy_of", "validity", "consumer", "target", "guard", "control", "rollback")


def _contract_violations(systems: list[dict]) -> list[str]:
    """ADR-2303: every B entry carries a ``signal_contract`` (A may); a proxy is never a
    target; ``validity`` is numbers or ``unmeasured``; ``consumer`` is ``none`` or a
    ``path:line:needle`` that resolves like a caller; a signal that is not a proxy and
    ``writes`` a parameter names it as its ``target``. Returns one message per violation."""
    out: list[str] = []
    for s in systems:
        contract = s.get("signal_contract")
        if contract is None:
            if s["state"] == "B":
                out.append(f"{s['id']} is B but has no signal_contract")
            continue
        entries = contract if isinstance(contract, list) else [contract]
        if not entries or not all(isinstance(e, dict) for e in entries):
            out.append(f"{s['id']}: signal_contract must be a mapping or a non-empty list of mappings")
            continue
        for e in entries:
            where = f"{s['id']}" + (f"/{e['skill']}" if e.get("skill") else "")
            if isinstance(contract, list) and not str(e.get("skill") or "").strip():
                out.append(f"{where}: a contract in a list names its skill")
            for key in CONTRACT_KEYS:
                if e.get(key) is None or str(e.get(key)).strip() == "":
                    out.append(f"{where}: signal_contract lacks {key!r}")
            proxy, target = str(e.get("proxy_of") or ""), str(e.get("target") or "")
            if proxy and proxy != "none" and target != "none":
                out.append(f"{where}: a proxy (proxy_of={proxy!r}) cannot be a target ({target!r})")
            validity = e.get("validity")
            if isinstance(validity, dict):
                if not validity or not all(
                        isinstance(v, (int, float)) and not isinstance(v, bool) for v in validity.values()):
                    out.append(f"{where}: validity bounds must be numbers")
            elif validity != "unmeasured":
                out.append(f"{where}: validity is a mapping of numbers or 'unmeasured'")
            writes = e.get("writes")
            if writes is not None:
                if not str(writes).strip() or str(writes).strip() == "none":
                    out.append(f"{where}: 'writes' names the stored parameter (omit it when nothing is written)")
                elif proxy == "none" and target != str(writes).strip():
                    # a non-proxy signal that moves a parameter must call it its target
                    out.append(f"{where}: writes {writes!r} from a signal that is not a proxy, "
                               f"but its target is {target!r}")
            consumer = str(e.get("consumer") or "")
            if consumer != "none":
                why = _ref_violation(consumer)
                if why is not None:
                    out.append(f"{where}: consumer does not resolve: {why}")
            elif target != "none":
                out.append(f"{where}: a target ({target!r}) needs a named consumer")
    return out


def test_every_shadow_system_carries_a_valid_signal_contract():
    systems = _load()
    assert [s["id"] for s in systems if s["state"] == "B"], "positive control: no B entry"
    assert _contract_violations(systems) == []


def test_contract_check_catches_a_missing_contract_and_a_proxy_target():
    """Negative controls on copies of the real register (the file is not touched)."""
    import copy

    systems = _load()
    b_ids = [s["id"] for s in systems if s["state"] == "B"]
    for sys_id in b_ids:
        broken = copy.deepcopy(systems)
        next(s for s in broken if s["id"] == sys_id).pop("signal_contract", None)
        assert f"{sys_id} is B but has no signal_contract" in _contract_violations(broken)

    broken = copy.deepcopy(systems)
    l07 = next(s for s in broken if s["id"] == "L07")["signal_contract"]
    assert l07["proxy_of"] != "none", "positive control: L07 is recorded as a proxy"
    l07.update(target="stage_selection",
               consumer="core/skills/os_skills/delegation_router.py:143:_learned_threshold")
    assert any("cannot be a target" in v for v in _contract_violations(broken))

    broken = copy.deepcopy(systems)
    broken_l11 = next(s for s in broken if s["id"] == "L11")["signal_contract"]
    broken_l11[0].pop("skill")
    broken_l11[1]["validity"] = {"min_samples": "many"}
    broken_l11[2]["consumer"] = "core/does/not/exist.py:1:anything"
    broken_l11[3]["consumer"] = "core/skills/os_skills/delegation_router.py:143"   # no needle
    found = _contract_violations(broken)
    assert any("names its skill" in v for v in found)
    assert any("bounds must be numbers" in v for v in found)
    assert any("does not exist" in v for v in found)
    assert any("is not path:line:needle" in v for v in found)


def test_router_threshold_writers_are_registered_as_proxies():
    """Review c120 s5_completeness-2: ``confidence_threshold`` is moved by the operator
    star rating (stream4) and by the epoch test on the tenant's last 10 outcomes - both
    proxies of routing correctness. Every L05 row that writes it says so, keeps
    ``target: none``, and both writers are listed callers. The routing-ledger join (L12)
    is not a proxy and learns no parameter."""
    import copy

    systems = _load()
    l05 = next(s for s in systems if s["id"] == "L05")
    rows = l05["signal_contract"]
    writers = [r for r in rows if r.get("writes") == "confidence_threshold"]
    assert len(writers) == 2, "positive control: L05 records two threshold writers"
    for r in writers:
        assert r["proxy_of"] != "none" and r["target"] == "none", r["writer"]
    callers = " ".join(l05["callers"])
    assert "stream4_skill_feedback.py" in callers and "apply_outcome_to_skill" in callers
    assert "method_discovery_api.py" in callers and "run_optimizer_epoch" in callers
    l12 = next(s for s in systems if s["id"] == "L12")["signal_contract"]
    assert l12["proxy_of"] == "none" and l12["target"] == "none" and "writes" not in l12

    # Negative controls on copies: calling a proxy row a target, or relabelling it as
    # "not a proxy" while it keeps writing the threshold without naming it, both fail.
    broken = copy.deepcopy(systems)
    next(s for s in broken if s["id"] == "L05")["signal_contract"][0]["target"] = "confidence_threshold"
    assert any("L05/os.delegation_router: a proxy" in v for v in _contract_violations(broken))
    broken = copy.deepcopy(systems)
    next(s for s in broken if s["id"] == "L05")["signal_contract"][1]["proxy_of"] = "none"
    assert any("writes 'confidence_threshold' from a signal that is not a proxy" in v
               for v in _contract_violations(broken))


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


def test_docs_register_table_equals_the_register():
    """ADR-2281 / T-0262: docs/claude-ref/learning-loop.md carries the register's
    table; every A/B row there must name the same state as the register, every
    A/B system must have a row, and the D entries are summarised as one row."""
    doc = (REPO / "docs" / "claude-ref" / "learning-loop.md").read_text(encoding="utf-8")
    start, end = "<!-- register-table:start", "<!-- register-table:end -->"
    assert start in doc and end in doc, "the register table markers are missing from learning-loop.md"
    block = doc.split(start, 1)[1].split(end, 1)[0]
    rows = {}
    for line in block.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 3 and cells[0] not in ("Id", "---"):
            rows[cells[0]] = cells[2]
    systems = _load()
    for s in systems:
        if s["state"] == "D":
            continue
        assert rows.get(s["id"]) == s["state"], (
            f"learning-loop.md says {s['id']} is {rows.get(s['id'])!r}, the register says {s['state']!r}")
    d_ids = sorted(s["id"] for s in systems if s["state"] == "D")
    if d_ids:
        d_row = f"{d_ids[0]}–{d_ids[-1]}"
        assert rows.get(d_row) == "D", f"learning-loop.md lacks the D summary row {d_row!r}"
    known = {s["id"] for s in systems} | ({f"{d_ids[0]}–{d_ids[-1]}"} if d_ids else set())
    assert set(rows) <= known, f"learning-loop.md lists ids the register lacks: {set(rows) - known}"


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


@pytest.mark.parametrize("sys_id", ["L05", "L12"])
def test_router_entries_record_the_readiness_measurement(sys_id):
    """T-0280: the router readiness is measured with readiness.evaluate, dated, per surface."""
    entry = next(s for s in _load() if s["id"] == sys_id)
    m = entry.get("measured")
    assert m, f"{sys_id}: no readiness measurement recorded"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(m["date"])), f"{sys_id}: measurement date not ISO"
    for surface in ("bridge", "console"):
        row = m[surface]
        assert {"ready", "reason", "decisions", "join_rate"} <= set(row), f"{sys_id}: {surface} incomplete"
    # Phase 2 stays off here: a measurement that says ready must come with a proposed decision.
    assert not any(m[s]["ready"] for s in ("bridge", "console")) or "decision" in m["verdict"].lower()


READINESS_SRC = REPO / "core" / "skills" / "os_skills" / "monitoring" / "readiness.py"


def _readiness_reasons() -> set:
    """Every reason readiness.evaluate() can return, read from its source (a trailing
    colon marks the ``reason:<bucket>`` variants)."""
    src = READINESS_SRC.read_text(encoding="utf-8")
    fixed = set(re.findall(r'Readiness\((?:True|False),\s*"([a-z_]+)"', src))
    prefixed = set(re.findall(r'Readiness\(False,\s*f"([a-z_]+):\{', src))
    return fixed | {p + ":" for p in prefixed}


@pytest.mark.parametrize("sys_id", ["L05", "L12"])
def test_recorded_readiness_reasons_are_ones_the_evaluator_can_return(sys_id):
    reasons = _readiness_reasons()
    assert {"insufficient_samples", "console_soak_incomplete", "ready"} <= reasons, (
        "positive control: the reason scan found too little in readiness.py")
    m = next(s for s in _load() if s["id"] == sys_id)["measured"]
    for surface in ("bridge", "console"):
        row = m[surface]
        ok = row["reason"] in reasons or any(
            p.endswith(":") and row["reason"].startswith(p) for p in reasons)
        assert ok, f"{sys_id}/{surface}: {row['reason']!r} is not a reason evaluate() returns"
        assert row["ready"] == (row["reason"] == "ready")


def _stale_classifications(classified: set, unreachable: set) -> list:
    """Classified-as-not-learning paths that are no longer unreachable.

    A classification answers "is this unreachable module a learning system?". Once the module
    is wired (or deleted) the question is gone and the entry is dead weight that a reader
    would still trust; the ratchet baseline shrinks, so the classification must shrink with it."""
    return sorted(p for p in classified if p not in unreachable)


def test_not_learning_classifications_are_still_about_unreachable_modules():
    """T-0260: a core/vibe_engineering classification does not outlive its module's
    unreachability."""
    data = yaml.safe_load(REGISTER.read_text(encoding="utf-8"))
    classified = {c["path"] for c in data.get("classified_not_learning", [])}
    unreachable = set(json.loads(REACH_BASELINE.read_text(encoding="utf-8"))["unreachable"])
    assert classified, "positive control: the register classifies no module"
    assert _stale_classifications(classified, unreachable) == []
    # the detector itself: a path missing from the baseline is reported, one inside is not
    assert _stale_classifications({"a.py", "b.py"}, {"a.py"}) == ["b.py"]
