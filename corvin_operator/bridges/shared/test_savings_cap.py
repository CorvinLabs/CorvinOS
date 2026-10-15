"""S3 of PLAN-0955 / ADR-2259: effective cap resolution and evaluation.

Table-driven over a REAL chain written under a sandboxed CORVIN_HOME, so the
evaluator is exercised with the same reader the product uses.
"""
from __future__ import annotations

import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
for _p in (str(_REPO), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import savings_cap as sc  # noqa: E402
from core.learning import savings_meter as sm  # noqa: E402

HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-5"
NOW = time.time()


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    sm.reset_cache()
    monkeypatch.setattr(sm, "_epoch_ts", lambda tenant_id: 0.0)
    monkeypatch.setattr(sc, "_tier", lambda: "free")
    yield tmp_path
    sm.reset_cache()


def _chain(home: Path) -> Path:
    from core.paths.tenant import tenant_audit_chain
    p = tenant_audit_chain("_default")
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _turn(model, i, o, n):
    return json.dumps({"ts": NOW, "event_type": "os_turn.completed", "details": {
        "turn_id": f"t{n}", "model": model, "exit_code": 0,
        "input_tokens": i, "output_tokens": o}}) + "\n"


def seed(home, *, haiku=0, sonnet=0):
    """haiku turn: actual .006 vs cf .012 (saves 50 %); sonnet turn: saves 0."""
    with _chain(home).open("a", encoding="utf-8") as fh:
        for n in range(haiku):
            fh.write(_turn(HAIKU, 1000, 1000, f"h{n}"))
        for n in range(sonnet):
            fh.write(_turn(SONNET, 1000, 1000, f"s{n}"))
    sm.reset_cache()


def settings(home, **kw):
    p = home / "tenants" / "_default" / "global" / "cost_optimize.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(kw), encoding="utf-8")


def ev(**kw):
    return sc.evaluate("_default", default_model=SONNET, **kw)


# ── ceilings come from the capability matrix, not from a literal ─────────────

def test_ceiling_is_read_from_capabilities_not_hardcoded():
    from corvin_operator.license.limits import CAPABILITIES
    r, f = sc.ceilings()
    assert r == CAPABILITIES["cost.optimize"]["free"]["ratio"]
    assert f == CAPABILITIES["cost.optimize"]["free"]["floor_usd"]


# ── effective value ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("tier, choice, effective, clamped", [
    ("free",   None,  0.10, False),   # tier default
    ("free",   0.5,   0.10, True),    # may not raise
    ("free",   0.05,  0.05, False),   # may lower
    ("free",   0.0,   0.0,  False),   # automation off
    ("member", None,  None, False),   # unlimited
    ("member", 0.4,   0.4,  False),   # voluntary self-cap
    ("member", 0.0,   0.0,  False),
])
def test_effective_ratio_table(home, monkeypatch, tier, choice, effective, clamped):
    monkeypatch.setattr(sc, "_tier", lambda: tier)
    if choice is not None:
        settings(home, cap_ratio=choice)
    a = sc.resolve("_default")
    assert a.effective_ratio == effective
    assert a.clamped is clamped
    assert a.tier == tier


def test_free_floor_can_be_lowered_never_raised(home, monkeypatch):
    settings(home, floor_usd=1000)
    a = sc.resolve("_default")
    assert a.effective_floor == 5.0 and a.clamped is True
    settings(home, floor_usd=0.5)
    assert sc.resolve("_default").effective_floor == 0.5
    monkeypatch.setattr(sc, "_tier", lambda: "member")
    settings(home, floor_usd=1000)
    assert sc.resolve("_default").effective_floor == 1000


# ── evaluation ───────────────────────────────────────────────────────────────

def test_below_the_floor_is_never_capped(home):
    seed(home, haiku=10)                       # cf 0.12 USD << 5 USD floor
    s = ev(block=True)
    assert s.capped is False and s.reason == "below_floor"


def test_cap_reached_above_floor(home):
    settings(home, floor_usd=0.01)
    seed(home, haiku=10)                       # ratio 0.5 >= 0.10
    s = ev(block=True)
    assert s.capped is True and s.reason == "cap_reached"
    assert s.reading.ratio == pytest.approx(0.5)


def test_lower_choice_caps_earlier(home):
    settings(home, floor_usd=0.01, cap_ratio=0.05)
    seed(home, haiku=1, sonnet=12)             # saved .006 / cf .156 = 0.038
    assert ev(block=True).capped is False
    seed(home, haiku=2)                        # ratio now > 0.05
    assert ev(block=True).capped is True


