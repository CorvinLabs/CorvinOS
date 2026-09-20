#!/usr/bin/env python3
"""Guard: the voice pipeline must pin UTF-8 across every process boundary.

WHY THIS IS LOAD-BEARING. ``subprocess.run(..., text=True)`` WITHOUT ``encoding=``
encodes stdin with the LOCALE codec — cp1252 on a German Windows install. The
moment the text carries a character cp1252 cannot map (an emoji is the everyday
case: assistant replies open with U+1F44B constantly) the write raises
UnicodeEncodeError inside subprocess's stdin writer THREAD. That exception is not
an ``OSError``, so it escapes ``Popen._stdin_write`` BEFORE the
``self.stdin.close()`` at the end of that function — the child's stdin never
reaches EOF, the child blocks in ``sys.stdin.read()`` forever, and the parent
burns its ENTIRE timeout before raising ``TimeoutExpired``. Because it happens in
a daemon thread, the calling code sees only a timeout and the traceback goes to
the server's stderr where nobody looks.

Measured on the live console, 2026-09-20:

  POST /v1/console/voice/tts, reply text beginning with U+1F44B …… 154.1 s
  POST /v1/console/voice/tts, byte-identical text without it ……… 37.8 s

154 s is past any browser's patience, which is what "TTS im Chat geht nicht"
actually was. On ``/voice/session-summary`` the same hang lands in a
``TimeoutExpired`` branch that returns **204** — the DESIGNED "voice is off"
answer — so the voice summary silently never played and no error surfaced
anywhere.

THE OTHER HALF, equally load-bearing: the CHILD's own stdio. Under cp1252 a
script that merely prints LLM-written prose containing ``→`` or an emoji exits 1
with EMPTY stdout, and every caller reads that as "summarizer unavailable" and
speaks the raw, truncated text instead. Measured the same day: a child printing
``'Ergebnis → fertig'`` to a pipe exits 1 under cp1252.

It LOOKED fine for a long time only because the two bugs cancelled: cp1252
decodes an unmapped byte to a surrogate (``errors='surrogateescape'`` on stdio)
and re-encodes that surrogate back to the same byte on the way out, so a pure
echo round-trips. The cancellation ends the instant anything between the two ends
inspects, slices or logs the text — and it never applied to the parent-side
ENCODE at all, which is the hang above.
"""
from __future__ import annotations

import ast
import locale
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SCRIPTS = _REPO / "corvin_operator" / "voice" / "scripts"

# Every character here is chosen for a reason:
#   U+1F44B  f0 9f 91 8b  — the emoji actually observed in the live failure
#   U+1F410  f0 9f 90 90  — contains 0x90, which cp1252 cannot even DECODE
#   U+270D   e2 9c 8d     — contains 0x8d, likewise undecodable
#   U+2192   →            — not an emoji; a char an LLM writes in ordinary prose
#   ü / ß                 — plain German, encodable in cp1252 (must not regress)
_HOSTILE = "\U0001f44b Hallo \U0001f410 Ziege, Ergebnis → fertig, ✍ Notiz. Grüße."

# A child spawned for a probe must not inherit an outer UTF-8 override, or the
# test proves nothing about what the script itself does.
_NEUTRAL_ENV = {
    k: v for k, v in os.environ.items()
    if k not in ("PYTHONIOENCODING", "PYTHONUTF8")
}
# On a UTF-8 locale (Linux/macOS CI) the locale codec can encode _HOSTILE, so
# nothing would be exercised. Forcing the C locale with coercion disabled gives
# an ASCII stdio default there, making these probes discriminating on EVERY
# platform rather than only on the Windows box where the bug was found.
_NEUTRAL_ENV.setdefault("PYTHONCOERCECLOCALE", "0")
if os.name != "nt":
    _NEUTRAL_ENV["LC_ALL"] = "C"
    _NEUTRAL_ENV["LANG"] = "C"

# The scripts that exchange TEXT over a pipe in the voice pipeline.
_STDIO_SCRIPTS = ("summarize.py", "strip_for_tts.py", "summarize_smart.py")

def _encodable(ch: str) -> bool:
    """True when this machine's locale codec can represent *ch*."""
    try:
        ch.encode(locale.getpreferredencoding(False))
        return True
    except UnicodeEncodeError:
        return False


# Production files whose text-mode stdin writers must pin the codec. Deliberately
# a NAMED list, not the whole repo: the repo has ~300 text-mode spawns whose
# children only ever emit ASCII (git, systemctl, node --version), and a guard
# that flags all of them would be turned off within a week. These are the files
# on the path that carries user prose to a provider.
_PINNED_FILES = (
    "core/console/corvin_console/routes/voice.py",
    "core/console/corvin_console/chat_runtime.py",
    "core/plugins/corvin_plugins/providers/summary_provider.py",
    "corvin_operator/voice/scripts/summarize.py",
    "corvin_operator/bridges/shared/adapter.py",
)


