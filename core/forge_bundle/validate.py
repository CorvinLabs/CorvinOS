"""Fail-closed Forge Bundle validation (ADR-2229 D3).

``validate_bundle`` runs six stages in order over the raw ZIP bytes and raises
:class:`BundleRejected` at the first defect. It extracts nothing, writes
nothing and reads no registry: the target install's inventory for the
staleness stage is injected by the caller.

  container  → ZIP is safe to read at all (paths, symlinks, sizes, ratio)
  envelope   → forge-bundle.json is a valid format-v1 manifest
  integrity  → every listed file exists with its size + sha256; nothing unlisted
  references → intra-bundle requirements resolve and form no cycle
  staleness  → requirements outside the bundle exist on the target
  secrets    → no credential-shaped string in any payload, at any nesting level

Every payload is scanned, binary or not: as raw bytes (latin-1, so one invalid
UTF-8 byte cannot switch the scan off), with NUL bytes removed (ASCII inside
UTF-16), and — when it parses as JSON — after unescaping (``\u0041KIA…``).
A payload that IS a ZIP archive (decided by its content, never by its name: a
wheel, a renamed ``.bin``) is opened and held to the container rules, and all
nested archives together share the bundle's uncompressed-size budget — they are
what SkillInstaller / StagingManager will later unpack.
"""
from __future__ import annotations

import hashlib
import io
import json
import stat
import zipfile
from dataclasses import dataclass
from typing import Collection, Iterator, Mapping

from core.pii.sensitive import PIIDetectionFailedClosed, detect_sensitive_types

from .envelope import (
    ARTIFACTS_PREFIX,
    ENVELOPE_NAME,
    LIMITS,
    ArtifactEntry,
    BundleEnvelope,
    EnvelopeError,
    Requirement,
    parse_envelope,
)

STAGES = ("container", "envelope", "integrity", "references", "staleness", "secrets")

