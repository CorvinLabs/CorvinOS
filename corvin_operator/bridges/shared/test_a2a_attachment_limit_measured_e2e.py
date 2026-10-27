"""T-0301: the real attachment limit of each A2A transport, measured through the real sender.

No wire change. This file MEASURES what the two transports accept today and pins the numbers that
``docs/claude-ref/layer-38-a2a-network.md`` § "Measured attachment limits" states:

* **direct HTTP** — real ``a2a_http_server`` on 127.0.0.1, real ``RemoteTriggerSender``, real HMAC pairing;
* **relay** — a LOCAL relay started from this repository (``a2a_relay.build_relay_router`` under uvicorn on
  127.0.0.1, the same router ``ops/a2a-relay`` deploys) with a real ``RelayListener``; the direct URL is dead,
  so every byte crosses the relay. No hosted relay and no external host is ever contacted.

Measured per transport, request direction (the sender's attachments) and response direction (files the
peer's worker writes):

* the accepted maximum is found by bisection over the total raw size of 16 attachments, through the real
  ``send``; just under / at the cap passes, one byte over is refused with the closed reason;
* the count cap (16 passes, 17 refused);
* what a NON-conforming sender meets (the sender's local check bypassed): the receiver's own refusal at the
  cap, and the transport ceiling above it (HTTP 413 at the 4 MiB body cap; the relay's ``message_too_large``).

Only the model call at the end of the peer's worker path is a stand-in (it writes the files it is told to).

Run: python -m pytest corvin_operator/bridges/shared/test_a2a_attachment_limit_measured_e2e.py
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import socket
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock as mock
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

import a2a_attachments  # noqa: E402
import a2a_friendship as ft  # noqa: E402
import a2a_http_server  # noqa: E402
import a2a_relay  # noqa: E402
import remote_trigger_sender as rts  # noqa: E402
from a2a_attachments import Attachment  # noqa: E402

CAP = a2a_attachments.MAX_ATTACHMENTS_TOTAL_BYTES
COUNT_CAP = a2a_attachments.MAX_ATTACHMENTS_COUNT
IID_A = "aaaaaaaa-0000-4000-8000-aaaaaaaaaaaa"
IID_B = "bbbbbbbb-0000-4000-8000-bbbbbbbbbbbb"

#: what this run measured, printed at the end (the doc states these numbers)
MEASURED: dict[str, Any] = {}

_SAVED_LICENSE_MODULES: dict[str, object | None] = {}


def setUpModule() -> None:
    import a2a_test_tier  # plain-script runs (run-all-tests.sh) have no conftest tier pin

    a2a_test_tier.pin_member_tier()
    for name in ("license.compute_quota", "license.limits"):
        _SAVED_LICENSE_MODULES[name] = sys.modules.get(name)
        sys.modules[name] = None  # type: ignore[assignment]


def tearDownModule() -> None:
    for name, mod in _SAVED_LICENSE_MODULES.items():
        if mod is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = mod
    if MEASURED:
        print("\n[T-0301 measured] " + json.dumps(MEASURED, sort_keys=True), flush=True)


# ── harness (re-used by test_a2a_large_object_reference_e2e.py) ──────────


@dataclass
class _Ev:
    type: str
    text: str | None = None
    usage: dict | None = None
    error: str | None = None


class FileEngine:
    """Worker stand-in: writes ``self.files`` (name -> bytes) into ``out/`` and answers ``{"ok": true}``."""

    name = "files"
    capabilities = {"deterministic": True, "supports_attachments": True, "mid_stream_inject": False,
                    "hooks": False, "skills_tool": False}

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.inputs: list[list[str]] = []
        self.payload: dict = {"ok": True}   # the worker's structured answer

    def spawn(self, prompt, *, working_dir=None, **_ignored) -> Iterator[_Ev]:
        wd = Path(working_dir)
        self.inputs.append(sorted(p.name for p in (wd / "in").iterdir()) if (wd / "in").is_dir() else [])
        out_dir = wd / "out"
        out_dir.mkdir(exist_ok=True)
        for name, data in self.files.items():
            (out_dir / name).write_bytes(data)
        payload = json.dumps(self.payload)
        yield _Ev(type="text_delta", text=payload)
        yield _Ev(type="turn_completed", text=payload)

    def cancel(self) -> None:
        pass


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class LocalRelay:
    """The relay from THIS repository (``a2a_relay.build_relay_router``) on 127.0.0.1 under uvicorn —
    the same router ``ops/a2a-relay`` deploys. Never a hosted relay."""

    def __init__(self) -> None:
        import uvicorn
        from fastapi import FastAPI

        self.state = a2a_relay.RelayState()
        app = FastAPI()
        app.include_router(a2a_relay.build_relay_router(self.state))
        self.port = free_port()
        self.url = f"ws://127.0.0.1:{self.port}/v1/a2a/relay/connect"
        cfg = uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="error",
                             ws_max_size=a2a_relay._WS_CLIENT_MAX_SIZE)
        self.server = uvicorn.Server(cfg)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.time() + 10
        while not self.server.started and time.time() < deadline:
            time.sleep(0.05)

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(5)


def write_json(path: Path, cfg: dict) -> None:
    path.write_text(json.dumps(cfg))
    path.chmod(0o600)


class TwoInstances:
    """A (requester) and B (peer, real HTTP server + real receiver), paired with one friendship kid.

    ``transport="relay"`` points A's endpoint at a dead URL and runs B's ``RelayListener`` against the
    local relay, so the relay is the only path. ``forge_se`` None means the REAL forge writer into the
    sandboxed tenant chain."""

    def __init__(self, root: Path, *, transport: str, engine: FileEngine, forge_se: Any = None,
                 relay: "LocalRelay | None" = None, loop: "asyncio.AbstractEventLoop | None" = None,
                 origin_extra: "dict | None" = None, n_kids: int = 1) -> None:
        self.root, self.transport, self.engine = root, transport, engine
        self.a_endpoints = root / "a" / "endpoints"
        self.b_origins = root / "b" / "origins"
        self.b_endpoints = root / "b" / "endpoints"
        for d in (self.a_endpoints, self.b_origins, self.b_endpoints):
            d.mkdir(parents=True)
        self.server = a2a_http_server.build_server(
            host="127.0.0.1", port=0, origins_dir=self.b_origins, endpoints_dir=self.b_endpoints,
            engine_factory=lambda: engine, instance_id=IID_B, forge_se=forge_se)
        h, p = self.server.server_address[:2]
        url = f"http://{h}:{p}" if transport == "direct" else f"http://127.0.0.1:{free_port()}"
        self.kids: list[str] = []
        for _ in range(n_kids):
            kid, key = secrets.token_hex(8), secrets.token_hex(32)
            self.kids.append(kid)
            tok = ft.FriendshipToken(kid=kid, key=key, url="http://127.0.0.1:1", label=None, expires=None)
            origin = ft.to_origin_dict(tok)
            origin.update({"spawn_worker": True, "allowed_personas": ["compute-csv"], "max_ttl_s": 300,
                           **(origin_extra or {})})
            write_json(self.b_origins / f"{kid}.json", origin)
            ep = ft.to_endpoint_dict(ft.FriendshipToken(kid=kid, key=key, url=url, label=None, expires=None))
            ep.update({"instance_id": IID_B, "default_ttl_s": 120})
            write_json(self.a_endpoints / f"{kid}.json", ep)
            # B's endpoint back to A: the kid's peer host for the holder-side L35 check (a friendship
            # pair always holds both files under one kid).
            write_json(self.b_endpoints / f"{kid}.json",
                       {**ep, "url": "http://127.0.0.1:9/v1/a2a/receive", "instance_id": IID_A})
        self.kid = self.kids[0]
        a2a_http_server.serve_in_thread(self.server)
        self.receiver = self.server.RequestHandlerClass.receiver
        self.sender = rts.RemoteTriggerSender(endpoints_dir=self.a_endpoints, instance_id=IID_A,
                                              forge_se=forge_se)
        self.listener = None
        if transport == "relay":
            assert relay is not None and loop is not None
            self.listener = a2a_relay.RelayListener(relay_url=relay.url, receiver=self.receiver,
                                                    origins_dir=self.b_origins, instance_id=IID_B)
            asyncio.run_coroutine_threadsafe(self.listener.run_forever(reconnect_backoff_s=0.5), loop)
            deadline = time.time() + 15
            while self.listener.status.get("registered", 0) < n_kids and time.time() < deadline:
                time.sleep(0.05)
            if self.listener.status.get("registered") != n_kids:
                raise RuntimeError(f"relay listener did not register: {self.listener.status}")

    def origin_path(self, kid: "str | None" = None) -> Path:
        return self.b_origins / f"{kid or self.kid}.json"

    def update_origin(self, kid: "str | None" = None, **changes: Any) -> None:
        p = self.origin_path(kid)
        write_json(p, {**json.loads(p.read_text()), **changes})

    def close(self, loop: "asyncio.AbstractEventLoop | None" = None) -> None:
        if self.listener is not None and loop is not None:
            loop.call_soon_threadsafe(self.listener.stop)
        self.server.shutdown()
        self.server.server_close()


class TransportCase(unittest.TestCase):
    """Sandbox + local relay + event loop, shared by every transport test here and in the T-0302 file."""

    transport = "direct"
    forge_se: Any = None   # None = the real tenant chain
    pair_kwargs: dict = {}

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.home = self.root / "home"
        (self.home / "tenants" / "_default" / "global").mkdir(parents=True)
        self._patches = [
            # VOICE_AUDIT_PATH: the repo conftest points it at a per-test sandbox file; here it is
            # the sandboxed tenant's own chain, which is what the T-0302 tests read and verify.
            mock.patch.dict(os.environ, {"CORVIN_HOME": str(self.home), "CORVIN_TENANT_ID": "_default",
                                         "CORVIN_A2A_ATTESTATION_DISABLED": "1",
                                         "VOICE_AUDIT_PATH": str(self.home / "tenants" / "_default" / "global"
                                                                 / "forge" / "audit.jsonl")}),
            mock.patch("remote_trigger_sender._record_feed_task", lambda *a, **k: None),
            mock.patch("remote_trigger_sender._record_feed_response", lambda *a, **k: None),
            mock.patch.object(rts.RemoteTriggerSender, "_build_network_attestation",
                              staticmethod(lambda cfg: None)),
        ]
        os.environ.pop("FORGE_ROOT", None)
        self.relay = None
        self.loop = None
        if self.transport == "relay":
            self.relay = LocalRelay()
            self._patches += [
                mock.patch("corvin_core.feature_flags.is_enabled", return_value=True),
                mock.patch("a2a_friendship.get_my_relay_url", return_value=self.relay.url),
            ]
        for p in self._patches:
            p.start()
        os.environ.pop(rts._REMOTE_ENDPOINTS_ENV, None)
        os.environ.pop("CORVIN_A2A_FEED_DIR", None)   # the object store lives in the tenant's feed dir
        if self.transport == "relay":
            self.loop = asyncio.new_event_loop()
            self._loop_thread = threading.Thread(target=self.loop.run_forever, daemon=True)
            self._loop_thread.start()
        self.engine = FileEngine()
        self.pair = self.make_pair(**self.pair_kwargs)

    def make_pair(self, **kw: Any) -> TwoInstances:
        return TwoInstances(self.root / secrets.token_hex(3), transport=self.transport, engine=self.engine,
                            forge_se=self.forge_se, relay=self.relay, loop=self.loop, **kw)

    def tearDown(self) -> None:
        self.pair.close(self.loop)
        if self.loop is not None:
            time.sleep(0.3)
            self.loop.call_soon_threadsafe(self.loop.stop)
            self._loop_thread.join(5)
        if self.relay is not None:
            self.relay.stop()
        for p in reversed(self._patches):
            p.stop()
        self._tmp.cleanup()

    def send(self, attachments: list | None = None, *, schema: "dict | None" = None, **kw: Any):
        return self.pair.sender.send(
            self.pair.kid, "measure the attachments", attachments=attachments,
            result_schema=schema or {"properties": {"ok": {"type": "boolean"}}},
            ttl_s=60, timeout_s=60, **kw)


def _attachments(total: int, count: int = COUNT_CAP) -> list[Attachment]:
    """``count`` attachments whose raw sizes sum to exactly ``total`` bytes."""
    base, rest = divmod(total, count)
    return [Attachment.from_bytes(name=f"part{i:02d}.bin", mime="application/octet-stream",
                                  content=bytes([i]) * (base + (1 if i < rest else 0)))
            for i in range(count)]


def bisect_max(accepts, lo: int, hi: int) -> int:
    """Largest n in [lo, hi) with accepts(n) True, given accepts(lo) and not accepts(hi)."""
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if accepts(mid):
            lo = mid
        else:
            hi = mid
    return lo


_REAL_VALIDATE = a2a_attachments.validate_attachments


def _sender_skips_validation(atts):
    """``validate_attachments`` for everyone but the sender's local pre-send check (a non-conforming
    sender); the peer, in this same process, keeps its own check."""
    import inspect

    caller = inspect.stack()[1].filename
    if caller.endswith("remote_trigger_sender.py"):
        return atts
    return _REAL_VALIDATE(atts)


def _nobody_validates(atts):
    return atts


class _LimitMeasurement:
    """The same measurement for each transport (mixed into a TransportCase per transport)."""

    def _accepts(self, total: int) -> bool:
        res = self.send(_attachments(total))
        return res.ok

    def test_request_attachments_accepted_maximum_through_the_real_sender(self):
        step = 64 * 1024
        self.assertTrue(self._accepts(CAP - step), "well under the cap must pass")
        self.assertFalse(self._accepts(CAP + step), "well over the cap must be refused")
        got = bisect_max(self._accepts, CAP - step, CAP + step)
        MEASURED[f"{self.transport}.request_max_total_bytes"] = got
        self.assertEqual(got, CAP)
        # just over: refused BEFORE anything leaves this machine, with the closed reason
        over = self.send(_attachments(CAP + 1))
        self.assertFalse(over.ok)
        self.assertEqual((over.status, over.error_category), ("error", rts.ErrorCategory.PROTOCOL_ERROR))
        self.assertEqual(over.error_detail, "Invalid attachment")
        self.assertEqual(self.engine.inputs[-1], [f"part{i:02d}.bin" for i in range(COUNT_CAP)],
                         "the last run the peer saw is the accepted maximum, not the refused one")

    def test_request_count_cap(self):
        ok = self.send(_attachments(COUNT_CAP * 10, COUNT_CAP))
        self.assertTrue(ok.ok, ok)
        over = self.send(_attachments((COUNT_CAP + 1) * 10, COUNT_CAP + 1))
        self.assertFalse(over.ok)
        self.assertEqual(over.error_detail, "Invalid attachment")
        MEASURED[f"{self.transport}.request_max_count"] = COUNT_CAP

    def test_non_conforming_sender_meets_the_receivers_own_cap(self):
        """The sender's local check bypassed: one byte over the cap reaches the peer, which refuses it
        (signed rejection, closed reason in its audit) — the transport carried it."""
        with mock.patch.object(a2a_attachments, "validate_attachments", _sender_skips_validation):
            res = self.send(_attachments(CAP + 1))
        self.assertFalse(res.ok)
        self.assertEqual(res.status, "rejected", res)
        MEASURED[f"{self.transport}.receiver_refuses_over_cap"] = res.status

    def test_transport_ceiling_above_the_attachment_cap(self):
        """With the sender's check out of the way, how big may an envelope get before the TRANSPORT
        refuses it (rather than the peer's attachment cap)? Reaching the peer = a signed rejection."""
        seen: dict[int, Any] = {}

        def reaches_peer(total: int) -> bool:
            with mock.patch.object(a2a_attachments, "validate_attachments", _sender_skips_validation):
                res = self.send(_attachments(total))
            seen[total] = res
            return res.status == "rejected" and res.error_category == rts.ErrorCategory.REJECTED

        lo, hi = 2 * 1024 * 1024, 4 * 1024 * 1024
        self.assertTrue(reaches_peer(lo))
        self.assertFalse(reaches_peer(hi))
        k = 1024   # KiB granularity keeps the bisection at ~11 sends of ~3-5 MB each
        got = bisect_max(lambda n: reaches_peer(n * k), lo // k, hi // k) * k
        MEASURED[f"{self.transport}.transport_ceiling_raw_bytes_kib_floor"] = got
        over = seen[[n for n in sorted(seen) if n > got][0]]
        MEASURED[f"{self.transport}.over_transport_ceiling"] = [over.status, over.error_category,
                                                                over.maybe_delivered]
        self.assertFalse(over.ok)
        self.assertFalse(over.maybe_delivered, "over the ceiling nothing was delivered")
        if self.transport == "direct":
            # The peer's body cap (a2a_http_server.max_body_bytes, 4 MiB of JSON) answers 413 and closes
            # the socket before reading the body; the sender, still writing, sees a broken pipe.
            self.assertEqual(over.error_category, rts.ErrorCategory.UNREACHABLE, over)
        else:
            # The sender's own frame check (relay plaintext cap == the same 4 MiB) refuses before connecting.
            self.assertEqual(over.error_category, rts.ErrorCategory.PROTOCOL_ERROR, over)
            self.assertEqual(over.error_detail, "Message exceeds relay size limit")
        # Both transports cap the same 4 MiB of envelope JSON; measured at 1 KiB resolution.
        self.assertEqual(got, 3069 * 1024)
        self.assertGreater(got, CAP * 2, "the transport carries far more than the attachment cap")
        self.assertLess(got, a2a_relay._MAX_PLAINTEXT_BYTES * 3 // 4)
        self.assertEqual(self.engine.inputs, [], "nothing over the attachment cap ever reached the worker")

    def test_response_files_inline_maximum(self):
        """The peer's worker writes one file: CAP bytes come back inline, CAP+1 is reported as dropped
        (inline only: a requester asking for references gets CAP+1 by reference, ADR-2287)."""
        inline_only = {"properties": {"ok": {"type": "boolean"}}, "attachments_by_reference": False}
        self.engine.files = {"result.bin": b"r" * CAP}
        res = self.send(schema=inline_only)
        self.assertTrue(res.ok, res)
        self.assertEqual([a["name"] for a in res.attachments], ["result.bin"])
        self.engine.files = {"result.bin": b"r" * (CAP + 1)}
        res = self.send(schema=inline_only)
        self.assertTrue(res.ok, res)
        self.assertEqual(res.attachments, [])
        self.assertEqual(res.data.get("attachments_dropped"), {"count": 1, "reasons": {"byte_cap": 1}})
        MEASURED[f"{self.transport}.response_max_total_bytes"] = CAP


class TestDirectHTTPLimit(_LimitMeasurement, TransportCase):
    transport = "direct"
    forge_se = mock.MagicMock()


class TestRelayLimit(_LimitMeasurement, TransportCase):
    transport = "relay"
    forge_se = mock.MagicMock()


class TestTheCapsAreOneChain(unittest.TestCase):
    """The constants the measurement depends on, so a change to one of them fails here first."""

    def test_caps(self):
        self.assertEqual(CAP, 1024 * 1024)
        self.assertEqual(COUNT_CAP, 16)
        self.assertEqual(a2a_http_server._A2AHandler.max_body_bytes, 4 * 1024 * 1024)
        self.assertEqual(a2a_relay._MAX_PLAINTEXT_BYTES, a2a_http_server._A2AHandler.max_body_bytes)
        self.assertEqual(rts._MAX_RESPONSE_BYTES, 6 * 1024 * 1024)
        # a max-size inline envelope (1 MiB raw, base64, JSON overhead) is far below both transports
        self.assertLess((CAP * 4) // 3 + 64 * 1024, a2a_relay._MAX_PLAINTEXT_BYTES)


if __name__ == "__main__":
    unittest.main()
