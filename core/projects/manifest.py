"""``project.yaml`` - the truth about a project (ADR-2315 Decision 1).

Schema 1. Unknown top-level keys are kept on rewrite (forward compatibility); the keys below are
validated. The manifest holds no secret and no machine-local path (absolute interpreter paths live
in generated, git-ignored files under ``.corvin/local/``).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from .fsutil import atomic_write

SCHEMA = 1
FILE = "project.yaml"
#: kebab-case, short enough that the projector's scope label ``prj-<id>`` passes its 32-char rule (ADR-2316)
PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,27}$")
FEATURE_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
TEMPLATE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,40}@[0-9]{1,4}$")
DOCS_MODES = ("nested", "plain")
#: ids that collide with fixed routes under /projects (release review R9)
RESERVED_IDS = frozenset({"templates", "root", "chat-attachment", "detach", "adopt", "plan", "settings", "activity"})
ROLES = ("software", "data_science", "marketing", "product", "sales", "legal", "video", "research", "knowledge")
CLASSES = ("public", "internal", "confidential", "secret")      # the L34 vocabulary, lowest to highest
ACCESS = ("read", "write")
DEFAULT_CLASS = "internal"
DEFAULT_ACCESS = "read"
_TENANT_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]{0,63}$")
_MAX_BYTES = 64 * 1024


class ManifestError(ValueError):
    """The manifest is missing, unreadable or invalid."""


def validate(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ManifestError("project.yaml must be a mapping")
    if data.get("schema") != SCHEMA:
        raise ManifestError(f"unsupported schema {data.get('schema')!r} (expected {SCHEMA})")
    pid = data.get("id")
    if not isinstance(pid, str) or not PROJECT_ID_RE.match(pid):
        raise ManifestError("id must be lowercase kebab-case, 1-28 characters")
    if pid in RESERVED_IDS:
        raise ManifestError(f"the id {pid!r} is reserved")
    title = data.get("title", pid)
    if not isinstance(title, str) or not title.strip() or len(title) > 120:
        raise ManifestError("title must be a non-empty string of at most 120 characters")
    tenant = data.get("tenant")
    if not isinstance(tenant, str) or not _TENANT_RE.match(tenant):
        raise ManifestError("tenant must name the owning tenant")
    template = data.get("template")
    if template is not None and (not isinstance(template, str) or not TEMPLATE_RE.match(template)):
        raise ManifestError("template must look like '<id>@<n>'")
    if data.get("docs_mode", "nested") not in DOCS_MODES:
        raise ManifestError(f"docs_mode must be one of {DOCS_MODES}")
    if data.get("role") is not None and data.get("role") not in ROLES:
        raise ManifestError(f"role must be one of {ROLES}")
    if data.get("classification", DEFAULT_CLASS) not in CLASSES:
        raise ManifestError(f"classification must be one of {CLASSES}")
    if data.get("assistant_access", DEFAULT_ACCESS) not in ACCESS:
        raise ManifestError(f"assistant_access must be one of {ACCESS}")
    feats = data.get("features", {})
    if not isinstance(feats, dict) or not all(
            isinstance(k, str) and FEATURE_ID_RE.match(k) and isinstance(v, bool) for k, v in feats.items()):
        raise ManifestError("features must map feature ids to true/false")
    directory = data.get("directory")
    if directory is not None:
        peers = directory.get("peers") if isinstance(directory, dict) else None
        if not isinstance(peers, list) or not all(isinstance(x, str) and 0 < len(x) <= 128 for x in peers):
            raise ManifestError("directory.peers must be a list of peer ids")
    videos = data.get("videos")
    if videos is not None:
        if not isinstance(videos, dict):
            raise ManifestError("videos must be a mapping")
        langs = videos.get("languages", ["de", "en"])
        if not isinstance(langs, list) or not all(isinstance(x, str) and re.match(r"^[a-z]{2}$", x) for x in langs):
            raise ManifestError("videos.languages must be a list of two-letter codes")
        root = videos.get("root")
        if root is not None and (not isinstance(root, str) or "\x00" in root or len(root) > 500):
            raise ManifestError("videos.root must be a path string")
    return data


def parse(text: str) -> dict[str, Any]:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ManifestError(f"project.yaml is not valid YAML: {type(exc).__name__}") from None
    return validate(data)


def load(root: Path) -> dict[str, Any]:
    p = Path(root) / FILE
    if p.is_symlink():
        raise ManifestError("project.yaml is a symlink")
    try:
        if p.stat().st_size > _MAX_BYTES:
            raise ManifestError("project.yaml is too large")
        return parse(p.read_text(encoding="utf-8"))
    except OSError:
        raise ManifestError("project.yaml is missing or unreadable") from None


def dumps(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)


def write(root: Path, data: dict[str, Any]) -> None:
    validate(data)
    atomic_write(Path(root) / FILE, dumps(data).encode("utf-8"), root=Path(root))


def active_features(data: dict[str, Any]) -> list[str]:
    return [k for k, v in (data.get("features") or {}).items() if v]


def classification_of(data: dict[str, Any]) -> str:
    return data.get("classification") or DEFAULT_CLASS


def access_of(data: dict[str, Any]) -> str:
    return data.get("assistant_access") or DEFAULT_ACCESS
