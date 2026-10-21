"""Test support: make "hermetic" an assertion instead of a claim.

The ACS tests replace the engine calls. If a regression sends a test down a path that is NOT
replaced, the bridge's direct turn would start the real `claude` CLI (a billed haiku/sonnet call),
and a fresh adapter import opens an HTTPS licence check. ``forbid_real_cli_and_network`` turns
either into a failing assertion. Loopback and unix sockets stay allowed.
"""
from __future__ import annotations

import socket
import subprocess
from pathlib import Path


def forbid_real_cli_and_network(monkeypatch) -> None:
    real_init = subprocess.Popen.__init__

    def guarded_popen(self, args, *a, **k):
        first = str(args[0] if isinstance(args, (list, tuple)) and args else args)
        assert "claude" not in Path(first).name, f"test tried to start the real CLI: {args!r}"
        return real_init(self, args, *a, **k)

    monkeypatch.setattr(subprocess.Popen, "__init__", guarded_popen)

    real_connect = socket.socket.connect

    def guarded_connect(self, address):
        host = address[0] if isinstance(address, tuple) else None     # a str is a unix-socket path
        assert host is None or host in ("127.0.0.1", "::1", "localhost"), (
            f"test tried to open a network connection to {address!r}")
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
