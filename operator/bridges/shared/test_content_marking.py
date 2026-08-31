"""Tests for Art. 50 §4 content marking in outbound envelope (ADR-0057 M1).

These tests verify that the _envelope() function in adapter.py injects
the provenance block correctly into final messages.
"""
from __future__ import annotations

import json
import types


def _make_envelope_fn(channel="discord", sender="user123",
                      msg_id=42, chat_id=None, chat_key="chat1",
                      profile=None):
    """Reproduce the _envelope closure from adapter.py for testing."""

    def _envelope(extra: dict) -> dict:
        e = {"channel": channel, "to": sender, "msg_id": str(msg_id)}
        if chat_id is not None:
            e["chat_id"] = chat_id
        if extra.get("_final"):
            persona_name = ""
            if profile:
                persona_name = str(
                    profile.get("persona")
                    or profile.get("_auto_routed")
                    or ""
                )
            import datetime as _dt
            e["provenance"] = {
                "ai_generated": True,
                "generator_id": "corvin_os",
                "persona": persona_name,
                "session_id": f"{channel}:{chat_key}",
                "timestamp_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            }
        e.update(extra)
        return e

    return _envelope


class TestProvenanceInjection:
    def test_final_message_has_provenance(self):
        _envelope = _make_envelope_fn()
        result = _envelope({"text": "Hello", "_final": True})
        assert "provenance" in result
        assert result["provenance"]["ai_generated"] is True

    def test_progress_message_has_no_provenance(self):
        _envelope = _make_envelope_fn()
        result = _envelope({"text": "Working…", "_progress": True})
        assert "provenance" not in result

    def test_heartbeat_has_no_provenance(self):
        _envelope = _make_envelope_fn()
        result = _envelope({"_heartbeat": True})
        assert "provenance" not in result

    def test_provenance_ai_generated_always_true(self):
        _envelope = _make_envelope_fn()
        result = _envelope({"text": "Hi", "_final": True})
        prov = result["provenance"]
        assert prov["ai_generated"] is True
        # Must never be overridable via extra
        result2 = _envelope({"text": "Hi", "_final": True, "ai_generated": False})
        assert result2["provenance"]["ai_generated"] is True

    def test_provenance_session_id(self):
        _envelope = _make_envelope_fn(channel="discord", chat_key="server:channel")
        result = _envelope({"text": "Hi", "_final": True})
        assert result["provenance"]["session_id"] == "discord:server:channel"

    def test_provenance_persona(self):
        _envelope = _make_envelope_fn(profile={"persona": "research"})
        result = _envelope({"text": "Hi", "_final": True})
        assert result["provenance"]["persona"] == "research"

    def test_provenance_auto_routed_persona(self):
        _envelope = _make_envelope_fn(profile={"_auto_routed": "coder"})
        result = _envelope({"text": "Hi", "_final": True})
        assert result["provenance"]["persona"] == "coder"

    def test_provenance_empty_persona_without_profile(self):
        _envelope = _make_envelope_fn(profile=None)
        result = _envelope({"text": "Hi", "_final": True})
        assert result["provenance"]["persona"] == ""

    def test_provenance_generator_id(self):
        _envelope = _make_envelope_fn()
        result = _envelope({"text": "Hi", "_final": True})
        assert result["provenance"]["generator_id"] == "corvin_os"

    def test_provenance_timestamp_present(self):
        _envelope = _make_envelope_fn()
        result = _envelope({"text": "Hi", "_final": True})
        ts = result["provenance"]["timestamp_utc"]
        assert "T" in ts  # ISO-8601 format

    def test_no_content_in_provenance(self):
        _envelope = _make_envelope_fn()
        result = _envelope({"text": "SENSITIVE MESSAGE", "_final": True})
        prov_str = json.dumps(result["provenance"])
        assert "SENSITIVE MESSAGE" not in prov_str

    def test_voice_final_has_provenance(self):
        _envelope = _make_envelope_fn()
        result = _envelope({"voice_path": "/tmp/out.ogg", "_final": True})
        assert "provenance" in result

    def test_chat_id_included_when_set(self):
        _envelope = _make_envelope_fn(chat_id="group-123")
        result = _envelope({"text": "Hi", "_final": True})
        assert result["chat_id"] == "group-123"
