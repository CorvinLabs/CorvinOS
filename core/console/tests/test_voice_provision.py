"""corvin_console.voice_provision — audit, egress gate, self-heal backoff.

The download itself is corvinOS.shared.voice_models (tests/test_voice_models.py);
here it is replaced by a fake whose request() runs on_done synchronously.
"""
from __future__ import annotations

import sys
import types
from unittest import mock

import pytest

from corvin_console import voice_provision as vp


class _FakeVM:
    HF_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0"

    def __init__(self, state="missing", outcome="ready"):
        self.state, self.outcome, self.requests = state, outcome, []

    def normalise_lang(self, c):
        return (c or "").split("-")[0].lower()

    def language_status(self, lang):
        return {"lang": self.normalise_lang(lang), "state": self.state}

    def request(self, lang, on_done=None):
        self.requests.append(lang)
        job = {"lang": self.normalise_lang(lang), "state": self.outcome}
        if self.outcome == "failed":
            job["error"] = "could not download the sv voice model (network or proxy?)"
        if on_done:
            on_done(job)
        return {"lang": job["lang"], "state": "queued"}


@pytest.fixture()
def audit():
    with mock.patch.object(vp.console_audit, "action_performed") as ok, \
         mock.patch.object(vp.console_audit, "action_failed") as failed, \
         mock.patch.object(vp.console_audit, "action_denied") as denied:
        yield types.SimpleNamespace(ok=ok, failed=failed, denied=denied)


@pytest.fixture(autouse=True)
def no_gate(monkeypatch):
    monkeypatch.setitem(sys.modules, "egress_gate", None)  # ImportError → fail-open
    vp._last_self_heal.clear()


def test_success_is_audited_with_trigger(monkeypatch, audit):
    fake = _FakeVM()
    monkeypatch.setattr(vp, "_vm", fake)
    st = vp.provision("sv-SE", tenant_id="t1", sid_fingerprint="abc", trigger="language_change")
    assert st["state"] == "queued" and fake.requests == ["sv-SE"]
    kw = audit.ok.call_args.kwargs
    assert kw["action"] == "voice.model_provision" and kw["target_id"] == "sv"
    assert kw["trigger"] == "language_change" and kw["tenant_id"] == "t1"
    audit.failed.assert_not_called()


def test_failure_is_audited_with_a_category_not_the_message(monkeypatch, audit):
    monkeypatch.setattr(vp, "_vm", _FakeVM(outcome="failed"))
    vp.provision("sv", tenant_id="t1", sid_fingerprint="abc", trigger="manual")
    assert audit.failed.call_args.kwargs["reason"] == "download-failed"


def test_ready_language_starts_nothing(monkeypatch, audit):
    fake = _FakeVM(state="ready")
    monkeypatch.setattr(vp, "_vm", fake)
    assert vp.provision("de", tenant_id="t", sid_fingerprint="s", trigger="manual")["state"] == "ready"
    assert fake.requests == []


def test_explicit_egress_denial_blocks_and_is_audited(monkeypatch, audit):
    fake = _FakeVM()
    monkeypatch.setattr(vp, "_vm", fake)

    class _Gate:
        def validate_or_raise(self, host, engine_id):
            raise PermissionError(f"{host} not allowed")

    mod = types.ModuleType("egress_gate")
    mod.load_egress_gate_for_tenant = lambda tid: _Gate()
    monkeypatch.setitem(sys.modules, "egress_gate", mod)
    st = vp.provision("sv", tenant_id="t", sid_fingerprint="s", trigger="manual")
    assert st["state"] == "failed" and "huggingface.co" in st["error"]
    assert fake.requests == []
    assert audit.denied.call_args.kwargs["reason"] == "egress-policy"


def test_self_heal_backs_off_after_an_attempt(monkeypatch, audit):
    fake = _FakeVM(state="missing", outcome="failed")
    monkeypatch.setattr(vp, "_vm", fake)
    vp.self_heal("fi", tenant_id="t")
    vp.self_heal("fi", tenant_id="t")   # a 30 s status poll later
    assert fake.requests == ["fi"], "an offline machine must not retry on every poll"
    assert audit.failed.call_args.kwargs["sid_fingerprint"] == "system"


def test_unavailable_module_degrades(monkeypatch):
    monkeypatch.setattr(vp, "_vm", None)
    assert vp.provision("de", tenant_id="t", sid_fingerprint="s", trigger="manual")["state"] == "unavailable"
