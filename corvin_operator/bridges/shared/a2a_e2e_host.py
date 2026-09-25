"""Test host for the zero-config A2A E2E suite (test_a2a_zero_config_e2e.py).

One process = one Corvin instance: its own CORVIN_HOME (own instance id,
own tenant feature flags, own audit chain), its own connection directories,
the real RemoteTriggerReceiver, the real A2A connectivity manager (relay
listener, ingress, hello/ping upkeep) and the real console route functions
for token creation / import — exactly the code the Agent Hub calls.

Control surface (127.0.0.1 only, test harness use):

    POST /create            {"label": str}          -> {"token", "kid"}
    POST /import            {"token": str}          -> import response
    POST /revoke/<kid>                              -> {"ok"}
    POST /send/<kid>                                -> real signed A2A envelope to the peer
    GET  /status                                    -> connection files + diagnostics

Run: python a2a_e2e_host.py --control-port N   (env carries the instance setup)
"""
from __future__ import annotations

import argparse
import asyncio
import http.server
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
for _p in (_HERE, _REPO / "core" / "console", _REPO / "corvin_operator" / "forge", _REPO):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


class _Rec:
    tenant_id = "_default"
    sid_fingerprint = "e2e-host"


def _dirs() -> tuple[Path, Path, Path]:
    return (Path(os.environ["REMOTE_ORIGINS_DIR"]), Path(os.environ["REMOTE_ENDPOINTS_DIR"]),
            Path(os.environ["REMOTE_PENDING_FRIENDSHIPS_DIR"]))


def _status() -> dict[str, Any]:
    from corvin_console.routes import a2a_pair as ap  # noqa: PLC0415
    out = ap.a2a_diagnostics(_Rec())
    # Harness-only extra: the stored peer URL per connection (the console
    # diagnostics deliberately report only whether one exists).
    _o, endpoints, _p = _dirs()
    urls: dict[str, str] = {}
    for p in endpoints.glob("*.json"):
        try:
            urls[p.stem] = json.loads(p.read_text("utf-8")).get("url") or ""
        except (OSError, ValueError):
            pass
    out["peer_urls"] = urls
    return out


class _E2EAgentEngine:
    """Scripted agent for the multi-peer E2E (``E2E_AGENT_MODE=echo``).

    Runs through the REAL worker path (a2a_worker.spawn_a2a_worker: framing,
    scratch workspace, input drop, output harvest, parse) — only the model is
    replaced. It proves content end to end: it echoes the ``[e2e-…]`` marker
    from the instruction, reports the SHA-256 of every input file it found in
    ``in/``, and returns an image it rendered itself as an out/ attachment.
    """

    name = "e2e_agent"
    capabilities: dict = {}

    def __init__(self, agent: str) -> None:
        self.agent = agent

    @staticmethod
    def _png(rgb: tuple[int, int, int], size: int = 32) -> bytes:
        import struct
        import zlib
        raw = b"".join(b"\x00" + bytes(rgb) * size for _ in range(size))

        def chunk(t: bytes, d: bytes) -> bytes:
            return (struct.pack(">I", len(d)) + t + d
                    + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF))

        return (b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))

    def spawn(self, prompt: str, *, working_dir: Any = None, **_kw: Any):
        import hashlib
        import re
        from agents import StreamEvent  # type: ignore[import-not-found]

        ws = Path(working_dir)
        inputs = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sorted((ws / "in").iterdir()) if p.is_file()}
        m = re.search(r"\[(e2e-[0-9a-f]+)\]", prompt)
        marker = m.group(1) if m else "no-marker"
        # "[sleep=N]" simulates a long model turn (N ≤ 90 s) so the E2E can
        # prove concurrency, the default timeout and the work executor.
        sl = re.search(r"\[sleep=(\d{1,2})\]", prompt)
        if sl:
            import time as _t
            _t.sleep(min(90, int(sl.group(1))))
        digest = hashlib.sha256(self.agent.encode()).digest()
        (ws / "out" / f"{self.agent}-reply.png").write_bytes(self._png(tuple(digest[:3])))
        reply = (f"**{self.agent}** received `{marker}` with {len(inputs)} attachment(s): "
                 + ", ".join(f"{n}={h[:12]}" for n, h in inputs.items()))
        yield StreamEvent(type="text_delta", text=reply)
        yield StreamEvent(type="turn_completed", usage={})


