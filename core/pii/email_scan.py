"""Linear-time twin of ``core.pii.patterns.EMAIL_RFC5322``.

The regex ``\\b[local]+@domain`` is QUADRATIC: its local-part class holds both
word characters and ``.``/``-``, so a run like ``a-a-a-…`` or ``a.a.a.…`` has a
word boundary at every other character, every boundary is a candidate start,
and every start scans the run to its end looking for ``@``. Measured
2026-10-08: 64 KB 1.9 s, x4 input = x16 time (1 MiB ~8 min) — in the
structured PII gate every scanned text passes through.

Here ``@`` is the only anchor. The local part is exactly the maximal class run
ending at that ``@`` (``@`` is not in the class, so the regex's greedy run can
end nowhere else); the regex's leftmost start is the first word boundary in
that run; the domain is matched by the UNCHANGED domain sub-expression from the
``@``. The domain never depends on where the local part started, so this is
the regex's own result, found without re-scanning. Equivalence is checked
differentially in ``tests/pii/test_email_scan.py``.
"""
from __future__ import annotations

import bisect
import re
from typing import Callable, Iterator, Optional, Union

from core.pii.jwt_scan import _Match, _word

LOCAL_CLASS = r"A-Za-z0-9.!#$%&'*+/=?^_`{|}~\-"
DOMAIN = (r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
          r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\.[A-Za-z]{2,}\b")
#: The regex this class replaces, verbatim (kept for the differential test and repr).
REGEX = r"\b[" + LOCAL_CLASS + r"]+@" + DOMAIN

_RUN = re.compile(r"[" + LOCAL_CLASS + r"]+")
_DOMAIN = re.compile(DOMAIN)


class LinearEmailPattern:
    """Drop-in for the compiled EMAIL_RFC5322 regex (search/finditer/findall/sub/subn)."""

    pattern = REGEX
    flags = 0

    def __repr__(self) -> str:
        return f"LinearEmailPattern({REGEX!r})"

    def _iter(self, text: str, pos: int = 0, endpos: Optional[int] = None) -> Iterator[tuple[int, int]]:
        if not isinstance(text, str):
            raise TypeError(f"expected str, got {type(text).__name__}")
        if endpos is not None:
            text = text[:endpos]
        n = len(text)
        starts: list[int] = []
        ends: list[int] = []
        for m in _RUN.finditer(text):
            starts.append(m.start())
            ends.append(m.end())
        floor = pos
        at = text.find("@", pos)
        while at != -1:
            k = bisect.bisect_right(starts, at - 1) - 1
            if k >= 0 and ends[k] == at:                 # a local-part run ends right at '@'
                s = max(starts[k], floor)
                while s < at:                            # leftmost word boundary = regex's start
                    if (s > 0 and _word(text[s - 1])) != (s < n and _word(text[s])):
                        break
                    s += 1
                if s < at:
                    dm = _DOMAIN.match(text, at + 1)
                    if dm:
                        yield s, dm.end()
                        floor = dm.end()
                        at = text.find("@", floor)
                        continue
            at = text.find("@", at + 1)

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
            raise ValueError("LinearEmailPattern.sub supports literal replacements only")
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
