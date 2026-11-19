"""Round 3 (R3-SEC-3): positive and negative controls for the unregistered-emission
scan of ``scripts/verify_audit_event_completeness.py``.

The refutation round planted 40 unregistered names and the scan reported 21: none of
the 16 that reached a FIXED writer in a non-literal spelling, and 6 of the 20 passed
to derived helpers. Every spelling is pinned here, next to the look-alikes the scan
must keep ignoring - a gate that reports noise gets its baseline widened, and a widened
baseline hides the next real name.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "scripts" / "verify_audit_event_completeness.py"

#: A stand-in registry: real namespaces, plus the prefixed form of a buffered name.
SEVERITY = {
    "license.loaded", "session.started", "memory.turn_indexed", "consent.granted",
    "egress.denied", "secret.accessed", "health.stall_detected", "a2a.task_received",
    "erasure.completed", "forge.tool_created", "compute.run_started", "browser.opened",
    "plugin.loaded", "tenant.created", "house_rules.denied", "data_flow.blocked",
    "disclosure.shown", "path_gate.denied", "rollback.done", "api.called", "os.booted",
    "workflow.run.started", "workflow.run.level", "known.event",
}


@pytest.fixture(scope="module")
def scanner():
    spec = importlib.util.spec_from_file_location("_r3_completeness_under_test", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _scan(scanner, tmp_path, **files: str) -> set[str]:
    paths = []
    for name, src in files.items():
        p = tmp_path / f"{name}.py"
        p.write_text(src, encoding="utf-8")
        paths.append(p)
    return set(scanner.find_unregistered_emissions(set(SEVERITY), sources=paths))


# --- literal forms at FIXED writers (0 of 16 were reported) -------------------------

FIXED_FORMS = '''
import asyncio
MODULE_CONST = "compute.r3_module_constant"
ANNOTATED: str = "compute.r3_annotated_constant"
TABLE = {"a": "consent.r3_table_a", "b": "consent.r3_table_b"}

def go(path, ok, kind, audit_fn, os_audit, tenant, session):
    write_event(path, MODULE_CONST, details={})
    write_event(path, ANNOTATED, details={})
    write_event(path, "data_flow.r3_ifexp_a" if ok else "data_flow.r3_ifexp_b", details={})
    local = "disclosure.r3_local_variable"
    audit_event(local, {})
    picked = "egress.r3_local_ifexp_a" if ok else "egress.r3_local_ifexp_b"
    audit_event(picked, details={})
    audit_event(f"erasure.r3_fstring_plain", {})
    part = "r3_fstring_part"
    audit_event(f"erasure.{part}", {})
    audit_event("house_rules." + "r3_concatenated", {})
    _audit(tenant, session, "license.r3_third_positional")
    _audit(audit_fn, tenant, MODULE_CONST)
    emit(name="path_gate.r3_name_keyword")
    audit_fn(event_name="plugin.r3_event_name_keyword")
    audit_event(event_type="rollback.r3_event_type_keyword")
    chosen = {"x": "secret.r3_dict_get_x", "y": "secret.r3_dict_get_y"}.get(kind, "secret.r3_dict_get_default")
    _write_event(path, chosen, details={})
    write_event(path, TABLE[kind], details={})
    write_event(path, (walrus := "tenant.r3_walrus"), details={})
    os_audit("browser.r3_injected_os_audit", {"a": 1})
    write_event(path, ok or "a2a.r3_boolop_default", details={})

async def off_loop(path, loop):
    await asyncio.to_thread(write_event, path, "a2a.r3_to_thread", details={})
    await asyncio.to_thread(security_events.write_event, path, MODULE_CONST)
    await loop.run_in_executor(None, write_event, path, "a2a.r3_run_in_executor")
'''

FIXED_EXPECTED = {
    "compute.r3_module_constant", "compute.r3_annotated_constant",
    "data_flow.r3_ifexp_a", "data_flow.r3_ifexp_b", "disclosure.r3_local_variable",
    "egress.r3_local_ifexp_a", "egress.r3_local_ifexp_b", "erasure.r3_fstring_plain",
    "erasure.r3_fstring_part", "house_rules.r3_concatenated",
    "license.r3_third_positional", "path_gate.r3_name_keyword",
    "plugin.r3_event_name_keyword", "rollback.r3_event_type_keyword",
    "secret.r3_dict_get_x", "secret.r3_dict_get_y", "secret.r3_dict_get_default",
    "consent.r3_table_a", "consent.r3_table_b", "tenant.r3_walrus",
    "browser.r3_injected_os_audit", "a2a.r3_boolop_default", "a2a.r3_to_thread",
    "a2a.r3_run_in_executor",
}


def test_every_literal_form_at_a_fixed_writer_is_reported(scanner, tmp_path):
    found = _scan(scanner, tmp_path, fixed_forms=FIXED_FORMS)
    assert FIXED_EXPECTED - found == set()
    assert found == FIXED_EXPECTED


def test_a_local_name_does_not_leak_into_another_function(scanner, tmp_path):
    found = _scan(scanner, tmp_path, scoped='''
def a():
    event = "consent.r3_scoped_in_a"
    return event

def b(path, event):
    write_event(path, event, details={})
''')
    assert found == set()


# --- derived helpers in real namespaces (6 of 20 were reported) ----------------------

HELPERS = '''
def _hr_audit(event_type, **details):
    write_event(None, event_type, details=details)

def _third(audit_fn, tenant_id, event, **details):
    audit_fn(tenant_id=tenant_id, event=event, details=details)

def _outer(kind, event_type):
    _hr_audit(event_type, kind=kind)

class Recorder:
    def log(self, event_type, **kw):
        write_event(None, event_type, details=kw)
    def publish(self, event_type):
        write_event(None, event_type, details={})
    def send(self, event_type, payload):
        write_event(None, event_type, details={})
    def get_and_record(self, key, event_type):
        write_event(None, event_type, details={"k": 1})
    def on_denied(self, event_type):
        write_event(None, event_type, details={})

def here(audit_fn, tid):
    # File-, lock-, key- and cgroup-shaped leaves: events once the namespace is known.
    _hr_audit("license.key")
    _hr_audit("session.lock")
    _hr_audit("memory.high")
    _hr_audit("erasure.log")
    _hr_audit("forge.policy")
    _hr_audit("a2a.pub")
    _third(audit_fn, tid, "browser.r3_third_param_helper", ttl_s=1)
    _third(audit_fn, tid, event="browser.r3_event_keyword_helper")
    _outer("k", "plugin.r3_two_hop_helper")
    Recorder().log("consent.r3_inline_instance_log")
'''

CALLERS = '''
EVENT = "tenant.r3_cross_file_constant"

def elsewhere(recorder, store, gate, sink):
    recorder.log("consent.r3_cross_file_log")
    recorder.publish("egress.r3_cross_file_publish")
    sink.send("secret.r3_cross_file_send", {})
    store.get_and_record("k", "secret.r3_cross_file_get_and_record")
    gate.on_denied("egress.r3_cross_file_on_denied")
    recorder.log(EVENT)
'''

HELPER_EXPECTED = {
    "license.key", "session.lock", "memory.high", "erasure.log", "forge.policy", "a2a.pub",
    "browser.r3_third_param_helper", "browser.r3_event_keyword_helper",
    "plugin.r3_two_hop_helper", "consent.r3_inline_instance_log",
    "consent.r3_cross_file_log", "egress.r3_cross_file_publish",
    "secret.r3_cross_file_send", "secret.r3_cross_file_get_and_record",
    "egress.r3_cross_file_on_denied", "tenant.r3_cross_file_constant",
}


def test_derived_and_generic_helpers_in_real_namespaces_are_reported(scanner, tmp_path):
    found = _scan(scanner, tmp_path, helpers=HELPERS, callers=CALLERS)
    assert HELPER_EXPECTED - found == set()
    assert found == HELPER_EXPECTED


# --- look-alikes that must stay out ---------------------------------------------------

NOISE = '''
import logging
logger = logging.getLogger(__name__)
_CLOUD_HOST = "api.anthropic.com"
CAPABILITY = "a2a.network"
_SKILL_ID = "os.definition_of_done_verifier"

class Gate:
    def validate(self, host, **kw):
        # ``host`` is a DETAIL, the event is a literal: ``validate`` is no event wrapper.
        audit_event("egress.denied", {"host_len": len(host)})
        self._deny(host, "egress.denied")
    def _deny(self, host, event_type):
        write_event(None, event_type, details={})

def require_capability(capability, *, tenant_id):
    _log_error("known.event", capability)

def _log_error(event_type, cap):
    write_event(None, event_type, details={})

class Bus:
    def publish(self, topic, payload=None):
        write_event(None, topic, details={})
    def log(self, event_type):
        write_event(None, event_type, details={})

class Runner:
    def _audit(self, event, **details):
        self._buffer.append({"event": event, **details})
        if self._sink is not None:
            self._sink({"event": event, **details})
    def run(self, resume):
        self._audit("run.started" if not resume else "run.level")
        self._audit("run.level", level=1)

def use(gate, hub, registry, event_bus):
    gate.validate(_CLOUD_HOST)
    require_capability(CAPABILITY, tenant_id="t")
    hub.publish("health.r3_bus_topic")
    event_bus.publish("health.r3_bus_topic_two", {})
    logger.log("memory.r3_logger_line")
    logging.log("memory.r3_logging_line")
    registry.execute(_SKILL_ID, {})
    ", ".join(["license.r3_join_item"])
    emit("known.event")
    emit("v1.0"); emit("os.path.join")
'''


def test_hosts_capabilities_bus_topics_and_buffered_names_are_not_reported(scanner, tmp_path):
    assert _scan(scanner, tmp_path, noise=NOISE) == set()


def test_buffer_helper_filter_needs_the_prefixed_form_registered(scanner, tmp_path):
    """The un-prefixed name is skipped ONLY while ``<prefix>.<name>`` is registered and
    the local helper reaches no writer; either condition gone, it is reported."""
    found = _scan(scanner, tmp_path, buf='''
class Runner:
    def _audit(self, event, **details):
        self._buffer.append({"event": event, **details})
    def run(self):
        self._audit("run.started")
        self._audit("run.r3_no_prefixed_registration")
''', direct='''
class Direct:
    def _audit(self, event, **details):
        write_event(None, event, details=details)
    def run(self):
        self._audit("run.level")
''')
    assert found == {"run.r3_no_prefixed_registration", "run.level"}


def test_parameter_position_decides_what_a_derived_helper_emits(scanner):
    import ast
    tree = ast.parse('''
def _third(audit_fn, tenant_id, event, **details):
    audit_fn(tenant_id=tenant_id, event=event, details=details)
class Gate:
    def validate(self, host):
        audit_event("egress.denied", {"n": len(host)})
def wrap(a, event_type):
    write_event(a, event_type)
''')
    params = scanner.derive_event_params([tree])
    assert params["_third"] == ({2}, {"event"})
    assert "validate" not in params
    # A fixed writer's first two positions are both candidates (path or event).
    assert params["wrap"] == ({0, 1}, {"a", "event_type"})
    assert "os_audit" in scanner._INJECTED_WRITERS


# --- the real emitters ------------------------------------------------------------------

REAL = {
    "core/compliance/corvin_compliance_reports/tripwire.py": {"compliance.chain_discontinuity"},
    "corvin_operator/bridges/shared/erasure_handlers.py": {"erasure.chain_seam"},
    "corvin_operator/bridges/shared/memory_bridge.py": {"memory.worker_harvest"},
    "corvin_operator/bridges/shared/decision_registry.py": {
        "ai.decision_pending", "ai.decision_recorded"},
    "corvin_operator/bridges/shared/adapter.py": {
        "codex.stream_timeout", "codex.turn_error", "voice.audio_deleted",
        "voice.audio_delete_failed"},
    "corvin_operator/bridges/shared/proactive.py": {
        "proactive.emitted", "proactive.denied", "proactive.rate_limited",
        "proactive.voice_synthesized", "proactive.voice_skipped"},
    "core/console/corvin_console/chat_runtime.py": {"os_turn.context_sync"},
    "core/console/corvin_console/browser/attach_consent.py": {
        "browser.attach.consent_granted", "browser.attach.consent_revoked"},
    "core/console/corvin_console/browser/confirm_mode.py": {
        "browser.attach.confirm_mode_each", "browser.attach.confirm_mode_watch"},
    "core/console/corvin_core/aco/htrace_uploader.py": {"htrace.upload.sent"},
}


@pytest.mark.parametrize("rel", sorted(REAL))
def test_real_emitter_is_seen_by_the_scan(scanner, rel):
    """Against an EMPTY registry the scan must name each of these in its real file -
    the constant / conditional / local-variable spellings that hid them."""
    found = scanner.find_unregistered_emissions(set(), sources=[_REPO / rel])
    assert REAL[rel] - set(found) == set()


def test_real_emitters_are_registered_with_their_fields(scanner):
    se = scanner.load_registry()
    assert Path(se.__file__).resolve().is_relative_to(_REPO)
    for names in REAL.values():
        for name in names:
            assert name in se.EVENT_SEVERITY, name
            assert "tenant_id" in se._EVENT_ALLOWLIST[name], name
    for name in ("htrace.upload.skipped", "htrace.upload.error"):
        assert name in se.EVENT_SEVERITY and name in se._EVENT_ALLOWLIST
    assert se.EVENT_SEVERITY["compliance.chain_discontinuity"] == "CRITICAL"
    assert se.EVENT_SEVERITY["ai.decision_pending"] == "WARNING"
    # The workflow runner's un-prefixed names are NOT chain names (the sink writes
    # ``workflow.<event>``): they must not be registered to silence the scan.
    assert "run.started" not in se.EVENT_SEVERITY
    assert "workflow.run.started" in se.EVENT_SEVERITY


def test_seam_record_keeps_the_fields_the_tripwire_reads_back(scanner):
    """``audit_chain_intact`` decides "already sealed" from ``seam`` and
    ``last_break_line`` of the last record; on the vocabulary floor they were dropped."""
    se = scanner.load_registry()
    details = {"tripwire": "audit_chain_intact", "seam": True, "broken_records": 2,
               "first_break_line": 7, "last_break_line": 9, "total_records": 40}
    kept, dropped = se.filter_audit_details(dict(details),
                                            event_type="compliance.chain_discontinuity")
    assert dropped == []
    assert {k: kept[k] for k in details} == details


@pytest.mark.parametrize("event,details", [
    ("erasure.chain_seam", {"request_id": "r-1", "task_id": "t-1", "removed_count": 2,
                            "first_seq": 3, "last_seq": 4, "reason": "gdpr_art17_erasure",
                            "tenant_id": "_default"}),
    ("memory.worker_harvest", {"channel": "discord", "chat_key": "c1", "tenant_id": "_default",
                               "scope": "session", "entry_count": 2, "engine_id": "codex_cli"}),
    ("voice.audio_deleted", {"msg_id": "m1", "file_size_bytes": 10, "sha256_prefix": "ab12",
                             "deleted": True}),
    ("codex.turn_error", {"engine_id": "codex_cli", "error_class": "StreamError"}),
    ("proactive.emitted", {"tenant_id": "_default", "channel": "discord", "kind": "task_done",
                           "dedup_key_hash": "0" * 64, "voice": False, "solicited": True,
                           "decision": "emitted", "reason": "ok", "lom": "proactive.py:emit"}),
    ("proactive.voice_skipped", {"tenant_id": "_default", "channel": "discord", "kind": "k",
                                 "mode": "summary", "chars": 12, "backend": "none",
                                 "decision": "skipped", "reason": "tts_failed", "lom": "x"}),
    ("ai.decision_pending", {"decision_id": "d1", "channel": "discord", "chat_key": "c1",
                             "risk_tier": "high_risk", "engine_id": "claude_code",
                             "persona": "assistant", "decision_class": "x",
                             "review_status": "pending"}),
    ("os_turn.context_sync", {"turn_id": "t1", "delegated_run_id": "r1", "synced": True}),
    ("browser.attach.consent_granted", {"ttl_s": 300}),
    ("htrace.upload.sent", {"record_count": 3}),
])
def test_registered_field_set_matches_the_emitter(scanner, event, details):
    se = scanner.load_registry()
    kept, dropped = se.filter_audit_details(dict(details), event_type=event)
    assert dropped == [], (event, dropped)
    assert {k: kept[k] for k in details} == details
    # A positive allowlist still drops an undeclared key.
    kept, dropped = se.filter_audit_details({**details, "r3_undeclared": 1}, event_type=event)
    assert dropped == ["r3_undeclared"]
