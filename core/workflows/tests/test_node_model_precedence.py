"""ADR-2308 / T-0327 — the node-model resolver and ``node_class``.

The resolver is a pure function over injected dependencies, so these tests need neither a
console nor a tenant home. The default-dependency wiring is exercised separately against the
real ``console_selection`` / ``helper_model`` modules.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from corvin_workflows import model_resolution as mr

_SHARED = str(Path(__file__).resolve().parents[3] / "corvin_operator" / "bridges" / "shared")


@pytest.fixture(autouse=True)
def _leave_no_trace():
    """The default-wiring tests import modules from ``bridges/shared`` and the bootstrap puts that
    directory on ``sys.path``. Other tests (the peer gate) resolve names differently once those
    are loaded, so undo all of it after every test."""
    before_mods, saved_path = set(sys.modules), list(sys.path)
    mr._ensure_shared_on_path()  # the same bootstrap production code uses, for the tests that import by name
    yield
    sys.path[:] = saved_path
    mr._DEPS = None
    for name in set(sys.modules) - before_mods:
        if (getattr(sys.modules.get(name), "__file__", None) or "").startswith(_SHARED):
            sys.modules.pop(name, None)


# Concrete ids used only inside the test; the resolver itself must hold none.
OPUS, SONNET, HAIKU = "opus-X", "sonnet-X", "haiku-X"
RANK = {HAIKU: 1, SONNET: 2, OPUS: 3}


class Deps:
    """Injected readers; every field is overridable per test."""

    def __init__(self, **kw):
        self.pin_value = kw.get("pin")
        self.env_value = kw.get("env")
        self.known = kw.get("known", {SONNET, OPUS, HAIKU})
        self.retired = kw.get("retired", {})
        self.high = kw.get("high", SONNET)
        self.autoselect_off = kw.get("autoselect_off", False)

    def pin(self, tenant_id):
        return self.pin_value

    def env(self):
        return self.env_value

    def registry_id(self, value):
        return value if value in self.known else None

    def tier(self, name):
        return {"haiku": HAIKU, "sonnet": SONNET, "opus": OPUS}[name]

    def rank(self, model):
        return RANK.get(model, 0)

    def current(self, model):
        return self.retired.get(model, model)

    def legacy_default(self, tenant_id):
        return self.high

    def autoselect_is_off(self):
        return self.autoselect_off


def node(**kw):
    base = {"id": "n1", "type": "agent", "agent": "a", "instructions": "x"}
    base.update(kw)
    return base


def row(**kw):
    base = {"model_id": HAIKU, "tier": "haiku", "r": SONNET, "gate_status": "adopt"}
    base.update(kw)
    return base


def resolve(n, *, deps=None, path="legacy", table=None):
    return mr.resolve_node_model(n, "_default", path, table or {}, deps=deps or Deps())


# ── scope ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("ntype", ["delegation_loop", "deliver", "ask_human", "peer_agent", "code"])
def test_excluded_types_are_untouched_even_with_a_pin(ntype):
    r = resolve(node(type=ntype, model=OPUS), deps=Deps(pin=OPUS))
    assert (r.model_id, r.source, r.gate_status, r.shadow_model_id) == ("", "site_default", "none", None)


# ── precedence ───────────────────────────────────────────────────────────────

def test_pin_beats_node_value_and_recommendation():
    r = resolve(node(model=OPUS), deps=Deps(pin=SONNET), table={"wf:agent": row()})
    assert (r.model_id, r.source) == (SONNET, "pin")


def test_node_value_beats_recommendation():
    r = resolve(node(model=OPUS), table={"wf:agent": row()})
    assert (r.model_id, r.source) == (OPUS, "node")


@pytest.mark.parametrize("name,expected", [("haiku", HAIKU), ("sonnet", SONNET), ("opus", OPUS)])
def test_tier_names_resolve_through_tier_model(name, expected):
    assert resolve(node(model=name)).model_id == expected


def test_unknown_node_value_is_rejected_never_replaced():
    with pytest.raises(mr.ModelUnknown) as ei:
        resolve(node(model="nonsense-9"), deps=Deps(pin=None))
    assert ei.value.reason_code == "model_unknown"


def test_retired_node_value_resolves_to_its_successor():
    deps = Deps(known={"old-1", OPUS}, retired={"old-1": OPUS})
    assert resolve(node(model="old-1"), deps=deps).model_id == OPUS


def test_no_node_value_and_nothing_else_changes_no_model():
    r = resolve(node())
    assert (r.model_id, r.source, r.gate_status, r.shadow_model_id) == ("", "site_default", "none", None)


# ── level 4 (site default) ───────────────────────────────────────────────────

def test_level4_variable_sets_the_model():
    r = resolve(node(), deps=Deps(env=OPUS))
    assert (r.model_id, r.source) == (OPUS, "site_default")


# ── level 3 (recommendation) ─────────────────────────────────────────────────

def test_adopt_row_acts_on_a_tools_free_node_when_cheaper_than_r():
    r = resolve(node(), table={"wf:agent": row()})
    assert (r.model_id, r.source, r.gate_status) == (HAIKU, "recommendation", "adopt")


def test_tools_node_stays_on_level4_with_gate_status_none_even_at_adopt():
    for key in ("tools", "forge_tools", "skills"):
        r = resolve(node(**{key: ["x"]}), table={"wf:agent": row()})
        assert (r.model_id, r.source, r.gate_status) == ("", "site_default", "none"), key


def test_shadow_row_runs_level4_and_reports_the_shadow_model():
    r = resolve(node(), deps=Deps(env=SONNET), table={"wf:agent": row(gate_status="shadow")})
    assert (r.model_id, r.source, r.gate_status, r.shadow_model_id) == (
        SONNET, "recommendation_shadow", "shadow", HAIKU)


def test_stale_row_does_not_act():
    # The measured id differs from what the tier resolves to today (lineage change).
    r = resolve(node(), table={"wf:agent": row(model_id="haiku-OLD")})
    assert (r.model_id, r.gate_status) == ("", "stale")


def test_rolled_back_row_does_not_act():
    r = resolve(node(), table={"wf:agent": row(gate_status="rolled_back")})
    assert (r.model_id, r.source, r.gate_status) == ("", "site_default", "rolled_back")


def test_row_whose_reference_differs_from_the_path_reference_does_not_act():
    r = resolve(node(), table={"wf:agent": row(r=OPUS)})
    assert (r.model_id, r.source) == ("", "site_default")


def test_level3_is_off_while_the_level4_variable_is_set():
    r = resolve(node(), deps=Deps(env=SONNET), table={"wf:agent": row()})
    assert (r.model_id, r.source) == (SONNET, "site_default")


def test_level3_is_off_with_autoselect_off():
    r = resolve(node(), deps=Deps(autoselect_off=True), table={"wf:agent": row()})
    assert (r.model_id, r.source) == ("", "site_default")


def test_level3_has_no_effect_on_the_dag_path_whose_reference_is_the_cheapest_tier():
    # DAG reference is haiku: there is no cheaper tier to recommend.
    r = resolve(node(), path="dag", table={"wf:agent": row(r=HAIKU)})
    assert (r.model_id, r.source) == ("", "site_default")


def test_level3_has_no_effect_on_the_legacy_path_while_its_default_is_unmeasured():
    # The CLI's own default is not a Corvin setting (measured: a tier above high_model). Unknown
    # reference -> no recommendation acts there, whatever the table says.
    r = resolve(node(), deps=Deps(high=None), table={"wf:agent": row(r=SONNET)})
    assert (r.model_id, r.source) == ("", "site_default")


def test_the_default_deps_do_not_invent_a_legacy_default():
    assert mr.DefaultDeps().legacy_default("_default") is None


def test_fan_out_uses_its_own_class_row():
    r = resolve(node(type="fan_out"), table={"wf:fan_out": row()})
    assert (r.model_id, r.source) == (HAIKU, "recommendation")


# ── node_class ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("n,plen,expected", [
    (node(), 100, "agent|n|<8k|json"),
    (node(), 8000, "agent|n|8k-32k|json"),
    (node(), 40000, "agent|n|>32k|json"),
    (node(tools=["a"]), 100, "agent|t|<8k|json"),
    (node(type="fan_out"), 100, "fan_out|n|<8k|json"),
    (node(), None, "agent|n||json"),
])
def test_node_class_golden(n, plen, expected):
    assert mr.node_class(n, plen) == expected


def test_gate_key_is_the_coarse_wf_type():
    assert mr.gate_key(node(type="fan_out")) == "wf:fan_out"


# ── source hygiene ───────────────────────────────────────────────────────────

def test_the_resolver_holds_no_model_id_literal():
    src = Path(mr.__file__).read_text(encoding="utf-8")
    assert "TIER_NAMES" in src
    assert re.search(r"claude-[a-z]+-\d", src) is None


# ── default wiring (the real readers) ────────────────────────────────────────

def test_default_deps_resolve_without_a_pin_to_the_untouched_path(monkeypatch):
    monkeypatch.delenv("CORVIN_HELPER_MODEL_WORKFLOW_NODE", raising=False)
    monkeypatch.delenv("CORVIN_OS_MODEL_AUTOSELECT", raising=False)
    r = mr.resolve_node_model(node(), "_default", "legacy", {})
    # Sandbox tenant: no operator pin, no variable, no node value -> nothing changes.
    assert (r.model_id, r.source, r.gate_status) == ("", "site_default", "none")


def test_default_deps_validate_a_tier_name_and_reject_nonsense(monkeypatch):
    monkeypatch.delenv("CORVIN_HELPER_MODEL_WORKFLOW_NODE", raising=False)
    r = mr.resolve_node_model(node(model="haiku"), "_default", "dag", {})
    assert r.source in ("node", "pin") and r.model_id
    with pytest.raises(mr.ModelUnknown):
        mr.resolve_node_model(node(model="definitely-not-a-model"), "_default", "dag", {})


# ── default wiring (real helper_model) ───────────────────────────────────────

def test_env_only_helper_model_reads_only_its_variable(monkeypatch):
    import helper_model as hm
    monkeypatch.delenv("CORVIN_HELPER_MODEL_WORKFLOW_NODE", raising=False)
    monkeypatch.setenv("CORVIN_HELPER_MODEL", "something-global")
    assert hm.env_only_helper_model("workflow_node") is None
    monkeypatch.setenv("CORVIN_HELPER_MODEL_WORKFLOW_NODE", "anthropic/some-model-1")
    assert hm.env_only_helper_model("workflow_node") == "some-model-1"


@pytest.mark.parametrize("val", ["", "none", "default", "off", "  OFF "])
def test_opt_out_values_count_as_not_set(monkeypatch, val):
    import helper_model as hm
    monkeypatch.setenv("CORVIN_HELPER_MODEL_WORKFLOW_NODE", val)
    assert hm.env_only_helper_model("workflow_node") is None


def test_workflow_node_is_not_a_registered_helper_site():
    import helper_model as hm
    assert "workflow_node" not in hm.ALL_SITES
