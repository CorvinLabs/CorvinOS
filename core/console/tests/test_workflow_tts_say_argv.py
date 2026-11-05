"""SRF-5: workflow voice replies call say.py with its POSITIONAL contract.

say.py is ``say.py <out_path> <text> [<lang>]``. ``routes/workflows.py::_tts_b64``
used to pass ``--lang <lang> --out <tmp> <text>``: say.py spoke the word
"en"/"de" into a file literally named ``--lang`` in the console's CWD and every
workflow voice reply was None. Driven through a real subprocess: a stub say.py
records its argv and writes audio to argv[1]; the real say.py must refuse a
flag-shaped out path.
"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

_STUB = '''import json, sys
from pathlib import Path
Path(sys.argv[0]).with_name("argv.json").write_text(json.dumps(sys.argv[1:]), encoding="utf-8")
Path(sys.argv[1]).write_bytes(b"OggS-stub-audio")
'''


def test_tts_b64_speaks_the_text_into_the_temp_file(monkeypatch, tmp_path):
    import corvin_console.routes.workflows as wf
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "say.py").write_text(_STUB, encoding="utf-8")
    monkeypatch.setattr(wf, "_VOICE_SCRIPTS", scripts)
    monkeypatch.chdir(tmp_path)

    b64 = wf._tts_b64("Hello there", "en")

    argv = json.loads((scripts / "argv.json").read_text(encoding="utf-8"))
    out_path, text, lang = argv
    assert not out_path.startswith("-") and out_path.endswith(".ogg")
    assert (text, lang) == ("Hello there", "en")
    assert b64 is not None and base64.b64decode(b64) == b"OggS-stub-audio"
    assert not Path(out_path).exists(), "temp audio file must be removed"
    assert not (tmp_path / "--lang").exists()


def test_real_say_py_refuses_a_flag_as_out_path(tmp_path):
    r = subprocess.run(
        [sys.executable, str(REPO / "corvin_operator" / "voice" / "scripts" / "say.py"),
         "--lang", "en", "--out", str(tmp_path / "x.ogg"), "Hello"],
        cwd=str(tmp_path), capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 2, r.stderr
    assert "takes no flags" in r.stderr
    assert not (tmp_path / "--lang").exists()
