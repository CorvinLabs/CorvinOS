"""Phase 13.5 — MCP bridge + Forge socket discovery tests.

The six acceptance cases per the implementation plan are split into
discovery-level tests (this file) and roundtrip-level tests that drive
a real worker + a real ForgeMcpServer instance.
"""
from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PLUGIN_ROOT.parents[1]

# corvin_compute first, forge second — both append themselves to sys.path
sys.path.insert(0, str(PLUGIN_ROOT))
sys.path.insert(0, str(REPO_ROOT / "operator" / "forge"))

# Local plugin imports
from corvin_compute.client import WorkerClient, is_socket_reachable  # noqa: E402
from corvin_compute.mcp_bridge import (  # noqa: E402
    COMPUTE_TOOL_NAMES,
    compute_tool_definitions,
)
from corvin_compute.worker import WorkerServer  # noqa: E402


def _identity_runner(slow_ms: int = 0):
    def runner(tool_name, payload):
        if slow_ms > 0:
            time.sleep(slow_ms / 1000.0)
        return {"loss": float(payload.get("x", 0))}
    return runner


class _SandboxedForge:
    """Bring up a fresh CORVIN_HOME + (optionally) a worker for it."""

    def __init__(self, with_worker: bool = True) -> None:
        self.td = tempfile.mkdtemp(prefix="corvin-compute-mcp-")
        self.corvin_home = Path(self.td) / "corvin"
        self.tenant_id = "_default"
        (self.corvin_home / "tenants" / self.tenant_id / "compute"
         ).mkdir(parents=True, exist_ok=True)
        self.socket_path = (self.corvin_home / "tenants" / self.tenant_id
                            / "compute" / "worker.sock")
        self._old_env: dict[str, str | None] = {}
        for k in ("CORVIN_HOME", "CORVIN_TENANT_ID"):
            self._old_env[k] = os.environ.get(k)
        os.environ["CORVIN_HOME"] = str(self.corvin_home)
        os.environ["CORVIN_TENANT_ID"] = self.tenant_id
        self.with_worker = with_worker
        self.loop = None
        self.server = None
        self.thread = None
        self._reset_caches()

    def _reset_caches(self) -> None:
        from forge import _compute_discovery
        _compute_discovery.reset_caches_for_tests()
        # Reset paths cache (corvin_home is cached) if exposed.
        try:
            from forge import paths as _paths
            for attr in ("_CORVIN_HOME_CACHE", "_corvin_home_cache"):
                if hasattr(_paths, attr):
                    setattr(_paths, attr, None)
        except ImportError:
            pass

    def start(self) -> None:
        if not self.with_worker:
            return
        ready = threading.Event()
        import asyncio

        def _runner():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.server = WorkerServer(
                tenant_id=self.tenant_id,
                corvin_home=self.corvin_home,
                socket_path=self.socket_path,
                max_concurrent_runs=2,
                runner_fn=_identity_runner(),
            )
            async def _serve():
                t = asyncio.create_task(self.server.serve_forever())
                while not self.socket_path.exists():
                    await asyncio.sleep(0.01)
                ready.set()
                await t
            try:
                self.loop.run_until_complete(_serve())
            except Exception:
                pass
            finally:
                self.loop.close()
        self.thread = threading.Thread(target=_runner, daemon=True)
        self.thread.start()
        if not ready.wait(timeout=5.0):
            raise RuntimeError("worker failed to start")

    def stop(self) -> None:
        if self.server and self.loop:
            import asyncio
            try:
                asyncio.run_coroutine_threadsafe(self.server.stop(),
                                                  self.loop).result(timeout=5.0)
            except Exception:
                pass
            try:
                self.loop.call_soon_threadsafe(self.loop.stop)
            except Exception:
                pass
        if self.thread:
            self.thread.join(timeout=5.0)
        # Restore env
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        import shutil
        shutil.rmtree(self.td, ignore_errors=True)


