"""ADR-0759: the model an ACS worker span closes on comes from the REAL CLI envelope.

The final ``result`` frame of ``claude -p --output-format json`` has no ``model`` key; the
model is only in ``modelUsage``. Until 2026-10-21 ``agents.attested_model`` read only
``model`` / ``usage.model`` / ``message.model``, so ``acs_runtime._reported_model`` returned
``""`` for every real run and the ACS worker span was unpriced (measured on the captured
frame below: ``''`` while ``modelUsage`` named ``claude-haiku-4-5``).

Run: .venv/bin/python -m pytest corvin_operator/bridges/shared/test_attested_model_real_envelope.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_SHARED = Path(__file__).resolve().parent
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

from agents import SpawnResult, StreamEvent, attested_model  # noqa: E402

_FIXTURE = _SHARED.parents[2] / "core" / "console" / "tests" / "fixtures" / "chat_diff" / "real_file_tools.jsonl"


def _real_result_frame() -> dict:
    for line in _FIXTURE.read_text(encoding="utf-8").splitlines():
        try:
            frame = json.loads(line)
        except ValueError:
            continue
        if isinstance(frame, dict) and frame.get("type") == "result":
            return frame
    raise AssertionError("fixture has no result frame")


def _wrap(frame: dict) -> SpawnResult:
    return SpawnResult(final_text="", events=[StreamEvent(type="turn_completed", raw=frame)])


def test_real_result_frame_has_no_model_key_but_names_one_in_model_usage():
    frame = _real_result_frame()
    assert "model" not in frame and "model" not in (frame.get("usage") or {})
    assert list(frame["modelUsage"]) == ["claude-haiku-4-5"]
    assert attested_model(_wrap(frame)) == "claude-haiku-4-5"


def test_acs_reported_model_reads_the_real_envelope():
    import acs_runtime  # noqa: PLC0415

    assert acs_runtime._reported_model(_real_result_frame()) == "claude-haiku-4-5"


def test_several_models_pick_the_one_with_the_most_output_tokens():
    frame = {"type": "result", "modelUsage": {
        "claude-haiku-4-5": {"outputTokens": 12},
        "claude-opus-5-5": {"outputTokens": 1700},
    }}
    assert attested_model(_wrap(frame)) == "claude-opus-5-5"


def test_an_explicit_model_field_still_wins_over_model_usage():
    frame = {"type": "result", "model": "claude-sonnet-5", "modelUsage": {"claude-haiku-4-5": {"outputTokens": 9}}}
    assert attested_model(_wrap(frame)) == "claude-sonnet-5"


def test_empty_or_malformed_model_usage_is_unpriced_not_guessed():
    assert attested_model(_wrap({"type": "result", "modelUsage": {}})) == ""
    assert attested_model(_wrap({"type": "result", "modelUsage": {"": {"outputTokens": 1}}})) == ""
    assert attested_model(_wrap({"type": "result", "modelUsage": "nope"})) == ""
