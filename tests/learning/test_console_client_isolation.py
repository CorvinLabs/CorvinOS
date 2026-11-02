"""``console_client()`` writes audit records into ITS OWN sandbox and nowhere else.

The root conftest's "protected chain grew (own instance id)" summary cannot tell a test's
write from the running live services: both carry the live install's instance id. This
test decides it for ``console_client()`` without looking at the live chain at all: every
open-for-write of an ``audit.jsonl`` made by this process while the sandboxed console
boots, authenticates and answers a write route is recorded, and each one must lie under
the home ``console_client()`` created (``sb.home``). A positive control (at least one
such write, and a record in the sandbox tenant chain) proves the recorder saw the audit
writer at all — without it an empty list would pass for a recorder that watched nothing.
"""
from __future__ import annotations

import builtins
import importlib.util
import io
import os
from pathlib import Path

from tests.learning.console_client import console_client
from tests.learning.test_l5_rollback_reset_route_e2e import RESET, _trip

_REPO = Path(__file__).resolve().parents[2]
_WRITE_MODE_CHARS = frozenset("wax+")
_O_WRITE = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC


def _under(p: Path, root: Path) -> bool:
    return p == root or p.is_relative_to(root)


def _bridges_paths():
    spec = importlib.util.spec_from_file_location(
        "_console_client_isolation_bridges_paths",
        _REPO / "corvin_operator" / "bridges" / "shared" / "paths.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_console_client_writes_audit_records_only_into_its_own_sandbox(tmp_path, monkeypatch):
    writes: list[Path] = []
    real_io_open, real_os_open = io.open, os.open  # the root conftest's fences, kept in the chain

    def _note(file: object) -> None:
        if isinstance(file, (str, bytes, os.PathLike)):
            p = Path(os.path.realpath(os.fsdecode(os.fspath(file))))
            if p.name == "audit.jsonl":
                writes.append(p)

    def rec_io_open(file, mode="r", *args, **kwargs):
        if isinstance(mode, str) and _WRITE_MODE_CHARS & set(mode):
            _note(file)
        return real_io_open(file, mode, *args, **kwargs)

    def rec_os_open(path, flags, *args, **kwargs):
        if flags & _O_WRITE:
            _note(path)
        return real_os_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(io, "open", rec_io_open)
    monkeypatch.setattr(builtins, "open", rec_io_open)
    monkeypatch.setattr(os, "open", rec_os_open)

    with console_client(tmp_path) as sb:
        # The boundary is console_client()'s OWN home, not tmp_path: tests/conftest.py
        # already parks CORVIN_HOME at tmp_path/corvin-home, so "under tmp_path" would
        # also accept a console_client() that never set its home.
        sandbox = Path(os.path.realpath(sb.home))
        from core.paths import tenant as core_paths
        from forge import paths as forge_paths

        # Every resolver mirror the audit writers use points into this test's sandbox.
        for label, chain in (
            ("forge.paths", forge_paths.tenant_audit_chain(sb.tenant_id)),
            ("core.paths", core_paths.tenant_audit_chain(sb.tenant_id)),
            ("bridges.paths", _bridges_paths().tenant_audit_chain(sb.tenant_id)),
        ):
            assert _under(Path(os.path.realpath(chain)), sandbox), f"{label} -> {chain}"
        # A real rollback trip (chains ``routing.rollback_triggered``), then the real
        # reset route through the real router (session cookie + CSRF) chains its own record.
        _trip(sb)
        r = sb.client.post(RESET, headers=sb.csrf_headers)
        assert r.status_code == 200, r.text
        tenant_chain = sb.tenant_home / "global" / "forge" / "audit.jsonl"
        sandbox_records = tenant_chain.read_text().splitlines() if tenant_chain.exists() else []

    # Positive control: the recorder saw the audit writer, and its records are in the sandbox.
    assert writes, "recorder saw no audit.jsonl write at all — it watched nothing"
    assert sandbox_records, f"no record in the sandbox tenant chain {tenant_chain}"
    outside = sorted({str(p) for p in writes if not _under(p, sandbox)})
    assert outside == [], f"console_client() opened an audit chain outside its sandbox: {outside}"