def _probe(code: str, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run *code* in a FRESH interpreter. The parent pins UTF-8 so that what the
    probe reports is the CHILD's own choice, never an artefact of this reader."""
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120, cwd=str(_REPO), env=env or _NEUTRAL_ENV,
    )


# ── Child side: the scripts' own stdio ───────────────────────────────────────

def test_a_fresh_interpreter_does_not_already_use_utf8_stdio() -> None:
    """POSITIVE CONTROL for the two tests below.

    Without it, ``test_voice_scripts_force_utf8_stdio`` passes vacuously on any
    environment that is already UTF-8 (a CI image with PYTHONUTF8=1, a UTF-8
    locale), and would keep passing after someone deleted every
    ``_force_utf8_stdio()`` call. Here we prove the baseline under the SAME env
    the other probes use is NOT utf-8, so a change observed there is genuinely
    caused by the script.
    """
    out = _probe("import sys; print(sys.stdout.encoding)").stdout.strip()
    assert out and out.lower().replace("_", "-") not in ("utf-8", "utf8"), (
        f"baseline stdio is already {out!r}, so the probes below cannot "
        "distinguish a script that pins UTF-8 from one that does not. Fix this "
        "test's environment scrubbing before trusting the rest of the file."
    )


@pytest.mark.parametrize("script", _STDIO_SCRIPTS)
def test_voice_script_exists_at_its_canonical_path(script: str) -> None:
    """A missing script FAILS. It must never skip: the last time a voice fix was
    lost, it was lost to a rename that re-added the pre-fix file, and a skipping
    guard would have stayed green through it (see test_voice_tls_trust_store.py).
    """
    assert (_SCRIPTS / script).is_file(), (
        f"{(_SCRIPTS / script).relative_to(_REPO)} is missing. If it MOVED, "
        "update this test AND verify the moved copy still calls "
        "_force_utf8_stdio() at import time."
    )


@pytest.mark.parametrize("script", _STDIO_SCRIPTS)
def test_voice_scripts_force_utf8_stdio(script: str) -> None:
    """Importing the script must leave stdin AND stdout on UTF-8.

    Behavioural, not a source grep: a ``_force_utf8_stdio`` that is defined but
    never called — or called after something already read stdin — passes any text
    search while leaving the bug in place.
    """
    mod = script[:-3]
    res = _probe(
        f"import sys; sys.path.insert(0, r'{_SCRIPTS}')\n"
        f"import {mod}\n"
        "print('STDIO', sys.stdin.encoding, sys.stdout.encoding)"
    )
    assert res.returncode == 0, f"importing {script} failed:\n{res.stderr[-1500:]}"
    line = next((l for l in res.stdout.splitlines() if l.startswith("STDIO")), "")
    assert line, f"probe printed no STDIO marker. stdout:\n{res.stdout[-1000:]}"
    _, stdin_enc, stdout_enc = line.split()
    norm = lambda e: e.lower().replace("_", "-")  # noqa: E731
    assert norm(stdin_enc) == "utf-8" and norm(stdout_enc) == "utf-8", (
        f"{script} runs with stdin={stdin_enc} stdout={stdout_enc}. Under a "
        "non-UTF-8 locale it will exit 1 with empty stdout the first time the "
        "summary contains '→' or an emoji, and every caller reads that as "
        "'summarizer unavailable' and speaks the raw truncated text."
    )


@pytest.mark.timeout(120)
@pytest.mark.parametrize("script,args,verbatim", [
    # strip_for_tts is a pure filter: on prose with no code fence its output is
    # the input, so every hostile character must come back verbatim.
    ("strip_for_tts.py", ["--mode", "code-only"], True),
    # summarize_smart REWRITES from templates and may drop a trailing sentence,
    # so demanding a specific character back would assert the fixture, not the
    # codec. What must hold is that it exits 0 and that at least one character
    # the locale codec cannot encode made it through both pipe ends.
    ("summarize_smart.py", ["--lang", "de", "--max-chars", "300"], False),
])
def test_hostile_text_survives_the_real_pipe(script: str, args: list[str],
                                             verbatim: bool) -> None:
    """END-TO-END over a real pipe: emoji in, emoji out, exit 0.

    ``summarize.py`` itself is deliberately NOT in this list — it would reach for
    the ``claude`` CLI and the network, making the test slow and flaky. Its own
    stdio is covered behaviourally above, and its two pipe boundaries (the CLI
    spawn, and the console spawning it) are covered by the AST guard below.
    """
    res = subprocess.run(
        [sys.executable, str(_SCRIPTS / script), *args],
        input=_HOSTILE, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        timeout=100, env=_NEUTRAL_ENV, cwd=str(_REPO),
    )
    assert res.returncode == 0, (
        f"{script} exited {res.returncode} on text containing an emoji.\n"
        f"stderr:\n{res.stderr[-1500:]}"
    )
    assert res.stdout.strip(), (
        f"{script} exited 0 but produced EMPTY stdout, which every caller in the "
        "voice pipeline reads as 'unavailable' and answers with raw text or a 204."
    )
    if verbatim:
        for ch, why in (("\U0001f44b", "the emoji observed in the live failure"),
                        ("\U0001f410", "an emoji whose UTF-8 contains 0x90"),
                        ("→", "an arrow, which cp1252 cannot encode at all"),
                        ("ü", "a plain German umlaut (must not regress)")):
            assert ch in res.stdout, (
                f"{script} dropped {ch!r} ({why}). stdout was:\n{res.stdout[:800]}"
            )
    else:
        survivors = [c for c in _HOSTILE if c in res.stdout and not _encodable(c)]
        assert survivors, (
            f"{script} returned text but not one character the locale codec "
            f"cannot encode survived the round trip. stdout:\n{res.stdout[:800]}"
        )


