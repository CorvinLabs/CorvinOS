"""The OpenAI TTS tier must be usable against an APPROVED endpoint, not only
against api.openai.com.

Both OpenAI tiers (``routes/voice.py::_try_openai_tts`` in-process and
``say.py::_try_openai`` in the subprocess chain) hardcoded the public endpoint
and ``model="tts-1"``. On any install where api.openai.com is unreachable the
preferred tier was therefore dead with no way to revive it — measured
2026-09-21 on a corporate-proxied box: every request to api.openai.com returns
HTTP 403 from the TLS proxy, website category "Generative AI and ML
Applications", with the block page pointing at approved internal AI systems.
There was no setting that could aim the tier at one of those.

Azure OpenAI is the usual shape of such an endpoint and is NOT reachable by a
plain base_url swap — it authenticates with an ``api-key`` header, requires an
``api-version`` and addresses the model by DEPLOYMENT name — hence the flavour
split these tests pin.

The endpoint test below is a real E2E: a live OpenAI-compatible HTTP server on
loopback, reached by the real ``openai`` SDK over a real socket from the real
route. No mock of the component under test.
"""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

_CONSOLE = Path(__file__).resolve().parents[1]
if str(_CONSOLE) not in sys.path:
    sys.path.insert(0, str(_CONSOLE))

from corvin_console.routes import voice as V

# A one-frame silent MP3 is enough: _detect_audio_mime sniffs the sync word, so
# the response is typed audio/mpeg exactly as a real provider's would be.
_FAKE_AUDIO = b"\xff\xfb\x90\x00" + b"\x00" * 512


class _OpenAICompatibleTTS(BaseHTTPRequestHandler):
    """Minimal stand-in for the one endpoint the tier calls."""

    requests: list[dict] = []

    def do_POST(self):  # noqa: N802 — BaseHTTPRequestHandler's API
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except ValueError:
            payload = {"_unparsable": True}
        type(self).requests.append({
            "path": self.path,
            "payload": payload,
            "authorization": self.headers.get("Authorization"),
            "api_key_header": self.headers.get("api-key"),
        })
        self.send_response(200)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Content-Length", str(len(_FAKE_AUDIO)))
        self.end_headers()
        self.wfile.write(_FAKE_AUDIO)

    def log_message(self, *a):  # silence the default stderr spam
        return


@pytest.fixture
def tts_server():
    _OpenAICompatibleTTS.requests = []
    srv = HTTPServer(("127.0.0.1", 0), _OpenAICompatibleTTS)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_port}/v1", _OpenAICompatibleTTS
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.fixture(autouse=True)
def _endpoint_env(monkeypatch):
    """Neutralise the box's own configuration so these tests are hermetic, and
    lift the conftest-wide local-only default that would skip the tier."""
    monkeypatch.delenv("CORVIN_TTS_LOCAL_ONLY", raising=False)
    for name in ("CORVIN_TTS_OPENAI_BASE_URL", "OPENAI_BASE_URL",
                 "CORVIN_TTS_OPENAI_MODEL", "CORVIN_TTS_OPENAI_API_VERSION"):
        monkeypatch.delenv(name, raising=False)
    # _openai_tts_endpoint resolves through provider_keys (env → secrets.enc →
    # service.env), so a value in this box's service.env would leak in.
    monkeypatch.setattr(V, "_openai_tts_endpoint",
                        lambda: _resolved_from_env(), raising=True)


def _resolved_from_env():
    import os
    return ((os.environ.get("CORVIN_TTS_OPENAI_BASE_URL") or "").strip(),
            (os.environ.get("CORVIN_TTS_OPENAI_API_VERSION")
             or V._AZURE_OPENAI_DEFAULT_API_VERSION).strip(),
            (os.environ.get("CORVIN_TTS_OPENAI_MODEL") or "tts-1").strip())


# ── E2E: the tier really speaks to a configured endpoint ────────────────────

