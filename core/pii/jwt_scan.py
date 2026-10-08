"""Linear-time JWT matcher with a regex-compatible surface.

Every JWT detector in this repo was a regex of the shape

    \\beyJ[A-Za-z0-9_-]{a,}\\.[A-Za-z0-9_-]{b,}\\.[A-Za-z0-9_-]{c,}\\b

which is QUADRATIC: the run class contains ``-`` (a non-word character), so
inside one long run such as ``eyJ-eyJ-eyJ-…`` every ``eyJ`` is a candidate
start, and every start re-scans the run to its end looking for the ``.``.
Measured 2026-10-08: 80 KB took 0.73 s in 10 of 11 copies, x4 input = x16 time,
1 MiB ~2 min — in the PII gate, the log scrubber, the telemetry scrubbers.
Possessive quantifiers do not help (each start still CONSUMES its run).

This matcher finds the maximal class runs once (linear), then answers each
``eyJ`` candidate with an O(log n) lookup of its run's end, memoising the
trailing-boundary search per third segment. It reproduces the regex's match
spans exactly — ``tests/pii/test_jwt_scan.py`` checks that differentially
against ``re`` on random inputs for every configuration used in the tree.

    LinearJwtPattern(a, b, c, start_boundary=True, end_boundary=True)
        == re.compile(r"\\beyJ[A-Za-z0-9_-]{a,}\\.[A-Za-z0-9_-]{b,}\\.[A-Za-z0-9_-]{c,}\\b")

``start_boundary`` / ``end_boundary`` drop the leading / trailing ``\\b``.
Stdlib only, so every scrubber (bridges, telemetry) can import it.
"""
from __future__ import annotations

import bisect
import re
from typing import Callable, Iterator, Optional, Union

_RUN = re.compile(r"[A-Za-z0-9_\-]+")


def _word(ch: str) -> bool:
    # Python's \w (str patterns, no ASCII flag): Unicode alphanumerics + "_".
    return ch.isalnum() or ch == "_"


class _Match:
    __slots__ = ("string", "_s", "_e")

    def __init__(self, string: str, s: int, e: int) -> None:
        self.string, self._s, self._e = string, s, e

    def span(self, group: int = 0) -> tuple[int, int]:
        return self._s, self._e

    def start(self, group: int = 0) -> int:
        return self._s

    def end(self, group: int = 0) -> int:
        return self._e

    def group(self, group: int = 0) -> str:
        if group != 0:
            raise IndexError("no such group")
        return self.string[self._s:self._e]

    __getitem__ = group

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<jwt match span=({self._s}, {self._e})>"


class _Scan:
    """Per-text state: run boundaries and the memo of third-segment ends."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.n = len(text)
        self.starts: list[int] = []
        self.ends: list[int] = []
        for m in _RUN.finditer(text):
            self.starts.append(m.start())
            self.ends.append(m.end())
        self.memo: dict[int, int] = {}

    def run_end(self, i: int) -> int:
        """End of the class run containing position ``i`` (``i`` if none)."""
        k = bisect.bisect_right(self.starts, i) - 1
        return self.ends[k] if k >= 0 and i < self.ends[k] else i

    def boundary(self, p: int) -> bool:
        t, n = self.text, self.n
        before = p > 0 and _word(t[p - 1])
        after = p < n and _word(t[p])
        return before != after


class LinearJwtPattern:
    """Drop-in for the compiled JWT regex: ``search`` / ``finditer`` /
    ``findall`` / ``sub`` with identical spans, in linear time."""

    def __init__(self, a: int, b: int, c: int, *, start_boundary: bool = True, end_boundary: bool = True) -> None:
        if min(a, b, c) < 1:
            raise ValueError("segment minimums must be >= 1")
        self.a, self.b, self.c = a, b, c
        self.start_boundary, self.end_boundary = start_boundary, end_boundary
        self.pattern = ((r"\b" if start_boundary else "") + rf"eyJ[A-Za-z0-9_\-]{{{a},}}\."
                        rf"[A-Za-z0-9_\-]{{{b},}}\.[A-Za-z0-9_\-]{{{c},}}" + (r"\b" if end_boundary else ""))

    def __repr__(self) -> str:
        return f"LinearJwtPattern({self.pattern!r})"

    # ── core ─────────────────────────────────────────────────────────────
    def _third_end(self, sc: _Scan, s3: int) -> int:
        """End of segment three starting at ``s3``, or -1. Greedy like the regex:
        the longest prefix of the run (>= c chars) that ends on a boundary."""
        if s3 in sc.memo:
            return sc.memo[s3]
        e = sc.run_end(s3)
        res = -1
        if e - s3 >= self.c:
            if not self.end_boundary:
                res = e
            else:
                p = e
                while p >= s3 + self.c:
                    if sc.boundary(p):
                        res = p
                        break
                    p -= 1
        sc.memo[s3] = res
        return res

    def _match_at(self, sc: _Scan, i: int) -> int:
        """End of a match starting at ``i`` (where text[i:i+3] == 'eyJ'), or -1."""
        t, n = sc.text, sc.n
        if self.start_boundary and i > 0 and _word(t[i - 1]):
            return -1
        r1 = sc.run_end(i)
        if r1 - (i + 3) < self.a or r1 >= n or t[r1] != ".":
            return -1
        r2 = sc.run_end(r1 + 1)
        if r2 - (r1 + 1) < self.b or r2 >= n or t[r2] != ".":
            return -1
        return self._third_end(sc, r2 + 1)

    def _iter(self, text: str, pos: int = 0, endpos: Optional[int] = None) -> Iterator[tuple[int, int]]:
        if not isinstance(text, str):
            raise TypeError(f"expected str, got {type(text).__name__}")
        if endpos is not None:
            text = text[:endpos]
        sc = _Scan(text)
        i = text.find("eyJ", pos)
        while i != -1:
            e = self._match_at(sc, i)
            if e != -1:
                yield i, e
                i = text.find("eyJ", e)
            else:
                i = text.find("eyJ", i + 1)

    # ── regex-compatible surface ─────────────────────────────────────────
    def search(self, text: str, pos: int = 0, endpos: Optional[int] = None) -> Optional[_Match]:
        for s, e in self._iter(text, pos, endpos):
            return _Match(text, s, e)
        return None

    def finditer(self, text: str, pos: int = 0, endpos: Optional[int] = None) -> Iterator[_Match]:
        for s, e in self._iter(text, pos, endpos):
            yield _Match(text, s, e)

    def findall(self, text: str, pos: int = 0, endpos: Optional[int] = None) -> list[str]:
        return [text[s:e] for s, e in self._iter(text, pos, endpos)]

    def sub(self, repl: Union[str, Callable[[_Match], str]], text: str, count: int = 0) -> str:
        return self.subn(repl, text, count)[0]

    def subn(self, repl: Union[str, Callable[[_Match], str]], text: str, count: int = 0) -> tuple[str, int]:
        if isinstance(repl, str) and "\\" in repl:
            raise ValueError("LinearJwtPattern.sub supports literal replacements only")
        out: list[str] = []
        last = done = 0
        for s, e in self._iter(text):
            if count and done >= count:
                break
            out.append(text[last:s])
            out.append(repl(_Match(text, s, e)) if callable(repl) else repl)
            last = e
            done += 1
        out.append(text[last:])
        return "".join(out), done