# ── Parent side: the spawn sites ─────────────────────────────────────────────

def _unpinned_stdin_writers(tree: ast.Module) -> list[int]:
    """Line numbers of text-mode subprocess calls that write stdin without
    pinning ``encoding``. Also catches the ``Popen(text=True)`` +
    ``communicate(input=...)`` split, which a naive scan for ``input=`` on the
    same call misses — and which is exactly how adapter.py's legacy engine path
    kept the defect after the obvious sites were fixed."""
    bad: list[int] = []
    popen_text_unpinned: list[ast.Call] = []
    writes_stdin = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        attr = node.func.attr
        kw = {k.arg for k in node.keywords if k.arg}
        textish = bool(kw & {"text", "universal_newlines", "encoding"})
        pinned = "encoding" in kw
        if attr in ("run", "check_output") and "input" in kw and textish and not pinned:
            bad.append(node.lineno)
        elif attr == "Popen" and textish and not pinned:
            popen_text_unpinned.append(node)
        elif attr == "communicate" and "input" in kw:
            writes_stdin = True
    if writes_stdin:
        bad.extend(p.lineno for p in popen_text_unpinned)
    return sorted(set(bad))


def test_the_ast_scanner_actually_detects_the_defect() -> None:
    """POSITIVE CONTROL for the scan below.

    An AST walk that silently stops matching — a renamed attribute, a changed
    keyword — reports zero findings, which is indistinguishable from a clean
    tree. Prove the scanner is red against known-bad code, and green against the
    pinned form, before believing a zero.
    """
    bad_run = ast.parse(
        "import subprocess\n"
        "subprocess.run(['x'], input=t, capture_output=True, text=True, timeout=5)\n"
    )
    assert _unpinned_stdin_writers(bad_run) == [2], "scanner missed an unpinned run()"

    bad_popen = ast.parse(
        "import subprocess\n"
        "p = subprocess.Popen(['x'], stdin=subprocess.PIPE, text=True)\n"
        "p.communicate(input=t, timeout=5)\n"
    )
    assert _unpinned_stdin_writers(bad_popen) == [2], (
        "scanner missed the Popen(text=True) + communicate(input=...) split"
    )

    good = ast.parse(
        "import subprocess\n"
        "subprocess.run(['x'], input=t, capture_output=True, text=True,\n"
        "               encoding='utf-8', errors='replace', timeout=5)\n"
    )
    assert _unpinned_stdin_writers(good) == [], "scanner false-positives on pinned code"


@pytest.mark.parametrize("rel", _PINNED_FILES)
def test_voice_path_spawns_pin_the_codec(rel: str) -> None:
    """No file on the voice path may write text to a child's stdin under the
    locale codec. See the module docstring for what that costs in production."""
    path = _REPO / rel
    assert path.is_file(), (
        f"{rel} is missing. If it MOVED, update _PINNED_FILES — do not delete "
        "the entry: an un-scanned file is how this defect returns."
    )
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    offenders = _unpinned_stdin_writers(tree)
    assert not offenders, (
        f"{rel} writes text to a child's stdin without encoding= at line(s) "
        f"{offenders}. Add encoding=\"utf-8\", errors=\"replace\". On a cp1252 "
        "locale the first emoji makes subprocess's writer thread die, the child "
        "never sees EOF, and the call hangs for its full timeout — silently, "
        "because the traceback is raised in a daemon thread."
    )


def _locale_can_encode_hostile() -> bool:
    try:
        _HOSTILE.encode(locale.getpreferredencoding(False))
        return True
    except UnicodeEncodeError:
        return False


@pytest.mark.skipif(
    _locale_can_encode_hostile(),
    reason="locale codec encodes the payload fine — no defect to reproduce here",
)
def test_the_hostile_payload_really_is_hostile_to_the_locale_codec() -> None:
    """POSITIVE CONTROL for the whole file, on the platform where it matters.

    Proves the payload is genuinely un-encodable in THIS machine's locale codec,
    i.e. that an unpinned parent really would have hung on it. Skipped with a
    reason on a UTF-8 locale, where there is no defect to reproduce — which is
    also why the probes above force a non-UTF-8 stdio default instead of trusting
    the ambient locale.
    """
    with pytest.raises(UnicodeEncodeError):
        _HOSTILE.encode(locale.getpreferredencoding(False))
