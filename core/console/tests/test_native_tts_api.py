"""corvin_console.native_tts — the chat TTS chain as a stable API for plugins (CorvinTV 1.8.0).

The module must not re-implement anything: argv, env, outer timeout and provider parsing are the chat
route's own (``routes/voice.py``), so a plugin narrates exactly like chat. These tests pin that parity,
the failure contract, and that a plugin job process can load the chain WITHOUT importing every console
router (``routes/__init__`` auto-imports all of them, some start schedulers).
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from corvin_console import native_tts
from corvin_console.routes import voice


def test_prepare_is_byte_identical_to_what_chat_tts_runs(tmp_path, monkeypatch):
    monkeypatch.delenv("CORVIN_TTS_PROVIDER", raising=False)
    out = tmp_path / "a.opus"
    text = "Hallo Welt. Ein kurzer Satz."
    prep = native_tts.prepare(text, out, "de")
    assert prep.argv == voice._say_cmd(out, text, "de")
    assert prep.timeout_s == voice._tts_timeout_for(text)
    assert prep.say_path == voice._VOICE_SCRIPTS / "say.py" and prep.say_path.is_file()
    assert set(voice._say_env()) == set(prep.env)
    assert native_tts.AUTO_CHAIN == ("openai", "edge", "piper")


def test_the_operator_pin_is_the_chat_pin(monkeypatch, tmp_path):
    monkeypatch.setenv("CORVIN_TTS_PROVIDER", "piper")
    assert native_tts.pinned_provider() == "piper" == voice._resolve_tts_provider()
    assert native_tts.expected_provider() == "piper"
    prep = native_tts.prepare("x", tmp_path / "a.opus", "de")
    assert prep.argv[-1] == "piper"  # say.py's provider slot, after the (empty) voice slot
    monkeypatch.setenv("CORVIN_TTS_PROVIDER", "auto")
    assert native_tts.pinned_provider() is None and native_tts.expected_provider() == "openai"


def test_stderr_parsing_names_the_tier_and_keeps_only_say_lines():
    err = "noise\nsay.py: no OPENAI_API_KEY — skipping OpenAI TTS\nsay.py: provider=edge\n"
    assert native_tts.provider_from_stderr(err) == "edge"
    assert native_tts.provider_from_stderr("say.py: provider=bogus\n") == ""
    assert native_tts.reasons_from_stderr(err) == ["say.py: no OPENAI_API_KEY — skipping OpenAI TTS"]


def test_finish_follows_the_chat_contract(tmp_path):
    prep = native_tts.Prepared(argv=[], env={}, timeout_s=1, out_path=tmp_path / "a.opus", say_path=tmp_path)
    with pytest.raises(native_tts.NativeTTSFailed) as ei:
        native_tts.finish(prep, 0, "", "say.py: edge-tts failed: TimeoutError\n")  # exit 0 + no path = no audio
    assert ei.value.reasons == ["say.py: edge-tts failed: TimeoutError"]
    with pytest.raises(native_tts.NativeTTSFailed, match="exited 2"):
        native_tts.finish(prep, 2, "", "")
    prep.out_path.write_bytes(b"ID3" + b"\0" * 64)
    res = native_tts.finish(prep, 0, str(prep.out_path), "say.py: provider=edge\n")
    assert (res.provider, res.mime, res.path) == ("edge", "audio/mpeg", prep.out_path)


def test_synthesize_runs_the_prepared_command(tmp_path, monkeypatch):
    fake = tmp_path / "fake_say.py"
    fake.write_text(textwrap.dedent("""
        import sys
        open(sys.argv[1], "wb").write(b"OggS" + b"\\0" * 64)
        sys.stderr.write("say.py: no OPENAI_API_KEY\\nsay.py: provider=piper\\n")
        sys.stdout.write(sys.argv[1])
    """), encoding="utf-8")
    monkeypatch.setattr(voice, "_say_cmd", lambda out, text, lang: [sys.executable, str(fake), str(out), text, lang])
    res = native_tts.synthesize("Hallo", tmp_path / "x.opus", "de")
    assert (res.provider, res.mime, res.reasons) == ("piper", "audio/ogg", ["say.py: no OPENAI_API_KEY"])


def test_a_plugin_process_loads_the_chain_without_every_console_router():
    code = ("import sys; from corvin_console import native_tts as n; v = n._voice(); "
            "print(v.__name__, n.say_path().is_file(), "
            "any(m.startswith('corvin_console.routes.') and m != 'corvin_console.routes.voice' "
            "and m.rsplit('.', 1)[-1] in ('models', 'task_graph_api', 'packages') for m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=180,
                         env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().splitlines()[-1] == "corvin_console.routes.voice True False"
