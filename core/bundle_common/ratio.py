"""The compression-ratio rule, the shared uncompressed budget and the budgeted inflate of gzip/bz2/xz streams (lifted from
``core/forge_bundle/validate.py``, ADR-2323 Decision 11)."""
from __future__ import annotations

import bz2
import lzma
import zlib
from typing import Mapping, Optional

from . import BundleCheckError
from .names import default_limits


def check_ratio(packed: int, unpacked: int, where: str, *, limits: Optional[Mapping] = None) -> None:
    """The container rule (<= 100:1 beyond 1 MiB) for a whole nested archive or stream:
    caps how much scanning a small upload can buy."""
    lim = limits if limits is not None else default_limits()
    if unpacked >= lim["ratio_check_min_bytes"] and unpacked > lim["max_compression_ratio"] * max(packed, 1):
        raise BundleCheckError("container", f"{where}: expands more than {lim['max_compression_ratio']}:1",
                               f"content expands more than {lim['max_compression_ratio']}:1")


class Budget:
    """Uncompressed bytes left for every nested archive / stream of one bundle together."""

    def __init__(self, left: int, *, limits: Optional[Mapping] = None) -> None:
        self.left = left
        self._limits = limits

    def spend(self, n: int, where: str) -> None:
        self.left -= n
        if self.left < 0:
            lim = self._limits if self._limits is not None else default_limits()
            raise BundleCheckError("container", f"{where}: nested content exceeds the bundle's "
                                                f"{lim['max_uncompressed_bytes']}-byte uncompressed budget",
                                   "the content exceeds the uncompressed budget")


def decompressor(data: bytes):
    if data[:2] == b"\x1f\x8b":
        return (lambda: zlib.decompressobj(16 + zlib.MAX_WBITS)), "gzip"
    if data[:3] == b"BZh":
        return bz2.BZ2Decompressor, "bz2"
    if data[:6] == b"\xfd7zXZ\x00":
        return lzma.LZMADecompressor, "xz"
    return None, None


def inflate(data: bytes, where: str, budget: Budget, *, limits: Optional[Mapping] = None) -> bytes | None:
    """Decompress EVERY member of a gzip/bz2/xz stream within the budget; None if it is not one."""
    make, kind = decompressor(data)
    if make is None:
        return None
    out = bytearray()
    rest = data
    try:
        while rest:
            dec = make()
            if kind == "gzip":
                chunk = dec.decompress(rest, 1 << 20)
                while True:
                    budget.spend(len(chunk), where)
                    out += chunk
                    check_ratio(len(data), len(out), where, limits=limits)
                    if not dec.unconsumed_tail:
                        break
                    chunk = dec.decompress(dec.unconsumed_tail, 1 << 20)
                if not dec.eof:
                    break
                rest = dec.unused_data
            else:
                chunk = dec.decompress(rest, max_length=1 << 20)
                while True:
                    budget.spend(len(chunk), where)
                    out += chunk
                    check_ratio(len(data), len(out), where, limits=limits)
                    if dec.eof or (not chunk and dec.needs_input):
                        break
                    chunk = dec.decompress(b"", max_length=1 << 20)
                if not dec.eof:
                    break
                rest = dec.unused_data
            # Concatenated members/streams (and trailing bytes) — the raw view of
            # the whole payload already covers non-stream trailing data.
            if rest and decompressor(rest)[0] is None:
                break
    except BundleCheckError:
        raise
    except (zlib.error, OSError, EOFError, lzma.LZMAError, ValueError) as exc:
        raise BundleCheckError("container", f"{where}: unreadable {kind} stream ({type(exc).__name__})",
                               "a compressed stream is unreadable") from None
    return bytes(out)
