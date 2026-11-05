"""SRF-10: the per-message "Voice summary" names its target message.

POST /voice/tts with ``msg_id`` (the clicked message) -> the live "voice" event
carries that id, so the client attaches the player to the CLICKED message instead
of the newest turn. Driven through the real route; only the synthesis is stubbed.
"""
from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from corvin_console import chat_runtime as cr
from corvin_console import deps
from corvin_console.routes import voice as voice_routes


def test_voice_tts_echoes_msg_id_in_the_live_event(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cr, "_workdir", lambda tid, s: tmp_path / s)
    vdir = tmp_path / "s1" / "voice"
    vdir.mkdir(parents=True)
    f = vdir / "older-message.ogg"
    f.write_bytes(b"OggS" + b"\x00" * 64)

    async def _fake_slot(sync_fn, *args, **kw):  # noqa: ANN001, ANN202
        return Response(content=b"OggS", media_type="audio/ogg",
                        headers={"X-Corvin-Voice-File": f.name})

    monkeypatch.setattr(voice_routes, "_run_with_tts_slot", _fake_slot)
    app = FastAPI()
    app.include_router(voice_routes.router, prefix="/v1/console")
    app.dependency_overrides[deps.require_csrf] = lambda: SimpleNamespace(
        tenant_id="_default", sid_fingerprint="fp", tier="owner")
    client = TestClient(app)
    q, unsubscribe = cr.subscribe_voice_live("s1")
    try:
        r = client.post("/v1/console/voice/tts", json={
            "text": "older answer", "lang": "en", "sid": "s1", "msg_id": "h3"})
        assert r.status_code == 200, r.text
        event = q.get_nowait()
        assert event["type"] == "voice" and event["msg_id"] == "h3"
        assert event["name"] == f.name
        # Without msg_id the event keeps its old shape (newest-turn attach).
        r = client.post("/v1/console/voice/tts", json={
            "text": "older answer", "lang": "en", "sid": "s1"})
        assert r.status_code == 200
        assert "msg_id" not in q.get_nowait()
        # msg_id is pattern-bounded.
        r = client.post("/v1/console/voice/tts", json={
            "text": "x", "lang": "en", "sid": "s1", "msg_id": "../etc"})
        assert r.status_code == 422
    finally:
        unsubscribe()
