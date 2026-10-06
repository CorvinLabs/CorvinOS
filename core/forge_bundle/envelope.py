"""Forge Bundle envelope — `forge-bundle.json` schema (ADR-2229 D1).

The envelope lists every artifact a bundle carries; each artifact's files live
under ``artifacts/<kind>/<id>@<version>/`` in that forge's native serialization.
Parsing is strict: unknown keys are refused, because ``format_version`` is the
compatibility contract and a silently ignored field is a field nobody checked.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

FORMAT = "corvin.forge-bundle"
FORMAT_VERSION = 1
ENVELOPE_NAME = "forge-bundle.json"
ARTIFACTS_PREFIX = "artifacts/"

ARTIFACT_KINDS = frozenset({"skill", "tool", "layer", "plugin"})

LIMITS = MappingProxyType({
    "max_compressed_bytes": 50 * 1024 * 1024,
    "max_uncompressed_bytes": 200 * 1024 * 1024,
    "max_entries": 2000,
    "max_artifacts": 64,
    "max_files_per_artifact": 500,
    "max_envelope_bytes": 1024 * 1024,
    "max_compression_ratio": 100,
    # Small files compress far beyond 100:1 legitimately (a run of zeros, a
    # repetitive fixture); the ratio guard only matters where it can exhaust disk.
    "ratio_check_min_bytes": 1024 * 1024,
    "max_description_chars": 2000,
    "max_requires_per_artifact": 64,
    "max_nested_zip_depth": 1,
})

# Same alphabet as the ADR-0674 skill installer: an id becomes a path segment.
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
_SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_TOP_KEYS = {"format", "format_version", "id", "version", "created_at", "description", "artifacts"}
_TOP_REQUIRED = {"format", "format_version", "id", "version", "artifacts"}
_ARTIFACT_KEYS = {"kind", "id", "version", "files", "requires"}
_ARTIFACT_REQUIRED = {"kind", "id", "version", "files"}
_FILE_KEYS = {"path", "sha256", "size"}
_REQUIRE_KEYS = {"kind", "id", "version"}


class EnvelopeError(ValueError):
    """The envelope is not a valid format-v1 forge bundle manifest."""

    def __init__(self, field: str, reason: str):
        super().__init__(f"{field}: {reason}")
        self.field = field
        self.reason = reason


@dataclass(frozen=True)
class FileEntry:
    path: str
    sha256: str
    size: int


@dataclass(frozen=True)
class Requirement:
    kind: str
    id: str
    version: str | None  # None = any version


@dataclass(frozen=True)
class ArtifactEntry:
    kind: str
    id: str
    version: str
    files: tuple[FileEntry, ...]
    requires: tuple[Requirement, ...]

    @property
    def key(self) -> tuple[str, str]:
        return (self.kind, self.id)

    @property
    def prefix(self) -> str:
        return f"{ARTIFACTS_PREFIX}{self.kind}/{self.id}@{self.version}/"


@dataclass(frozen=True)
class BundleEnvelope:
    id: str
    version: str
    created_at: str | None
    description: str | None
    artifacts: tuple[ArtifactEntry, ...]


def is_safe_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_SAFE_ID_RE.match(value)) and ".." not in value


def is_semver(value: Any) -> bool:
    return isinstance(value, str) and bool(_SEMVER_RE.match(value))


def _check_keys(obj: Any, *, field: str, allowed: set[str], required: set[str]) -> dict:
    if not isinstance(obj, dict):
        raise EnvelopeError(field, "must be an object")
    unknown = set(obj) - allowed
    if unknown:
        raise EnvelopeError(field, f"unknown keys {sorted(unknown)}")
    missing = required - set(obj)
    if missing:
        raise EnvelopeError(field, f"missing keys {sorted(missing)}")
    return obj


def _id(obj: dict, key: str, field: str) -> str:
    value = obj[key]
    if not is_safe_id(value):
        raise EnvelopeError(f"{field}.{key}", "must match ^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$ without '..'")
    return value


def _semver(obj: dict, key: str, field: str) -> str:
    value = obj[key]
    if not is_semver(value):
        raise EnvelopeError(f"{field}.{key}", "must be a semantic version (x.y.z)")
    return value


def _kind(obj: dict, field: str) -> str:
    value = obj["kind"]
    if value not in ARTIFACT_KINDS:
        raise EnvelopeError(f"{field}.kind", f"must be one of {sorted(ARTIFACT_KINDS)}")
    return value


def _parse_file(raw: Any, field: str, prefix: str) -> FileEntry:
    obj = _check_keys(raw, field=field, allowed=_FILE_KEYS, required=_FILE_KEYS)
    path, sha, size = obj["path"], obj["sha256"], obj["size"]
    if not isinstance(path, str) or not path.startswith(prefix) or path == prefix:
        raise EnvelopeError(f"{field}.path", f"must be a file under {prefix!r}")
    if not isinstance(sha, str) or not _SHA256_RE.match(sha):
        raise EnvelopeError(f"{field}.sha256", "must be 64 lowercase hex chars")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise EnvelopeError(f"{field}.size", "must be a non-negative integer")
    return FileEntry(path=path, sha256=sha, size=size)


def _parse_requirement(raw: Any, field: str) -> Requirement:
    obj = _check_keys(raw, field=field, allowed=_REQUIRE_KEYS, required={"kind", "id"})
    version = obj.get("version")
    if version is not None:
        version = _semver(obj, "version", field)
    return Requirement(kind=_kind(obj, field), id=_id(obj, "id", field), version=version)


def _parse_artifact(raw: Any, field: str) -> ArtifactEntry:
    obj = _check_keys(raw, field=field, allowed=_ARTIFACT_KEYS, required=_ARTIFACT_REQUIRED)
    kind = _kind(obj, field)
    aid = _id(obj, "id", field)
    version = _semver(obj, "version", field)
    prefix = f"{ARTIFACTS_PREFIX}{kind}/{aid}@{version}/"

    files_raw = obj["files"]
    if not isinstance(files_raw, list) or not files_raw:
        raise EnvelopeError(f"{field}.files", "must be a non-empty list")
    if len(files_raw) > LIMITS["max_files_per_artifact"]:
        raise EnvelopeError(f"{field}.files", f"more than {LIMITS['max_files_per_artifact']} files")
    files = tuple(_parse_file(f, f"{field}.files[{i}]", prefix) for i, f in enumerate(files_raw))

    requires_raw = obj.get("requires", [])
    if not isinstance(requires_raw, list):
        raise EnvelopeError(f"{field}.requires", "must be a list")
    if len(requires_raw) > LIMITS["max_requires_per_artifact"]:
        raise EnvelopeError(f"{field}.requires", f"more than {LIMITS['max_requires_per_artifact']} entries")
    requires = tuple(_parse_requirement(r, f"{field}.requires[{i}]") for i, r in enumerate(requires_raw))
    return ArtifactEntry(kind=kind, id=aid, version=version, files=files, requires=requires)


def parse_envelope(raw: Any) -> BundleEnvelope:
    """Parse a decoded ``forge-bundle.json``; raise :class:`EnvelopeError` on any defect."""
    obj = _check_keys(raw, field="envelope", allowed=_TOP_KEYS, required=_TOP_REQUIRED)
    if obj["format"] != FORMAT:
        raise EnvelopeError("format", f"must be {FORMAT!r}")
    fv = obj["format_version"]
    if isinstance(fv, bool) or fv != FORMAT_VERSION:
        raise EnvelopeError("format_version", f"unsupported (this build reads {FORMAT_VERSION})")

    created_at = obj.get("created_at")
    if created_at is not None and (not isinstance(created_at, str) or len(created_at) > 64):
        raise EnvelopeError("created_at", "must be a string of at most 64 chars")
    description = obj.get("description")
    if description is not None and (
        not isinstance(description, str) or len(description) > LIMITS["max_description_chars"]
    ):
        raise EnvelopeError("description", f"must be a string of at most {LIMITS['max_description_chars']} chars")

    artifacts_raw = obj["artifacts"]
    if not isinstance(artifacts_raw, list) or not artifacts_raw:
        raise EnvelopeError("artifacts", "must be a non-empty list")
    if len(artifacts_raw) > LIMITS["max_artifacts"]:
        raise EnvelopeError("artifacts", f"more than {LIMITS['max_artifacts']} artifacts")
    artifacts = tuple(_parse_artifact(a, f"artifacts[{i}]") for i, a in enumerate(artifacts_raw))

    seen: set[tuple[str, str]] = set()
    for i, art in enumerate(artifacts):
        # One version per (kind, id): an import must never have to pick between two.
        if art.key in seen:
            raise EnvelopeError(f"artifacts[{i}]", f"duplicate {art.kind} {art.id!r}")
        seen.add(art.key)

    return BundleEnvelope(
        id=_id(obj, "id", "envelope"),
        version=_semver(obj, "version", "envelope"),
        created_at=created_at,
        description=description,
        artifacts=artifacts,
    )
