"""A REAL compute worker for end-to-end tests: Unix socket, the worker's own forge runner
(``corvin_compute.cli._build_runner_fn``: a forge Registry at the tenant's USER scope, every
iteration a bwrap-sandboxed forge run), crash recovery on start.

Used by tests that must prove a behaviour across the worker boundary rather than against a
stubbed runner. Tools are seeded straight into the user-scope registry (the forge licence gate
on ``registry.create`` is bypassed there: licensing is not what these tests are about).

AF_UNIX paths are capped at ~108 bytes, so ``home`` must be a SHORT directory
(``tempfile.mkdtemp(prefix="cw-")``), never pytest's ``tmp_path``.
"""
from __future__ import annotations

import asyncio
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from unittest import mock

_COMPUTE = Path(__file__).resolve().parents[1]
_FORGE = Path(__file__).resolve().parents[3] / "corvin_operator" / "forge"
for _p in (_COMPUTE, _FORGE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

TENANT = "_default"


def seed_user_tool(name: str, impl: str, input_schema: dict, *, tenant: str = TENANT,
                   description: str = "e2e tool") -> None:
    """Register a tool in the tenant's user-scope forge registry (``CORVIN_HOME`` must be set)."""
    import forge.registry as freg
    from forge.scope import scope_root
    root = scope_root("user", tenant_id=tenant)
    root.mkdir(parents=True, exist_ok=True)
    with mock.patch.object(freg, "_require_forge_create", lambda *a, **k: None):
        freg.Registry(root).create(name=name, description=description, input_schema=input_schema,
                                   impl=impl, scope="user", tenant_id=tenant)


@contextmanager
def real_worker(home: Path, *, tenant: str = TENANT, max_concurrent_runs: int = 2,
                runner_fn=None) -> Iterator[tuple]:
    """Start a worker over ``home`` and yield ``(client, server)``; stop it on exit.

    ``CORVIN_HOME`` must already point at ``home`` (the forge runner reads it) unless a stub
    ``runner_fn`` is passed (no forge, no bwrap). Recovery of
    non-terminal runs on disk runs as part of ``serve_forever``, exactly like a service start.
    """
    from corvin_compute import cli
    from corvin_compute.client import WorkerClient
    from corvin_compute.endpoint import endpoint_exists
    from corvin_compute.worker import WorkerServer

    runner = runner_fn or cli._build_runner_fn(tenant_id=tenant, corvin_home_path=home)
    sock = home / "tenants" / tenant / "compute" / "worker.sock"
    sock.parent.mkdir(parents=True, exist_ok=True)
    state: dict = {}
    ready = threading.Event()

    def _serve() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        server = WorkerServer(tenant_id=tenant, corvin_home=home, socket_path=sock,
                              max_concurrent_runs=max_concurrent_runs, runner_fn=runner)
        state.update(loop=loop, server=server)

        async def _main() -> None:
            t = asyncio.create_task(server.serve_forever())
            while not endpoint_exists(sock):
                await asyncio.sleep(0.01)
            ready.set()
            await t

        try:
            loop.run_until_complete(_main())
        except Exception:  # noqa: BLE001
            pass
        finally:
            loop.close()

    th = threading.Thread(target=_serve, daemon=True)
    th.start()
    if not ready.wait(15):
        raise RuntimeError("real worker did not start")
    try:
        yield WorkerClient(sock, timeout_s=60), state["server"]
    finally:
        try:
            asyncio.run_coroutine_threadsafe(state["server"].stop(), state["loop"]).result(10)
        except Exception:  # noqa: BLE001
            pass
        th.join(10)
