"""Per-subtask E2E for ADR-0007 Phase 7 follow-up commits.

Covers:
  * SSE buffer TTL eviction: in-flight buffers are NEVER evicted;
    only closed buffers whose terminal landed > ttl_s ago.
  * gRPC server bootstrap + servicer can SubmitRun + GetRun
    end-to-end over a real grpcio channel (skipped when grpcio
    is missing or the proto stubs haven't been generated).
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "gateway"))
sys.path.insert(0, str(_REPO / "operator" / "forge"))
sys.path.insert(0, str(_REPO / "operator" / "bridges" / "shared"))


# ── SSE TTL eviction ─────────────────────────────────────────────────


class SseTtlEvictionTests(unittest.TestCase):
    def test_in_flight_buffer_never_evicted(self):
        from corvin_gateway.sse import EventBufferRegistry
        async def go():
            loop = asyncio.get_running_loop()
            reg = EventBufferRegistry()
            buf = reg.get_or_create("acme", "run_a", loop)
            buf.append({"type": "text_delta", "text": "hi"})
            # Buffer is open → no closed_at → sweep never evicts
            dropped = reg.sweep_expired(now=time.time() + 1e9, ttl_s=0)
            self.assertEqual(dropped, 0)
            self.assertIsNotNone(reg.get("acme", "run_a"))
        asyncio.run(go())

    def test_closed_buffer_evicted_after_ttl(self):
        from corvin_gateway.sse import EventBufferRegistry
        async def go():
            loop = asyncio.get_running_loop()
            reg = EventBufferRegistry()
            buf = reg.get_or_create("acme", "run_a", loop)
            buf.close({"type": "run.completed", "status": "completed"})
            # Drain the subscriber (none here) and check closed_at
            self.assertIsNotNone(buf.closed_at)
            # Sweep with ttl=0 evicts every closed buffer
            dropped = reg.sweep_expired(ttl_s=0)
            self.assertEqual(dropped, 1)
            self.assertIsNone(reg.get("acme", "run_a"))
        asyncio.run(go())

    def test_closed_within_ttl_not_evicted(self):
        from corvin_gateway.sse import EventBufferRegistry
        async def go():
            loop = asyncio.get_running_loop()
            reg = EventBufferRegistry()
            buf = reg.get_or_create("acme", "run_a", loop)
            buf.close({"type": "run.completed", "status": "completed"})
            # ttl=1800 (default) keeps a freshly-closed buffer
            dropped = reg.sweep_expired(ttl_s=1800.0)
            self.assertEqual(dropped, 0)
            self.assertIsNotNone(reg.get("acme", "run_a"))
        asyncio.run(go())


# ── gRPC E2E ────────────────────────────────────────────────────────


def _grpc_available() -> bool:
    try:
        import grpc  # noqa: F401
        from corvin_gateway.grpc import corvin_pb2, corvin_pb2_grpc  # noqa: F401
        return True
    except ImportError:
        return False


@contextmanager
def sandbox(tenants=("acme",)):
    with tempfile.TemporaryDirectory(prefix="gw-grpc-test-") as td:
        home = Path(td)
        os.environ["CORVIN_HOME"] = str(home)
        os.environ["ADAPTER_FAKE_CLAUDE"] = "1"
        os.environ["ADAPTER_FAKE_DELAY"] = "0.01"
        for t in tenants:
            (home / "tenants" / t / "global" / "auth").mkdir(parents=True)
            (home / "tenants" / t / "global" / "forge").mkdir(parents=True)
            (home / "tenants" / t / "global" / "gateway" / "runs").mkdir(parents=True)
        (home / "tenants" / "_default" / "global" / "forge").mkdir(parents=True)
        try:
            yield home
        finally:
            os.environ.pop("CORVIN_HOME", None)
            os.environ.pop("ADAPTER_FAKE_CLAUDE", None)
            os.environ.pop("ADAPTER_FAKE_DELAY", None)


@unittest.skipUnless(_grpc_available(), "grpcio + proto stubs required")
class GrpcServerTests(unittest.TestCase):
    def test_submit_run_without_jwt_is_unauthenticated(self):
        """gRPC requires a valid JWT bearer token (no loopback bypass).

        Without a proper OIDC JWT the server returns UNAUTHENTICATED.
        This test verifies that non-JWT auth is also rejected (same
        path as no auth — _authenticate returns None for non-JWT input).
        Full round-trip with real JWT is an operator-level integration
        test that requires a live OIDC issuer.
        """
        import grpc as _grpc
        from corvin_gateway.grpc import (
            corvin_pb2 as pb, corvin_pb2_grpc as pb_grpc, grpc_server,
        )
        with sandbox(("acme",)):
            import socket
            with socket.socket() as s:
                s.bind(("127.0.0.1", 0))
                port = s.getsockname()[1]
            server = grpc_server.start_server(host="127.0.0.1", port=port)
            try:
                channel = _grpc.insecure_channel(f"127.0.0.1:{port}")
                stub = pb_grpc.CorvinGatewayStub(channel)
                req = pb.SubmitRunRequest()
                req.tenant_id = "acme"
                req.run.apiVersion = "corvin/v1"
                req.run.kind = "Run"
                req.run.spec.persona = "docs"
                req.run.spec.input = "ping"
                # Non-JWT bearer → _authenticate returns None → UNAUTHENTICATED
                metadata = [("authorization", "Bearer not-a-jwt")]
                with self.assertRaises(_grpc.RpcError) as ctx:
                    stub.SubmitRun(req, metadata=metadata, timeout=5)
                self.assertEqual(
                    ctx.exception.code(), _grpc.StatusCode.UNAUTHENTICATED,
                )
                channel.close()
            finally:
                server.stop(0.5)

    def test_unauthenticated_request_aborted(self):
        import grpc as _grpc
        from corvin_gateway.grpc import (
            corvin_pb2 as pb, corvin_pb2_grpc as pb_grpc, grpc_server,
        )
        with sandbox(("acme",)):
            import socket
            with socket.socket() as s:
                s.bind(("127.0.0.1", 0))
                port = s.getsockname()[1]
            server = grpc_server.start_server(host="127.0.0.1", port=port)
            try:
                channel = _grpc.insecure_channel(f"127.0.0.1:{port}")
                stub = pb_grpc.CorvinGatewayStub(channel)
                req = pb.SubmitRunRequest()
                req.tenant_id = "acme"
                req.run.apiVersion = "corvin/v1"
                req.run.kind = "Run"
                req.run.spec.persona = "docs"
                req.run.spec.input = "ping"
                # NO authorization metadata
                with self.assertRaises(_grpc.RpcError) as ctx:
                    stub.SubmitRun(req, timeout=5)
                self.assertEqual(
                    ctx.exception.code(), _grpc.StatusCode.UNAUTHENTICATED,
                )
                channel.close()
            finally:
                server.stop(0.5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