class DiscoveryTests(unittest.TestCase):
    def test_discovery_returns_true_when_worker_present(self) -> None:
        sandbox = _SandboxedForge(with_worker=True)
        sandbox.start()
        try:
            from forge._compute_discovery import is_worker_reachable
            self.assertTrue(is_worker_reachable(
                tenant_id=sandbox.tenant_id,
                socket_path=sandbox.socket_path,
            ))
        finally:
            sandbox.stop()

    def test_discovery_returns_false_when_absent(self) -> None:
        sandbox = _SandboxedForge(with_worker=False)
        try:
            from forge._compute_discovery import is_worker_reachable
            self.assertFalse(is_worker_reachable(
                tenant_id=sandbox.tenant_id,
                socket_path=sandbox.socket_path,
            ))
        finally:
            sandbox.stop()

    def test_discovery_returns_false_when_stale(self) -> None:
        """Socket file exists but no listener — must be detected as unreachable."""
        sandbox = _SandboxedForge(with_worker=False)
        try:
            # Touch a fake socket file
            sandbox.socket_path.parent.mkdir(parents=True, exist_ok=True)
            sandbox.socket_path.touch()
            from forge._compute_discovery import is_worker_reachable
            self.assertFalse(is_worker_reachable(
                tenant_id=sandbox.tenant_id,
                socket_path=sandbox.socket_path,
            ))
        finally:
            sandbox.stop()

    def test_audit_emit_one_shot_per_tenant(self) -> None:
        sandbox = _SandboxedForge(with_worker=False)
        try:
            from forge import _compute_discovery
            _compute_discovery.reset_caches_for_tests()
            calls: list[tuple] = []

            def _audit(event, **kw):
                calls.append((event, kw.get("details", {})))

            for _ in range(3):
                _compute_discovery.is_worker_reachable(
                    tenant_id=sandbox.tenant_id,
                    audit_emit=_audit,
                    socket_path=sandbox.socket_path,
                )
                # Bust the time-based cache so we'd re-emit if dedup broke.
                _compute_discovery._cache.clear()  # type: ignore[attr-defined]
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], "compute.worker_unreachable")
            self.assertEqual(calls[0][1]["tenant_id"], sandbox.tenant_id)
        finally:
            sandbox.stop()

    def test_discovery_cache(self) -> None:
        """Within the 5 s window, repeated calls don't reach the OS."""
        sandbox = _SandboxedForge(with_worker=True)
        sandbox.start()
        try:
            from forge import _compute_discovery
            _compute_discovery.reset_caches_for_tests()
            t0 = time.time()
            for _ in range(100):
                _compute_discovery.is_worker_reachable(
                    tenant_id=sandbox.tenant_id,
                    socket_path=sandbox.socket_path,
                )
            elapsed = time.time() - t0
            self.assertLess(elapsed, 0.5,
                            f"100 cached probes should be sub-second, took {elapsed:.2f}")
        finally:
            sandbox.stop()


class ToolDefinitionTests(unittest.TestCase):
    def test_four_canonical_tools(self) -> None:
        defs = compute_tool_definitions()
        names = {t["name"] for t in defs}
        self.assertEqual(names, COMPUTE_TOOL_NAMES)
        self.assertEqual(names, {"compute_run", "compute_status",
                                 "compute_result", "compute_abort"})

    def test_schemas_strict(self) -> None:
        defs = compute_tool_definitions()
        for t in defs:
            self.assertIn("inputSchema", t)
            self.assertEqual(t["inputSchema"].get("type"), "object")
            self.assertFalse(t["inputSchema"].get("additionalProperties", True),
                             f"{t['name']}: must reject extra properties")


class ForgeIntegrationTests(unittest.TestCase):
    """End-to-end: bring up a fake forge MCP server in-process + worker."""

    def test_forge_server_advertises_compute_when_worker_up(self) -> None:
        sandbox = _SandboxedForge(with_worker=True)
        sandbox.start()
        try:
            # Construct a minimal forge server using its public API.
            from forge.mcp_server import MCPServer
            forge_root = Path(sandbox.td) / "forge-ws"
            forge_root.mkdir(parents=True, exist_ok=True)
            server = MCPServer(root=forge_root)
            tools = server._all_tools()
            tool_names = {t["name"] for t in tools}
            for n in COMPUTE_TOOL_NAMES:
                self.assertIn(n, tool_names,
                              f"compute tool {n!r} not advertised when worker up")
        finally:
            sandbox.stop()

    def test_forge_server_hides_compute_when_worker_down(self) -> None:
        sandbox = _SandboxedForge(with_worker=False)
        try:
            from forge.mcp_server import MCPServer
            forge_root = Path(sandbox.td) / "forge-ws"
            forge_root.mkdir(parents=True, exist_ok=True)
            server = MCPServer(root=forge_root)
            tools = server._all_tools()
            tool_names = {t["name"] for t in tools}
            for n in COMPUTE_TOOL_NAMES:
                self.assertNotIn(n, tool_names,
                                 f"compute tool {n!r} leaked when worker down")
        finally:
            sandbox.stop()


if __name__ == "__main__":
    unittest.main(verbosity=2)