def test_openai_tier_synthesizes_against_a_configured_endpoint(monkeypatch, tts_server):
    """The whole point: with a base URL configured, the OpenAI tier produces
    audio from THAT endpoint over a real socket — the tier is alive on an
    install where api.openai.com is unreachable."""
    base_url, handler = tts_server
    monkeypatch.setenv("CORVIN_TTS_OPENAI_BASE_URL", base_url)
    monkeypatch.setenv("CORVIN_TTS_OPENAI_MODEL", "my-tts-deployment")
    monkeypatch.setattr(V, "_resolve_tts_voice", lambda lang: "shimmer")
    import provider_keys as _pk
    monkeypatch.setattr(_pk, "resolve_key", lambda name: "sk-test-key")

    data = V._try_openai_tts("Hallo Welt.", "de", "shimmer", "_default")

    assert data == _FAKE_AUDIO, "audio must come from the configured endpoint"
    assert len(handler.requests) == 1, handler.requests
    req = handler.requests[0]
    assert req["path"].endswith("/audio/speech"), req["path"]
    assert req["payload"]["model"] == "my-tts-deployment", (
        "a hardcoded tts-1 cannot address an operator-named deployment")
    assert req["payload"]["voice"] == "shimmer"
    assert req["payload"]["input"] == "Hallo Welt."
    assert req["authorization"] == "Bearer sk-test-key", (
        "the non-Azure flavour must use bearer auth")


def test_unconfigured_endpoint_still_targets_openai(monkeypatch):
    """No setting → the SDK default. The out-of-the-box path must not change,
    so this asserts the client was built WITHOUT a base_url override."""
    seen = {}

    class _FakeClient:
        def __init__(self, **kwargs):
            seen.update(kwargs)
            self.audio = self

        @property
        def speech(self):
            return self

        def create(self, **kwargs):
            raise RuntimeError("stop here — construction is what we assert")

    monkeypatch.setitem(sys.modules, "openai",
                        type(sys)("openai"))
    sys.modules["openai"].OpenAI = _FakeClient
    sys.modules["openai"].AzureOpenAI = _FakeClient
    import provider_keys as _pk
    monkeypatch.setattr(_pk, "resolve_key", lambda name: "sk-test-key")

    assert V._try_openai_tts("Hallo.", "de", "nova", "_default") is None
    assert "base_url" not in seen, (
        "an unconfigured install must use the SDK default endpoint")


def test_azure_endpoint_uses_the_azure_client(monkeypatch):
    """Azure needs api-key auth + api-version + deployment-as-model. A plain
    OpenAI client pointed at the same host 401s every request, so the flavour
    split is load-bearing, not cosmetic."""
    built = {}

    class _Azure:
        def __init__(self, **kwargs):
            built["azure"] = kwargs
            raise RuntimeError("construction is what we assert")

    class _Plain:
        def __init__(self, **kwargs):
            built["plain"] = kwargs
            raise RuntimeError("must not be used for an Azure endpoint")

    monkeypatch.setitem(sys.modules, "openai", type(sys)("openai"))
    sys.modules["openai"].OpenAI = _Plain
    sys.modules["openai"].AzureOpenAI = _Azure
    monkeypatch.setenv("CORVIN_TTS_OPENAI_BASE_URL",
                       "https://allianz-ai.openai.azure.com")
    monkeypatch.setenv("CORVIN_TTS_OPENAI_API_VERSION", "2025-01-01-preview")
    import provider_keys as _pk
    monkeypatch.setattr(_pk, "resolve_key", lambda name: "sk-test-key")

    assert V._try_openai_tts("Hallo.", "de", "nova", "_default") is None
    assert "plain" not in built, "an Azure endpoint must not use the plain client"
    assert built["azure"]["azure_endpoint"] == "https://allianz-ai.openai.azure.com"
    assert built["azure"]["api_version"] == "2025-01-01-preview"
    assert built["azure"]["max_retries"] == 0


# ── the endpoint is where the API KEY goes — so it is validated ─────────────

