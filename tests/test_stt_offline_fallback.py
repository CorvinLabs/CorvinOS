"""Regression: the offline STT model fallback must pick the BEST present model.

VOICE-F4 — `_first_present_model()` used to return the alphabetically-first
`sorted()` glob hit, so a lingering `base-q5_1` (sorts before `medium`/`small`)
shadowed a strictly better model sitting in the same directory, silently
defeating the tier ladder. It must now return the highest-accuracy family
present.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parents[1]
for _p in (_REPO, _REPO / "operator" / "voice" / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from stt import local_whisper as lw  # type: ignore  # noqa: E402


def _make_models(tmp: Path, names: list[str]) -> None:
    for n in names:
        (tmp / f"ggml-{n}.bin").write_bytes(b"x")  # non-empty


def test_prefers_best_family_over_alphabetical_first(tmp_path):
    _make_models(tmp_path, ["base-q5_1", "small-q5_1"])
    with mock.patch.object(lw, "_models_dir", return_value=tmp_path):
        assert lw._first_present_model() == "small-q5_1"


def test_prefers_medium_when_present(tmp_path):
    _make_models(tmp_path, ["base-q5_1", "small-q5_1", "medium-q5_0"])
    with mock.patch.object(lw, "_models_dir", return_value=tmp_path):
        assert lw._first_present_model() == "medium-q5_0"


def test_none_when_empty(tmp_path):
    with mock.patch.object(lw, "_models_dir", return_value=tmp_path):
        assert lw._first_present_model() is None


def test_skips_zero_byte_files(tmp_path):
    (tmp_path / "ggml-medium-q5_0.bin").write_bytes(b"")  # empty → ignored
    (tmp_path / "ggml-small-q5_1.bin").write_bytes(b"x")
    with mock.patch.object(lw, "_models_dir", return_value=tmp_path):
        assert lw._first_present_model() == "small-q5_1"
