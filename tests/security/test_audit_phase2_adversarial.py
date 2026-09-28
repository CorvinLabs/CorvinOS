"""Adversarial tests for the Phase 2 audit emitters (Layer 4, 22, 38).

Rewritten 2026-09-28 (adversarial review round 5). The previous version called
functions that do not exist (``emit_worker_heartbeat``,
``emit_worker_spawn_initiated``) and kwargs the real helpers do not take
(``write_event_fn=`` on the typed helpers, ``plugin_code=``), so 17 of its 32
cases failed and the rest asserted against a mocked writer.

The positive contract (each event reaches the chain, registries agree, the two
unwired worker events are unemittable) lives in ``test_audit_phase2_events.py``.
What is asserted HERE is the hostile-caller side, through the REAL
``forge.security_events.write_event`` into a temp chain:

* PII / secret / content smuggled in as an extra kwarg is REFUSED before any
  write — nothing reaches the chain, not even a partial record;
* a full nonce passed where a prefix is expected is truncated to 8 hex chars;
* numeric fields given as strings are coerced (a non-numeric one raises);
* a writer failure is loud for the audit-first plugin emitter (raises) and
  logged-not-raised for the a2a emitter (whose one production caller,
  ``corvin_a2a pair --offline-pair``, observes the commit through an injected
  writer and refuses the pair when it did not happen);
* the raw bytes of the chain never contain the smuggled values.
"""
from __future__ import annotations

import json
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

_EMAIL = "victim@example.com"
_FULL_NONCE = "deadbeef" + "0123456789abcdef" * 3 + "cafebabe"  # 64 hex chars


@pytest.fixture
def chain(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(tmp_path / "keys" / "anchor.key"))
    monkeypatch.setattr(se, "_ANCHOR_KEY", None)
    monkeypatch.setattr(se, "_ANCHOR_KEY_LOADED", False)
    monkeypatch.setattr(se, "_ANCHOR_KEY_REFUSED", None)
    return tmp_path / "audit.jsonl"


def _records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ── smuggled content is refused before any write ──────────────────────────

@pytest.mark.parametrize("extra", [
    {"user_prompt": f"my email is {_EMAIL}"},
    {"task_instruction": "SELECT * FROM users"},
    {"socket_path": "/run/user/1000/compute.sock"},
])
def test_compute_refuses_smuggled_fields(chain, extra):
    with pytest.raises(compute_audit.AuditFieldNotAllowed):
        compute_audit.emit("compute.worker_terminated", path=chain, tenant_id="_default",
                           worker_id="w", termination_reason="shutdown", **extra)
    assert not chain.exists()


@pytest.mark.parametrize("extra", [
    {"private_key": "-----BEGIN PRIVATE KEY-----MIIE"},
    {"full_nonce": _FULL_NONCE},
    {"peer_email": _EMAIL},
])
def test_a2a_refuses_smuggled_fields(chain, extra):
    with pytest.raises(a2a_audit.AuditFieldNotAllowed):
        a2a_audit.emit("a2a.offline_pair_initiated", path=chain, tenant_id="_default",
                       task_id="t", peer_id="p", pairing_id="x", ttl_s=60, **extra)
    assert not chain.exists()


@pytest.mark.parametrize("extra", [
    {"error_message": f"cannot open /home/alice/{_EMAIL}.db"},
    {"stack_trace": 'File "/home/alice/plugin.py", line 3'},
])
def test_plugin_refuses_smuggled_fields(chain, extra):
    with pytest.raises(plugin_audit.AuditFieldNotAllowed):
        plugin_audit.emit("plugin.initialization_failed", path=chain, tenant_id="_default",
                          plugin_id="p", boot_layer="bundled", error_class="E", **extra)
    assert not chain.exists()


def test_a2a_refuses_missing_tenant(chain):
    with pytest.raises(a2a_audit.AuditFieldNotAllowed):
        a2a_audit.emit_genesis_block_created(chain, instance_id="i", network_id="n",
                                             nonce_prefix="ab", epoch=1)
    assert not chain.exists()


def test_unknown_event_types_are_refused(chain):
    with pytest.raises(compute_audit.AuditFieldNotAllowed):
        compute_audit.emit("compute.anything_goes", path=chain, worker_id="w")
    with pytest.raises(a2a_audit.AuditFieldNotAllowed):
        a2a_audit.emit("a2a.anything_goes", path=chain, tenant_id="_default")
    with pytest.raises(plugin_audit.AuditFieldNotAllowed):
        plugin_audit.emit("plugin.anything_goes", path=chain, plugin_id="p")
    assert not chain.exists()


# ── truncation + coercion ─────────────────────────────────────────────────

def test_full_nonce_is_truncated_to_prefix(chain):
    a2a_audit.emit_genesis_block_created(chain, instance_id="i", network_id="n",
                                         nonce_prefix=_FULL_NONCE, epoch="7",
                                         tenant_id="_default")
    a2a_audit.emit_nonce_collision_detected(chain, nonce_prefix=_FULL_NONCE, epoch=7,
                                            collision_count="3", tenant_id="_default")
    recs = _records(chain)
    assert [r["details"]["nonce_prefix"] for r in recs] == ["deadbeef", "deadbeef"]
    assert recs[0]["details"]["epoch"] == 7
    assert recs[1]["details"]["collision_count"] == 3
    assert _FULL_NONCE not in chain.read_text()
    assert se.verify_chain(chain) == (True, [])


def test_non_numeric_counter_raises_instead_of_writing(chain):
    with pytest.raises(ValueError):
        plugin_audit.emit_execution_timeout(chain, plugin_id="p", boot_layer="bundled",
                                            timeout_ms=f"5000; {_EMAIL}",
                                            tenant_id="_default")
    assert not chain.exists()


# ── writer failure semantics ──────────────────────────────────────────────

def _failing_writer(*_a, **_k):
    raise OSError("disk full")


def test_plugin_emitter_is_audit_first_on_writer_failure(chain):
    with pytest.raises(RuntimeError, match="audit-first"):
        plugin_audit.emit("plugin.initialization_failed", path=chain, tenant_id="_default",
                          plugin_id="p", boot_layer="bundled", error_class="ImportError",
                          write_event_fn=_failing_writer)


def test_a2a_emitter_logs_writer_failure(chain, caplog):
    a2a_audit.emit("a2a.nonce_collision_detected", path=chain, tenant_id="_default",
                   nonce_prefix="ab", epoch=1, collision_count=1,
                   write_event_fn=_failing_writer)
    assert any("a2a audit emit failed" in r.getMessage() for r in caplog.records)
    assert not chain.exists()


# ── the real writer's floor, independent of the emitter modules ───────────

def test_writer_floor_drops_denylisted_keys_even_without_an_emitter(chain):
    """A caller bypassing the emitter modules still cannot land content-named
    keys: the chain writer's floor drops them (metadata-only, ADR-0129)."""
    se.write_event(chain, "plugin.initialization_failed",
                   details={"plugin_id": "p", "boot_layer": "bundled",
                            "error_class": "E", "prompt": f"hi {_EMAIL}",
                            "tenant_id": "_default"})
    raw = chain.read_text()
    assert _EMAIL not in raw
    assert _records(chain)[0]["details"]["plugin_id"] == "p"
    assert se.verify_chain(chain) == (True, [])