# Only the credential detectors of the ADR-0297 gate. Its prose detectors
# (personal names, addresses, @-handles, high entropy, `token = …`) fire on
# ordinary source code and on every sha256 in the envelope.
_SECRET_TYPES = frozenset({
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

Known = Mapping[str, Mapping[str, Collection[str]]]


class BundleRejected(Exception):
    """The bundle failed a validation stage; nothing may be imported from it."""

    def __init__(self, stage: str, reason: str):
        super().__init__(f"[{stage}] {reason}")
        self.stage = stage
        self.reason = reason


@dataclass(frozen=True)
class BundleReport:
    envelope: BundleEnvelope
    total_uncompressed_bytes: int
    # Requirements outside the bundle that could not be checked (no inventory given).
    unchecked_references: tuple[Requirement, ...]
    # ADR-2229 D4: format v1 carries no origin proof. Always False; render it.
    origin_verified: bool = False


def _reject(stage: str, reason: str) -> BundleRejected:
    return BundleRejected(stage, reason)


def _check_name(name: str, where: str) -> None:
    if not name or len(name) > 512:
        raise _reject("container", f"{where}: entry name empty or longer than 512 chars")
    if "\\" in name or ":" in name or name.startswith("/"):
        raise _reject("container", f"{where}: unsafe entry name {name!r}")
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise _reject("container", f"{where}: control character in entry name {name!r}")
    parts = name[:-1].split("/") if name.endswith("/") else name.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise _reject("container", f"{where}: path traversal or empty segment in {name!r}")


def _check_container(zf: zipfile.ZipFile, *, where: str) -> list[zipfile.ZipInfo]:
    """Container-level safety for one archive; returns its file (non-dir) entries."""
    infos = zf.infolist()
    if len(infos) > LIMITS["max_entries"]:
        raise _reject("container", f"{where}: more than {LIMITS['max_entries']} entries")
    seen: set[str] = set()
    total = 0
    files: list[zipfile.ZipInfo] = []
    for info in infos:
        _check_name(info.filename, where)
        # Case-insensitive: two names that differ only in case collide on
        # Windows and default macOS filesystems.
        folded = info.filename.casefold()
        if folded in seen:
            raise _reject("container", f"{where}: duplicate entry {info.filename!r}")
        seen.add(folded)
        if stat.S_ISLNK(info.external_attr >> 16):
            raise _reject("container", f"{where}: symlink entry {info.filename!r}")
        if info.flag_bits & 0x1:
            raise _reject("container", f"{where}: encrypted entry {info.filename!r}")
        if info.is_dir():
            continue
        total += info.file_size
        if total > LIMITS["max_uncompressed_bytes"]:
            raise _reject("container", f"{where}: uncompressed size exceeds {LIMITS['max_uncompressed_bytes']} bytes")
        if info.file_size >= LIMITS["ratio_check_min_bytes"]:
            ratio = info.file_size / max(info.compress_size, 1)
            if ratio > LIMITS["max_compression_ratio"]:
                raise _reject("container", f"{where}: compression ratio {ratio:.0f}:1 on {info.filename!r}")
        files.append(info)
    return files


def _open_zip(data: bytes, *, where: str) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, zipfile.LargeZipFile, ValueError) as exc:
        raise _reject("container", f"{where}: not a readable ZIP ({type(exc).__name__})") from exc


def _read(zf: zipfile.ZipFile, name: str, stage: str) -> bytes:
    try:
        return zf.read(name)
    except (zipfile.BadZipFile, OSError, RuntimeError, EOFError) as exc:
        # CRC mismatch, truncated stream, unsupported compression.
        raise _reject(stage, f"cannot read {name!r} ({type(exc).__name__})") from exc


def _load_envelope(zf: zipfile.ZipFile, names: set[str]) -> BundleEnvelope:
    if ENVELOPE_NAME not in names:
        raise _reject("envelope", f"{ENVELOPE_NAME} missing at the archive root")
    if zf.getinfo(ENVELOPE_NAME).file_size > LIMITS["max_envelope_bytes"]:
        raise _reject("envelope", f"{ENVELOPE_NAME} larger than {LIMITS['max_envelope_bytes']} bytes")
    raw = _read(zf, ENVELOPE_NAME, "envelope")
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _reject("envelope", f"{ENVELOPE_NAME} is not UTF-8 JSON ({type(exc).__name__})") from exc
    try:
        return parse_envelope(decoded)
    except EnvelopeError as exc:
        raise _reject("envelope", str(exc)) from exc


def _check_integrity(zf: zipfile.ZipFile, envelope: BundleEnvelope, file_infos: list[zipfile.ZipInfo]) -> None:
    listed: dict[str, tuple[str, int]] = {}
    folded: set[str] = set()
    for art in envelope.artifacts:
        for f in art.files:
            if f.path.casefold() in folded:
                raise _reject("integrity", f"{f.path!r} listed twice")
            folded.add(f.path.casefold())
            listed[f.path] = (f.sha256, f.size)

    present = {i.filename for i in file_infos}
    unlisted = sorted(present - set(listed) - {ENVELOPE_NAME})
    if unlisted:
        raise _reject("integrity", f"files not declared in the envelope: {unlisted[:5]}")
    missing = sorted(set(listed) - present)
    if missing:
        raise _reject("integrity", f"declared files missing from the archive: {missing[:5]}")

    for path, (sha, size) in listed.items():
        data = _read(zf, path, "integrity")
        if len(data) != size:
            raise _reject("integrity", f"{path!r}: size {len(data)} != declared {size}")
        if hashlib.sha256(data).hexdigest() != sha:
            raise _reject("integrity", f"{path!r}: sha256 mismatch")


def _resolve_references(envelope: BundleEnvelope) -> list[Requirement]:
    """Check intra-bundle requirements; return the ones that point outside the bundle."""
    by_key: dict[tuple[str, str], ArtifactEntry] = {a.key: a for a in envelope.artifacts}
    external: list[Requirement] = []
    edges: dict[tuple[str, str], list[tuple[str, str]]] = {a.key: [] for a in envelope.artifacts}
    for art in envelope.artifacts:
        for req in art.requires:
            target = by_key.get((req.kind, req.id))
            if target is None:
                external.append(req)
                continue
            if req.version is not None and req.version != target.version:
                raise _reject(
                    "references",
                    f"{art.kind} {art.id!r} requires {req.kind} {req.id}@{req.version}, "
                    f"bundle carries {target.version}",
                )
            edges[art.key].append(target.key)

    # Iterative DFS: a recursive one is a stack-depth DoS on a hostile envelope.
    WHITE, GREY, BLACK = 0, 1, 2
    colour = {k: WHITE for k in edges}
    for start in edges:
        if colour[start] != WHITE:
            continue
        stack: list[tuple[tuple[str, str], Iterator[tuple[str, str]]]] = [(start, iter(edges[start]))]
        colour[start] = GREY
        while stack:
            node, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                colour[node] = BLACK
                stack.pop()
            elif colour[nxt] == GREY:
                raise _reject("references", f"dependency cycle through {nxt[0]} {nxt[1]!r}")
            elif colour[nxt] == WHITE:
                colour[nxt] = GREY
                stack.append((nxt, iter(edges[nxt])))
    return external


def _check_staleness(external: list[Requirement], known: Known) -> None:
    stale = []
    for req in external:
        versions = known.get(req.kind, {}).get(req.id)
        if versions is None or (req.version is not None and req.version not in versions):
            stale.append(f"{req.kind} {req.id}" + (f"@{req.version}" if req.version else ""))
    if stale:
        raise _reject("staleness", f"requires what the target install does not have: {stale[:10]}")


def _scan_text(text: str, where: str) -> None:
    try:
        hits = set(detect_sensitive_types(text)) & _SECRET_TYPES
    except PIIDetectionFailedClosed as exc:
        raise _reject("secrets", f"{where}: scan failed, treated as sensitive") from exc
    if hits:
        # Names the detector class only — never the matched value.
        raise _reject("secrets", f"{where}: credential-shaped content ({', '.join(sorted(hits))})")


class _Budget:
    """Uncompressed bytes left for every nested archive of one bundle together."""

    def __init__(self, left: int) -> None:
        self.left = left

    def spend(self, n: int, where: str) -> None:
        self.left -= n
        if self.left < 0:
            raise _reject("container", f"{where}: nested archives exceed the bundle's "
                                       f"{LIMITS['max_uncompressed_bytes']}-byte uncompressed budget")


def _scan_bytes(data: bytes, where: str) -> None:
    _scan_text(data.decode("latin-1"), where)
    if b"\x00" in data:
        _scan_text(data.replace(b"\x00", b"").decode("latin-1"), where)
    try:
        decoded = json.dumps(json.loads(data), ensure_ascii=False)
    except ValueError:
        return
    except RecursionError:
        raise _reject("secrets", f"{where}: JSON nested too deeply to scan") from None
    _scan_text(decoded, where)


def _scan_payload(data: bytes, where: str, depth: int, budget: _Budget) -> None:
    if zipfile.is_zipfile(io.BytesIO(data)):
        if depth >= LIMITS["max_nested_zip_depth"]:
            raise _reject("container", f"{where}: archive nested deeper than {LIMITS['max_nested_zip_depth']}")
        inner = _open_zip(data, where=where)
        with inner:
            inner_files = _check_container(inner, where=where)
            budget.spend(sum(i.file_size for i in inner_files), where)
            for info in inner_files:
                _scan_payload(_read(inner, info.filename, "secrets"), f"{where}!{info.filename}", depth + 1, budget)
        return
    _scan_bytes(data, where)


def validate_bundle(data: bytes, *, known: Known | None = None) -> BundleReport:
    """Validate a Forge Bundle ZIP fail-closed.

    ``known`` is the target install's inventory, ``{kind: {id: versions}}``.
    Without it the staleness stage cannot run and every requirement pointing
    outside the bundle is returned in ``unchecked_references`` — a caller that
    imports must supply it.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise _reject("container", "bundle must be bytes")
    if len(data) > LIMITS["max_compressed_bytes"]:
        raise _reject("container", f"archive larger than {LIMITS['max_compressed_bytes']} bytes")

    with _open_zip(bytes(data), where="bundle") as zf:
        file_infos = _check_container(zf, where="bundle")
        names = {i.filename for i in file_infos}
        for name in names - {ENVELOPE_NAME}:
            if not name.startswith(ARTIFACTS_PREFIX):
                raise _reject("container", f"entry outside {ARTIFACTS_PREFIX!r}: {name!r}")

        envelope = _load_envelope(zf, names)
        _check_integrity(zf, envelope, file_infos)
        external = _resolve_references(envelope)
        if known is not None:
            _check_staleness(external, known)

        total = sum(i.file_size for i in file_infos)
        budget = _Budget(LIMITS["max_uncompressed_bytes"] - total)
        _scan_bytes(_read(zf, ENVELOPE_NAME, "secrets"), ENVELOPE_NAME)
        for art in envelope.artifacts:
            for f in art.files:
                _scan_payload(_read(zf, f.path, "secrets"), f.path, 0, budget)

        return BundleReport(
            envelope=envelope,
            total_uncompressed_bytes=total,
            unchecked_references=tuple(external) if known is None else (),
        )
