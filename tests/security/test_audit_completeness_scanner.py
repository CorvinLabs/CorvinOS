"""Positive and negative controls for ``scripts/verify_audit_event_completeness.py``.

Review 2026-11-19 (GOV-1): the unregistered-emission scan promoted any helper to a
"writer" by name, so file names and import paths read as audit events and the gate
was red for noise. Narrowing a gate is only safe with a positive control: a real new
event name passed to a real writer must still be reported.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "verify_audit_event_completeness.py"


@pytest.fixture()
def scanner(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("_completeness_under_test", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    src = tmp_path / "emitters.py"
    src.write_text(
        "import os, sys\n"
        "def get(event_type, d):\n"
        "    write_event(None, event_type, details=d)\n"
        "def join(event_type, d):\n"
        "    write_event(None, event_type, details=d)\n"
        "def subscribe(event_type, cb):\n"
        "    write_event(None, event_type, details={})\n"
        "def _audit_thing(event_type, **kw):\n"
        "    write_event(None, event_type, details=kw)\n"
        "def run(hub, report, path):\n"
        "    write_event(path, 'zzreview.direct_new_event', details={})\n"
        "    _audit_thing('zzreview.helper_new_event', a=1)\n"
        "    emit('zzreview.emit_new_event')\n"
        "    audit_fn(event_type='zzreview.keyword_new_event')\n"
        "    sys.modules.get('core.skills.os_skills.model_selector')\n"
        "    sys.modules.get('corvin_console.routes.chat')\n"
        "    os.path.join(path, 'title.png')\n"
        "    join('audio.aac', {})\n"
        "    hub.subscribe('zzreview.bus_subscription', None)\n"
        # Noise filters apply to DERIVED helpers only (round 2, R2-SEC-1).
        "    _audit_thing('final.mp4'); _audit_thing('slides.pptx')\n"
        "    _audit_thing('cpu.max'); _audit_thing('memory.limit_in_bytes')\n"
        "    _audit_thing('core.skills.os_skills.model_selector')\n"
        "    emit('os.path.join'); emit('sys.modules.get'); emit('v1.0')\n"
        "    emit('known.event')\n"
        # Positive control in REAL namespaces, through the FIXED writers: every one of
        # these was swallowed by a filter keyed on the name's first or last segment.
        "    write_event(path, 'code.exec_denied', details={})\n"
        "    write_event(path, 'http.request_blocked', details={})\n"
        "    write_event(path, 'secrets.vault_opened', details={})\n"
        "    write_event(path, 'token.rotated', details={})\n"
        "    write_event(path, 'platform.credentials_restored', details={})\n"
        "    write_event(path, 'queue.overflow', details={})\n"
        "    write_event(path, 'operator.declaration_changed', details={})\n"
        "    write_event(path, 'signal.inject_refused', details={})\n"
        "    write_event(path, 'license.key', details={})\n"
        "    write_event(path, 'session.lock', details={})\n"
        "    write_event(path, 'erasure.log', details={})\n"
        "    write_event(path, 'a2a.pub', details={})\n"
        "    write_event(path, 'forge.policy', details={})\n"
        "    write_event(path, 'memory.high', details={})\n"
        "    audit_event('secrets.rotation_failed'); audit_fn('token.revoked', {})\n"
        # A generically named helper that really writes: matched in its own file and
        # through an audit-named receiver, never through an unrelated ``.log(...)``.
        "class AuditLog:\n"
        "    def log(self, event_type, **kw):\n"
        "        write_event(None, event_type, details=kw)\n"
        "    def publish(self, event_type):\n"
        "        write_event(None, event_type, details={})\n"
        "    def both(self):\n"
        "        self.log('http.generic_log_helper')\n"
        "def use(audit_log, logger):\n"
        "    audit_log.publish('queue.generic_publish_helper')\n"
        "    get('zzreview.same_file_generic_get', {})\n",
        encoding="utf-8",
    )
    other = tmp_path / "unrelated.py"
    other.write_text(
        "def run(logger, bus, audit):\n"
        "    logger.log('zzreview.plain_logger_call')\n"
        # A generic name defined as a forwarding helper in ANOTHER file: a bare
        # ``get(...)`` / ``d.get(...)`` here is not that helper.
        "    get('zzreview.read_by_generic_get', {})\n"
        "    cfg.get('zzreview.read_by_generic_get')\n"
        "    bus.publish('zzreview.plain_bus_publish')\n"
        "    audit.log('code.audit_receiver_log')\n"
        "    self_audit_trail.publish('http.audit_receiver_publish')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "_py_sources", lambda: iter([src, other]))
    monkeypatch.setattr(mod, "REPO_ROOT", mod.REPO_ROOT)
    orig_rel = Path.relative_to

    def _rel(self, *a, **kw):
        try:
            return orig_rel(self, *a, **kw)
        except ValueError:
            return self

    monkeypatch.setattr(Path, "relative_to", _rel)
    yield mod
    sys.modules.pop(spec.name, None)


def test_real_new_event_names_still_fail_the_scan(scanner):
    found = scanner.find_unregistered_emissions({"known.event"})
    for name in ("zzreview.direct_new_event", "zzreview.helper_new_event",
                 "zzreview.emit_new_event", "zzreview.keyword_new_event"):
        assert name in found, f"positive control lost: {name} not reported ({sorted(found)})"


def test_file_names_import_paths_and_generic_callees_are_not_events(scanner):
    found = scanner.find_unregistered_emissions({"known.event"})
    for noise in ("title.png", "audio.aac", "final.mp4", "slides.pptx", "os.path.join", "v1.0",
                  "sys.modules.get",
                  "cpu.max", "memory.limit_in_bytes", "core.skills.os_skills.model_selector",
                  "corvin_console.routes.chat", "zzreview.read_by_generic_get",
                  "zzreview.bus_subscription", "known.event"):
        assert noise not in found, f"noise reported as an audit event: {noise}"


_REAL_NAMESPACE_EVENTS = (
    "code.exec_denied", "http.request_blocked", "secrets.vault_opened", "token.rotated",
    "platform.credentials_restored", "queue.overflow", "operator.declaration_changed",
    "signal.inject_refused", "license.key", "session.lock", "erasure.log", "a2a.pub",
    "forge.policy", "memory.high", "secrets.rotation_failed", "token.revoked",
)


def test_real_namespace_events_at_fixed_writers_are_reported(scanner):
    """Round 2 (R2-SEC-1): the first positive control lived in a ``zzreview.*`` namespace
    no filter could touch, so it proved nothing about names whose first segment is a
    stdlib module, whose last segment reads like a file suffix, or that resolve to a
    module path / cgroup file. Of 42 such names handed to ``write_event`` 3 were seen."""
    found = scanner.find_unregistered_emissions({"known.event"})
    missing = [n for n in _REAL_NAMESPACE_EVENTS if n not in found]
    assert not missing, f"real event names swallowed by a filter: {missing}"


def test_stdlib_filter_only_drops_real_attribute_paths(scanner):
    assert not scanner.looks_like_event_name("os.path.join", fixed_writer=True)
    assert not scanner.looks_like_event_name("sys.modules.get", fixed_writer=True)
    for name in ("secrets.vault_opened", "token.rotated", "signal.inject_refused",
                 "operator.declaration_verified", "platform.credentials_restored"):
        assert scanner.looks_like_event_name(name), name
        assert scanner.looks_like_event_name(name, fixed_writer=True), name
    # Suffix / module-path / cgroup filters: derived helpers only.
    for name in ("license.key", "session.lock", "memory.high"):
        assert scanner.looks_like_event_name(name, fixed_writer=True), name
        assert not scanner.looks_like_event_name(name), name


def test_generic_named_writers_are_matched_narrowly(scanner):
    found = scanner.find_unregistered_emissions({"known.event"})
    for name in ("http.generic_log_helper", "queue.generic_publish_helper",
                 "zzreview.same_file_generic_get",
                 "code.audit_receiver_log", "http.audit_receiver_publish"):
        assert name in found, f"generic-named writer lost: {name}"
    for noise in ("zzreview.plain_logger_call", "zzreview.plain_bus_publish"):
        assert noise not in found, noise


def test_shrink_baseline_only_drops_registered_names(tmp_path, scanner, monkeypatch, capsys):
    """A name the scan merely stopped seeing must survive ``--shrink-baseline``."""
    import json
    import types
    base = tmp_path / "baseline.json"
    base.write_text(json.dumps(["known.event", "zzreview.vanished_never_registered",
                                "zzreview.direct_new_event"]), encoding="utf-8")
    fake = types.SimpleNamespace(EVENT_SEVERITY={"known.event": "INFO"},
                                 _EVENT_ALLOWLIST={"known.event": frozenset({"tenant_id"})})
    monkeypatch.setattr(scanner, "load_registry", lambda: fake)
    monkeypatch.setattr(scanner, "find_emitters", lambda names: {})
    scanner.main(["--baseline", str(base), "--shrink-baseline"])
    out = capsys.readouterr().out
    kept = set(json.loads(base.read_text("utf-8")))
    assert "known.event" not in kept
    assert "zzreview.vanished_never_registered" in kept, out
    assert "zzreview.direct_new_event" in kept
    assert "not seen by the scan any more and NOT registered" in out


def test_unknown_first_segment_is_still_an_event_name(scanner):
    # A deny list, not an allow list of subsystem prefixes: a NEW subsystem counts.
    assert scanner.looks_like_event_name("brand_new_subsystem.something_happened")
    assert not scanner.looks_like_event_name("brand_new_subsystem")
    assert not scanner.looks_like_event_name("report.PDF")


def test_generic_and_reader_names_are_never_derived_writers(scanner):
    for name in ("get", "join", "add", "append", "subscribe", "execute", "_read_by_type",
                 "open", "publish", "_get_handlers"):
        assert not scanner._derived_callee_ok(name), name
    # A deny list: an arbitrarily named forwarding helper is still a writer (RS-10).
    for name in ("_audit_thing", "_emit_audit", "append_event", "_l16_write", "record_event",
                 "_inner", "outer", "_notice"):
        assert scanner._derived_callee_ok(name), name


def test_live_registry_passes_the_gate():
    import subprocess
    r = subprocess.run([sys.executable, str(_SCRIPT)], capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout[-3000:]
    assert "PASS" in r.stdout
