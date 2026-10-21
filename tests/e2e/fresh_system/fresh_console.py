"""Shared driver for the fresh-system console E2E tests — real HTTP + WebSocket
against an INSTALLED CorvinOS (never an in-process app, never a mocked engine).

Run from inside the fresh system (the console binds 127.0.0.1 there), with the
product's own interpreter, which has httpx and websockets:

    CORVIN_FRESH_E2E=1 /home/tester/.local/share/uv/tools/corvinos/bin/python \\
        -m pytest tests/e2e/fresh_system -q

Environment:
  CORVIN_FRESH_E2E=1                 gate — the tests skip without it
  CORVIN_FRESH_BASE_URL              default http://127.0.0.1:8765
  CORVIN_FRESH_TENANT_HOME           default <checkout>/.corvin/tenants/_default (for the
                                     audit-chain and workdir assertions; those are skipped
                                     when the path is not readable, the API ones never are)
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

GATED = os.environ.get("CORVIN_FRESH_E2E") != "1"
BASE = os.environ.get("CORVIN_FRESH_BASE_URL", "http://127.0.0.1:8765").rstrip("/")
API = BASE + "/v1/console"
WS_API = BASE.replace("http://", "ws://").replace("https://", "wss://") + "/v1/console"
_REPO = Path(__file__).resolve().parents[3]
TENANT_HOME = Path(os.environ.get("CORVIN_FRESH_TENANT_HOME", str(_REPO / ".corvin" / "tenants" / "_default")))
CHAIN = TENANT_HOME / "global" / "forge" / "audit.jsonl"

skip_unless_fresh = pytest.mark.skipif(GATED, reason="set CORVIN_FRESH_E2E=1 against an installed fresh system")


def login() -> tuple[httpx.Client, str]:
    """Local-login (loopback, credential-less) → session cookie + CSRF header."""
    c = httpx.Client(base_url=API, timeout=120, follow_redirects=False)
    r = c.get("/auth/local-login")
    assert r.status_code in (200, 302, 303), (r.status_code, r.text[:200])
    cookie = c.cookies.get("corvin_console_sid")
    assert cookie, "local-login set no corvin_console_sid cookie"
    who = c.get("/auth/whoami")
    assert who.status_code == 200, who.text[:200]
    c.headers["X-CSRF-Token"] = who.json()["csrf_token"]
    return c, cookie


def new_session(c: httpx.Client, title: str) -> str:
    r = c.post("/chat/sessions", json={"title": title})
    assert r.status_code == 200, r.text[:300]
    return r.json()["session"]["sid"]


async def _ws_turn(cookie: str, sid: str, text: str, *, voice_on: bool, timeout: float,
                   cancel_after_first_delta: bool) -> list[dict[str, Any]]:
    import websockets

    events: list[dict[str, Any]] = []
    t0 = time.time()
    async with websockets.connect(f"{WS_API}/chat/sessions/{sid}/stream",
                                  additional_headers={"Cookie": f"corvin_console_sid={cookie}"},
                                  max_size=16 * 1024 * 1024) as ws:
        ready = json.loads(await ws.recv())
        assert ready.get("type") == "ready", ready
        await ws.send(json.dumps({"type": "user", "text": text, "voice_on": voice_on}))
        cancelled = False
        deadline = time.time() + timeout
        while time.time() < deadline:
            ev = json.loads(await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.time())))
            ev["_t"] = round(time.time() - t0, 2)
            events.append(ev)
            if cancel_after_first_delta and not cancelled and ev.get("type") == "delta":
                await ws.send(json.dumps({"type": "cancel"}))
                cancelled = True
            if ev.get("type") == "done":
                return events
    raise AssertionError(f"no done event within {timeout}s: {[e.get('type') for e in events]}")


def ws_turn(cookie: str, sid: str, text: str, *, voice_on: bool = False, timeout: float = 240.0,
            cancel_after_first_delta: bool = False) -> list[dict[str, Any]]:
    """One user turn over the real chat WebSocket; returns every event up to `done`."""
    return asyncio.run(_ws_turn(cookie, sid, text, voice_on=voice_on, timeout=timeout,
                                cancel_after_first_delta=cancel_after_first_delta))


def final_result(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    results = [e for e in events if e.get("type") == "result"]
    return results[-1] if results else None


def answer_text(events: list[dict[str, Any]]) -> str:
    res = final_result(events)
    return (res or {}).get("text") or "".join(e.get("text", "") for e in events if e.get("type") == "delta")


def chain_rows(since_ts: float, event_type: str) -> list[dict[str, Any]] | None:
    """Records of `event_type` written to the tenant chain since `since_ts`; None
    when the chain is not readable from here (then the caller skips, never passes)."""
    try:
        text = CHAIN.read_text(encoding="utf-8")
    except OSError:
        return None
    out = []
    for line in text.splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("ts", 0) >= since_ts and r.get("event_type") == event_type:
            out.append(r)
    return out


def wait_for(fn, *, timeout: float, every: float = 5.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(every)
    return None


def audio_kind(data: bytes) -> str:
    if data[:3] == b"ID3" or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "mp3"
    if data[:4] == b"OggS":
        return "ogg"
    if data[:4] == b"RIFF":
        return "wav"
    return "unknown"


def product_python() -> str:
    """The installed product's interpreter (where piper-tts, edge-tts, pywhispercpp live):
    CORVIN_FRESH_PYTHON, else the uv tool venv the installer creates, else this interpreter."""
    import shutil
    import sys
    env = os.environ.get("CORVIN_FRESH_PYTHON", "").strip()
    if env:
        return env
    tool = Path.home() / ".local" / "share" / "uv" / "tools" / "corvinos" / "bin" / "python"
    if tool.exists():
        return str(tool)
    serve = shutil.which("corvinos-serve")
    if serve:
        cand = Path(serve).resolve().parent / "python"
        if cand.exists():
            return str(cand)
    return sys.executable

