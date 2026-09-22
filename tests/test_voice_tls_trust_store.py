#!/usr/bin/env python3
"""test_voice_tls_trust_store.py — guard for the voice subprocess's TLS trust anchor.

``say.py`` must verify outbound TLS against the OPERATING-SYSTEM trust store, not
only against certifi's bundled CA list.

WHY THIS IS LOAD-BEARING. On a machine behind an HTTPS-inspecting proxy every
connection is re-signed with an internal root CA that lives in the OS store and is
ABSENT from ``certifi/cacert.pem``. edge-tts builds its aiohttp context as
``ssl.create_default_context(cafile=certifi.where())`` (``edge_tts/communicate.py``),
so it fails the handshake to ``speech.platform.bing.com`` with
``CERTIFICATE_VERIFY_FAILED``. ``say.py::_try_edge`` swallows that content-free as
``edge-tts failed: ClientConnectorCertificateError``, the whole provider chain runs
out (OpenAI needs a key, Piper needs a downloaded model), and say.py exits 0 with
empty stdout. The console turns that into a 204, and a 204 is the DESIGNED silent
degradation for turn voice — so the chat simply never speaks, with no error
anywhere. Measured live 2026-09-20 on ALLIANZDE.

Note that ``SSL_CERT_FILE`` / ``REQUESTS_CA_BUNDLE`` cannot fix this: an explicit
``cafile=`` argument overrides both. Injection is the only lever that reaches a
dependency pinning certifi in its own module scope.

FOUR SITES, ALL REQUIRED — they are not redundant. The anchor is process-wide and
these four processes share no import: ``standalone.py`` anchors the console/uvicorn
PROCESS; ``say.py`` runs as a separate SUBPROCESS and inherits no Python-level
monkeypatch; ``bridges/shared/adapter.py`` is the bridge daemon, which synthesises
IN-PROCESS (``_try_edge_tts`` / ``synthesize_voice_note``) and is also what
``corvin-voice doctor`` exercises; ``corvin_gateway/app.py`` is the host that
``corvin-service`` runs and that the installer registers on every fresh install.
"Console healthy, TTS silent" is exactly the shape of such a gap, and the bridge half
of it was still open after the console half was fixed — visible on 2026-09-20 as
``edge TTS: synthesis failed: ... CERTIFICATE_VERIFY_FAILED`` in
``test_adapter_progress.py``'s own log output while the console spoke fine. Hence the
parity assertions below.

This guard exists because the fix was silently reverted once: commit c7ea7449
(2026-09-15) added it under the then-current ``operator/`` tree, which was later
renamed to ``corvin_operator/`` by re-adding PRE-FIX files instead of ``git mv``.
``say.py`` regressed to byte-identical-to-pre-fix and nothing failed.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SAY = _REPO / "corvin_operator" / "voice" / "scripts" / "say.py"
_STANDALONE = _REPO / "core" / "console" / "corvin_console" / "standalone.py"


def _probe(code: str) -> str:
    """Run *code* in a FRESH interpreter and return its stdout, stripped.

    A subprocess is mandatory, not stylistic: ``truststore.inject_into_ssl()``
    mutates the ``ssl`` module process-wide, so if anything else in the pytest
    session injected first, an in-process assertion would pass no matter what
    say.py does. Each probe here starts from a clean interpreter.
    """
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=120,
        cwd=str(_REPO),
        encoding="utf-8", errors="replace",
    )
    assert proc.returncode == 0, (
        f"probe interpreter failed (rc={proc.returncode}):\n{proc.stderr[-2000:]}"
    )
    return proc.stdout.strip()


def test_say_py_exists_at_its_canonical_path() -> None:
    """A missing say.py is a FAILURE, not a skip — see the module docstring: a
    rename that re-adds files is precisely how this fix was lost before."""
    assert _SAY.is_file(), (
        f"{_SAY.relative_to(_REPO)} is missing. If it MOVED, update this test "
        "AND verify the moved copy still calls _use_os_trust_store() at import."
    )


def test_a_fresh_interpreter_does_not_have_truststore_injected() -> None:
    """POSITIVE CONTROL for the test below.

    Without this, ``test_importing_say_py_anchors_tls_to_the_os_store`` would pass
    vacuously on any environment that injects truststore some other way (a
    sitecustomize hook, a vendored patch). Here we prove the baseline is plain
    stdlib ssl, so the change observed below is genuinely caused by say.py.
    """
    assert _probe("import ssl; print(ssl.SSLContext.__module__)") == "ssl"


def test_importing_say_py_anchors_tls_to_the_os_store() -> None:
    """Importing say.py must swap ``ssl.SSLContext`` for truststore's.

    Behavioural, not a source grep: it proves the call actually runs at import
    time. A ``_use_os_trust_store`` that is defined but never invoked — or invoked
    too late, after a provider built its context — would pass a text search and
    still leave TTS dead.
    """
    out = _probe(
        "import sys; sys.path.insert(0, r'%s')\n"
        "import say, ssl\n"
        "print(ssl.SSLContext.__module__)" % _SAY.parent
    )
    assert out.startswith("truststore"), (
        f"say.py did not anchor TLS to the OS trust store (ssl.SSLContext came "
        f"from {out!r}, expected a truststore module). edge-tts will fail with "
        "ClientConnectorCertificateError behind a TLS-inspecting proxy and the "
        "console chat will silently never speak."
    )


def test_the_console_process_anchors_tls_too() -> None:
    """``standalone.py`` covers the console process. Asserted at source level
    because importing the console app pulls in the whole route tree; the point
    here is only that the second of the two required sites has not vanished."""
    assert _STANDALONE.is_file(), f"{_STANDALONE.relative_to(_REPO)} is missing"
    src = _STANDALONE.read_text(encoding="utf-8", errors="replace")
    assert "inject_into_ssl" in src, (
        "standalone.py no longer anchors TLS to the OS trust store — the console "
        "process itself (not just the say.py subprocess) needs this."
    )


def test_the_bridge_daemon_anchors_tls_too() -> None:
    """``bridges/shared/adapter.py`` synthesises edge-tts IN-PROCESS, so it needs
    its own anchor — the say.py subprocess's injection cannot reach it.

    Behavioural (a real import in a fresh interpreter), not a source grep: adapter
    is ~10k lines with several conditional import blocks, and the only thing that
    matters is whether the call has actually run by the time a provider could open
    a socket. The marker prefix is needed because importing adapter emits log lines
    of its own.
    """
    adapter_path = _REPO / "corvin_operator" / "bridges" / "shared" / "adapter.py"
    assert adapter_path.is_file(), (
        f"{adapter_path.relative_to(_REPO)} is missing. If it MOVED, update this "
        "test AND verify the moved copy still calls _use_os_trust_store()."
    )
    out = _probe(
        "import sys; sys.path.insert(0, r'%s')\n"
        "import adapter, ssl\n"
        "print('SSLCTX=' + ssl.SSLContext.__module__)" % adapter_path.parent
    )
    line = next((l for l in out.splitlines() if l.startswith("SSLCTX=")), "")
    assert line, f"probe printed no SSLCTX marker; stdout was:\n{out[-2000:]}"
    module = line.split("=", 1)[1]
    assert module.startswith("truststore"), (
        f"adapter.py did not anchor TLS to the OS trust store (ssl.SSLContext came "
        f"from {module!r}). The bridge's in-process edge-tts — and every "
        "`corvin-voice doctor` TTS round-trip — will fail the handshake behind a "
        "TLS-inspecting proxy while the console itself works, which is the most "
        "misleading shape this bug has."
    )


def test_the_gateway_host_anchors_tls_without_help_from_the_console() -> None:
    """``corvin_gateway.app`` must anchor TLS BY ITSELF, not as a side effect.

    That host is what ``corvin-service`` runs and what ``corvinOS/installer/core.py``
    registers as the persistent WebUI service on every fresh install, so its own
    egress — A2A pairing, the marketplace index, the run dispatcher, outbound
    webhooks — depends on this anchor.

    WHY THE OBVIOUS TEST IS VACUOUS. A bare ``import corvin_gateway.app`` followed by
    an ``ssl.SSLContext`` check passed LONG BEFORE the gateway had a
    ``_use_os_trust_store()`` of its own: the ADR-0015 opt-in console mount
    (``try: from corvin_console import app``) transitively imports
    ``corvin_console.standalone`` and ``say``, and each injects at its own module
    import. That mount is deliberately failure-tolerant — an ImportError anywhere in
    the console's ~120 route modules drops ``/console`` and every ``/v1/console/*``
    route while the gateway keeps serving (it happened: 9433de4b, 2026-09-17) — so
    the naive assertion cannot distinguish "the gateway is anchored" from "the
    gateway is anchored only for as long as the console happens to import".

    So this probe BLOCKS all three accidental injectors and then requires the anchor
    anyway. The ``LOADED[...]=False`` lines are the positive control: without them a
    blocker that silently failed to reach its targets would make this test green for
    the wrong reason. Measured 2026-09-22: pre-fix this probe printed ``SSLCTX=ssl``,
    post-fix ``SSLCTX=truststore._api``.
    """
    gateway_path = _REPO / "core" / "gateway" / "corvin_gateway" / "app.py"
    assert gateway_path.is_file(), (
        f"{gateway_path.relative_to(_REPO)} is missing. If it MOVED, update this "
        "test AND verify the moved copy still calls _use_os_trust_store()."
    )
    blocked = ("say", "corvin_console.standalone", "adapter")
    out = _probe(
        "import sys, ssl\n"
        "_BLOCKED = %r\n"
        "class _Block:\n"
        "    def find_spec(self, name, target=None, path=None):\n"
        "        if name in _BLOCKED:\n"
        "            raise ImportError('blocked by probe: ' + name)\n"
        "        return None\n"
        "sys.meta_path.insert(0, _Block())\n"
        "sys.path.insert(0, r'%s')\n"
        "import corvin_gateway.app  # noqa: F401\n"
        "print('SSLCTX=' + ssl.SSLContext.__module__)\n"
        "for _m in _BLOCKED:\n"
        "    print('LOADED[%%s]=%%s' %% (_m, _m in sys.modules))\n"
        % (blocked, _REPO / "core" / "gateway")
    )
    lines = dict(
        l.split("=", 1) for l in out.splitlines() if "=" in l and l.startswith(("SSLCTX", "LOADED"))
    )
    # Positive control FIRST: prove the blocker actually reached every injector,
    # so a green result below cannot come from one of them sneaking in.
    for name in blocked:
        assert lines.get(f"LOADED[{name}]") == "False", (
            f"probe failed to block {name!r} (sys.modules says it loaded), so the "
            "anchor assertion below would prove nothing about the gateway's own "
            f"call. Probe output:\n{out[-2000:]}"
        )
    module = lines.get("SSLCTX", "")
    assert module.startswith("truststore"), (
        f"corvin_gateway.app does not anchor TLS on its own (ssl.SSLContext came "
        f"from {module!r}). With the console mount failing — which it survives by "
        "design — every outbound HTTPS call this host makes reverts to certifi-only "
        "and fails CERTIFICATE_VERIFY_FAILED behind a TLS-inspecting proxy."
    )


def test_the_gateway_anchors_before_it_mounts_the_console() -> None:
    """Order matters, and only source order can express it.

    ``_use_os_trust_store()`` has to run before the module-level console mount and
    before anything that could open a socket; an injection that lands after a client
    built its SSL context is a no-op for that client. The behavioural test above
    cannot see ordering — it only samples the end state.
    """
    src = (_REPO / "core" / "gateway" / "corvin_gateway" / "app.py").read_text(
        encoding="utf-8", errors="replace")
    call = src.find("\n_use_os_trust_store()")
    mount = src.find("from corvin_console import app as _console_app")
    assert call != -1, (
        "corvin_gateway/app.py no longer calls _use_os_trust_store() at module "
        "level — see test_the_gateway_host_anchors_tls_without_help_from_the_console."
    )
    assert mount != -1, (
        "the console-mount line moved; re-point this ordering assertion at whatever "
        "now performs the ADR-0015 opt-in console import."
    )
    assert call < mount, (
        "_use_os_trust_store() runs AFTER the console mount. The mount imports "
        "route modules that open no socket today, but the ordering is the whole "
        "point: the anchor must be in force before anything can build an SSL "
        "context, not merely present somewhere in the file."
    )


@pytest.mark.parametrize("rel,needle", [
    ("pyproject.toml", "truststore"),
    ("core/console/requirements.txt", "truststore"),
])
def test_truststore_is_a_declared_dependency(rel: str, needle: str) -> None:
    """A FRESH INSTALL must get truststore without anyone remembering to add it.

    ``_use_os_trust_store()`` is deliberately a guarded no-op when the package is
    absent — which keeps a vendored env working, but also means a missing
    declaration degrades silently back to the broken-behind-a-proxy behaviour
    instead of failing loudly. The declaration is what makes the fix real on a
    machine nobody has debugged yet.
    """
    text = (_REPO / rel).read_text(encoding="utf-8", errors="replace")
    assert needle in text, (
        f"{rel} does not declare {needle}. Fresh installs would ship without it "
        "and TTS would be silent behind any TLS-inspecting corporate proxy."
    )


@pytest.mark.timeout(180)
@pytest.mark.skipif(
    os.environ.get("CORVIN_LIVE_VOICE_E2E") != "1",
    reason="live network test — opt in with CORVIN_LIVE_VOICE_E2E=1",
)
def test_live_edge_tts_actually_synthesizes_audio(tmp_path) -> None:
    """END-TO-END through the real say.py subprocess, over the real network.

    This is the only assertion here that proves the USER-VISIBLE outcome: bytes
    of speakable audio come back. Pinned to ``edge`` with ``CORVIN_SAY_NO_FALLBACK``
    so a working Piper/OpenAI tier cannot mask an edge-tts failure — the exact
    masking that let this regression hide.
    """
    out_path = tmp_path / "spoken.out"
    env = {**os.environ, "CORVIN_TTS_PROVIDER": "edge", "CORVIN_SAY_NO_FALLBACK": "1"}
    proc = subprocess.run(
        [sys.executable, str(_SAY), str(out_path),
         "Die Sprachausgabe funktioniert.", "de", "", "edge"],
        capture_output=True, text=True, timeout=150, env=env,
        encoding="utf-8", errors="replace",
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert out_path.is_file() and out_path.stat().st_size > 1000, (
        "edge-tts produced no audio. stderr:\n" + proc.stderr[-2000:]
    )
    # MP3 (ID3 tag or a raw MPEG frame sync) — what edge-tts emits.
    head = out_path.read_bytes()[:3]
    assert head[:3] == b"ID3" or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"), (
        f"output is not the MP3 edge-tts should produce (first bytes: {head!r})"
    )
