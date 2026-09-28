"""Phase 2 audit events (ADR-2041/2042/2043) — contract of the REAL emitters.

Rewritten 2026-09-27 (adversarial review round 4). The previous version called
``emit_worker_spawn_initiated`` / ``emit_worker_heartbeat`` /
``emit_worker_terminated`` (which do not exist) and a ``write_event_fn`` kwarg
the public helpers do not take, so all 18 cases failed on main while CLAUDE.md
cited them as proof.

What is asserted now, through the real ``forge.security_events.write_event``
into a temp chain (no mocked writer):

* each wired emitter module writes its event with its fields intact (the
  writer's detail floor does not drop them) and the chain verifies;
* each module rejects a non-allowlisted field (fail-closed) and, for A2A,
  a missing ``tenant_id``;
* the registries agree: every Phase 2 event is in ``EVENT_SEVERITY`` and its
  ``_EVENT_ALLOWLIST`` covers the emitter module's field set;
* the two worker events with NO emitter (``compute.worker_spawn_initiated``,
  ``compute.worker_heartbeat``) are honestly unemittable: the compute emitter
  refuses them, and no production file names them outside the registry.

The WIRING of the live call sites (``WorkerServer.stop`` →
``compute.worker_terminated``, ``corvin_a2a pair --offline-pair`` →
``a2a.offline_pair_initiated``, no creator for ``a2a.genesis_block_created``)
is proven end-to-end in ``tests/security/test_wave1c_audit_wiring_e2e.py``;
``plugin.execution_timeout`` in
``core/plugins/tests/test_plugin_execution_timeout_audit.py``.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
for _p in (_REPO / "corvin_operator" / "forge", _REPO / "corvin_operator" / "bridges" / "shared"):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))

from forge import security_events as se  # noqa: E402
from core.compute.corvin_compute import audit as compute_audit  # noqa: E402
from core.plugins.corvin_plugins import audit as plugin_audit  # noqa: E402
from corvin_operator.bridges.shared import a2a_audit  # noqa: E402


@pytest.fixture
def chain(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(tmp_path / "keys" / "anchor.key"))
    monkeypatch.setattr(se, "_ANCHOR_KEY", None)
    monkeypatch.setattr(se, "_ANCHOR_KEY_LOADED", False)
    monkeypatch.setattr(se, "_ANCHOR_KEY_REFUSED", None)
    return tmp_path / "audit.jsonl"


def _records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _last(path: Path, event: str) -> dict:
    recs = [r for r in _records(path) if r.get("event_type") == event]
    assert recs, f"{event} not written"
    return recs[-1]


# ── emitted through the real writer ────────────────────────────────────────

def test_compute_worker_terminated_reaches_the_chain(chain):
    compute_audit.emit("compute.worker_terminated", path=chain, tenant_id="_default",
                       worker_id="compute:4242", termination_reason="shutdown")
    rec = _last(chain, "compute.worker_terminated")
    assert rec["details"]["worker_id"] == "compute:4242"
    assert rec["details"]["termination_reason"] == "shutdown"
    assert "_dropped_fields" not in rec["details"]
    assert se.verify_chain(chain) == (True, [])


def test_a2a_events_reach_the_chain(chain):
    a2a_audit.emit_offline_pair_initiated(chain, task_id="t-1", peer_id="peer-9",
                                          pairing_id="pair-1", ttl_s=3600, tenant_id="_default")
    a2a_audit.emit_nonce_collision_detected(chain, nonce_prefix="cafebabe0000", epoch=2,
                                            collision_count=3, tenant_id="_default")
    a2a_audit.emit_genesis_block_created(chain, instance_id="i-1", network_id="n-1",
                                         nonce_prefix="deadbeefdeadbeef", epoch=1,
                                         tenant_id="_default")
    pair = _last(chain, "a2a.offline_pair_initiated")["details"]
    assert (pair["task_id"], pair["ttl_s"]) == ("t-1", 3600)
    coll = _last(chain, "a2a.nonce_collision_detected")["details"]
    assert coll["nonce_prefix"] == "cafebabe" and coll["collision_count"] == 3
    gen = _last(chain, "a2a.genesis_block_created")["details"]
    assert gen["nonce_prefix"] == "deadbeef"  # never the full nonce
    for r in _records(chain):
        assert "_dropped_fields" not in r["details"], r
    assert se.verify_chain(chain) == (True, [])


def test_plugin_events_reach_the_chain(chain):
    plugin_audit.emit_execution_timeout(chain, plugin_id="p.x", boot_layer="bundled",
                                        timeout_ms="5000", tenant_id="_default")
    plugin_audit.emit_initialization_failed(chain, plugin_id="p.x", boot_layer="bundled",
                                            error_class="ImportError", tenant_id="_default")
    to = _last(chain, "plugin.execution_timeout")["details"]
    assert to["timeout_ms"] == 5000 and isinstance(to["timeout_ms"], int)
    assert _last(chain, "plugin.initialization_failed")["details"]["error_class"] == "ImportError"
    assert se.verify_chain(chain) == (True, [])


# ── fail-closed field contracts ────────────────────────────────────────────

def test_compute_emitter_rejects_extra_field(chain):
    with pytest.raises(compute_audit.AuditFieldNotAllowed):
        compute_audit.emit("compute.worker_terminated", path=chain, worker_id="w",
                           termination_reason="shutdown", socket_path="/run/x.sock")
    assert not chain.exists()


def test_a2a_emitter_requires_tenant_and_rejects_extras(chain):
    with pytest.raises(a2a_audit.AuditFieldNotAllowed):
        a2a_audit.emit("a2a.nonce_collision_detected", path=chain, nonce_prefix="ab", epoch=1,
                       collision_count=1)  # no tenant_id
    with pytest.raises(a2a_audit.AuditFieldNotAllowed):
        a2a_audit.emit("a2a.nonce_collision_detected", path=chain, tenant_id="_default",
                       nonce_prefix="ab", epoch=1, collision_count=1, full_nonce="ab" * 32)
    assert not chain.exists()


def test_plugin_emitter_rejects_extra_field(chain):
    with pytest.raises(plugin_audit.AuditFieldNotAllowed):
        plugin_audit.emit("plugin.initialization_failed", path=chain, plugin_id="p",
                          boot_layer="bundled", error_class="E", stack_trace="...")
    assert not chain.exists()


# ── registry agreement ─────────────────────────────────────────────────────

_MODULE_FIELDS = {
    "compute.worker_terminated": compute_audit._ALLOWED_FIELDS["compute.worker_terminated"],
    **a2a_audit._ALLOWED_FIELDS,
    **plugin_audit._ALLOWED_FIELDS,
}


@pytest.mark.parametrize("event", sorted(_MODULE_FIELDS))
def test_writer_allowlist_covers_emitter_fields(event):
    assert event in se.EVENT_SEVERITY
    missing = set(_MODULE_FIELDS[event]) - set(se._EVENT_ALLOWLIST.get(event, frozenset())) - {"tenant_id"}
    assert not missing, f"{event}: writer floor would drop {sorted(missing)}"


# ── no emitter: stated honestly ────────────────────────────────────────────

_NO_EMITTER = ("compute.worker_spawn_initiated", "compute.worker_heartbeat")


@pytest.mark.parametrize("event", _NO_EMITTER)
def test_unwired_worker_events_cannot_be_emitted(event, chain):
    assert event in se.EVENT_SEVERITY  # registered …
    with pytest.raises(compute_audit.AuditFieldNotAllowed):  # … but not emittable
        compute_audit.emit(event, path=chain, worker_id="w")
    assert not chain.exists()


def test_unwired_worker_events_have_no_production_caller():
    """If this fails, someone wired an emitter: register its fields in
    compute/audit.py::_ALLOWED_FIELDS, prove it in an E2E test, and update the
    CLAUDE.md Phase 2 list in the same commit."""
    hits = []
    skip = {".git", ".venv", "node_modules", "tests", "test", "__pycache__", ".corvin"}
    registry = _REPO / "corvin_operator" / "forge" / "forge" / "security_events.py"
    for root, dirs, files in os.walk(_REPO):
        dirs[:] = [d for d in dirs if d not in skip and not d.startswith(".")]
        for fn in files:
            if not fn.endswith(".py") or fn.startswith("test_"):
                continue
            p = Path(root) / fn
            if p == registry or p.parent.name == "scripts":
                continue
            src = p.read_text("utf-8", errors="ignore")
            hits += [f"{p.relative_to(_REPO)}:{e}" for e in _NO_EMITTER if e in src]
    assert hits == []