def test_automation_off_is_capped_even_below_the_floor(home):
    settings(home, cap_ratio=0.0)
    s = ev(block=True)
    assert s.capped is True and s.reason == "automation_off"


def test_member_default_is_unlimited(home, monkeypatch):
    monkeypatch.setattr(sc, "_tier", lambda: "member")
    settings(home, floor_usd=0.01)
    seed(home, haiku=50)
    s = ev(block=True)
    assert s.capped is False and s.reason == "tier_unlimited"


def test_member_self_cap_applies(home, monkeypatch):
    monkeypatch.setattr(sc, "_tier", lambda: "member")
    settings(home, floor_usd=0.01, cap_ratio=0.4)
    seed(home, haiku=10)
    assert ev(block=True).capped is True


def test_dilution_resumes_the_automation(home):
    settings(home, floor_usd=0.01)
    seed(home, haiku=10)
    assert ev(block=True).capped is True
    seed(home, sonnet=200)                     # capped turns on the default model
    s = ev(block=True)
    assert s.capped is False and s.reason == "under_cap"


# ── fail contracts ───────────────────────────────────────────────────────────

def test_meter_failure_is_not_a_cap(home, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk")
    monkeypatch.setattr(sm, "read", boom)
    s = ev(block=True)
    assert s.capped is False and s.reason == "meter_unavailable"


def test_licence_failure_means_free_allowance_not_unlimited(home, monkeypatch):
    monkeypatch.undo()                         # use the real _tier with a broken licence
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setattr(sm, "_epoch_ts", lambda tenant_id: 0.0)
    monkeypatch.setattr(sc, "_licence_active_tier", lambda: (_ for _ in ()).throw(ImportError("x")))
    assert sc.resolve("_default").tier == "free"


def test_first_call_does_not_block_and_reports_warming(home):
    seed(home, haiku=3)
    s = ev(block=False)
    assert s.capped is False and s.reason == "meter_warming"
    deadline = time.time() + 5
    while time.time() < deadline:
        s = ev(block=False)
        if s.reason != "meter_warming":
            break
        time.sleep(0.02)
    assert s.reason in {"below_floor", "under_cap", "cap_reached"}


# ── hostile settings files never raise and never raise the ceiling ──────────

@pytest.mark.parametrize("payload", [
    "not json", "[]", "null", '{"cap_ratio": NaN}', '{"cap_ratio": Infinity}',
    '{"cap_ratio": true}', '{"cap_ratio": "0.9"}', '{"cap_ratio": -1}',
    '{"cap_ratio": 2}', '{"floor_usd": 1e999}', '{"floor_usd": -5}',
])
def test_hostile_settings_are_ignored(home, payload):
    p = home / "tenants" / "_default" / "global" / "cost_optimize.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(payload, encoding="utf-8")
    a = sc.resolve("_default")
    assert a.effective_ratio == 0.10 and a.effective_floor == 5.0


def test_validate_choice_rejects_what_the_reader_ignores():
    ok, errs = sc.validate_choice({"cap_ratio": 0.3, "floor_usd": 2})
    assert ok == {"cap_ratio": 0.3, "floor_usd": 2.0} and errs == []
    for bad in (math.nan, math.inf, -0.1, 1.1, True, "0.5", [0.1]):
        _, errs = sc.validate_choice({"cap_ratio": bad})
        assert errs, bad
    _, errs = sc.validate_choice({"unknown": 1})
    assert errs
    ok, errs = sc.validate_choice({"cap_ratio": None})     # null = tier default
    assert ok == {"cap_ratio": None} and errs == []


# ── ceilings(): tier branches and a missing "member" entry (fail-safe) ───────

def test_ceilings_free_tier_is_the_matrix_free_entry(monkeypatch):
    monkeypatch.setattr(sc, "_tier", lambda: "free")
    assert sc.ceilings() == (0.10, 5.0)


def test_ceilings_member_tier_is_unlimited(monkeypatch):
    monkeypatch.setattr(sc, "_tier", lambda: "member")
    assert sc.ceilings()[0] == 1.0


def test_ceilings_missing_member_entry_behaves_like_free(monkeypatch):
    monkeypatch.setattr(sc, "_tier", lambda: "member")
    monkeypatch.setattr(sc, "_capability",
                        lambda: {"free": {"limit": 0, "ratio": 0.10, "floor_usd": 5.0}})
    assert sc.ceilings() == (0.10, 5.0)
