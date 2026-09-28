"""ADR-0703 ``compute.run`` on the console compute surfaces (adversarial review
2026-09-28).

``routes/{compute,flows,workflows}.py`` imported the BARE
``license.capability_api`` — a second module object next to the canonical
``corvin_operator.license.capability_api`` every other console gate uses.
Where it imported, ``POST /flows/trigger`` took the capability verdict and
never charged the daily ``compute_units_per_day`` counter; ``submit_run``
charged twice when it did not import; ``start_run`` imported it unused.
All three now go through ``compute.require_compute_run``: canonical verdict,
then exactly one charge.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest
from fastapi import HTTPException

_CONSOLE = Path(__file__).resolve().parents[1]
if str(_CONSOLE) not in sys.path:
    sys.path.insert(0, str(_CONSOLE))

from corvin_console.routes import _compute_license_gate as G  # noqa: E402
from corvin_console.routes import compute as C  # noqa: E402

import corvin_operator.license.capability_api as CA  # noqa: E402


class _Rec:
    tenant_id = "_default"
    sid_fingerprint = "fp123456"


@pytest.fixture
def charges(monkeypatch):
    """Count charges on the REAL shared gate (only the counter write is stubbed)."""
    calls: list = []
    monkeypatch.setattr(G, "_COMPUTE_QUOTA_OK", True)
    monkeypatch.setattr(G, "_cq_increment", lambda *a, **kw: calls.append(kw))
    return calls


def _deny(monkeypatch):
    def _raise(capability, *_a, **_k):
        raise CA.LicenseDenied(capability, CA.Tier.FREE, "not_available_in_tier")
    monkeypatch.setattr(CA, "require_capability", _raise)


def test_allowed_compute_run_charges_the_daily_counter_once(monkeypatch, charges):
    monkeypatch.setattr(CA, "active_tier", lambda **_k: "member")
    C.require_compute_run("_default", "fp123456", audit_action="flows.trigger",
                          channel="flows", entry_point="test")
    assert len(charges) == 1
    assert charges[0]["chat_key"].startswith("flows:_default:")


def test_canonical_denial_is_402_and_charges_nothing(monkeypatch, charges):
    """Fails on the old import: patching the CANONICAL module had no effect on
    a route that consulted the bare ``license.capability_api`` copy."""
    _deny(monkeypatch)
    with pytest.raises(HTTPException) as ei:
        C.require_compute_run("_default", "fp123456", audit_action="x",
                              channel="console", entry_point="test")
    assert ei.value.status_code == 402
    assert ei.value.detail["error"] == "license_limit"
    assert charges == []


def test_enforcement_error_is_503_and_charges_nothing(monkeypatch, charges):
    def _boom(*_a, **_k):
        raise RuntimeError("tier store unreadable")
    monkeypatch.setattr(CA, "require_capability", _boom)
    with pytest.raises(HTTPException) as ei:
        C.require_compute_run("_default", "fp123456", audit_action="x",
                              channel="console", entry_point="test")
    assert ei.value.status_code == 503
    assert charges == []


def test_unimportable_licensing_module_is_503(monkeypatch, charges):
    monkeypatch.setitem(sys.modules, "corvin_operator.license.capability_api", None)
    with pytest.raises(HTTPException) as ei:
        C.require_compute_run("_default", "fp123456", audit_action="x",
                              channel="console", entry_point="test")
    assert ei.value.status_code == 503
    assert charges == []


def _flow_env(monkeypatch, tmp_path):
    """A flow definition on disk + stand-ins for the definition parser and the
    runner (their own behaviour is not under test; the gate order is)."""
    from corvin_console.routes import flows as F
    home = tmp_path / "home"
    fdir = home / "tenants" / "_default" / "global" / "flows" / "demo"
    fdir.mkdir(parents=True)
    (fdir / "flow.yaml").write_text("name: demo\n", encoding="utf-8")
    monkeypatch.setenv("CORVIN_HOME", str(home))
    fake_paths = types.ModuleType("paths")
    fake_paths.corvin_home = lambda: home
    monkeypatch.setitem(sys.modules, "paths", fake_paths)
    fd = types.ModuleType("flow_definition")
    fd.FlowDefinition = type("FD", (), {"from_file": staticmethod(lambda p: object())})
    monkeypatch.setitem(sys.modules, "flow_definition", fd)
    started: list = []

    class _Runner:
        def __init__(self, *_a):
            self._run_id = "run_test"
            started.append(self)

        def run(self):
            return None

    fr = types.ModuleType("flow_runner")
    fr.FlowRunner = _Runner
    monkeypatch.setitem(sys.modules, "flow_runner", fr)
    monkeypatch.setattr(F, "_runs_dir", lambda tid: tmp_path / "runs")
    monkeypatch.setattr(F.console_audit, "action_performed", lambda **_k: None)
    return F, started


def test_flow_trigger_charges_the_daily_counter(monkeypatch, tmp_path, charges):
    """FLOW-COMPUTE-01 on the route itself: an allowed trigger charges once."""
    monkeypatch.setattr(CA, "active_tier", lambda **_k: "member")
    F, started = _flow_env(monkeypatch, tmp_path)
    out = F.trigger_flow_run("demo", rec=_Rec(), body=None)
    assert out["status"] == "started"
    assert len(charges) == 1
    assert len(started) == 1


def test_flow_trigger_denied_starts_nothing(monkeypatch, tmp_path, charges):
    F, started = _flow_env(monkeypatch, tmp_path)
    _deny(monkeypatch)
    with pytest.raises(HTTPException) as ei:
        F.trigger_flow_run("demo", rec=_Rec(), body=None)
    assert ei.value.status_code == 402
    assert charges == [] and started == []


def test_submit_run_charges_exactly_once(monkeypatch, tmp_path, charges):
    """submit_run used to charge up front AND again in its ImportError branch."""
    monkeypatch.setattr(CA, "active_tier", lambda **_k: "member")
    _compute_pkg = _CONSOLE.parents[0] / "compute"
    if str(_compute_pkg) not in sys.path:
        sys.path.insert(0, str(_compute_pkg))
    import corvin_compute.client as _client_mod

    sock = tmp_path / "worker.sock"
    sock.write_text("")
    monkeypatch.setattr(C, "_socket_path", lambda tid: sock)
    monkeypatch.setattr(C, "verify_reauth", lambda *a, **k: True, raising=False)

    class _Client:
        def __init__(self, _p):
            pass

        def submit_run(self, **_kw):
            return {"compute_handle": "run_x", "state": "running"}

    monkeypatch.setattr(_client_mod, "WorkerClient", _Client)
    body = C.SubmitRunRequest(tool_name="t", strategy="grid",
                              budget={"max_iterations": 1}, objective="minimize_loss",
                              params={"w": [0.0]})
    assert C.submit_run(body, rec=_Rec())["run_id"] == "run_x"
    assert len(charges) == 1
