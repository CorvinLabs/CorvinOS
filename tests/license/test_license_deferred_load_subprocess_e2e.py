"""A paying member is a member in EVERY process, not only in the one that booted the licence.

Defect (measured on a member install, 2026-11-06):

* ``forge_tool`` over the forge MCP stdio server answered
  ``license_required: forge.create denied (not_available_in_tier)`` and the
  chain recorded ``forge.create tier=free deny entry_point=mcp:forge_tool``;
  ``skill-forge:create`` recorded the same 20 times, while processes that had
  booted the licence recorded ``member allow``. ``load_license_from_env()`` ran
  only in the console/gateway/adapter boot; the MCP servers the engine spawns
  never called it, so ``active_tier()`` read "no licence" -> free.
* ``compute_run`` ran on the 500-iteration TRIAL (``compute.license.checked
  mode=trial tier=free``) because the compute gate's only licence source, the
  enterprise ``corvin_license`` package, was deleted in 853ee7c54.

These tests drive the real forge MCP server over stdio. The child pins ONE seam:
``license.validator.load_license_from_env`` is wrapped so that, WHEN it is
called, it activates a member licence (no signing key exists in a test). It is
never called by the launcher itself — so the test is red exactly when nothing
in the server process loads the licence, which is the defect. The gate, the
capability matrix, the compute licence gate and the audit chain run unmodified.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_FORGE_TESTS = Path(__file__).resolve().parents[2] / "corvin_operator" / "forge" / "tests"
sys.path.insert(0, str(_FORGE_TESTS))
import test_mcp as _harness  # noqa: E402

# The launcher wraps the loader; it does NOT call it and does NOT touch the
# tier resolver (unlike test_mcp._LAUNCH_WITH_TIER, which pinned active_tier and
# therefore could not see a licence that was never loaded).
_LAUNCH_WITH_MEMBER_LOADER = (
    "import runpy, sys\n"
    "import corvin_operator.license.validator as _v\n"
    "_real = _v.load_license_from_env\n"
    "def _load(*a, **k):\n"
    "    _real(*a, **k)\n"
    "    _v._set_active_license({{'tier': 'member', 'jti': 'e2e-deferred-load'}})\n"
    "_v.load_license_from_env = _load\n"
    "sys.argv = sys.argv[1:]\n"
    "runpy.run_path(sys.argv[0], run_name='__main__')\n"
)

IMPL = ("import json, sys\n"
        "x = float(json.loads(sys.stdin.read())['x'])\n"
        "print(json.dumps({'loss': (x - 3.0) ** 2}))\n")


# No seam at all: the server process loads whatever the sandbox holds — no key.
_LAUNCH_UNPATCHED = (
    "import runpy, sys\n"
    "sys.argv = sys.argv[1:]\n"
    "runpy.run_path(sys.argv[0], run_name='__main__')\n"
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    # The forge harness puts corvin_operator/ on the child's path via forge.py;
    # `license` must import as the same module object the gate reads.
    monkeypatch.setattr(_harness, "_LAUNCH_WITH_TIER", _LAUNCH_WITH_MEMBER_LOADER)
    c = _harness.MCPClient(tmp_path / "ws", licence_tier="member")
    c.initialize()
    try:
        yield c
    finally:
        c.close()


def _call(client, tool, args, timeout=60):
    resp = client.request("tools/call", {"name": tool, "arguments": args}, timeout=timeout)
    result = resp.get("result", {})
    text = "".join(c.get("text", "") for c in result.get("content", []))
    return bool(result.get("isError")), text


def _events(root: Path, event_type: str) -> list[dict]:
    chain = root / "audit.jsonl"
    if not chain.exists():
        return []
    return [r.get("details", {}) for r in map(json.loads, chain.read_text(encoding="utf-8").splitlines())
            if r.get("event_type") == event_type]


def _capability_decisions(home: Path) -> list[dict]:
    out = []
    for chain in home.rglob("audit.jsonl"):
        for line in chain.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if r.get("event_type") == "license.capability_decision":
                out.append(r["details"])
    return out


def test_forge_tool_is_allowed_for_a_member_whose_licence_the_server_never_booted(client, tmp_path):
    import os
    home = Path(os.environ["CORVIN_HOME"])
    before = len(_capability_decisions(home))
    err, text = _call(client, "forge_tool", {
        "name": "deferred_load_probe", "description": "probe",
        "input_schema": {"type": "object", "properties": {"x": {"type": "number"}},
                         "required": ["x"]},
        "impl": IMPL,
    })
    assert not err, text
    assert "license_required" not in text
    got = [(d["capability"], d["tier"], d["decision"], d["entry_point"])
           for d in _capability_decisions(home)[before:]]
    assert ("forge.create", "member", "allow", "mcp:forge_tool") in got, got
    assert not any(t == "free" for _, t, _, _ in got), got


def test_compute_run_is_licensed_not_trial_for_a_member(client, tmp_path):
    err, text = _call(client, "forge_tool", {
        "name": "deferred_load_quadratic", "description": "probe",
        "input_schema": {"type": "object", "properties": {"x": {"type": "number"}},
                         "required": ["x"]},
        "impl": IMPL,
    })
    assert not err, text
    resp = client.request("tools/call", {"name": "compute_run", "arguments": {
        "tool_name": "deferred_load_quadratic",
        "param_grid": {"x": [1, 3, 5]},
        "loss_metric": "loss", "strategy": "grid", "minimise": True,
        "budget": {"max_iterations": 3, "max_wall_clock_s": 60},
    }}, timeout=60)
    # The sandbox runs no compute worker, so the run itself fails AFTER the
    # licence gate decided (the gate is the subject here). Either outcome is
    # named explicitly; anything else (a licence refusal included) fails.
    if "error" in resp:
        assert "compute worker socket not present" in resp["error"].get("message", ""), resp
    else:
        text = "".join(c.get("text", "") for c in resp["result"].get("content", []))
        assert not resp["result"].get("isError"), text
        assert "_license" not in json.loads(text), text   # the trial watermark
    checked = _events(tmp_path / "ws", "compute.license.checked")
    assert checked, "the compute licence gate did not run"
    assert checked[-1]["mode"] == "licensed", checked[-1]
    assert checked[-1]["tier"] == "member", checked[-1]


def test_without_a_licence_key_the_deferred_load_grants_nothing(tmp_path, monkeypatch):
    """Over-grant counterpart: no key in the sandbox -> forge.create denied, compute on trial."""
    import os
    home = Path(os.environ["CORVIN_HOME"])
    assert not (home / "global" / "license.key").exists()
    monkeypatch.setattr(_harness, "_LAUNCH_WITH_TIER", _LAUNCH_UNPATCHED)
    c = _harness.MCPClient(tmp_path / "ws", licence_tier="free")
    c.initialize()
    try:
        before = len(_capability_decisions(home))
        err, text = _call(c, "forge_tool", {
            "name": "deferred_load_free_probe", "description": "probe",
            "input_schema": {"type": "object"}, "impl": IMPL,
        })
        assert err and text.startswith("license_required:"), text
        got = [(d["capability"], d["tier"], d["decision"])
               for d in _capability_decisions(home)[before:]]
        assert got == [("forge.create", "free", "deny")], got
    finally:
        c.close()


def test_a_paid_tier_without_unlimited_compute_stays_on_the_trial(monkeypatch):
    """The tier string alone never unlocks compute; the capability matrix decides."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "core" / "compute"))
    from corvin_compute import license_gate as lg
    import license.limits as limits

    monkeypatch.setattr(lg, "_operator_active_tier", lambda: "member")
    assert lg._operator_licence_access().mode == "licensed"
    assert lg._operator_licence_access().fabric_allowed is False
    patched = dict(limits.CAPABILITIES)
    patched["compute.run"] = {"free": {"limit": 10}, "member": {"limit": 50}}
    monkeypatch.setattr(limits, "CAPABILITIES", patched)
    assert lg._operator_licence_access() is None
    monkeypatch.setattr(lg, "_operator_active_tier", lambda: "free")
    assert lg._operator_licence_access() is None
    monkeypatch.setattr(lg, "_operator_active_tier", lambda: None)
    assert lg._operator_licence_access() is None


_RACE_CHILD = r"""
import sys, threading, time
import corvin_operator.license.validator as v
real = v._load_license_from_env_locked
def slow(*a, **k):
    real(*a, **k)
    time.sleep(0.5)                       # a slow first load (revocation fetch)
    v._set_active_license({'tier': 'member', 'jti': 'e2e-race'})
v._load_license_from_env_locked = slow
out = {}
def read(name):
    out[name] = v.active_tier()
a = threading.Thread(target=read, args=('a',)); a.start()
time.sleep(0.1)                           # b arrives while a's load is running
b = threading.Thread(target=read, args=('b',)); b.start()
a.join(); b.join()
print(out['a'], out['b'])
"""


def test_a_concurrent_first_read_waits_for_the_load_instead_of_reading_free():
    import os
    import subprocess
    root = Path(__file__).resolve().parents[2]
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(root), str(root / "corvin_operator")]))
    r = subprocess.run([sys.executable, "-c", _RACE_CHILD], capture_output=True, text=True,
                       env=env, timeout=60, cwd=str(root))
    assert r.returncode == 0, r.stderr[-2000:]
    assert r.stdout.split()[-2:] == ["member", "member"], (r.stdout, r.stderr[-1500:])
