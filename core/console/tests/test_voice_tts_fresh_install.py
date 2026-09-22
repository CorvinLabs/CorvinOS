"""The OpenAI TTS tier must work for a user who JUST installed CorvinOS.

Goal being pinned here (operator request, 2026-09-22): *"openai tts läuft und auch
bei jedem neuen nutzer der corvin os installiert auf allen platformen."*

WHY THIS FILE EXISTS. The claim "the OpenAI tier works on a fresh install" was, until
this file, an unmeasured claim — exactly the failure class CONCEPT-0007 is about. It
was never wrong, but nothing proved it, and every ingredient is individually easy to
break without noticing: a key candidate renamed, ``openai`` demoted from a core
dependency to an optional extra, ``_resolve_tts_provider()`` growing a non-None
default that routes a fresh install past the in-process branch, a ``base_url``
default that aims at something other than the public endpoint, or a response format
that the mime sniffer mislabels so the tier "succeeds" and the browser plays nothing.
Each of those ships green today and makes a new user's voice silent.

WHAT "FRESH INSTALL" MEANS HERE. Empty ``CORVIN_HOME``, a ``VOICE_CONFIG_DIR`` that
does not exist (so there is no ``service.env`` to fall back on), no encrypted secrets
store, no provider pin, no endpoint override — and exactly ONE thing configured: a
plain ``OPENAI_API_KEY`` in the process environment, which is how a new user on any
platform sets a key before they have discovered the console's settings page.

NO NETWORK, NO BILLING. ``openai.OpenAI`` is replaced with a recording fake BEFORE
``CORVIN_TTS_LOCAL_ONLY`` is cleared, so there is no window in which a real client
could be constructed. That ordering is deliberate: ``conftest.py`` sets that flag for
every test in this tree precisely because a route-level test which does not stub key
resolution would otherwise make a LIVE billable synthesis call from pytest whenever a
real key is present on the host (observed 2026-07-19). This file keeps that guard
intact and works around it in the child of its own scope rather than deleting it.

Run: python -m pytest core/console/tests/test_voice_tts_fresh_install.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_CONSOLE = Path(__file__).resolve().parents[1]
if str(_CONSOLE) not in sys.path:
    sys.path.insert(0, str(_CONSOLE))

from corvin_console.routes import voice as V  # noqa: E402

_REPO = Path(__file__).resolve().parents[3]

# A sentinel that cannot possibly be a real credential, so a leak into a log line
# or an assertion message is harmless AND recognisable.
_FRESH_KEY = "sk-fresh-install-probe-not-a-real-key"

# Every key name the resolver might pick up from an operator's real environment.
# All of them are cleared except the one the test is about, or a developer running
# this suite on a keyed box would prove nothing about a fresh install.
_KEY_NAMES = ("CORVIN_TTS_OPENAI_KEY", "OPENAI_API_KEY", "OPENAI_APIKEY")

# Anything that would make this NOT a fresh install.
_PINS = ("CORVIN_TTS_PROVIDER", "CORVIN_TTS_OPENAI_BASE_URL", "OPENAI_BASE_URL",
         "CORVIN_TTS_OPENAI_API_VERSION", "CORVIN_TTS_OPENAI_MODEL",
         "CORVIN_TTS_OPENAI_UNREACHABLE", "CORVIN_TTS_LOCAL_ONLY")

# OpenAI's audio.speech default response_format is mp3, so these are the bytes the
# real SDK hands back. The magic matters: _detect_audio_mime sniffs the container.
_MP3_BYTES = b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\x00" * 64


class _FakeSpeech:
    def __init__(self, calls: list[dict]) -> None:
        self._calls = calls

    def create(self, **kwargs):  # noqa: ANN003
        self._calls.append(kwargs)

        class _Resp:
            content = _MP3_BYTES

        return _Resp()


class _FakeAudio:
    def __init__(self, calls: list[dict]) -> None:
        self.speech = _FakeSpeech(calls)


class _FakeOpenAI:
    """Records how the tier constructed its client, and never opens a socket."""

    instances: list[dict] = []
    calls: list[dict] = []

    def __init__(self, **kwargs) -> None:  # noqa: ANN003
        type(self).instances.append(kwargs)
        self.audio = _FakeAudio(type(self).calls)


class _ExplodingAzureOpenAI:
    """A fresh install must never take the Azure branch — it has no Azure endpoint."""

    def __init__(self, **kwargs) -> None:  # noqa: ANN003
        raise AssertionError(
            "the fresh-install path constructed an AzureOpenAI client; with no "
            f"endpoint configured it must use the plain public client. kwargs={sorted(kwargs)}"
        )


@pytest.fixture()
def fresh_install(monkeypatch, tmp_path):
    """A process that looks like CorvinOS was installed a minute ago."""
    import openai as openai_module

    _FakeOpenAI.instances = []
    _FakeOpenAI.calls = []

    # Stub the SDK FIRST — before the local-only guard is lifted, so no ordering of
    # fixture teardown or test body can let a real client through.
    monkeypatch.setattr(openai_module, "OpenAI", _FakeOpenAI)
    monkeypatch.setattr(openai_module, "AzureOpenAI", _ExplodingAzureOpenAI)

    for name in _KEY_NAMES + _PINS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "corvin"))
    # Deliberately NOT created: a fresh install has no service.env.
    monkeypatch.setenv("VOICE_CONFIG_DIR", str(tmp_path / "voice-config"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("OPENAI_API_KEY", _FRESH_KEY)

    V._openai_tts_forget_verdict()
    yield _FakeOpenAI
    V._openai_tts_forget_verdict()


# ── the ingredients a new user cannot supply themselves ──────────────────────

@pytest.mark.parametrize("package", ["openai", "truststore"])
def test_the_tier_s_packages_are_core_dependencies_not_optional_extras(package: str) -> None:
    """``pip install corvinos`` must be enough — on every platform.

    A declaration that sits under ``[project.optional-dependencies]`` installs fine
    for whoever added it and is absent for everyone else, and both of these fail
    SILENTLY when missing: ``_try_openai_tts`` returns None on ImportError (falls
    through to say.py), and ``_use_os_trust_store`` is a guarded no-op. So the
    degradation is invisible exactly where it matters — a machine nobody has
    debugged yet.
    """
    text = (_REPO / "pyproject.toml").read_text(encoding="utf-8", errors="replace")
    core_block = text.split("[project.optional-dependencies]", 1)[0]
    assert f'"{package} ' in core_block, (
        f"{package!r} is not declared in pyproject.toml's core [project] dependencies "
        "(it may have moved under an optional extra). A fresh install would then "
        "have no OpenAI TTS tier at all, and nothing would say so."
    )


def test_a_fresh_install_has_no_provider_pin_and_so_takes_the_openai_branch(fresh_install) -> None:
    """The route enters the in-process OpenAI tier only for ``provider in (None, "openai")``.

    A fresh install must resolve to None. If a future default resolved to "edge" or
    "auto", a keyed new user would be handed edge-tts forever and the OpenAI key they
    configured would never be used — the exact confusion this whole pass came from.
    """
    assert V._resolve_tts_provider() is None, (
        "a fresh install now resolves a TTS provider pin; the in-process OpenAI tier "
        "is gated on `provider in (None, 'openai')` and would be skipped"
    )


def test_a_fresh_install_aims_at_the_public_openai_endpoint(fresh_install) -> None:
    """No endpoint override configured means the SDK's own default host.

    ``base_url`` must stay empty so the tier does NOT pass the kwarg at all — a
    non-empty default here would point every new user's install at whatever host
    happened to be baked in, and would additionally route them through the Azure
    client branch if it carried the Azure suffix.
    """
    base_url, api_version, model = V._openai_tts_endpoint()
    assert base_url == "", f"fresh install has a baked-in TTS base_url: {base_url!r}"
    assert model == "tts-1", f"fresh-install TTS model changed to {model!r}"
    assert api_version, "api_version must have a default even when unused"


# ── the end-to-end wiring, hermetically ──────────────────────────────────────

def test_a_plain_OPENAI_API_KEY_is_enough_to_synthesize(fresh_install) -> None:
    """THE load-bearing assertion: one env var, and the tier produces audio.

    This is the fresh-install path a new user actually walks. It proves the key was
    resolved (not merely present), the public client was built, the request was
    formed with the default model, and bytes came back — everything Corvin owns,
    end to end, without the network.
    """
    data = V._try_openai_tts("Kurzer Test.", "de", None)

    assert data == _MP3_BYTES, (
        "the in-process OpenAI tier returned no audio on a fresh install with a key "
        "configured; it fell through to say.py instead"
    )
    assert len(fresh_install.instances) == 1, (
        f"expected exactly one client construction, got {len(fresh_install.instances)}"
    )
    ctor = fresh_install.instances[0]
    assert ctor.get("api_key") == _FRESH_KEY, (
        "the tier did not resolve the key from a plain OPENAI_API_KEY — a new user "
        "who exports only that env var would get no OpenAI TTS"
    )
    assert "base_url" not in ctor, (
        f"a fresh install passed base_url={ctor.get('base_url')!r}; the unconfigured "
        "path must leave the SDK default untouched"
    )
    assert ctor.get("max_retries") == 0, (
        "max_retries must stay 0: the tier is a first attempt in a fallback chain, "
        "and SDK-level retries multiply the wait before edge-tts is reached"
    )

    assert len(fresh_install.calls) == 1
    call = fresh_install.calls[0]
    assert call["model"] == "tts-1", f"fresh install requested model {call['model']!r}"
    assert call["input"] == "Kurzer Test."
    assert call["voice"] in V._OPENAI_TTS_VOICES, (
        f"voice {call['voice']!r} is not an OpenAI voice name; the SDK rejects it "
        "and a fresh install would hear nothing"
    )


def test_the_default_mp3_response_is_labelled_so_a_browser_will_play_it(fresh_install) -> None:
    """A tier can succeed and still be silent.

    ``audio.speech.create`` is called with no ``response_format``, so the SDK's
    default applies and the bytes are MP3 — while say.py's own OpenAI tier emits
    OGG-Opus. Nothing transcodes. The served Content-Type therefore has to come from
    sniffing the bytes, never from a hard-coded container guess, or the browser is
    handed a mislabelled file and plays nothing with no error anywhere.
    """
    data = V._try_openai_tts("Kurzer Test.", "de", None)
    assert data is not None
    assert V._detect_audio_mime(data) == "audio/mpeg", (
        "OpenAI's default MP3 output is no longer sniffed correctly; the response "
        "would be served under the wrong media type"
    )


def test_a_fresh_install_makes_no_claim_it_has_not_measured(fresh_install) -> None:
    """The status panel must not be polluted by the hermetic success either.

    A success CLEARS the verdict (CONCEPT-0007 step 6) — the mechanism exists to
    report a measured failure, and a fresh install that has just synthesized fine
    must leave the ``openai`` row exactly as configuration found it.
    """
    assert V._try_openai_tts("Kurzer Test.", "de", None) is not None
    assert V._openai_tts_fresh_verdict() is None, (
        "a successful synthesis left a verdict behind; the status row would keep "
        "reporting a failure the system no longer observes"
    )


def test_the_subprocess_tier_inherits_the_same_key_and_endpoint(fresh_install) -> None:
    """say.py must aim where the in-process tier aimed.

    ``say.py`` re-derives its own configuration in a child process and reads only the
    process env plus ``service.env`` — it has no access to the encrypted secrets
    store. On a fresh install keyed through the console UI the in-process tier would
    therefore succeed while the subprocess fallback silently skipped OpenAI, so
    ``_say_env`` hands both the key and the endpoint down explicitly. Pinned here
    because the two tiers contradicting each other is invisible until the first
    fallback.
    """
    env = V._say_env()
    assert env.get("CORVIN_TTS_OPENAI_KEY") == _FRESH_KEY, (
        "_say_env did not hand the resolved key to say.py; the subprocess OpenAI "
        "tier would be skipped for 'no OPENAI_API_KEY' on a keyed install"
    )
    # No endpoint override configured → nothing to hand down, and in particular no
    # empty-string export that say.py would read as a configured base URL.
    assert not (env.get("CORVIN_TTS_OPENAI_BASE_URL") or "").strip(), (
        "a fresh install exported a TTS base URL to say.py: "
        f"{env.get('CORVIN_TTS_OPENAI_BASE_URL')!r}"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
