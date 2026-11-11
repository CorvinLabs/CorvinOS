"""Container rules for one ZIP archive (lifted from ``core/forge_bundle/validate.py``, ADR-2323 Decision 11).

``check_name`` judges one entry name; ``check_container`` judges every entry of an open ``ZipFile`` (count, names,
case-insensitive duplicates, symlink and encrypted entries, total uncompressed size, per-entry compression ratio) and
returns the file entries. A caller may add its own per-entry rule (``entry_rule``): it is called for every entry after
the built-in checks of that entry and answers ``None`` or ``(detail, reason)``.
"""
from __future__ import annotations

import stat
import zipfile
from typing import Callable, Mapping, Optional

from . import BundleCheckError

EntryRule = Callable[[zipfile.ZipInfo], Optional[tuple[str, str]]]


def default_limits() -> Mapping:
    from core.forge_bundle.envelope import LIMITS  # noqa: PLC0415 - lazy: forge_bundle imports this module
    return LIMITS


def _reject(reason: str, detail: str) -> BundleCheckError:
    return BundleCheckError("container", reason, detail)


def check_name(name: str, where: str, *, limits: Optional[Mapping] = None) -> None:
    lim = limits if limits is not None else default_limits()
    max_chars = lim.get("max_name_chars", 512)
    max_bytes = lim.get("max_name_bytes")
    too_long = len(name) > max_chars or (max_bytes is not None and len(name.encode("utf-8", "surrogatepass")) > max_bytes)
    if not name or too_long:
        raise _reject(f"{where}: entry name empty or longer than {max_chars} chars", "an entry name is empty or too long")
    if "\\" in name or ":" in name or name.startswith("/"):
        raise _reject(f"{where}: unsafe entry name {name!r}",
                      "an entry name is absolute or contains a backslash or a colon")
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise _reject(f"{where}: control character in entry name {name!r}", "an entry name contains a control character")
    parts = name[:-1].split("/") if name.endswith("/") else name.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise _reject(f"{where}: path traversal or empty segment in {name!r}", "an entry name contains '..' or an empty segment")


def check_container(zf: zipfile.ZipFile, *, where: str, limits: Optional[Mapping] = None,
                    entry_rule: Optional[EntryRule] = None) -> list[zipfile.ZipInfo]:
    """Container-level safety for one archive; returns its file (non-dir) entries."""
    lim = limits if limits is not None else default_limits()
    infos = zf.infolist()
    if len(infos) > lim["max_entries"]:
        raise _reject(f"{where}: more than {lim['max_entries']} entries", "too many entries")
    seen: set[str] = set()
    total = 0
    files: list[zipfile.ZipInfo] = []
    for info in infos:
        check_name(info.filename, where, limits=lim)
        # Case-insensitive: two names that differ only in case collide on
        # Windows and default macOS filesystems.
        folded = info.filename.casefold()
        if folded in seen:
            raise _reject(f"{where}: duplicate entry {info.filename!r}", "two entries have the same name")
        seen.add(folded)
        if stat.S_ISLNK(info.external_attr >> 16):
            raise _reject(f"{where}: symlink entry {info.filename!r}", "an entry is a symbolic link")
        if info.flag_bits & 0x1:
            raise _reject(f"{where}: encrypted entry {info.filename!r}", "an entry is encrypted")
        if entry_rule is not None:
            hit = entry_rule(info)
            if hit is not None:
                detail, reason = hit
                raise _reject(f"{where}: {reason}", detail)
        if info.is_dir():
            continue
        total += info.file_size
        if total > lim["max_uncompressed_bytes"]:
            raise _reject(f"{where}: uncompressed size exceeds {lim['max_uncompressed_bytes']} bytes",
                          "the uncompressed size is above the limit")
        if info.file_size >= lim["ratio_check_min_bytes"]:
            ratio = info.file_size / max(info.compress_size, 1)
            if ratio > lim["max_compression_ratio"]:
                raise _reject(f"{where}: compression ratio {ratio:.0f}:1 on {info.filename!r}",
                              f"an entry is compressed more than {lim['max_compression_ratio']}:1")
        files.append(info)
    return files
