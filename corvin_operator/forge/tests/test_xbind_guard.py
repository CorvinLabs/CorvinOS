"""R3-6 regression: x-bind guard rejects non-regular special files for BOTH
ro and rw binds (a ro-bound host UNIX socket would otherwise expose a host
service into the sandbox)."""
import socket
import sys
import tempfile
from pathlib import Path

import pytest

# Make the inner `forge` package importable when this test is collected by
# pytest (mirrors test_requirements.py / test_audit_detail_floor.py).
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from forge.runner import _guard_bind_target  # noqa: E402


def _sock_in_tmp() -> Path:
    p = Path(tempfile.mkdtemp(dir="/tmp")) / "svc.sock"
    s = socket.socket(socket.AF_UNIX)
    s.bind(str(p))
    _sock_in_tmp._keep.append(s)  # keep the socket fd alive for the test
    return p


_sock_in_tmp._keep = []  # type: ignore[attr-defined]


@pytest.mark.parametrize("writable", [False, True])
def test_unix_socket_rejected_for_both_ro_and_rw(writable):
    p = _sock_in_tmp()
    with pytest.raises(ValueError):
        _guard_bind_target(str(p), p.resolve(), writable=writable)


@pytest.mark.parametrize("writable", [False, True])
def test_fifo_rejected_for_both_ro_and_rw(writable):
    import os
    d = Path(tempfile.mkdtemp(dir="/tmp"))
    fifo = d / "pipe"
    os.mkfifo(str(fifo))
    with pytest.raises(ValueError):
        _guard_bind_target(str(fifo), fifo.resolve(), writable=writable)


@pytest.mark.parametrize("writable", [False, True])
def test_regular_tmp_file_allowed_for_both(writable):
    f = Path(tempfile.mkdtemp(dir="/tmp")) / "data.csv"
    f.write_text("a,b\n1,2\n")
    _guard_bind_target(str(f), f.resolve(), writable=writable)  # must not raise
