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

from . import safe_yaml
from .fsutil import atomic_write, is_link, read_text_any, write_at

SCHEMA = 1
FILE = "project.yaml"
#: kebab-case, short enough that the projector's scope label ``prj-<id>`` passes its 32-char rule (ADR-2316)
PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,27}$")
FEATURE_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
TEMPLATE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,40}@[0-9]{1,4}$")
DOCS_MODES = ("nested", "plain")
#: ids that collide with fixed routes under /projects (release review R9)
RESERVED_IDS = frozenset({"corvin", "templates", "root", "chat-attachment", "detach", "adopt", "plan", "settings", "activity"})
ROLES = ("software", "data_science", "marketing", "product", "sales", "legal", "video", "research", "knowledge")
CLASSES = ("public", "internal", "confidential", "secret")      # the L34 vocabulary, lowest to highest
ACCESS = ("read", "write")
DEFAULT_CLASS = "internal"
DEFAULT_ACCESS = "read"
#: Windows device names: a folder or file with one of these names (plus any extension) cannot be created or opened normally, so a NEW
#: project id (which becomes the default folder name) must never be one (an existing project.yaml keeps loading)
WINDOWS_DEVICE_NAMES = frozenset({"con", "prn", "aux", "nul", *(f"com{i}" for i in range(10)), *(f"lpt{i}" for i in range(10))})
_TENANT_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]{0,63}$")
_MAX_BYTES = 64 * 1024
#: C0/C1 control characters (line breaks, tab, NUL, ESC ...) and the Unicode line/paragraph separators
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")


def one_line(text: str, limit: int = 120) -> str:
    """*text* as one plain line for a prompt: control characters and line breaks become spaces, leading markdown heading or
    quote markers are dropped, runs of whitespace collapse (REVIEW-0066 m1)."""
    t = _CONTROL.sub(" ", str(text))
    t = re.sub(r"^[\s#>]+", "", t)
    return re.sub(r"\s+", " ", t).strip()[:limit]


class ManifestError(ValueError):
    """The manifest is missing, unreadable or invalid."""


#: the longest title a project may carry (create, rename, import, duplicate; the console dialogs clip to it)
TITLE_MAX = 120


def check_title(title: Any) -> str:
    """The one title rule of every path that names a project (create, import apply, duplicate): a non-empty string of at most
    ``TITLE_MAX`` characters on one line - a title is rendered into the chat's system prompt (REVIEW-0066 m1). Returns it
    unchanged; raises :class:`ManifestError` (HTTP 422) so a caller can refuse BEFORE anything is written (REVIEW-0071 N4)."""
    if not isinstance(title, str) or not title.strip() or len(title) > TITLE_MAX:
        raise ManifestError(f"title must be a non-empty string of at most {TITLE_MAX} characters")
    if _CONTROL.search(title):
        raise ManifestError("title must be one line (no line break or control character)")
    return title


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
    check_title(data.get("title", pid))
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


def is_new_id_allowed(pid: str) -> bool:
    """A NEW project id: the id rules, not a route word, and not a Windows device name (it is the default folder name)."""
    return bool(isinstance(pid, str) and PROJECT_ID_RE.match(pid)) and pid not in RESERVED_IDS and pid not in WINDOWS_DEVICE_NAMES


def parse(text: str) -> dict[str, Any]:
    try:
        data = safe_yaml.load(text)          # no aliases: an imported project.yaml is text the console did not write (REVIEW-0066 M1)
    except (yaml.YAMLError, ValueError, TypeError, RecursionError, OverflowError) as exc:
        # an impossible date (2024-02-30) is a plain ValueError from the YAML constructor - never a 500 for the whole list
        raise ManifestError(f"project.yaml is not valid YAML: {type(exc).__name__}") from None
    return validate(data)


def load(root: Path) -> dict[str, Any]:
    p = Path(root) / FILE
    if is_link(p):
        raise ManifestError("project.yaml is a link")
    try:
        if not p.is_file():
            raise ManifestError("project.yaml is missing or unreadable")
        if p.stat().st_size > _MAX_BYTES:
            raise ManifestError("project.yaml is too large")
        text = read_text_any(p, limit=_MAX_BYTES)
    except ManifestError:
        raise
    except OSError:
        raise ManifestError("project.yaml is missing or unreadable") from None
    except (UnicodeError, ValueError):
        # Windows editors write UTF-8 (with or without BOM) or UTF-16 with a BOM - those are read; anything else is named, never a 500
        raise ManifestError("project.yaml is not UTF-8 or UTF-16 text; save it as UTF-8") from None
    return parse(text)


def dumps(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)


def write(root: Path, data: dict[str, Any]) -> None:
    validate(data)
    atomic_write(Path(root) / FILE, dumps(data).encode("utf-8"), root=Path(root))


def write_into(dir_fd: int, data: dict[str, Any]) -> None:
    """:func:`write` into the directory held by *dir_fd* (POSIX), never through a path: create writes the manifest into the
    folder ``make_location`` made, so a component swapped for a link afterwards cannot place it at the link's target
    (REVIEW-0065, REVIEW-0063 m4)."""
    validate(data)
    write_at(dir_fd, FILE, dumps(data).encode("utf-8"))


def active_features(data: dict[str, Any]) -> list[str]:
    return [k for k, v in (data.get("features") or {}).items() if v]


def classification_of(data: dict[str, Any]) -> str:
    return data.get("classification") or DEFAULT_CLASS


def legacy_class(data: dict[str, Any]) -> str:
    """Class for a registry entry with no protection record (written before the record existed): the stricter of the default and what
    project.yaml says. project.yaml can only RAISE the class here, never lower it (release review round 3)."""
    c = data.get("classification")
    return c if c in CLASSES and CLASSES.index(c) > CLASSES.index(DEFAULT_CLASS) else DEFAULT_CLASS


def access_of(data: dict[str, Any]) -> str:
    return data.get("assistant_access") or DEFAULT_ACCESS