def main() -> int:
    ap_ = argparse.ArgumentParser()
    ap_.add_argument("--control-port", type=int, required=True)
    args = ap_.parse_args()

    origins, endpoints, pending = _dirs()
    for d in (origins, endpoints, pending):
        d.mkdir(parents=True, exist_ok=True)

    import remote_trigger_receiver as rtr  # noqa: PLC0415
    import a2a_connectivity  # noqa: PLC0415
    from corvin_console.routes import a2a_pair as ap  # noqa: PLC0415
    from fastapi import HTTPException  # noqa: PLC0415

    agent = os.environ.get("E2E_AGENT_NAME", "")
    if os.environ.get("E2E_AGENT_MODE") == "echo" and agent:
        receiver = rtr.RemoteTriggerReceiver(
            origins_dir=origins, engine_factory=lambda: _E2EAgentEngine(agent))
    else:
        receiver = rtr.RemoteTriggerReceiver(origins_dir=origins, force_m1_only=True)

    loop = asyncio.new_event_loop()

    def _run_loop() -> None:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(a2a_connectivity.start_manager(
            receiver=receiver, origins_dir=origins, endpoints_dir=endpoints,
            pending_dir=pending))
        loop.run_forever()

    threading.Thread(target=_run_loop, name="e2e-loop", daemon=True).start()

    class _Ctl(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a: Any) -> None:  # noqa: D401
            pass

        def _send(self, code: int, obj: Any) -> None:
            body = json.dumps(obj, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict[str, Any]:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}") if n else {}

        def do_GET(self):  # noqa: N802
            if self.path == "/status":
                self._send(200, _status())
            elif self.path == "/feed":
                import a2a_feed  # noqa: PLC0415
                self._send(200, {"messages": a2a_feed.read(limit=0)})
            else:
                self._send(404, {})

        def do_POST(self):  # noqa: N802
            try:
                if self.path == "/create":
                    b = self._body()
                    res = ap.friendship_create(
                        ap.FriendshipCreateRequest(label=b.get("label", "")), _Rec())
                    self._send(200, {"token": res.token, "kid": res.kid})
                elif self.path == "/import":
                    b = self._body()
                    res = ap.friendship_import(
                        ap.FriendshipImportRequest(
                            token=b["token"], spawn_worker=bool(b.get("spawn_worker"))),
                        _Rec())
                    self._send(200, res.model_dump())
                elif self.path.startswith("/refresh/"):
                    self._send(200, ap.friendship_recheck(self.path.split("/")[-1], _Rec()))
                elif self.path.startswith("/revoke/"):
                    self._send(200, ap.friendship_revoke(self.path.split("/")[-1], _Rec()))
                elif self.path.startswith("/send/"):
                    kid = self.path.split("/")[-1]
                    import remote_trigger_sender as rts  # noqa: PLC0415
                    sender = rts.RemoteTriggerSender(endpoints, rts.RemoteEndpointRegistry(endpoints))
                    b = self._body()
                    result = sender.send(kid, b.get("text") or "e2e ping message",
                                         attachments=b.get("attachments") or None,
                                         ttl_s=60, timeout_s=int(b.get("timeout_s") or 20))
                    self._send(200, {"ok": result.ok, "status": result.status,
                                     "task_id": result.task_id, "data": result.data,
                                     "attachments": [
                                         {k: a[k] for k in ("name", "mime", "sha256")}
                                         for a in result.attachments],
                                     "error": getattr(result, "error_category", None),
                                     "detail": getattr(result, "error_detail", None)})
                else:
                    self._send(404, {})
            except HTTPException as exc:
                self._send(exc.status_code, {"detail": exc.detail})
            except Exception as exc:  # noqa: BLE001 — surface to the harness
                self._send(500, {"error": type(exc).__name__, "detail": str(exc)[:300]})

    server = http.server.ThreadingHTTPServer(("127.0.0.1", args.control_port), _Ctl)
    print(f"e2e-host ready on {args.control_port}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
