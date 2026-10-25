#!/usr/bin/env python3
"""classify_task(detector=True): the opt-in per-tenant path (ADR-2268 Decision 6, T-0219).

For a tenant with an enabled sensitive route the effective class is
max(marker, secret bank, personal_data compliance zone, detector). A marker may only RAISE, so
``[class:public]`` cannot switch the detector off. The secret bank still outranks everything. A
failing classifier step is fail-closed for such a tenant (it raises) instead of silently becoming
PUBLIC. A tenant without the setting keeps today's behaviour exactly.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import data_classification as dc  # type: ignore  # noqa: E402

C = dc.DataClassification
MAIL = "reply to jane.doe@example.com please"
KEY = "key sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"


class TestUnchangedWithoutOptIn:
    @pytest.mark.parametrize("text", ["plain", MAIL, KEY, "[class:public] " + MAIL, "[class:internal] x", ""])
    def test_default_path_is_untouched(self, text):
        assert dc.classify_task(text) == dc.classify_task(text, detector=False)

    def test_a_public_marker_still_means_public_without_opt_in(self):
        assert dc.classify_task("[class:public] " + MAIL) is C.PUBLIC


class TestOptedIn:
    def test_pii_raises_to_confidential(self):
        assert dc.classify_task(MAIL, detector=True) is C.CONFIDENTIAL

    def test_a_credential_is_secret(self):
        assert dc.classify_task(KEY, detector=True) is C.SECRET

    def test_plain_text_stays_public(self):
        assert dc.classify_task("write a haiku about spring", detector=True) is C.PUBLIC

    def test_a_public_marker_cannot_switch_the_detector_off(self):
        assert dc.classify_task("[class:public] " + MAIL, detector=True) is C.CONFIDENTIAL

    def test_a_marker_may_raise(self):
        assert dc.classify_task("[class:confidential] hello", detector=True) is C.CONFIDENTIAL
        assert dc.classify_task("[class:internal] hello", detector=True) is C.INTERNAL

    def test_the_maximum_wins(self):
        assert dc.classify_task("[class:internal] " + MAIL, detector=True) is C.CONFIDENTIAL
        assert dc.classify_task("[class:secret] hello", detector=True) is C.SECRET

    def test_the_secret_bank_outranks_a_marker(self):
        assert dc.classify_task("[class:public] " + KEY, detector=True) is C.SECRET

    def test_the_personal_data_zone_holds_against_a_public_marker(self, monkeypatch):
        import compliance_zone_classifier as zc  # type: ignore
        monkeypatch.setattr(zc, "classify_zone", lambda task, persona=None: {"zone": "personal_data"})
        assert dc.classify_task("[class:public] hello", detector=True) is C.CONFIDENTIAL
        assert dc.classify_task("[class:public] hello") is C.PUBLIC       # not opted in: as today

    def test_a_failing_zone_classifier_is_fail_closed(self, monkeypatch):
        import compliance_zone_classifier as zc  # type: ignore

        def boom(task, persona=None):
            raise RuntimeError("zone classifier down")
        monkeypatch.setattr(zc, "classify_zone", boom)
        with pytest.raises(dc.ClassifierError):
            dc.classify_task("hello", detector=True)
        assert dc.classify_task("hello") is C.PUBLIC                      # not opted in: swallowed as today

    def test_a_missing_zone_classifier_is_fail_closed(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "compliance_zone_classifier", None)
        with pytest.raises(dc.ClassifierError):
            dc.classify_task("hello", detector=True)

    def test_a_detector_failure_is_fail_closed(self, monkeypatch):
        import sensitive_detector as sd  # type: ignore
        monkeypatch.setattr(sd, "raise_class", lambda c, t: (_ for _ in ()).throw(sd.DetectorError("x")))
        with pytest.raises(dc.ClassifierError):
            dc.classify_task("hello", detector=True)

    def test_empty_text_is_public(self):
        assert dc.classify_task("", detector=True) is C.PUBLIC
        assert dc.classify_task("   ", detector=True) is C.PUBLIC

    def test_a_non_string_is_fail_closed_when_opted_in(self):
        with pytest.raises(dc.ClassifierError):
            dc.classify_task(None, detector=True)  # type: ignore[arg-type]
        assert dc.classify_task(None) is C.PUBLIC                          # as today


class TestGuardKnowsTheOptIn:
    def _guard(self, route):
        return dc.DataFlowGuard.from_tenant_config({"spec": {"data_classification": {"sensitive_route": route}}})

    def test_detector_on_follows_an_enabled_wellformed_route(self):
        assert self._guard({"enabled": True, "provider": "p", "model_id": "m"}).detector_on is True

    @pytest.mark.parametrize("route", [{"enabled": False}, {"provider": "p"}, None, "x"])
    def test_detector_is_off_otherwise(self, route):
        assert self._guard(route).detector_on is False

    def test_a_malformed_route_marks_the_guard(self):
        g = self._guard({"enabled": "true", "provider": "p", "model_id": "m"})
        assert g.route_malformed is True and g.detector_on is False
        assert self._guard({"enabled": False}).route_malformed is False

    def test_no_config_means_off(self):
        g = dc.DataFlowGuard.from_tenant_config(None)
        assert (g.detector_on, g.route_malformed) == (False, False)
