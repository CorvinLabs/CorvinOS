"""Per-subtask E2E for ADR-0007 Phase 2.5 — SSE streaming.

Covers:
  * Pure ``RunEventBuffer``: append + subscribe, replay before live tap,
    multiple subscribers see the same events, close delivers terminal
    event to every subscriber.
  * ``format_sse_frame`` shape: ``event:`` + ``data:`` + blank line.
  * Late subscriber (connect after terminal): receives the full replay
    + terminal event, then disconnects.
  * Concurrent live subscriber: starts subscribing mid-run, sees the
    tail of the stream + terminal event.
  * Auth gates on the SSE endpoint: 401 missing bearer, 403 cross-tenant.
  * 404 for unknown run_id.
  * Fallback for runs with no in-memory buffer (process-restart shape):
    SSE delivers a one-shot snapshot event derived from disk.
  * Auth on cross-tenant SSE: token for A, URL for B → 403.

Tests use FastAPI's TestClient with ``client.stream(...)`` to read the
text/event-stream body line-by-line.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "gateway"))
sys.path.insert(0, str(_REPO / "operator" / "forge"))
sys.path.insert(0, str(_REPO / "operator" / "bridges" / "shared"))

from fastapi.testclient import TestClient  # noqa: E402

from agents import StreamEvent  # noqa: E402
from corvin_gateway.app import app  # noqa: E402
from corvin_gateway.dispatcher import RunDispatcher  # noqa: E402
from corvin_gateway.runs import RunRegistry, RunRequest  # noqa: E402
from corvin_gateway.sse import (  # noqa: E402
    EventBufferRegistry,
    RunEventBuffer,
    format_sse_frame,
)


# ── Common fixtures ──────────────────────────────────────────────────


@contextmanager
def sandbox(tenants=("acme",)):
    with tempfile.TemporaryDirectory(prefix="gw-sse-test-") as td:
        home = Path(td)
        os.environ["CORVIN_HOME"] = str(home)
        os.environ["ADAPTER_FAKE_CLAUDE"] = "1"
        os.environ["ADAPTER_FAKE_DELAY"] = "0.05"
        for t in tenants:
            (home / "tenants" / t / "global" / "auth").mkdir(parents=True)
            (home / "tenants" / t / "global" / "forge").mkdir(parents=True)
            (home / "tenants" / t / "global" / "gateway" / "runs").mkdir(parents=True)
        try:
            yield home
        finally:
            os.environ.pop("CORVIN_HOME", None)
            os.environ.pop("ADAPTER_FAKE_CLAUDE", None)
            os.environ.pop("ADAPTER_FAKE_DELAY", None)


def _good_run_body(persona="docs", input_text="ping"):
    return {
        "apiVersion": "corvin/v1",
        "kind":       "Run",
        "spec":       {"persona": persona, "input": input_text},
    }


@contextmanager
def gateway_client(engine_factory=None):
    """Engage lifespan with an optional injected engine."""
    if engine_factory is not None:
        app.state.dispatcher = RunDispatcher(engine_factory=engine_factory)
    try:
        with TestClient(app) as client:
            yield client
    finally:
        if hasattr(app.state, "dispatcher"):
            app.state.dispatcher = None


def _parse_sse_lines(lines: Iterator[str]) -> list[dict[str, Any]]:
    """Group consecutive non-blank lines into SSE frames; return
    list of dicts with `event` + `data` (parsed JSON)."""
    frames: list[dict[str, Any]] = []
    buf: dict[str, str] = {}
    for line in lines:
        if line == "":
            if buf:
                ev = buf.get("event", "message")
                data = buf.get("data", "")
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    payload = {"raw": data}
                frames.append({"event": ev, "data": payload})
                buf = {}
            continue
        if ":" in line:
            key, _, val = line.partition(":")
            buf[key.strip()] = val.strip()
    if buf:
        ev = buf.get("event", "message")
        data = buf.get("data", "")
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            payload = {"raw": data}
        frames.append({"event": ev, "data": payload})
    return frames


# ── Pure RunEventBuffer tests (no HTTP) ──────────────────────────────


class _ScriptedEngine:
    """Engine that emits a controlled sequence of events with a small
    sleep between them. Lets tests assert order under real streaming."""
    name = "test-scripted"
    capabilities = {"stream_json": True}

    def __init__(self, events_to_yield: list[StreamEvent], delay_s: float = 0.05):
        self._events = events_to_yield
        self._delay = delay_s

    def spawn(self, prompt: str, *, env=None) -> Iterator[StreamEvent]:
        for ev in self._events:
            time.sleep(self._delay)
            yield ev

    def cancel(self) -> None:
        pass


class RunEventBufferTests(unittest.TestCase):
    def test_replay_history_then_close(self):
        # Inside a fresh loop so the buffer has somewhere to schedule.
        async def go():
            loop = asyncio.get_running_loop()
            buf = RunEventBuffer(loop)
            buf.append({"type": "text_delta", "text": "Hi"})
            buf.append({"type": "text_delta", "text": "!"})
            buf.close({"type": "run.completed", "status": "completed"})
            received = []
            async for ev in buf.subscribe():
                received.append(ev)
            return received
        events = asyncio.run(go())
        self.assertEqual(len(events), 3)
        self.assertEqual(events[0]["text"], "Hi")
        self.assertEqual(events[1]["text"], "!")
        self.assertEqual(events[2]["type"], "run.completed")

    def test_multiple_subscribers(self):
        async def go():
            loop = asyncio.get_running_loop()
            buf = RunEventBuffer(loop)

            async def reader():
                received = []
                async for ev in buf.subscribe():
                    received.append(ev)
                return received

            t1 = asyncio.create_task(reader())
            t2 = asyncio.create_task(reader())
            await asyncio.sleep(0.01)  # let subscribers register
            buf.append({"type": "text_delta", "text": "x"})
            await asyncio.sleep(0.01)
            buf.close({"type": "run.completed", "status": "completed"})
            r1, r2 = await t1, await t2
            return r1, r2

        r1, r2 = asyncio.run(go())
        # Both subscribers see the same events
        self.assertEqual(r1, r2)
        self.assertEqual(len(r1), 2)
        self.assertEqual(r1[0]["text"], "x")
        self.assertEqual(r1[1]["status"], "completed")


# ── format_sse_frame shape ───────────────────────────────────────────


class FrameShapeTests(unittest.TestCase):
    def test_event_field_and_data_field(self):
        out = format_sse_frame({"type": "text_delta", "text": "hi"})
        # event: <type>\n  data: <json>\n\n
        self.assertTrue(out.startswith("event: text_delta\n"))
        self.assertIn("data: ", out)
        self.assertTrue(out.endswith("\n\n"))
        # JSON payload survives the round-trip
        data_line = [l for l in out.split("\n") if l.startswith("data:")][0]
        payload = json.loads(data_line[len("data: "):])
        self.assertEqual(payload["type"], "text_delta")
        self.assertEqual(payload["text"], "hi")

    def test_falls_back_to_message_on_no_type(self):
        out = format_sse_frame({"foo": "bar"})
        self.assertTrue(out.startswith("event: message\n"))


# ── HTTP — late subscriber (run already completed) ───────────────────


class LateSubscriberTests(unittest.TestCase):
    def test_subscribe_after_completion_replays_history(self):
        with sandbox(("acme",)) as home:
            engine_factory = lambda: _ScriptedEngine([
                StreamEvent(type="session_started"),
                StreamEvent(type="text_delta", text="hello"),
                StreamEvent(type="text_delta", text=" world"),
                StreamEvent(type="turn_completed", text="hello world",
                            usage={"input_tokens": 1, "output_tokens": 2}),
            ], delay_s=0.01)
            with gateway_client(engine_factory=engine_factory) as client:
                r = client.post(
                    "/v1/tenants/acme/runs",
                    json=_good_run_body(),
                )
                run_id = r.json()["run_id"]
                # Wait for completion via polling GET
                for _ in range(100):
                    rs = client.get(f"/v1/tenants/acme/runs/{run_id}")
                    if rs.json().get("status") in (
                        "completed", "failed", "budget_exceeded",
                    ):
                        break
                    time.sleep(0.02)
                # Now subscribe — buffer should have the full history
                with client.stream(
                    "GET",
                    f"/v1/tenants/acme/runs/{run_id}/events",
                ) as s:
                    self.assertEqual(s.status_code, 200)
                    self.assertTrue(
                        s.headers["content-type"].startswith("text/event-stream"),
                    )
                    frames = _parse_sse_lines(s.iter_lines())
            types = [f["event"] for f in frames]
            self.assertIn("session_started", types)
            self.assertIn("text_delta", types)
            self.assertIn("turn_completed", types)
            self.assertEqual(types[-1], "run.completed", types)
            # Verify the terminal frame carries result info
            terminal = frames[-1]["data"]
            self.assertEqual(terminal["status"], "completed")


# ── HTTP — concurrent / live subscriber ──────────────────────────────


class LiveSubscriberTests(unittest.TestCase):
    def test_subscribe_during_run_picks_up_remaining_plus_terminal(self):
        with sandbox(("acme",)) as home:
            engine_factory = lambda: _ScriptedEngine([
                StreamEvent(type="session_started"),
                StreamEvent(type="text_delta", text="A"),
                StreamEvent(type="text_delta", text="B"),
                StreamEvent(type="text_delta", text="C"),
                StreamEvent(type="turn_completed", text="ABC",
                            usage={}),
            ], delay_s=0.08)
            with gateway_client(engine_factory=engine_factory) as client:
                r = client.post(
                    "/v1/tenants/acme/runs",
                    json=_good_run_body(),
                )
                run_id = r.json()["run_id"]
                # Give the dispatch enough time to emit a couple of
                # events but NOT finish — the engine sleeps 0.08s per
                # event, totalling ~0.4s.
                time.sleep(0.15)
                with client.stream(
                    "GET",
                    f"/v1/tenants/acme/runs/{run_id}/events",
                ) as s:
                    self.assertEqual(s.status_code, 200)
                    frames = _parse_sse_lines(s.iter_lines())
            types = [f["event"] for f in frames]
            self.assertEqual(types[-1], "run.completed")
            # The subscriber saw at least the terminal event;
            # depending on timing it also sees some text_delta
            # frames (replay of buffered) and live ones.
            self.assertGreaterEqual(types.count("text_delta"), 1)


# ── HTTP — fallback for unknown buffer ───────────────────────────────


class FallbackSnapshotTests(unittest.TestCase):
    def test_run_with_no_buffer_returns_snapshot(self):
        with sandbox(("acme",)) as home:
            # Manually create a record without engaging the
            # dispatcher (simulating a process restart).
            registry = RunRegistry()
            run_request = RunRequest.model_validate(_good_run_body())
            record = registry.create("acme", run_request)
            # Manually mark it completed on disk
            registry.set_status(
                "acme", record.run_id, "completed",
                result={"final_text": "from disk"},
            )
            # Now hit the SSE endpoint — no buffer in memory
            with gateway_client() as client:
                with client.stream(
                    "GET",
                    f"/v1/tenants/acme/runs/{record.run_id}/events",
                ) as s:
                    self.assertEqual(s.status_code, 200)
                    frames = _parse_sse_lines(s.iter_lines())
            self.assertEqual(len(frames), 1, frames)
            f = frames[0]
            self.assertEqual(f["event"], "run.completed")
            self.assertEqual(f["data"]["status"], "completed")
            self.assertEqual(f["data"]["result"]["final_text"], "from disk")


# AuthGateTests removed — gateway no longer enforces bearer auth.
# Loopback binding is the local security boundary.

    def test_unknown_run_id_is_404(self):
        with sandbox(("acme",)):
            with gateway_client() as client:
                r = client.get(
                    "/v1/tenants/acme/runs/run_doesnotexist0000000/events",
                )
                self.assertEqual(r.status_code, 404)


# ── Buffer-registry housekeeping ─────────────────────────────────────


class BufferRegistryTests(unittest.TestCase):
    def test_get_or_create_returns_same_instance(self):
        async def go():
            loop = asyncio.get_running_loop()
            reg = EventBufferRegistry()
            b1 = reg.get_or_create("acme", "run_a", loop)
            b2 = reg.get_or_create("acme", "run_a", loop)
            self.assertIs(b1, b2)
            # Different tenant — different buffer
            b3 = reg.get_or_create("globex", "run_a", loop)
            self.assertIsNot(b1, b3)
            return reg
        reg = asyncio.run(go())
        self.assertEqual(len(reg), 2)

    def test_evict(self):
        async def go():
            loop = asyncio.get_running_loop()
            reg = EventBufferRegistry()
            reg.get_or_create("acme", "run_a", loop)
            reg.evict("acme", "run_a")
            self.assertIsNone(reg.get("acme", "run_a"))
            # idempotent
            reg.evict("acme", "run_a")
        asyncio.run(go())


if __name__ == "__main__":
    unittest.main(verbosity=2)
