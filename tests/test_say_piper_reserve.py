"""say.py keeps budget for the offline Piper tier.

Regression: with OpenAI and edge-tts both hanging until their timeouts (an
offline laptop, a proxy that blackholes instead of refusing — routine on
corporate / Citrix desktops) the two network attempts consumed the whole chain
budget and Piper was skipped with "<1 s left". The one tier that needs no
network never spoke. The network tiers are now clamped so Piper keeps a
reserve whenever it has a model for the language.
"""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

_SAY = Path(__file__).resolve().parents[1] / "corvin_operator/voice/scripts/say.py"


def _load_say():
    spec = importlib.util.spec_from_file_location("_say_reserve_under_test", _SAY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(monkeypatch, tmp_path, *, piper_model: bool):
    monkeypatch.setenv("CORVIN_TTS_TOTAL_BUDGET_S", "6")
    monkeypatch.delenv("CORVIN_TTS_PROVIDER", raising=False)
    monkeypatch.delenv("CORVIN_TTS_LOCAL_ONLY", raising=False)
    say = _load_say()
    calls: dict[str, float] = {}

    def hang(name):
        def _f(out_path, text, lang, *rest):
            timeout_s = rest[-1]
            calls[name] = timeout_s
            time.sleep(timeout_s)   # a blackholed connection: uses all it gets
            return False
        return _f

    def piper(out_path, text, lang, timeout_s=None):
        calls["piper"] = timeout_s
        Path(out_path).write_bytes(b"RIFF")
        return True

    monkeypatch.setattr(say, "_try_openai", hang("openai"))
    monkeypatch.setattr(say, "_try_edge", hang("edge"))
    monkeypatch.setattr(say, "_try_piper", piper)
    monkeypatch.setattr(say, "_piper_model_exact",
                        lambda lang: tmp_path / "m.onnx" if piper_model else None)
    out = tmp_path / "out.wav"
    monkeypatch.setattr(sys, "argv", ["say.py", str(out), "Hallo Welt, ein Test.", "de"])
    say._PROCESS_START_MONOTONIC = time.monotonic()
    rc = say.main()
    return rc, calls


def test_piper_still_speaks_when_both_network_tiers_hang(monkeypatch, tmp_path, capsys):
    rc, calls = _run(monkeypatch, tmp_path, piper_model=True)
    assert rc == 0
    assert "piper" in calls, f"Piper was starved of budget: {calls}"
    assert calls["piper"] >= 1.0
    assert capsys.readouterr().out.strip().endswith("out.wav")


def test_no_reserve_without_a_model_for_the_language(monkeypatch, tmp_path):
    """No exact model → nothing to reserve for; network tiers get it all."""
    rc, calls = _run(monkeypatch, tmp_path, piper_model=False)
    assert calls["openai"] > 4.0


def test_network_budget_helper():
    say = _load_say()
    assert say.network_budget(None, 20, True) is None
    assert say.network_budget(10.0, 30, False) == 10.0
    assert say.network_budget(10.0, 30, True) == 10.0 - say._PIPER_RESERVE_S
    # a tiny total budget reserves at most a third of it
    assert abs(say.network_budget(6.0, 6, True) - 4.0) < 1e-9
