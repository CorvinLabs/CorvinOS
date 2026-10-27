"""gTTS is the last-resort tier of the core TTS manager: OpenAI -> Edge TTS -> gTTS."""
import asyncio
import sys
import types

from core.voice import tts_providers as tp


class _Down(tp.TTSProviderBase):
    def __init__(self, name):
        super().__init__(name)

    def is_available(self):
        return True

    async def synthesize(self, text, voice="", language="en-US"):
        return None


def _manager(monkeypatch, gtts_module):
    monkeypatch.setitem(sys.modules, "gtts", gtts_module)
    m = tp.QualityFirstTTSManager()
    m.primary, m.fallback = _Down("openai"), _Down("edge_tts")
    return m


def test_gtts_answers_when_openai_and_edge_are_down(monkeypatch):
    seen = []

    class FakeGTTS:
        def __init__(self, text, lang, timeout):
            seen.append(lang)

        def write_to_fp(self, fp):
            fp.write(b"ID3audio")

    m = _manager(monkeypatch, types.SimpleNamespace(gTTS=FakeGTTS))
    result = asyncio.run(m.synthesize("Hallo Welt", language="de-DE"))
    assert result and result.provider == "gtts" and result.audio_bytes == b"ID3audio"
    assert seen == ["de"] and m.last_provider_used == "gtts"
    assert m.get_status()["last_resort_available"] is True


def test_all_tiers_down_returns_none_not_an_exception(monkeypatch):
    class Boom:
        def __init__(self, *a, **k):
            raise ConnectionError("offline")

    m = _manager(monkeypatch, types.SimpleNamespace(gTTS=Boom))
    assert asyncio.run(m.synthesize("x")) is None
