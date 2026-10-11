"""Regressions for core/skills/os_skills/workflow_optimizer_skill
(adversarial review 2026-09-27). Each test failed before its fix."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from core.learning.event_store import EventStore
from core.learning.learning_events import EventType
from core.skills.os_skills.workflow_optimizer_skill.confidence_calculator import (
    ConfidenceCalculator,
    RoutingWeights,
)














def test_single_incorrect_does_not_discard_prior(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    calc = _calc_with_feedback(tmp_path, [False])
    w, _ = calc.update_from_feedback()
    # prior 0.80, n0=2: (1.6 + 0) / 3 = 0.533 — Laplace gave 1/3.
    assert w.weights["medium_sonnet"] == pytest.approx(1.6 / 3)


def test_repeated_update_does_not_recount_feedback(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    calc = _calc_with_feedback(tmp_path, [True, True, False, True])
    w1, _ = calc.update_from_feedback()
    w2, _ = calc.update_from_feedback()
    assert w1.weights["medium_sonnet"] == pytest.approx(w2.weights["medium_sonnet"])




def test_save_weights_is_audited_and_versioned(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    store = Mock(spec=EventStore)
    calc = ConfidenceCalculator(event_store=store, tenant_id="_default", config_dir=tmp_path / "c")
    v1 = calc.save_weights(RoutingWeights(weights={"simple_haiku": 0.9}))
    v2 = calc.save_weights(RoutingWeights(weights={"simple_haiku": 0.7}))
    assert v1.version != v2.version
    assert len(list(calc.history_dir.glob("v*.json"))) == 2
    assert store.write_event.call_count == 2
    store.write_event.side_effect = RuntimeError("down")
    with pytest.raises(RuntimeError):
        calc.save_weights(RoutingWeights(weights={"simple_haiku": 0.1}))
    assert json.loads(calc.weights_file.read_text())["weights"]["simple_haiku"] == 0.7
