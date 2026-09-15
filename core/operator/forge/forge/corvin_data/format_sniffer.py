"""Format detection for the snapshot pipeline.

Two-stage detection: magic-byte check first (cheap, definitive for
Parquet's PAR1 magic), extension fall-back second (carries social
signal — ``.csv`` is almost always CSV even when the bytes look
ambiguous).

Returns one of ``"csv" | "tsv" | "json" | "jsonl" | "parquet"``,
or raises ``UnsupportedFormat`` for anything we don't recognise. The
caller (data_register) surfaces that as a clean ``400`` shape, not as
a silent fall-through to "treat as text" (which would be the
disastrous default).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal


Format = Literal["csv", "tsv", "json", "jsonl", "parquet"]


class UnsupportedFormat(ValueError):
    """Raised when format cannot be determined or is not supported."""


# Magic bytes — definitive when present.
_PARQUET_MAGIC = b"PAR1"

# Extension hints — used after magic-byte fallthrough.
_EXTENSION_MAP: dict[str, Format] = {
    ".csv":     "csv",
    ".tsv":     "tsv",
    ".json":    "json",
    ".jsonl":   "jsonl",
    ".ndjson":  "jsonl",  # alternative name
    ".parquet": "parquet",
    ".pq":      "parquet",
}


def _read_head(path: Path, n: int = 4096) -> bytes:
    with path.open("rb") as fh:
        return fh.read(n)


def _looks_like_json_object(head: bytes) -> bool:
    """First non-whitespace byte is ``{`` AND the head parses as JSON.

    A JSON file (``{...}`` or ``[...]``) starts with ``{`` or ``[``.
    JSONL starts with ``{`` per line — so the FIRST line being a
    complete JSON object is the signal that distinguishes JSON-as-array
    from JSONL.
    """
    stripped = head.lstrip()
    if not stripped or stripped[0:1] not in (b"{", b"["):
        return False
    try:
        json.loads(stripped.decode("utf-8", errors="replace"))
        return True
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False


def _looks_like_jsonl(head: bytes) -> bool:
    """At least the first line parses as a JSON object, AND the head
    contains more than one ``\\n`` (otherwise it's a single-line JSON
    file, not JSONL).
    """
    if head.count(b"\n") < 1:
        return False
    first_newline = head.find(b"\n")
    if first_newline < 0:
        return False
    first_line = head[:first_newline].strip()
    if not first_line.startswith(b"{"):
        return False
    try:
        obj = json.loads(first_line.decode("utf-8", errors="replace"))
        return isinstance(obj, dict)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False


def _looks_like_tsv(head: bytes) -> bool:
    """First line contains at least one tab AND no comma (TSV is a
    superset of CSV in semicolon terms; we use the tab presence as the
    discriminator).
    """
    first_newline = head.find(b"\n")
    if first_newline < 0:
        first_line = head
    else:
        first_line = head[:first_newline]
    return b"\t" in first_line


def _looks_like_csv(head: bytes) -> bool:
    """First line has at least one comma AND a reasonable line-break
    density (rules out random binary that happens to contain commas).
    """
    first_newline = head.find(b"\n")
    if first_newline < 0:
        return b"," in head
    first_line = head[:first_newline]
    return b"," in first_line


def sniff_format(path: str | Path, *, format_hint: str | None = None) -> Format:
    """Detect the format of *path*.

    Resolution order:
      1. If *format_hint* is provided AND valid, trust it.
      2. Magic bytes (Parquet's ``PAR1`` is the only one we check).
      3. Extension match against ``_EXTENSION_MAP``.
      4. Content heuristics (JSON-object → JSON, line-starts-with-{ →
         JSONL, tab-in-first-line → TSV, comma-in-first-line → CSV).
      5. Otherwise ``UnsupportedFormat``.

    The caller is the data_register MCP tool; surface errors verbatim.
    """
    p = Path(path)

    if format_hint is not None:
        fh = format_hint.lower().strip()
        if fh in _EXTENSION_MAP.values():
            return fh  # type: ignore[return-value]
        raise UnsupportedFormat(
            f"format hint {format_hint!r} is not one of "
            f"{sorted(set(_EXTENSION_MAP.values()))}"
        )

    if not p.exists():
        raise UnsupportedFormat(f"path does not exist: {path}")
    if not p.is_file():
        raise UnsupportedFormat(f"path is not a regular file: {path}")

    # Stage 1 — magic bytes
    head = _read_head(p, 4096)
    if head.startswith(_PARQUET_MAGIC):
        return "parquet"

    # Stage 2 — extension
    ext = p.suffix.lower()
    if ext in _EXTENSION_MAP:
        fmt = _EXTENSION_MAP[ext]
        # Extension is authoritative when content doesn't contradict.
        # E.g. a .csv with tabs is still CSV (operator named it).
        return fmt

    # Stage 3 — content heuristics (when extension is missing/unknown)
    if _looks_like_json_object(head):
        return "json"
    if _looks_like_jsonl(head):
        return "jsonl"
    if _looks_like_tsv(head):
        return "tsv"
    if _looks_like_csv(head):
        return "csv"

    raise UnsupportedFormat(
        f"could not determine format of {path} "
        f"(no magic bytes, unknown extension {ext!r}, no clear content signal)"
    )
