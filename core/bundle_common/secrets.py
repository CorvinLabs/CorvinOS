"""The credential-shape scan shared by both bundle formats (lifted from ``core/forge_bundle/validate.py``, ADR-2323
Decision 11; ADR-2229 ADV-05 for the linear JWT check).

Only the credential detectors of the ADR-0297 gate run (never the prose detectors). Bytes are scanned in every view a
credential can hide in: latin-1 printable ASCII, UTF-16/32 at every alignment, and with string escapes resolved. A hit
names the detector class only - never the matched value. ``detect`` lets a caller inject the detector (the Forge Bundle
passes its own module attribute so its tests can replace it).
"""
from __future__ import annotations

import re
from typing import BinaryIO, Callable, Iterator, Optional

from core.pii.jwt_scan import LinearJwtPattern
from core.pii.sensitive import PIIDetectionFailedClosed, detect_named_types

from . import BundleCheckError

# Only the credential detectors of the ADR-0297 gate. Its prose detectors
# (personal names, addresses, @-handles, high entropy, `token = …`) fire on
# ordinary source code and on every sha256 in the envelope.
SECRET_TYPES = frozenset({
    "private_key_block",
    "aws_access_key",
    "aws_secret_key",
    "github_token",
    "github_pat",
    "slack_token",
    "google_api_key",
    "prefixed_secret_key",
    "jwt",
})

# The credential detectors of core.pii.sensitive. "jwt" there is now the
# linear core.pii.jwt_scan matcher (2026-10-08); this module keeps its own
# instance WITHOUT either word boundary — it can only reject more, never less. (The old comment here said "a 999-byte upload cost 43 s";
# that figure did not reproduce — 1.5 M samples of 0.5-2 KB, worst 3.3 ms —
# but the regex IS quadratic: 160 KB took 3.1 s, x4 input = x16 time.)
REGEX_SECRET_TYPES = SECRET_TYPES - {"jwt"}
# No start boundary on purpose: the previous check treated any non-ASCII-word char as a
# boundary, \b is Unicode — a JWT glued to "é" or "²" slipped through (R4-R-1, 511 of 1M
# fuzz cases). Without it every "eyJ…" candidate is judged, which can only reject more.
JWT = LinearJwtPattern(10, 10, 10, start_boundary=False, end_boundary=False)

Detect = Callable[[str, frozenset], object]


def has_jwt(text: str) -> bool:
    return JWT.search(text) is not None


def _secret(reason: str) -> BundleCheckError:
    return BundleCheckError("secrets", reason, "credential-shaped content")


def scan_text(text: str, where: str, *, detect: Optional[Detect] = None) -> None:
    det = detect if detect is not None else detect_named_types
    try:
        hits = set(det(text, REGEX_SECRET_TYPES))
        if has_jwt(text):
            hits.add("jwt")
    except PIIDetectionFailedClosed as exc:
        raise _secret(f"{where}: scan failed, treated as sensitive") from exc
    except Exception as exc:  # noqa: BLE001 — a scan that cannot finish must refuse
        raise _secret(f"{where}: scan failed ({type(exc).__name__}), treated as sensitive") from None
    if hits:
        # Names the detector class only — never the matched value.
        raise _secret(f"{where}: credential-shaped content ({', '.join(sorted(hits))})")


# bytes -> printable ASCII; every other byte (NUL, high bytes, controls but \t\n) becomes a space.
ASCII = bytes(b if 0x20 <= b <= 0x7E or b in (0x09, 0x0A) else 0x20 for b in range(256))
_ESCAPE_RE = re.compile(
    r"\\(?:u\{([0-9a-fA-F]{1,6})\}|u([0-9a-fA-F]{4})|U([0-9a-fA-F]{8})|x([0-9a-fA-F]{2})"
    r"|N\{([A-Za-z0-9 \-]{1,80})\}|([0-7]{1,3}))"
)


def unescape(text: str) -> str:
    import unicodedata

    def sub(m: re.Match) -> str:
        if m.group(5):
            try:
                return unicodedata.lookup(m.group(5))
            except KeyError:
                return " "
        if m.group(6):
            return chr(int(m.group(6), 8))
        cp = int(m.group(1) or m.group(2) or m.group(3) or m.group(4), 16)
        return chr(cp) if cp < 0x110000 else " "
    return _ESCAPE_RE.sub(sub, text)


def ascii_view(text: str) -> str:
    return text.encode("ascii", errors="replace").translate(ASCII).decode("ascii")


def text_views(data: bytes) -> Iterator[str]:
    views = [data.translate(ASCII).decode("ascii")]
    nuls = data.count(b"\x00")
    bom = data[:2] in (b"\xff\xfe", b"\xfe\xff")
    if bom or nuls * 50 >= len(data) > 0:
        # UTF-16/32 text is >= 2 % NUL bytes even when mostly CJK without a BOM
        # (~6 %); random binary is ~0.4 %. UTF-32 carries >= 2 NULs per BMP char.
        codecs = ["utf-16-le", "utf-16-be"]
        if nuls * 5 >= len(data) * 2 or data[:4] in (b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff"):
            codecs += ["utf-32-le", "utf-32-be"]
        for codec in codecs:
            unit = 4 if codec.startswith("utf-32") else 2
            for shift in range(unit):  # every alignment: a window may start mid-character
                chunk = data[shift:]
                chunk = chunk[: len(chunk) - len(chunk) % unit]
                views.append(ascii_view(chunk.decode(codec, errors="replace")))
    for view in views:
        yield view
        if "\\" in view:
            resolved = unescape(view)
            if resolved != view:
                yield resolved


WINDOW = 1 << 20
OVERLAP = 64 << 10   # longer than any credential shape the detectors know


def scan_bytes(data: bytes, where: str, *, detect: Optional[Detect] = None,
               scan: Optional[Callable[[str, str], None]] = None) -> None:
    """Scan in overlapping 1 MiB windows: memory stays flat on a 200 MiB payload. ``scan`` replaces :func:`scan_text` (the
    Forge Bundle routes its own alias through here)."""
    one = scan if scan is not None else (lambda text, w: scan_text(text, w, detect=detect))
    step = WINDOW - OVERLAP
    for off in range(0, max(len(data), 1), step):
        window = data[off: off + WINDOW]
        for view in text_views(window):
            one(view, where)
        if off + WINDOW >= len(data):
            break


class StreamScanner:
    """The windows of :func:`scan_bytes` over a stream fed in pieces (a file read in chunks): every window of WINDOW bytes
    overlaps the previous one by OVERLAP, so memory stays at about one window whatever the stream's length."""

    def __init__(self, where: str, *, detect: Optional[Detect] = None) -> None:
        self.where = where
        self.detect = detect
        self._buf = bytearray()
        self._scanned_any = False

    def feed(self, data: bytes) -> None:
        self._buf += data
        while len(self._buf) >= WINDOW:
            scan_bytes(bytes(self._buf[:WINDOW]), self.where, detect=self.detect)
            self._scanned_any = True
            del self._buf[: WINDOW - OVERLAP]

    def close(self) -> None:
        if self._buf and (not self._scanned_any or len(self._buf) > OVERLAP):
            scan_bytes(bytes(self._buf), self.where, detect=self.detect)
        elif not self._scanned_any:
            scan_bytes(b"", self.where, detect=self.detect)
        self._buf = bytearray()


def scan_stream(fh: BinaryIO, where: str, *, detect: Optional[Detect] = None, chunk: int = 1 << 20) -> None:
    sc = StreamScanner(where, detect=detect)
    while True:
        b = fh.read(chunk)
        if not b:
            break
        sc.feed(b)
    sc.close()
