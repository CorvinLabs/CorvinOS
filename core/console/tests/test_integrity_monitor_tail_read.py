"""integrity_monitor reads the chain's TAIL, never the chain (2026-11-07).

``check_global_acs_anomaly`` used ``read_text().splitlines()[-500:]``: on the live
500 MB chain that was 1–2 GB of transient heap and a GIL-holding decode+split on every
integrity scan (boot and every 300 s), stalling the console's event loop.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core/console"))

from corvin_core.aco import integrity_monitor as im  # noqa: E402


def _write_lines(path: Path, n: int, width: int = 200) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for i in range(n):
            fh.write(f'{{"i": {i}, "pad": "{"x" * width}"}}\n')


def test_tail_lines_returns_the_last_n_lines_across_blocks(tmp_path):
    p = tmp_path / "audit.jsonl"
    _write_lines(p, 20_000)                       # ~4.3 MB, several 1 MiB blocks
    out = im._tail_lines(p, 500)
    assert len(out) == 500
    assert out[0].startswith('{"i": 19500,')
    assert out[-1].startswith('{"i": 19999,')


def test_tail_lines_small_file_and_empty_file(tmp_path):
    p = tmp_path / "audit.jsonl"
    _write_lines(p, 7)
    assert [ln[:8] for ln in im._tail_lines(p, 500)] == ['{"i": %d,' % i for i in range(7)]
    p.write_bytes(b"")
    assert im._tail_lines(p, 500) == []


def test_tail_lines_reads_at_most_max_bytes(tmp_path, monkeypatch):
    p = tmp_path / "audit.jsonl"
    _write_lines(p, 20_000)
    reads: list[int] = []
    real_open = Path.open

    class _Counting:
        def __init__(self, fh):
            self._fh = fh
        def read(self, n=-1):
            data = self._fh.read(n)
            reads.append(len(data))
            return data
        def __getattr__(self, name):
            return getattr(self._fh, name)
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return self._fh.__exit__(*exc)

    monkeypatch.setattr(Path, "open", lambda self, *a, **k: _Counting(real_open(self, *a, **k)))
    im._tail_lines(p, 500, block=1 << 20, max_bytes=2 << 20)
    assert 0 < sum(reads) <= 2 << 20


def test_acs_anomaly_check_never_reads_the_whole_chain():
    src = (_REPO / "core/console/corvin_core/aco/integrity_monitor.py").read_text(encoding="utf-8")
    body = src[src.index("def check_global_acs_anomaly("):]
    body = body[:body.index("\ndef ", 10)]
    assert "read_text(" not in body
    assert "_tail_lines(audit_file, 500)" in body