def test_plaintext_endpoint_is_refused_before_the_key_travels(monkeypatch):
    """http:// to a remote host would put the API key on the wire in clear
    text. Refuse and fall through to say.py rather than send it."""
    monkeypatch.setenv("CORVIN_TTS_OPENAI_BASE_URL", "http://tts.example.com/v1")
    import provider_keys as _pk
    monkeypatch.setattr(_pk, "resolve_key", lambda name: "sk-test-key")

    def _explode(**kwargs):
        raise AssertionError("no client may be constructed for a plaintext URL")

    monkeypatch.setitem(sys.modules, "openai", type(sys)("openai"))
    sys.modules["openai"].OpenAI = _explode
    sys.modules["openai"].AzureOpenAI = _explode

    assert V._try_openai_tts("Hallo.", "de", "nova", "_default") is None


def test_loopback_http_is_allowed():
    """A local OpenAI-compatible server is a legitimate, egress-free setup —
    and refusing it would make the E2E above impossible to write honestly."""
    assert V._openai_tts_base_url_rejected(
        "http://127.0.0.1:1234/v1", "_default") is None
    assert V._openai_tts_base_url_rejected(
        "http://localhost:1234/v1", "_default") is None


@pytest.mark.parametrize("url", ["http://tts.example.com/v1", "ftp://x/y", "://"])
def test_non_https_remote_urls_are_rejected(url):
    assert V._openai_tts_base_url_rejected(url, "_default") is not None


def test_local_only_still_wins_over_a_configured_endpoint(monkeypatch):
    """CORVIN_TTS_LOCAL_ONLY=1 is the EU local-only egress guarantee (L35).
    A configured endpoint is still a cloud endpoint — the flag must keep
    disabling the tier, checked before any key or endpoint resolution."""
    monkeypatch.setenv("CORVIN_TTS_LOCAL_ONLY", "1")
    monkeypatch.setenv("CORVIN_TTS_OPENAI_BASE_URL",
                       "https://allianz-ai.openai.azure.com")

    def _explode(**kwargs):
        raise AssertionError("local-only must short-circuit before any client")

    monkeypatch.setitem(sys.modules, "openai", type(sys)("openai"))
    sys.modules["openai"].OpenAI = _explode
    sys.modules["openai"].AzureOpenAI = _explode

    assert V._try_openai_tts("Hallo.", "de", "nova", "_default") is None


# ── both tiers must aim at the SAME endpoint ───────────────────────────────

def test_say_env_forwards_the_endpoint_to_the_subprocess(monkeypatch):
    """say.py re-derives its own config in a child process. If the endpoint did
    not travel, the console's in-process tier would call the approved endpoint
    while its own subprocess fallback called the unreachable public one."""
    monkeypatch.setenv("CORVIN_TTS_OPENAI_BASE_URL",
                       "https://allianz-ai.openai.azure.com")
    monkeypatch.setenv("CORVIN_TTS_OPENAI_MODEL", "my-tts-deployment")
    env = V._say_env()
    assert env["CORVIN_TTS_OPENAI_BASE_URL"] == "https://allianz-ai.openai.azure.com"
    assert env["CORVIN_TTS_OPENAI_MODEL"] == "my-tts-deployment"
    assert env["CORVIN_TTS_OPENAI_API_VERSION"], "Azure needs an api-version"


def test_say_py_reads_the_same_three_settings():
    """Source-coupling guard: the two tiers are separate processes and cannot
    share a module (say.py is deliberately dependency-light and runs under the
    system interpreter), so the ONLY thing keeping them aimed at the same
    endpoint is that they read the same variable names. A rename on either side
    is silent — the subprocess tier just quietly reverts to api.openai.com."""
    src = (Path(__file__).resolve().parents[3] / "corvin_operator" / "voice"
           / "scripts" / "say.py").read_text(encoding="utf-8")
    for name in ("CORVIN_TTS_OPENAI_BASE_URL", "CORVIN_TTS_OPENAI_API_VERSION",
                 "CORVIN_TTS_OPENAI_MODEL"):
        assert name in src, f"say.py no longer reads {name}"
    assert "AzureOpenAI" in src, "say.py lost the Azure flavour"
